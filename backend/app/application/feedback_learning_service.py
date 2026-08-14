"""反馈闭环的受控回流服务。

反馈不是训练数据，也不能把一次点踩直接写入知识库。本服务把已经完成分诊的
结构化结论转成待审核草稿，并将最终写入限制在授权角色的显式批准动作中。
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TypeVar

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.application.knowledge_service import KnowledgeService
from app.core.errors import AuthorizationError, ProcessingError, ResourceNotFoundError
from app.core.workspace import configured_actor_role, ensure_workspace
from app.domain.agent.models import (
    AuditEvent,
    FeedbackEvaluationCase,
    FeedbackKnowledgeDraft,
    FeedbackTriage,
)
from app.domain.agent.repositories import (
    AuditEventRepository,
    FeedbackEvaluationCaseRepository,
    FeedbackKnowledgeDraftRepository,
)

_DRAFT_STATES = {"pending", "approved", "rejected"}
FeedbackDraftType = TypeVar("FeedbackDraftType", FeedbackKnowledgeDraft, FeedbackEvaluationCase)


class FeedbackLearningService:
    """隔离反馈分诊、草稿审核和知识/评测回流三个边界。"""

    def __init__(self) -> None:
        self.knowledge_drafts = FeedbackKnowledgeDraftRepository()
        self.evaluation_cases = FeedbackEvaluationCaseRepository()
        self.audit_events = AuditEventRepository()

    def create_knowledge_draft(
        self,
        session: Session,
        *,
        feedback_triage_id: str,
        title: str,
        content: str,
        actor_id: str | None,
        actor_role: str | None = None,
        workspace_id: str | None = None,
        commit: bool = True,
    ) -> FeedbackKnowledgeDraft:
        workspace = ensure_workspace(session, workspace_id=workspace_id)
        self._require_reviewer(workspace.id, actor_id, actor_role)
        triage = self._get_eligible_triage(
            session,
            triage_id=feedback_triage_id,
            workspace_id=workspace.id,
            resolution_target="knowledge_draft",
        )
        if self.knowledge_drafts.get_by_triage_for_update(
            session, triage_id=triage.id, workspace_id=workspace.id
        ):
            raise ProcessingError(message="该分诊项已经创建过知识草稿。")
        if not title.strip() or not content.strip():
            raise ProcessingError(message="知识草稿标题和正文不能为空。")

        draft = FeedbackKnowledgeDraft(
            workspace_id=workspace.id,
            knowledge_base_id=triage.knowledge_base_id,
            feedback_triage_id=triage.id,
            title=title.strip(),
            content=content.strip(),
        )
        self.knowledge_drafts.create(session, draft)
        # UUID 默认值在 flush 时生成，审计事件必须引用真实资源 ID。
        session.flush()
        self._audit(
            session,
            workspace_id=workspace.id,
            actor_id=actor_id,
            action="feedback_knowledge_draft_created",
            target_id=draft.id,
        )
        return self._finish(session, draft, commit)

    def review_knowledge_draft(
        self,
        session: Session,
        *,
        draft_id: str,
        decision: str,
        actor_id: str | None,
        actor_role: str | None = None,
        workspace_id: str | None = None,
        commit: bool = True,
    ) -> FeedbackKnowledgeDraft:
        workspace = ensure_workspace(session, workspace_id=workspace_id)
        self._require_reviewer(workspace.id, actor_id, actor_role)
        if decision not in {"approved", "rejected"}:
            raise ProcessingError(message="草稿审核决定不受支持。")
        draft = self.knowledge_drafts.get_for_update(
            session, draft_id=draft_id, workspace_id=workspace.id
        )
        if draft is None:
            raise ResourceNotFoundError(details={"resource": "feedback_knowledge_draft"})
        if draft.state != "pending":
            raise ProcessingError(message="仅待审核知识草稿可被处理。")

        if decision == "approved":
            # create_note 使用同一 Session 且不提交，索引失败会使草稿状态与笔记一起回滚。
            note = KnowledgeService().create_note(
                session,
                knowledge_base_id=draft.knowledge_base_id,
                title=draft.title,
                content=draft.content,
                workspace_id=workspace.id,
                commit=False,
            )
            draft.created_note_id = note.id
        draft.state = decision
        draft.reviewer_id = actor_id
        draft.reviewed_at = datetime.now(UTC)
        self._audit(
            session,
            workspace_id=workspace.id,
            actor_id=actor_id,
            action=f"feedback_knowledge_draft_{decision}",
            target_id=draft.id,
        )
        return self._finish(session, draft, commit)

    def list_knowledge_drafts(
        self,
        session: Session,
        *,
        knowledge_base_id: str,
        state: str | None,
        workspace_id: str | None = None,
        limit: int = 50,
    ) -> list[FeedbackKnowledgeDraft]:
        workspace = ensure_workspace(session, workspace_id=workspace_id)
        self._validate_state(state)
        KnowledgeService().get_knowledge_base(session, knowledge_base_id, workspace_id=workspace.id)
        return self.knowledge_drafts.list_by_knowledge_base(
            session,
            knowledge_base_id=knowledge_base_id,
            workspace_id=workspace.id,
            state=state,
            limit=limit,
        )

    def create_evaluation_case(
        self,
        session: Session,
        *,
        feedback_triage_id: str,
        query: str,
        expected_source_titles: list[str],
        required_keywords: list[str],
        limit: int,
        actor_id: str | None,
        actor_role: str | None = None,
        workspace_id: str | None = None,
        commit: bool = True,
    ) -> FeedbackEvaluationCase:
        workspace = ensure_workspace(session, workspace_id=workspace_id)
        self._require_reviewer(workspace.id, actor_id, actor_role)
        triage = self._get_eligible_triage(
            session,
            triage_id=feedback_triage_id,
            workspace_id=workspace.id,
            resolution_target="evaluation_case",
        )
        if self.evaluation_cases.get_by_triage_for_update(
            session, triage_id=triage.id, workspace_id=workspace.id
        ):
            raise ProcessingError(message="该分诊项已经创建过回归评测用例。")
        cleaned_titles = self._clean_values(expected_source_titles, "预期来源标题")
        cleaned_keywords = self._clean_values(required_keywords, "必备关键词", allow_empty=True)
        if not 1 <= limit <= 20:
            raise ProcessingError(message="评测用例的检索数量必须在 1 到 20 之间。")

        case = FeedbackEvaluationCase(
            workspace_id=workspace.id,
            knowledge_base_id=triage.knowledge_base_id,
            feedback_triage_id=triage.id,
            query=query.strip(),
            expected_source_titles=cleaned_titles,
            required_keywords=cleaned_keywords,
            limit=limit,
        )
        if not case.query:
            raise ProcessingError(message="评测问题不能为空。")
        self.evaluation_cases.create(session, case)
        # 与知识草稿一致，先生成主键再写入审计关联。
        session.flush()
        self._audit(
            session,
            workspace_id=workspace.id,
            actor_id=actor_id,
            action="feedback_evaluation_case_created",
            target_id=case.id,
        )
        return self._finish(session, case, commit)

    def review_evaluation_case(
        self,
        session: Session,
        *,
        case_id: str,
        decision: str,
        actor_id: str | None,
        actor_role: str | None = None,
        workspace_id: str | None = None,
        commit: bool = True,
    ) -> FeedbackEvaluationCase:
        workspace = ensure_workspace(session, workspace_id=workspace_id)
        self._require_reviewer(workspace.id, actor_id, actor_role)
        if decision not in {"approved", "rejected"}:
            raise ProcessingError(message="评测用例审核决定不受支持。")
        case = self.evaluation_cases.get_for_update(
            session, case_id=case_id, workspace_id=workspace.id
        )
        if case is None:
            raise ResourceNotFoundError(details={"resource": "feedback_evaluation_case"})
        if case.state != "pending":
            raise ProcessingError(message="仅待审核评测用例可被处理。")
        case.state = decision
        case.reviewer_id = actor_id
        case.reviewed_at = datetime.now(UTC)
        self._audit(
            session,
            workspace_id=workspace.id,
            actor_id=actor_id,
            action=f"feedback_evaluation_case_{decision}",
            target_id=case.id,
        )
        return self._finish(session, case, commit)

    def list_evaluation_cases(
        self,
        session: Session,
        *,
        knowledge_base_id: str,
        state: str | None,
        workspace_id: str | None = None,
        limit: int = 50,
    ) -> list[FeedbackEvaluationCase]:
        workspace = ensure_workspace(session, workspace_id=workspace_id)
        self._validate_state(state)
        KnowledgeService().get_knowledge_base(session, knowledge_base_id, workspace_id=workspace.id)
        return self.evaluation_cases.list_by_knowledge_base(
            session,
            knowledge_base_id=knowledge_base_id,
            workspace_id=workspace.id,
            state=state,
            limit=limit,
        )

    @staticmethod
    def _finish(
        session: Session,
        item: FeedbackDraftType,
        commit: bool,
    ) -> FeedbackDraftType:
        session.flush()
        if commit:
            session.commit()
            session.refresh(item)
        return item

    @staticmethod
    def _clean_values(values: list[str], field: str, *, allow_empty: bool = False) -> list[str]:
        cleaned = list(dict.fromkeys(value.strip() for value in values if value.strip()))
        if not cleaned and not allow_empty:
            raise ProcessingError(message=f"{field}至少需要一项。")
        return cleaned

    @staticmethod
    def _validate_state(state: str | None) -> None:
        if state is not None and state not in _DRAFT_STATES:
            raise ProcessingError(message="草稿状态不受支持。")

    @staticmethod
    def _require_reviewer(
        workspace_id: str, actor_id: str | None, actor_role: str | None = None
    ) -> None:
        role = configured_actor_role(
            workspace_id=workspace_id, actor_id=actor_id, trusted_role=actor_role
        )
        if role not in {"approver", "owner"}:
            raise AuthorizationError(message="当前角色没有反馈回流审批权限。")

    @staticmethod
    def _get_eligible_triage(
        session: Session,
        *,
        triage_id: str,
        workspace_id: str,
        resolution_target: str,
    ) -> FeedbackTriage:
        triage = session.scalar(
            select(FeedbackTriage)
            .where(
                FeedbackTriage.id == triage_id,
                FeedbackTriage.workspace_id == workspace_id,
            )
            .with_for_update()
        )
        if triage is None:
            raise ResourceNotFoundError(details={"resource": "feedback_triage"})
        if triage.state != "resolved" or triage.resolution_target != resolution_target:
            raise ProcessingError(message="仅已完成且目标匹配的分诊项可以创建此类草稿。")
        return triage

    def _audit(
        self,
        session: Session,
        *,
        workspace_id: str,
        actor_id: str | None,
        action: str,
        target_id: str,
    ) -> None:
        # 审计只留下动作和资源 ID，不复制问题、回答、草稿正文或 Prompt。
        self.audit_events.create(
            session,
            AuditEvent(
                workspace_id=workspace_id,
                actor_type="user",
                actor_id=actor_id,
                action=action,
                target_type="feedback_learning",
                target_id=target_id,
                payload={},
            ),
        )
