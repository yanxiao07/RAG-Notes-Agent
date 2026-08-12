"""受控业务标签的词表、提议与审批服务。"""

from __future__ import annotations

import re
import unicodedata
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.errors import (
    DuplicateResourceError,
    ProcessingError,
    ResourceNotFoundError,
    VersionConflictError,
)
from app.core.workspace import ensure_workspace
from app.domain.agent.models import AuditEvent
from app.domain.agent.repositories import AuditEventRepository
from app.domain.knowledge.models import (
    Document,
    KnowledgeBase,
    KnowledgeTag,
    KnowledgeTagAssignment,
    Note,
)

_SPACE_PATTERN = re.compile(r"\s+")
_TAG_ASSET_TYPES = {"document", "note"}
_TAG_STATES = {"pending", "approved", "rejected"}


def normalize_tag_name(value: str) -> str:
    """统一大小写和空白，保证词表唯一约束与自动匹配使用同一语义。"""

    return _SPACE_PATTERN.sub(" ", unicodedata.normalize("NFKC", value).strip().casefold())


class TagGovernanceService:
    """标签治理的事务边界，自动建议不会直接改变检索结果。"""

    def __init__(self) -> None:
        self.audit_repository = AuditEventRepository()

    def create_tag(
        self,
        session: Session,
        *,
        knowledge_base_id: str,
        name: str,
        description: str | None,
        workspace_id: str | None = None,
    ) -> KnowledgeTag:
        workspace = ensure_workspace(session, workspace_id=workspace_id)
        self._knowledge_base(session, knowledge_base_id, workspace.id)
        normalized_name = normalize_tag_name(name)
        if not normalized_name:
            raise ProcessingError(message="标签名称不能为空")
        tag = KnowledgeTag(
            workspace_id=workspace.id,
            knowledge_base_id=knowledge_base_id,
            name=name.strip(),
            normalized_name=normalized_name,
            description=description.strip() if description else None,
        )
        session.add(tag)
        try:
            session.commit()
        except IntegrityError as exc:
            session.rollback()
            raise DuplicateResourceError(message="当前知识库已存在同名标签") from exc
        session.refresh(tag)
        self._audit(session, workspace_id=workspace.id, action="tag_created", target=tag)
        session.commit()
        return tag

    def list_tags(
        self,
        session: Session,
        *,
        knowledge_base_id: str,
        workspace_id: str | None = None,
        include_archived: bool = False,
    ) -> list[KnowledgeTag]:
        workspace = ensure_workspace(session, workspace_id=workspace_id)
        self._knowledge_base(session, knowledge_base_id, workspace.id)
        statement = select(KnowledgeTag).where(
            KnowledgeTag.workspace_id == workspace.id,
            KnowledgeTag.knowledge_base_id == knowledge_base_id,
        )
        if not include_archived:
            statement = statement.where(KnowledgeTag.state == "active")
        return list(session.scalars(statement.order_by(KnowledgeTag.name.asc())))

    def archive_tag(
        self,
        session: Session,
        *,
        tag_id: str,
        expected_version: int,
        workspace_id: str | None = None,
    ) -> KnowledgeTag:
        workspace = ensure_workspace(session, workspace_id=workspace_id)
        tag = self._tag(session, tag_id, workspace.id)
        if tag.version != expected_version:
            raise VersionConflictError(details={"currentVersion": tag.version})
        tag.state = "archived"
        tag.version += 1
        self._audit(session, workspace_id=workspace.id, action="tag_archived", target=tag)
        session.commit()
        session.refresh(tag)
        return tag

    def propose_assignment(
        self,
        session: Session,
        *,
        knowledge_base_id: str,
        tag_id: str,
        asset_type: str,
        asset_id: str,
        source: str,
        confidence: float | None = None,
        workspace_id: str | None = None,
        commit: bool = True,
    ) -> KnowledgeTagAssignment:
        workspace = ensure_workspace(session, workspace_id=workspace_id)
        tag = self._tag(session, tag_id, workspace.id)
        if tag.knowledge_base_id != knowledge_base_id:
            raise ResourceNotFoundError(details={"resource": "knowledge_tag"})
        if tag.state != "active":
            raise ProcessingError(message="已归档标签不能创建新提议")
        if asset_type not in _TAG_ASSET_TYPES:
            raise ProcessingError(message="标签目标类型不受支持")
        self._asset(
            session,
            asset_type=asset_type,
            asset_id=asset_id,
            tag=tag,
            workspace_id=workspace.id,
        )
        assignment = KnowledgeTagAssignment(
            workspace_id=workspace.id,
            knowledge_base_id=tag.knowledge_base_id,
            tag_id=tag.id,
            asset_type=asset_type,
            asset_id=asset_id,
            state="pending",
            source=source,
            confidence=confidence,
        )
        session.add(assignment)
        try:
            session.flush()
        except IntegrityError as exc:
            session.rollback()
            raise DuplicateResourceError(message="该资料已存在相同标签提议") from exc
        self._audit(
            session,
            workspace_id=workspace.id,
            action="tag_assignment_proposed",
            target=assignment,
            payload={"source": source},
        )
        if commit:
            session.commit()
            session.refresh(assignment)
        return assignment

    def auto_propose_for_document(
        self, session: Session, *, document: Document, workspace_id: str
    ) -> int:
        """只对受控词表精确文本命中生成 pending 提议，不调用 LLM。"""

        return self._auto_propose(
            session,
            knowledge_base_id=document.knowledge_base_id,
            asset_type="document",
            asset_id=document.id,
            text=f"{document.title}\n{document.raw_content}",
            workspace_id=workspace_id,
        )

    def auto_propose_for_note(self, session: Session, *, note: Note, workspace_id: str) -> int:
        return self._auto_propose(
            session,
            knowledge_base_id=note.knowledge_base_id,
            asset_type="note",
            asset_id=note.id,
            text=f"{note.title}\n{note.content}",
            workspace_id=workspace_id,
        )

    def list_assignments(
        self,
        session: Session,
        *,
        knowledge_base_id: str,
        state: str | None,
        workspace_id: str | None = None,
    ) -> list[tuple[KnowledgeTagAssignment, KnowledgeTag]]:
        workspace = ensure_workspace(session, workspace_id=workspace_id)
        self._knowledge_base(session, knowledge_base_id, workspace.id)
        if state is not None and state not in _TAG_STATES:
            raise ProcessingError(message="标签提议状态不受支持")
        statement = (
            select(KnowledgeTagAssignment, KnowledgeTag)
            .join(KnowledgeTag, KnowledgeTag.id == KnowledgeTagAssignment.tag_id)
            .where(
                KnowledgeTagAssignment.workspace_id == workspace.id,
                KnowledgeTagAssignment.knowledge_base_id == knowledge_base_id,
            )
            .order_by(KnowledgeTagAssignment.created_at.desc())
        )
        if state is not None:
            statement = statement.where(KnowledgeTagAssignment.state == state)
        return list(session.execute(statement).tuples())

    def review_assignment(
        self,
        session: Session,
        *,
        assignment_id: str,
        decision: str,
        reviewer_id: str | None,
        workspace_id: str | None = None,
    ) -> KnowledgeTagAssignment:
        workspace = ensure_workspace(session, workspace_id=workspace_id)
        if decision not in {"approved", "rejected"}:
            raise ProcessingError(message="标签提议只能批准或拒绝")
        assignment = session.scalar(
            select(KnowledgeTagAssignment)
            .where(
                KnowledgeTagAssignment.id == assignment_id,
                KnowledgeTagAssignment.workspace_id == workspace.id,
            )
            .with_for_update()
        )
        if assignment is None:
            raise ResourceNotFoundError(details={"resource": "tag_assignment"})
        if assignment.state != "pending":
            raise ProcessingError(message="该标签提议已处理", details={"state": assignment.state})
        assignment.state = decision
        assignment.reviewer_id = reviewer_id
        assignment.reviewed_at = datetime.now(UTC)
        self._audit(
            session,
            workspace_id=workspace.id,
            action=f"tag_assignment_{decision}",
            target=assignment,
            payload={"reviewerId": reviewer_id or ""},
        )
        session.commit()
        session.refresh(assignment)
        return assignment

    def _auto_propose(
        self,
        session: Session,
        *,
        knowledge_base_id: str,
        asset_type: str,
        asset_id: str,
        text: str,
        workspace_id: str,
    ) -> int:
        haystack = normalize_tag_name(text)
        if not haystack:
            return 0
        tags = list(
            session.scalars(
                select(KnowledgeTag).where(
                    KnowledgeTag.workspace_id == workspace_id,
                    KnowledgeTag.knowledge_base_id == knowledge_base_id,
                    KnowledgeTag.state == "active",
                )
            )
        )
        if not tags:
            return 0
        existing_tag_ids = set(
            session.scalars(
                select(KnowledgeTagAssignment.tag_id).where(
                    KnowledgeTagAssignment.workspace_id == workspace_id,
                    KnowledgeTagAssignment.asset_type == asset_type,
                    KnowledgeTagAssignment.asset_id == asset_id,
                )
            )
        )
        created = 0
        for tag in tags:
            if tag.id in existing_tag_ids or tag.normalized_name not in haystack:
                continue
            session.add(
                KnowledgeTagAssignment(
                    workspace_id=workspace_id,
                    knowledge_base_id=knowledge_base_id,
                    tag_id=tag.id,
                    asset_type=asset_type,
                    asset_id=asset_id,
                    state="pending",
                    source="rule_match",
                    confidence=1.0,
                )
            )
            created += 1
        if created:
            session.flush()
        return created

    @staticmethod
    def _knowledge_base(
        session: Session, knowledge_base_id: str, workspace_id: str
    ) -> KnowledgeBase:
        knowledge_base = session.scalar(
            select(KnowledgeBase).where(
                KnowledgeBase.id == knowledge_base_id,
                KnowledgeBase.workspace_id == workspace_id,
                KnowledgeBase.status == "active",
            )
        )
        if knowledge_base is None:
            raise ResourceNotFoundError(details={"resource": "knowledge_base"})
        return knowledge_base

    @staticmethod
    def _tag(session: Session, tag_id: str, workspace_id: str) -> KnowledgeTag:
        tag = session.scalar(
            select(KnowledgeTag).where(
                KnowledgeTag.id == tag_id,
                KnowledgeTag.workspace_id == workspace_id,
            )
        )
        if tag is None:
            raise ResourceNotFoundError(details={"resource": "knowledge_tag"})
        return tag

    @staticmethod
    def _asset(
        session: Session,
        *,
        asset_type: str,
        asset_id: str,
        tag: KnowledgeTag,
        workspace_id: str,
    ) -> None:
        model = Document if asset_type == "document" else Note
        asset = session.scalar(
            select(model).where(
                model.id == asset_id,
                model.workspace_id == workspace_id,
                model.knowledge_base_id == tag.knowledge_base_id,
            )
        )
        if asset is None or getattr(asset, "status", "active") not in {"active", "indexed"}:
            raise ResourceNotFoundError(details={"resource": asset_type})

    def _audit(
        self,
        session: Session,
        *,
        workspace_id: str,
        action: str,
        target: KnowledgeTag | KnowledgeTagAssignment,
        payload: dict[str, str] | None = None,
    ) -> None:
        self.audit_repository.create(
            session,
            AuditEvent(
                workspace_id=workspace_id,
                actor_type="user",
                actor_id=None,
                action=action,
                target_type=(
                    "knowledge_tag" if isinstance(target, KnowledgeTag) else "tag_assignment"
                ),
                target_id=target.id,
                payload=payload or {},
            ),
        )
