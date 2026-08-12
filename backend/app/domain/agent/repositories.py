"""Agent 领域仓储。"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.agent.models import (
    AgentCheckpoint,
    AgentRun,
    AgentToolCall,
    AnswerFeedback,
    AuditEvent,
    ChangeProposal,
    Conversation,
    ConversationMessage,
    FeedbackEvaluationCase,
    FeedbackKnowledgeDraft,
    FeedbackTriage,
    RagBadcase,
    RagStageEvent,
)


class ConversationRepository:
    def create(self, session: Session, conversation: Conversation) -> Conversation:
        session.add(conversation)
        return conversation

    def get(
        self, session: Session, conversation_id: str, *, workspace_id: str
    ) -> Conversation | None:
        return session.scalar(
            select(Conversation).where(
                Conversation.id == conversation_id,
                Conversation.workspace_id == workspace_id,
            )
        )

    def list_by_knowledge_base(
        self,
        session: Session,
        *,
        knowledge_base_id: str,
        workspace_id: str,
        limit: int,
    ) -> list[Conversation]:
        return list(
            session.scalars(
                select(Conversation)
                .where(
                    Conversation.knowledge_base_id == knowledge_base_id,
                    Conversation.workspace_id == workspace_id,
                    Conversation.state == "active",
                )
                .order_by(Conversation.updated_at.desc())
                .limit(limit)
            )
        )


class ConversationMessageRepository:
    def create(self, session: Session, message: ConversationMessage) -> ConversationMessage:
        session.add(message)
        return message

    def list_by_conversation(
        self, session: Session, *, conversation_id: str, workspace_id: str
    ) -> list[ConversationMessage]:
        return list(
            session.scalars(
                select(ConversationMessage)
                .where(
                    ConversationMessage.conversation_id == conversation_id,
                    ConversationMessage.workspace_id == workspace_id,
                )
                .order_by(ConversationMessage.created_at.asc())
            )
        )


class AgentRunRepository:
    def create(self, session: Session, run: AgentRun) -> AgentRun:
        session.add(run)
        return run

    def get(self, session: Session, run_id: str, *, workspace_id: str) -> AgentRun | None:
        return session.scalar(
            select(AgentRun).where(
                AgentRun.id == run_id,
                AgentRun.workspace_id == workspace_id,
            )
        )

    def get_by_thread(
        self, session: Session, thread_id: str, *, workspace_id: str
    ) -> AgentRun | None:
        """按线程获取最近一次运行，始终附带 workspace 条件避免跨租户恢复。"""

        return session.scalar(
            select(AgentRun)
            .where(AgentRun.thread_id == thread_id, AgentRun.workspace_id == workspace_id)
            .order_by(AgentRun.created_at.desc())
            .limit(1)
        )


class AgentToolCallRepository:
    """工具调用仓储；事务提交仍由 Runtime 应用服务统一控制。"""

    def create(self, session: Session, call: AgentToolCall) -> AgentToolCall:
        session.add(call)
        return call

    def list_by_run(
        self, session: Session, *, run_id: str, workspace_id: str
    ) -> list[AgentToolCall]:
        return list(
            session.scalars(
                select(AgentToolCall)
                .where(
                    AgentToolCall.agent_run_id == run_id,
                    AgentToolCall.workspace_id == workspace_id,
                )
                .order_by(AgentToolCall.created_at.asc())
            )
        )


class AgentCheckpointRepository:
    """Runtime 快照仓储；事务由应用服务统一提交。"""

    def create(self, session: Session, checkpoint: AgentCheckpoint) -> AgentCheckpoint:
        session.add(checkpoint)
        return checkpoint

    def list_by_run(
        self, session: Session, *, run_id: str, workspace_id: str
    ) -> list[AgentCheckpoint]:
        return list(
            session.scalars(
                select(AgentCheckpoint)
                .where(
                    AgentCheckpoint.agent_run_id == run_id,
                    AgentCheckpoint.workspace_id == workspace_id,
                )
                .order_by(AgentCheckpoint.sequence.asc())
            )
        )

    def latest_by_run(
        self, session: Session, *, run_id: str, workspace_id: str
    ) -> AgentCheckpoint | None:
        return session.scalar(
            select(AgentCheckpoint)
            .where(
                AgentCheckpoint.agent_run_id == run_id,
                AgentCheckpoint.workspace_id == workspace_id,
            )
            .order_by(AgentCheckpoint.sequence.desc())
            .limit(1)
        )


class ChangeProposalRepository:
    def create(self, session: Session, proposal: ChangeProposal) -> ChangeProposal:
        session.add(proposal)
        return proposal

    def get(
        self, session: Session, proposal_id: str, *, workspace_id: str
    ) -> ChangeProposal | None:
        return session.scalar(
            select(ChangeProposal).where(
                ChangeProposal.id == proposal_id,
                ChangeProposal.workspace_id == workspace_id,
            )
        )

    def get_pending_for_update(
        self, session: Session, proposal_id: str, *, workspace_id: str
    ) -> ChangeProposal | None:
        """在支持行锁的数据库中锁定 pending 行，避免并发审批重复写入。"""

        return session.scalar(
            select(ChangeProposal)
            .where(
                ChangeProposal.id == proposal_id,
                ChangeProposal.workspace_id == workspace_id,
                ChangeProposal.state == "pending",
            )
            .with_for_update()
        )

    def get_pending_by_run(
        self, session: Session, *, run_id: str, workspace_id: str
    ) -> ChangeProposal | None:
        """返回 Runtime 下唯一的待审批提议，保证重试不会重复创建写动作。"""

        return session.scalar(
            select(ChangeProposal)
            .where(
                ChangeProposal.agent_run_id == run_id,
                ChangeProposal.workspace_id == workspace_id,
                ChangeProposal.state == "pending",
            )
            .order_by(ChangeProposal.created_at.asc())
            .limit(1)
        )


class AuditEventRepository:
    def create(self, session: Session, event: AuditEvent) -> AuditEvent:
        session.add(event)
        return event


class RagStageEventRepository:
    """阶段事件仓储，读取必须按运行和工作区共同限定。"""

    def create(self, session: Session, event: RagStageEvent) -> RagStageEvent:
        session.add(event)
        return event

    def get_by_sequence(
        self, session: Session, *, run_id: str, sequence: int, workspace_id: str
    ) -> RagStageEvent | None:
        return session.scalar(
            select(RagStageEvent).where(
                RagStageEvent.agent_run_id == run_id,
                RagStageEvent.sequence == sequence,
                RagStageEvent.workspace_id == workspace_id,
            )
        )

    def list_by_run(
        self, session: Session, *, run_id: str, workspace_id: str
    ) -> list[RagStageEvent]:
        return list(
            session.scalars(
                select(RagStageEvent)
                .where(
                    RagStageEvent.agent_run_id == run_id,
                    RagStageEvent.workspace_id == workspace_id,
                )
                .order_by(RagStageEvent.sequence.asc())
            )
        )


class RagBadcaseRepository:
    """待复核质量问题仓储，自动归因不会重复创建同一类别。"""

    def create(self, session: Session, badcase: RagBadcase) -> RagBadcase:
        session.add(badcase)
        return badcase

    def get_by_category(
        self, session: Session, *, run_id: str, category: str, workspace_id: str
    ) -> RagBadcase | None:
        return session.scalar(
            select(RagBadcase).where(
                RagBadcase.agent_run_id == run_id,
                RagBadcase.category == category,
                RagBadcase.workspace_id == workspace_id,
            )
        )

    def list_by_run(self, session: Session, *, run_id: str, workspace_id: str) -> list[RagBadcase]:
        return list(
            session.scalars(
                select(RagBadcase)
                .where(
                    RagBadcase.agent_run_id == run_id,
                    RagBadcase.workspace_id == workspace_id,
                )
                .order_by(RagBadcase.created_at.asc())
            )
        )


class AnswerFeedbackRepository:
    def get_by_message_for_update(
        self, session: Session, *, message_id: str, workspace_id: str
    ) -> AnswerFeedback | None:
        return session.scalar(
            select(AnswerFeedback)
            .where(
                AnswerFeedback.assistant_message_id == message_id,
                AnswerFeedback.workspace_id == workspace_id,
            )
            .with_for_update()
        )

    def create(self, session: Session, feedback: AnswerFeedback) -> AnswerFeedback:
        session.add(feedback)
        return feedback


class FeedbackTriageRepository:
    def get_by_feedback_for_update(
        self, session: Session, *, feedback_id: str, workspace_id: str
    ) -> FeedbackTriage | None:
        return session.scalar(
            select(FeedbackTriage)
            .where(
                FeedbackTriage.feedback_id == feedback_id,
                FeedbackTriage.workspace_id == workspace_id,
            )
            .with_for_update()
        )

    def create(self, session: Session, triage: FeedbackTriage) -> FeedbackTriage:
        session.add(triage)
        return triage

    def list_by_knowledge_base(
        self,
        session: Session,
        *,
        knowledge_base_id: str,
        workspace_id: str,
        state: str | None,
        limit: int,
    ) -> list[FeedbackTriage]:
        statement = select(FeedbackTriage).where(
            FeedbackTriage.knowledge_base_id == knowledge_base_id,
            FeedbackTriage.workspace_id == workspace_id,
        )
        if state is not None:
            statement = statement.where(FeedbackTriage.state == state)
        return list(
            session.scalars(statement.order_by(FeedbackTriage.created_at.desc()).limit(limit))
        )


class FeedbackKnowledgeDraftRepository:
    """知识草稿仓储，所有读取均携带工作区条件。"""

    def create(self, session: Session, draft: FeedbackKnowledgeDraft) -> FeedbackKnowledgeDraft:
        session.add(draft)
        return draft

    def get_for_update(
        self, session: Session, *, draft_id: str, workspace_id: str
    ) -> FeedbackKnowledgeDraft | None:
        return session.scalar(
            select(FeedbackKnowledgeDraft)
            .where(
                FeedbackKnowledgeDraft.id == draft_id,
                FeedbackKnowledgeDraft.workspace_id == workspace_id,
            )
            .with_for_update()
        )

    def get_by_triage_for_update(
        self, session: Session, *, triage_id: str, workspace_id: str
    ) -> FeedbackKnowledgeDraft | None:
        return session.scalar(
            select(FeedbackKnowledgeDraft)
            .where(
                FeedbackKnowledgeDraft.feedback_triage_id == triage_id,
                FeedbackKnowledgeDraft.workspace_id == workspace_id,
            )
            .with_for_update()
        )

    def list_by_knowledge_base(
        self,
        session: Session,
        *,
        knowledge_base_id: str,
        workspace_id: str,
        state: str | None,
        limit: int,
    ) -> list[FeedbackKnowledgeDraft]:
        statement = select(FeedbackKnowledgeDraft).where(
            FeedbackKnowledgeDraft.knowledge_base_id == knowledge_base_id,
            FeedbackKnowledgeDraft.workspace_id == workspace_id,
        )
        if state is not None:
            statement = statement.where(FeedbackKnowledgeDraft.state == state)
        return list(
            session.scalars(
                statement.order_by(FeedbackKnowledgeDraft.created_at.desc()).limit(limit)
            )
        )


class FeedbackEvaluationCaseRepository:
    """回归评测草稿仓储，避免业务服务散落 SQL 条件。"""

    def create(self, session: Session, case: FeedbackEvaluationCase) -> FeedbackEvaluationCase:
        session.add(case)
        return case

    def get_for_update(
        self, session: Session, *, case_id: str, workspace_id: str
    ) -> FeedbackEvaluationCase | None:
        return session.scalar(
            select(FeedbackEvaluationCase)
            .where(
                FeedbackEvaluationCase.id == case_id,
                FeedbackEvaluationCase.workspace_id == workspace_id,
            )
            .with_for_update()
        )

    def get_by_triage_for_update(
        self, session: Session, *, triage_id: str, workspace_id: str
    ) -> FeedbackEvaluationCase | None:
        return session.scalar(
            select(FeedbackEvaluationCase)
            .where(
                FeedbackEvaluationCase.feedback_triage_id == triage_id,
                FeedbackEvaluationCase.workspace_id == workspace_id,
            )
            .with_for_update()
        )

    def list_by_knowledge_base(
        self,
        session: Session,
        *,
        knowledge_base_id: str,
        workspace_id: str,
        state: str | None,
        limit: int,
    ) -> list[FeedbackEvaluationCase]:
        statement = select(FeedbackEvaluationCase).where(
            FeedbackEvaluationCase.knowledge_base_id == knowledge_base_id,
            FeedbackEvaluationCase.workspace_id == workspace_id,
        )
        if state is not None:
            statement = statement.where(FeedbackEvaluationCase.state == state)
        return list(
            session.scalars(
                statement.order_by(FeedbackEvaluationCase.created_at.desc()).limit(limit)
            )
        )

    def list_approved_for_export(
        self, session: Session, *, knowledge_base_id: str, workspace_id: str
    ) -> list[FeedbackEvaluationCase]:
        """按创建顺序导出，保证同一受控集合重复导出的文件顺序稳定。"""

        return list(
            session.scalars(
                select(FeedbackEvaluationCase)
                .where(
                    FeedbackEvaluationCase.knowledge_base_id == knowledge_base_id,
                    FeedbackEvaluationCase.workspace_id == workspace_id,
                    FeedbackEvaluationCase.state == "approved",
                )
                .order_by(
                    FeedbackEvaluationCase.created_at.asc(),
                    FeedbackEvaluationCase.id.asc(),
                )
            )
        )
