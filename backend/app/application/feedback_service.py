"""回答反馈与 Badcase 分诊服务。

反馈不是自由文本日志：用户只能提交明确的正负评价和原因枚举。服务通过阶段事件关联
Agent Run，自动创建或更新待分诊项，不保存问题/回答正文，也不允许反馈直接写入知识库。
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.application.knowledge_service import KnowledgeService
from app.core.errors import ProcessingError, ResourceNotFoundError
from app.core.workspace import ensure_workspace
from app.domain.agent.models import (
    AgentRun,
    AnswerFeedback,
    AuditEvent,
    ConversationMessage,
    FeedbackTriage,
    RagStageEvent,
)
from app.domain.agent.repositories import (
    AnswerFeedbackRepository,
    AuditEventRepository,
    FeedbackTriageRepository,
    RagStageEventRepository,
)

_REASON_TO_CATEGORY = {
    "incorrect_answer": "generation_grounding",
    "missing_evidence": "retrieval_miss",
    "irrelevant_evidence": "rerank_error",
    "citation_problem": "generation_grounding",
    "outdated_information": "knowledge_stale_or_conflict",
    "other": "product_or_bug",
}
_TRIAGE_STATES = {"open", "in_review", "resolved", "dismissed"}
_RESOLUTION_TARGETS = {"knowledge_draft", "evaluation_case", "product_bug"}


class FeedbackService:
    """保证反馈、阶段事件和分诊项在同一工作区事务内一致。"""

    def __init__(self) -> None:
        self.feedbacks = AnswerFeedbackRepository()
        self.triages = FeedbackTriageRepository()
        self.stage_events = RagStageEventRepository()
        self.audit_events = AuditEventRepository()

    def submit(
        self,
        session: Session,
        *,
        assistant_message_id: str,
        sentiment: str,
        reason_code: str | None,
        workspace_id: str | None = None,
    ) -> tuple[AnswerFeedback, FeedbackTriage | None]:
        """幂等更新一条回答反馈；无帮助反馈进入或重开分诊队列。"""

        workspace = ensure_workspace(session, workspace_id=workspace_id)
        if sentiment not in {"helpful", "unhelpful"}:
            raise ProcessingError(message="反馈类型只支持 helpful 或 unhelpful。")
        if sentiment == "unhelpful" and reason_code not in _REASON_TO_CATEGORY:
            raise ProcessingError(message="无帮助反馈必须选择受支持的原因。")
        if sentiment == "helpful" and reason_code is not None:
            raise ProcessingError(message="helpful 反馈不应包含问题原因。")
        message, run = self._answer_context(session, assistant_message_id, workspace.id)
        feedback = self.feedbacks.get_by_message_for_update(
            session, message_id=assistant_message_id, workspace_id=workspace.id
        )
        event_ids = [
            event.id
            for event in self.stage_events.list_by_run(
                session, run_id=run.id, workspace_id=workspace.id
            )
        ]
        if feedback is None:
            feedback = AnswerFeedback(
                workspace_id=workspace.id,
                knowledge_base_id=run.knowledge_base_id,
                assistant_message_id=message.id,
                agent_run_id=run.id,
                sentiment=sentiment,
                reason_code=reason_code,
                stage_event_ids=event_ids,
            )
            self.feedbacks.create(session, feedback)
            session.flush()
        else:
            feedback.sentiment = sentiment
            feedback.reason_code = reason_code
            feedback.agent_run_id = run.id
            feedback.stage_event_ids = event_ids
        triage = self.triages.get_by_feedback_for_update(
            session, feedback_id=feedback.id, workspace_id=workspace.id
        )
        if sentiment == "unhelpful":
            category = _REASON_TO_CATEGORY[str(reason_code)]
            if triage is None:
                triage = FeedbackTriage(
                    workspace_id=workspace.id,
                    knowledge_base_id=run.knowledge_base_id,
                    feedback_id=feedback.id,
                    category=category,
                    state="open",
                )
                self.triages.create(session, triage)
            elif triage.state == "dismissed":
                triage.state = "open"
                triage.category = category
                triage.resolution_target = None
                triage.reviewer_id = None
                triage.reviewed_at = None
            else:
                triage.category = category
        elif triage is not None and triage.state in {"open", "in_review"}:
            triage.state = "dismissed"
            triage.reviewer_id = None
            triage.reviewed_at = datetime.now(UTC)
        self.audit_events.create(
            session,
            AuditEvent(
                workspace_id=workspace.id,
                actor_type="user",
                actor_id=None,
                action="answer_feedback_recorded",
                target_type="conversation_message",
                target_id=message.id,
                payload={
                    "sentiment": sentiment,
                    "reasonCode": reason_code or "",
                    "agentRunId": run.id,
                    "stageEventCount": str(len(event_ids)),
                },
            ),
        )
        session.commit()
        session.refresh(feedback)
        if triage is not None:
            session.refresh(triage)
        return feedback, triage

    def list_triage(
        self,
        session: Session,
        *,
        knowledge_base_id: str,
        state: str | None,
        workspace_id: str | None = None,
        limit: int = 50,
    ) -> list[FeedbackTriage]:
        workspace = ensure_workspace(session, workspace_id=workspace_id)
        KnowledgeService().get_knowledge_base(session, knowledge_base_id, workspace_id=workspace.id)
        if state is not None and state not in _TRIAGE_STATES:
            raise ProcessingError(message="分诊状态不受支持。")
        return self.triages.list_by_knowledge_base(
            session,
            knowledge_base_id=knowledge_base_id,
            workspace_id=workspace.id,
            state=state,
            limit=limit,
        )

    def review_triage(
        self,
        session: Session,
        *,
        triage_id: str,
        state: str,
        resolution_target: str | None,
        reviewer_id: str | None,
        workspace_id: str | None = None,
    ) -> FeedbackTriage:
        """人工分诊只记录决定；创建草稿或评测用例由后续受审批流程处理。"""

        workspace = ensure_workspace(session, workspace_id=workspace_id)
        if state not in _TRIAGE_STATES:
            raise ProcessingError(message="分诊状态不受支持。")
        if resolution_target is not None and resolution_target not in _RESOLUTION_TARGETS:
            raise ProcessingError(message="分诊目标不受支持。")
        if state == "resolved" and resolution_target is None:
            raise ProcessingError(message="完成分诊时必须指定知识草稿、评测用例或产品缺陷目标。")
        triage = session.scalar(
            select(FeedbackTriage)
            .where(FeedbackTriage.id == triage_id, FeedbackTriage.workspace_id == workspace.id)
            .with_for_update()
        )
        if triage is None:
            raise ResourceNotFoundError(details={"resource": "feedback_triage"})
        if triage.state in {"resolved", "dismissed"}:
            raise ProcessingError(message="已完成或已忽略的分诊项不能再次修改。")
        triage.state = state
        triage.resolution_target = resolution_target
        triage.reviewer_id = reviewer_id
        triage.reviewed_at = datetime.now(UTC) if state in {"resolved", "dismissed"} else None
        self.audit_events.create(
            session,
            AuditEvent(
                workspace_id=workspace.id,
                actor_type="user",
                actor_id=reviewer_id,
                action="feedback_triage_reviewed",
                target_type="feedback_triage",
                target_id=triage.id,
                payload={"state": state, "resolutionTarget": resolution_target or ""},
            ),
        )
        session.commit()
        session.refresh(triage)
        return triage

    @staticmethod
    def _answer_context(
        session: Session, assistant_message_id: str, workspace_id: str
    ) -> tuple[ConversationMessage, AgentRun]:
        message = session.scalar(
            select(ConversationMessage).where(
                ConversationMessage.id == assistant_message_id,
                ConversationMessage.workspace_id == workspace_id,
                ConversationMessage.role == "assistant",
                ConversationMessage.state == "completed",
            )
        )
        if message is None:
            raise ResourceNotFoundError(details={"resource": "assistant_message"})
        event = session.scalar(
            select(RagStageEvent)
            .where(
                RagStageEvent.assistant_message_id == message.id,
                RagStageEvent.workspace_id == workspace_id,
                RagStageEvent.stage == "answer",
                RagStageEvent.state == "completed",
            )
            .order_by(RagStageEvent.created_at.desc())
            .limit(1)
        )
        if event is None:
            raise ProcessingError(message="该回答缺少可追溯的 RAG 运行，暂不支持提交反馈。")
        run = session.scalar(
            select(AgentRun).where(
                AgentRun.id == event.agent_run_id,
                AgentRun.workspace_id == workspace_id,
            )
        )
        if run is None:
            raise ResourceNotFoundError(details={"resource": "agent_run"})
        return message, run
