"""Agent 写操作的兼容 Facade。

历史 API 仍然通过 ``AgentService`` 调用，但实际审批逻辑统一委托给
``AgentApprovalService``，避免路由层和不同 Runtime 各自实现一套状态流转。
"""

from sqlalchemy.orm import Session

from app.application.agent_approval_service import AgentApprovalService
from app.domain.agent.models import AgentRun, ChangeProposal


class AgentService:
    """保留旧调用入口，内部使用统一提议/审批执行器。"""

    def __init__(self, approval_service: AgentApprovalService | None = None) -> None:
        self.approval_service = approval_service or AgentApprovalService()

    def propose_note_creation(
        self,
        session: Session,
        *,
        knowledge_base_id: str,
        title: str,
        content: str,
        rationale: str,
        evidence_snapshot: list[dict[str, object]] | None = None,
        workspace_id: str | None = None,
        commit: bool = True,
    ) -> tuple[AgentRun, ChangeProposal]:
        return self.approval_service.propose_note_creation(
            session,
            knowledge_base_id=knowledge_base_id,
            title=title,
            content=content,
            rationale=rationale,
            evidence_snapshot=evidence_snapshot,
            workspace_id=workspace_id,
            commit=commit,
        )

    def approve_proposal(
        self,
        session: Session,
        *,
        proposal_id: str,
        actor_id: str | None,
        actor_role: str | None = None,
        workspace_id: str | None = None,
        commit: bool = True,
    ) -> ChangeProposal:
        return self.approval_service.approve_proposal(
            session,
            proposal_id=proposal_id,
            actor_id=actor_id,
            actor_role=actor_role,
            workspace_id=workspace_id,
            commit=commit,
        )

    def reject_proposal(
        self,
        session: Session,
        *,
        proposal_id: str,
        actor_id: str | None,
        actor_role: str | None = None,
        workspace_id: str | None = None,
        commit: bool = True,
    ) -> ChangeProposal:
        return self.approval_service.reject_proposal(
            session,
            proposal_id=proposal_id,
            actor_id=actor_id,
            actor_role=actor_role,
            workspace_id=workspace_id,
            commit=commit,
        )
