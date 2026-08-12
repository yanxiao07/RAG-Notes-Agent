"""Agent 写操作的统一提议与审批执行服务。

该模块是 Agent Runtime 与知识库写模型之间的安全边界：

* Agent 只能创建 ``ChangeProposal``，不能直接写入 Note；
* 每个 action 通过显式 registry 注册，未知 action 默认拒绝执行；
* approve/reject 都严格携带 workspace_id，并保持运行、工具调用、快照和审计的一致性。

后续增加 ``update_note``、``archive_document`` 等动作时，只需要实现
``ProposalAction`` 并注册，不需要修改 API 路由或 Runtime 状态图。
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Protocol

from sqlalchemy.orm import Session

from app.application.ingestion_service import IngestionService
from app.application.knowledge_service import KnowledgeService
from app.core.config import get_settings
from app.core.errors import ProcessingError, ProposalExpiredError, ResourceNotFoundError
from app.core.workspace import configured_actor_role, ensure_workspace
from app.domain.agent.models import (
    AgentCheckpoint,
    AgentRun,
    AgentToolCall,
    AuditEvent,
    ChangeProposal,
)
from app.domain.agent.repositories import (
    AgentCheckpointRepository,
    AgentRunRepository,
    AgentToolCallRepository,
    AuditEventRepository,
    ChangeProposalRepository,
)
from app.domain.knowledge.repositories import DocumentRepository, NoteRepository


class ProposalAction(Protocol):
    """一个可审批执行的写动作。实现必须自行完成领域校验。"""

    name: str

    def approve(
        self,
        session: Session,
        *,
        proposal: ChangeProposal,
        actor_id: str | None,
        workspace_id: str,
    ) -> None: ...


class CreateNoteProposalAction:
    """将已审批的提议写入 Note，并复用知识服务的索引事务。"""

    name = "create_note"

    def approve(
        self,
        session: Session,
        *,
        proposal: ChangeProposal,
        actor_id: str | None,
        workspace_id: str,
    ) -> None:
        del actor_id  # actor 已由审批服务写入审计事件。
        title = proposal.payload.get("title")
        content = proposal.payload.get("content")
        if not isinstance(title, str) or not title.strip():
            raise ProcessingError(message="笔记提议缺少有效标题")
        if not isinstance(content, str) or not content.strip():
            raise ProcessingError(message="笔记提议缺少有效内容")
        KnowledgeService().create_note(
            session,
            knowledge_base_id=proposal.knowledge_base_id,
            title=title,
            content=content,
            workspace_id=workspace_id,
            commit=False,
        )


class UpdateNoteProposalAction:
    """审批后按乐观锁版本更新笔记，并同步重新建立笔记向量。"""

    name = "update_note"

    def approve(
        self,
        session: Session,
        *,
        proposal: ChangeProposal,
        actor_id: str | None,
        workspace_id: str,
    ) -> None:
        del actor_id
        note_id = proposal.payload.get("noteId")
        expected_version = _as_int(proposal.payload.get("expectedVersion"))
        if not note_id or expected_version is None:
            raise ProcessingError(message="笔记更新提议缺少目标或版本号")
        note = NoteRepository().get(session, note_id, workspace_id=workspace_id)
        if note is None or note.knowledge_base_id != proposal.knowledge_base_id:
            raise ResourceNotFoundError(details={"resource": "note"})
        title = proposal.payload.get("title")
        content = proposal.payload.get("content")
        KnowledgeService().update_note(
            session,
            note_id=note_id,
            title=title if isinstance(title, str) else None,
            content=content if isinstance(content, str) else None,
            expected_version=expected_version,
            workspace_id=workspace_id,
            commit=False,
        )


class ArchiveDocumentProposalAction:
    """审批后归档文档并清理其切块/向量，保留原始资产审计记录。"""

    name = "archive_document"

    def approve(
        self,
        session: Session,
        *,
        proposal: ChangeProposal,
        actor_id: str | None,
        workspace_id: str,
    ) -> None:
        del actor_id
        document_id = proposal.payload.get("documentId")
        if not document_id:
            raise ProcessingError(message="文档归档提议缺少目标文档")
        document = DocumentRepository().get(session, document_id, workspace_id=workspace_id)
        if document is None or document.knowledge_base_id != proposal.knowledge_base_id:
            raise ResourceNotFoundError(details={"resource": "document"})
        IngestionService().archive_document(
            session,
            document_id=document_id,
            workspace_id=workspace_id,
            commit=False,
        )


class ProposalActionRegistry:
    """显式动作注册表，禁止隐式反射调用任意写方法。"""

    def __init__(self, actions: list[ProposalAction] | None = None) -> None:
        self._actions: dict[str, ProposalAction] = {}
        for action in actions or []:
            self.register(action)

    def register(self, action: ProposalAction) -> None:
        if action.name in self._actions:
            raise ValueError(f"提议动作重复注册: {action.name}")
        self._actions[action.name] = action

    def get(self, name: str) -> ProposalAction:
        try:
            return self._actions[name]
        except KeyError as exc:
            raise ProcessingError(message="当前提议动作未实现", details={"action": name}) from exc

    def names(self) -> tuple[str, ...]:
        return tuple(self._actions)


class AgentApprovalService:
    """统一管理 ChangeProposal 生命周期和审批后的副作用。"""

    policy_version = "agent-runtime-v1"

    def __init__(self, registry: ProposalActionRegistry | None = None) -> None:
        self.registry = registry or ProposalActionRegistry(
            [
                CreateNoteProposalAction(),
                UpdateNoteProposalAction(),
                ArchiveDocumentProposalAction(),
            ]
        )
        self.run_repository = AgentRunRepository()
        self.checkpoint_repository = AgentCheckpointRepository()
        self.tool_call_repository = AgentToolCallRepository()
        self.proposal_repository = ChangeProposalRepository()
        self.audit_repository = AuditEventRepository()

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
        """兼容手工提议 API：创建一个等待审批的 Agent Run。"""

        resolved_workspace_id = ensure_workspace(session, workspace_id=workspace_id).id
        KnowledgeService().get_knowledge_base(
            session, knowledge_base_id, workspace_id=resolved_workspace_id
        )
        run = AgentRun(
            workspace_id=resolved_workspace_id,
            knowledge_base_id=knowledge_base_id,
            state="awaiting_approval",
            policy_version=self.policy_version,
            current_node="approval",
        )
        self.run_repository.create(session, run)
        session.flush()
        proposal = self.create_for_run(
            session,
            run_id=run.id,
            workspace_id=resolved_workspace_id,
            knowledge_base_id=knowledge_base_id,
            action="create_note",
            payload={"title": title, "content": content},
            rationale=rationale,
            evidence_snapshot=evidence_snapshot,
        )
        session.flush()
        tool_call = AgentToolCall(
            workspace_id=resolved_workspace_id,
            agent_run_id=run.id,
            node="approval",
            tool_name="create_note_proposal",
            input_json={
                "titleHash": _hash_text(title),
                "titleLength": len(title),
                "contentLength": len(content),
                "rationaleLength": len(rationale),
            },
            output_json={"proposalId": proposal.id, "action": proposal.action},
            state="awaiting_approval",
            requires_approval=True,
        )
        self.tool_call_repository.create(session, tool_call)
        self._create_checkpoint(
            session,
            run=run,
            workspace_id=resolved_workspace_id,
            node="approval",
            state={"proposalId": proposal.id, "action": proposal.action},
        )
        session.flush()
        if commit:
            session.commit()
            session.refresh(run)
            session.refresh(proposal)
        return run, proposal

    def create_for_run(
        self,
        session: Session,
        *,
        run_id: str,
        workspace_id: str,
        knowledge_base_id: str,
        action: str,
        payload: Mapping[str, str],
        rationale: str,
        evidence_snapshot: list[dict[str, object]] | None = None,
    ) -> ChangeProposal:
        """为现有 Runtime 创建提议；不会执行任何知识库写入。"""

        resolved_workspace_id = ensure_workspace(session, workspace_id=workspace_id).id
        run = self.run_repository.get(session, run_id, workspace_id=resolved_workspace_id)
        if run is None or run.knowledge_base_id != knowledge_base_id:
            raise ResourceNotFoundError(details={"resource": "agent_run"})
        if run.state not in {"running", "awaiting_approval"}:
            raise ProcessingError(
                message="当前 Agent Run 不允许创建提议", details={"state": run.state}
            )
        # Runtime 重试时复用同一 pending 提议，避免同一个写工具产生重复副作用。
        existing = self.proposal_repository.get_pending_by_run(
            session, run_id=run_id, workspace_id=resolved_workspace_id
        )
        if existing is not None:
            if existing.action != action:
                raise ProcessingError(message="同一 Agent Run 已存在其他待审批动作")
            return existing
        if action not in self.registry.names():
            raise ProcessingError(message="当前提议动作未实现", details={"action": action})
        proposal = ChangeProposal(
            workspace_id=resolved_workspace_id,
            agent_run_id=run_id,
            knowledge_base_id=knowledge_base_id,
            action=action,
            payload=dict(payload),
            rationale=rationale,
            state="pending",
            risk_level=_risk_policy(action)[0],
            required_role=_risk_policy(action)[1],
            evidence_snapshot=_sanitize_evidence_snapshot(evidence_snapshot),
            expires_at=datetime.now(UTC)
            + timedelta(seconds=get_settings().agent_proposal_ttl_seconds),
        )
        self.proposal_repository.create(session, proposal)
        session.flush()
        self.audit_repository.create(
            session,
            AuditEvent(
                actor_type="agent",
                actor_id=run_id,
                workspace_id=resolved_workspace_id,
                action="change_proposed",
                target_type="change_proposal",
                target_id=proposal.id,
                payload={"proposalAction": action},
            ),
        )
        return proposal

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
        resolved_workspace_id = ensure_workspace(session, workspace_id=workspace_id).id
        proposal = self._get_pending_proposal(
            session, proposal_id, workspace_id=resolved_workspace_id
        )
        _require_role(
            configured_actor_role(
                workspace_id=resolved_workspace_id,
                actor_id=actor_id,
                claimed_role=actor_role,
            ),
            proposal.required_role,
        )
        action = self.registry.get(proposal.action)
        action.approve(
            session,
            proposal=proposal,
            actor_id=actor_id,
            workspace_id=resolved_workspace_id,
        )
        proposal.state = "approved"
        run = self.run_repository.get(
            session, proposal.agent_run_id, workspace_id=resolved_workspace_id
        )
        if run is None:
            raise ResourceNotFoundError(details={"resource": "agent_run"})
        run.state = "completed"
        run.current_node = "finish"
        run.output_json = {"proposalId": proposal.id, "approved": True}
        self._mark_approval_tool(session, run.id, resolved_workspace_id, proposal.id, "completed")
        self._create_checkpoint(
            session,
            run=run,
            workspace_id=resolved_workspace_id,
            node="finish",
            state={"proposalId": proposal.id, "approved": True},
        )
        self.audit_repository.create(
            session,
            AuditEvent(
                actor_type="user",
                actor_id=actor_id,
                workspace_id=resolved_workspace_id,
                action="change_approved",
                target_type="change_proposal",
                target_id=proposal.id,
                payload={"proposalAction": proposal.action},
            ),
        )
        session.flush()
        if commit:
            session.commit()
            session.refresh(proposal)
        return proposal

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
        resolved_workspace_id = ensure_workspace(session, workspace_id=workspace_id).id
        proposal = self._get_pending_proposal(
            session, proposal_id, workspace_id=resolved_workspace_id
        )
        _require_role(
            configured_actor_role(
                workspace_id=resolved_workspace_id,
                actor_id=actor_id,
                claimed_role=actor_role,
            ),
            proposal.required_role,
        )
        proposal.state = "rejected"
        run = self.run_repository.get(
            session, proposal.agent_run_id, workspace_id=resolved_workspace_id
        )
        if run is None:
            raise ResourceNotFoundError(details={"resource": "agent_run"})
        run.state = "cancelled"
        run.current_node = "finish"
        run.output_json = {"proposalId": proposal.id, "approved": False}
        self._mark_approval_tool(session, run.id, resolved_workspace_id, proposal.id, "rejected")
        self._create_checkpoint(
            session,
            run=run,
            workspace_id=resolved_workspace_id,
            node="finish",
            state={"proposalId": proposal.id, "approved": False},
        )
        self.audit_repository.create(
            session,
            AuditEvent(
                actor_type="user",
                actor_id=actor_id,
                workspace_id=resolved_workspace_id,
                action="change_rejected",
                target_type="change_proposal",
                target_id=proposal.id,
                payload={"proposalAction": proposal.action},
            ),
        )
        session.flush()
        if commit:
            session.commit()
            session.refresh(proposal)
        return proposal

    def _get_pending_proposal(
        self, session: Session, proposal_id: str, *, workspace_id: str
    ) -> ChangeProposal:
        # PostgreSQL 下通过行锁串行化审批，SQLite 降级时继续由状态检查保护幂等性。
        locked = self.proposal_repository.get_pending_for_update(
            session, proposal_id, workspace_id=workspace_id
        )
        proposal = locked or self.proposal_repository.get(
            session, proposal_id, workspace_id=workspace_id
        )
        if proposal is None:
            raise ResourceNotFoundError(details={"resource": "change_proposal"})
        if proposal.state != "pending":
            raise ProcessingError(message="该提议已被处理", details={"state": proposal.state})
        if proposal.expires_at is not None and _is_expired(proposal.expires_at):
            proposal.state = "expired"
            run = self.run_repository.get(session, proposal.agent_run_id, workspace_id=workspace_id)
            if run is not None and run.state == "awaiting_approval":
                run.state = "cancelled"
                run.current_node = "finish"
            self.audit_repository.create(
                session,
                AuditEvent(
                    actor_type="system",
                    actor_id=None,
                    workspace_id=workspace_id,
                    action="change_expired",
                    target_type="change_proposal",
                    target_id=proposal.id,
                    payload={"proposalAction": proposal.action},
                ),
            )
            session.commit()
            raise ProposalExpiredError()
        return proposal

    def _mark_approval_tool(
        self,
        session: Session,
        run_id: str,
        workspace_id: str,
        proposal_id: str,
        state: str,
    ) -> None:
        for call in self.tool_call_repository.list_by_run(
            session, run_id=run_id, workspace_id=workspace_id
        ):
            output = call.output_json if isinstance(call.output_json, dict) else {}
            if output.get("proposalId") == proposal_id and call.state == "awaiting_approval":
                call.state = state

    def _create_checkpoint(
        self,
        session: Session,
        *,
        run: AgentRun,
        workspace_id: str,
        node: str,
        state: dict[str, object],
    ) -> None:
        latest = self.checkpoint_repository.latest_by_run(
            session, run_id=run.id, workspace_id=workspace_id
        )
        sequence = (latest.sequence if latest is not None else 0) + 1
        state_json = {
            "runId": run.id,
            "workspaceId": workspace_id,
            "knowledgeBaseId": run.knowledge_base_id,
            "node": node,
            **state,
        }
        self.checkpoint_repository.create(
            session,
            AgentCheckpoint(
                workspace_id=workspace_id,
                agent_run_id=run.id,
                thread_id=run.thread_id or run.id,
                sequence=sequence,
                node=node,
                state_json=state_json,
                state_checksum=_state_checksum(state_json),
            ),
        )


def _hash_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _as_int(value: object) -> int | None:
    """兼容 JSON 字符串/数字版本号，拒绝布尔值和小数。"""

    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isdecimal():
        return int(value)
    return None


def _state_checksum(state: dict[str, object]) -> str:
    serialized = json.dumps(state, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _risk_policy(action: str) -> tuple[str, str]:
    """将动作映射为最小风险等级和审批角色，未知写动作默认最高保护。"""

    return {
        "create_note": ("medium", "approver"),
        "update_note": ("medium", "approver"),
        "archive_document": ("high", "owner"),
    }.get(action, ("high", "owner"))


def _is_expired(expires_at: datetime) -> bool:
    """SQLite 返回无时区时间，统一按 UTC 解释后再比较。"""

    normalized = expires_at if expires_at.tzinfo is not None else expires_at.replace(tzinfo=UTC)
    return normalized <= datetime.now(UTC)


def _require_role(actual: str, required: str) -> None:
    levels = {"viewer": 0, "editor": 1, "approver": 2, "owner": 3}
    if levels.get(actual, -1) < levels.get(required, 3):
        raise ProcessingError(
            message="当前操作者角色不足，无法审批该风险等级的变更。",
            details={"requiredRole": required},
        )


def _sanitize_evidence_snapshot(
    evidence_snapshot: list[dict[str, object]] | None,
) -> list[dict[str, object]]:
    """只保留可定位字段，限制数量和长度，拒绝把正文混入审批快照。"""

    sanitized: list[dict[str, object]] = []
    for item in (evidence_snapshot or [])[:20]:
        if not isinstance(item, dict):
            continue
        entry: dict[str, object] = {}
        for key in ("sourceType", "sourceId", "title", "locator", "sourceUrl", "score"):
            value = item.get(key)
            if isinstance(value, (str, int, float)):
                entry[key] = value if not isinstance(value, str) else value[:500]
        if entry:
            sanitized.append(entry)
    return sanitized
