"""知识库和笔记用例。"""

from sqlalchemy.orm import Session

from app.application.configuration_service import ConfigurationService
from app.application.embedding_service import EmbeddingService
from app.application.tag_governance_service import TagGovernanceService
from app.core.errors import ResourceNotFoundError, VersionConflictError
from app.core.workspace import ensure_workspace
from app.domain.knowledge.models import KnowledgeBase, Note
from app.domain.knowledge.repositories import KnowledgeBaseRepository, NoteRepository
from app.rag.embeddings import build_embedding_provider


class KnowledgeService:
    """集中维护知识资产的业务规则与事务提交边界。"""

    def __init__(
        self,
        knowledge_base_repository: KnowledgeBaseRepository | None = None,
        note_repository: NoteRepository | None = None,
    ) -> None:
        self.knowledge_base_repository = knowledge_base_repository or KnowledgeBaseRepository()
        self.note_repository = note_repository or NoteRepository()

    def create_knowledge_base(
        self,
        session: Session,
        *,
        name: str,
        description: str | None,
        workspace_id: str | None = None,
    ) -> KnowledgeBase:
        resolved_workspace_id = ensure_workspace(session, workspace_id=workspace_id).id
        knowledge_base = KnowledgeBase(
            workspace_id=resolved_workspace_id,
            name=name,
            description=description,
            # 空知识库从当前嵌入版本起步，首次导入即可直接进入可检索状态。
            embedding_revision=ConfigurationService().embedding_revision(
                session, workspace_id=resolved_workspace_id
            ),
        )
        self.knowledge_base_repository.create(session, knowledge_base)
        session.commit()
        session.refresh(knowledge_base)
        return knowledge_base

    def list_knowledge_bases(
        self,
        session: Session,
        *,
        workspace_id: str | None = None,
        offset: int,
        limit: int,
    ) -> tuple[list[KnowledgeBase], int]:
        resolved_workspace_id = ensure_workspace(session, workspace_id=workspace_id).id
        return self.knowledge_base_repository.list(
            session,
            workspace_id=resolved_workspace_id,
            offset=offset,
            limit=limit,
        )

    def get_knowledge_base(
        self,
        session: Session,
        knowledge_base_id: str,
        *,
        workspace_id: str | None = None,
    ) -> KnowledgeBase:
        resolved_workspace_id = ensure_workspace(session, workspace_id=workspace_id).id
        knowledge_base = self.knowledge_base_repository.get(
            session,
            knowledge_base_id,
            workspace_id=resolved_workspace_id,
        )
        if knowledge_base is None or knowledge_base.status != "active":
            raise ResourceNotFoundError(details={"resource": "knowledge_base"})
        return knowledge_base

    def update_knowledge_base(
        self,
        session: Session,
        *,
        knowledge_base_id: str,
        name: str | None,
        description: str | None,
        workspace_id: str | None = None,
    ) -> KnowledgeBase:
        resolved_workspace_id = ensure_workspace(session, workspace_id=workspace_id).id
        knowledge_base = self.knowledge_base_repository.get(
            session,
            knowledge_base_id,
            workspace_id=resolved_workspace_id,
        )
        if knowledge_base is None or knowledge_base.status != "active":
            raise ResourceNotFoundError(details={"resource": "knowledge_base"})
        if name is not None:
            knowledge_base.name = name
        if description is not None:
            knowledge_base.description = description
        session.commit()
        session.refresh(knowledge_base)
        return knowledge_base

    def archive_knowledge_base(
        self,
        session: Session,
        *,
        knowledge_base_id: str,
        workspace_id: str | None = None,
    ) -> KnowledgeBase:
        """归档代替物理删除，保留会话、审计和引用的可追溯性。"""

        resolved_workspace_id = ensure_workspace(session, workspace_id=workspace_id).id
        knowledge_base = self.knowledge_base_repository.get(
            session,
            knowledge_base_id,
            workspace_id=resolved_workspace_id,
        )
        if knowledge_base is None or knowledge_base.status != "active":
            raise ResourceNotFoundError(details={"resource": "knowledge_base"})
        knowledge_base.status = "archived"
        session.commit()
        session.refresh(knowledge_base)
        return knowledge_base

    def create_note(
        self,
        session: Session,
        *,
        knowledge_base_id: str,
        title: str,
        content: str,
        workspace_id: str | None = None,
        commit: bool = True,
    ) -> Note:
        resolved_workspace_id = ensure_workspace(session, workspace_id=workspace_id).id
        self.get_knowledge_base(
            session,
            knowledge_base_id,
            workspace_id=resolved_workspace_id,
        )
        note = Note(
            workspace_id=resolved_workspace_id,
            knowledge_base_id=knowledge_base_id,
            title=title,
            content=content,
        )
        self.note_repository.create(session, note)
        session.flush()
        self._index_note(session, note=note, workspace_id=resolved_workspace_id)
        self._auto_propose_note_tags(session, note=note, workspace_id=resolved_workspace_id)
        if commit:
            session.commit()
            session.refresh(note)
        return note

    def list_notes(
        self,
        session: Session,
        *,
        knowledge_base_id: str,
        workspace_id: str | None = None,
        offset: int,
        limit: int,
    ) -> tuple[list[Note], int]:
        resolved_workspace_id = ensure_workspace(session, workspace_id=workspace_id).id
        self.get_knowledge_base(
            session,
            knowledge_base_id,
            workspace_id=resolved_workspace_id,
        )
        return self.note_repository.list_by_knowledge_base(
            session,
            knowledge_base_id=knowledge_base_id,
            workspace_id=resolved_workspace_id,
            offset=offset,
            limit=limit,
        )

    def update_note(
        self,
        session: Session,
        *,
        note_id: str,
        title: str | None,
        content: str | None,
        expected_version: int,
        workspace_id: str | None = None,
        commit: bool = True,
    ) -> Note:
        resolved_workspace_id = ensure_workspace(session, workspace_id=workspace_id).id
        note = self.note_repository.get(session, note_id, workspace_id=resolved_workspace_id)
        if note is None or note.status != "active":
            raise ResourceNotFoundError(details={"resource": "note"})
        if note.version != expected_version:
            # 使用显式版本号阻止“最后一次写入覆盖一切”的知识丢失问题。
            raise VersionConflictError(details={"currentVersion": note.version})

        if title is not None:
            note.title = title
        if content is not None:
            note.content = content
        note.version += 1
        self._index_note(session, note=note, workspace_id=resolved_workspace_id)
        self._auto_propose_note_tags(session, note=note, workspace_id=resolved_workspace_id)
        if commit:
            session.commit()
            session.refresh(note)
        return note

    @staticmethod
    def _index_note(session: Session, *, note: Note, workspace_id: str) -> None:
        """笔记写入与向量更新保持同一事务，失败时不留下正文已更新但索引过期的状态。"""

        configuration = ConfigurationService()
        settings = configuration.resolve_settings(session, workspace_id=workspace_id)
        EmbeddingService(
            provider=build_embedding_provider(settings),
            expected_dimensions=settings.embedding_dimensions,
        ).index_note(
            session,
            note=note,
            embedding_revision=configuration.embedding_revision(session, workspace_id=workspace_id),
        )

    @staticmethod
    def _auto_propose_note_tags(session: Session, *, note: Note, workspace_id: str) -> None:
        """标签建议失败时隔离副作用，不能影响用户保存笔记或其向量快照。"""

        try:
            with session.begin_nested():
                TagGovernanceService().auto_propose_for_note(
                    session,
                    note=note,
                    workspace_id=workspace_id,
                )
        except Exception:
            # 标签是受审核的检索增强数据，不是笔记写入成功的必要条件。
            return
