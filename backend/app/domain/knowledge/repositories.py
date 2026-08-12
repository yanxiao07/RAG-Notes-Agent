"""知识领域的仓储；这里只处理持久化，不包含 HTTP 或业务策略。"""

from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

from app.domain.knowledge.models import (
    ChunkEmbedding,
    Document,
    DocumentChunk,
    IngestionJob,
    KnowledgeBase,
    Note,
    NoteEmbedding,
)


class KnowledgeBaseRepository:
    def create(self, session: Session, knowledge_base: KnowledgeBase) -> KnowledgeBase:
        session.add(knowledge_base)
        return knowledge_base

    def get(
        self, session: Session, knowledge_base_id: str, *, workspace_id: str
    ) -> KnowledgeBase | None:
        return session.scalar(
            select(KnowledgeBase).where(
                KnowledgeBase.id == knowledge_base_id,
                KnowledgeBase.workspace_id == workspace_id,
            )
        )

    def list(
        self, session: Session, *, workspace_id: str, offset: int, limit: int
    ) -> tuple[list[KnowledgeBase], int]:
        items = list(
            session.scalars(
                select(KnowledgeBase)
                .where(
                    KnowledgeBase.status == "active",
                    KnowledgeBase.workspace_id == workspace_id,
                )
                .order_by(KnowledgeBase.updated_at.desc())
                .offset(offset)
                .limit(limit)
            )
        )
        total = session.scalar(
            select(func.count())
            .select_from(KnowledgeBase)
            .where(
                KnowledgeBase.status == "active",
                KnowledgeBase.workspace_id == workspace_id,
            )
        )
        return items, int(total or 0)

    def mark_indexes_stale(self, session: Session, *, workspace_id: str) -> None:
        """模型语义空间变化后一次性使该工作区的活动知识库失效。"""

        session.query(KnowledgeBase).filter(
            KnowledgeBase.workspace_id == workspace_id,
            KnowledgeBase.status == "active",
        ).update({KnowledgeBase.index_status: "stale"}, synchronize_session=False)


class NoteRepository:
    def create(self, session: Session, note: Note) -> Note:
        session.add(note)
        return note

    def get(self, session: Session, note_id: str, *, workspace_id: str) -> Note | None:
        return session.scalar(
            select(Note).where(Note.id == note_id, Note.workspace_id == workspace_id)
        )

    def list_by_knowledge_base(
        self,
        session: Session,
        *,
        knowledge_base_id: str,
        workspace_id: str,
        offset: int,
        limit: int,
    ) -> tuple[list[Note], int]:
        query: Select[tuple[Note]] = (
            select(Note)
            .where(
                Note.knowledge_base_id == knowledge_base_id,
                Note.workspace_id == workspace_id,
                Note.status == "active",
            )
            .order_by(Note.updated_at.desc())
        )
        items = list(session.scalars(query.offset(offset).limit(limit)))
        total = session.scalar(
            select(func.count())
            .select_from(Note)
            .where(
                Note.knowledge_base_id == knowledge_base_id,
                Note.workspace_id == workspace_id,
                Note.status == "active",
            )
        )
        return items, int(total or 0)


class DocumentRepository:
    def create(self, session: Session, document: Document) -> Document:
        session.add(document)
        return document

    def get(self, session: Session, document_id: str, *, workspace_id: str) -> Document | None:
        return session.scalar(
            select(Document).where(
                Document.id == document_id,
                Document.workspace_id == workspace_id,
            )
        )

    def get_by_content_hash(
        self,
        session: Session,
        *,
        knowledge_base_id: str,
        workspace_id: str,
        content_hash: str,
    ) -> Document | None:
        return session.scalar(
            select(Document).where(
                Document.workspace_id == workspace_id,
                Document.knowledge_base_id == knowledge_base_id,
                Document.status != "archived",
                Document.content_hash == content_hash,
            )
        )

    def get_by_source_url(
        self, session: Session, *, knowledge_base_id: str, workspace_id: str, source_url: str
    ) -> Document | None:
        """按规范化 URL 去重，只返回当前仍可检索的网页文档。"""

        return session.scalar(
            select(Document).where(
                Document.workspace_id == workspace_id,
                Document.knowledge_base_id == knowledge_base_id,
                Document.status != "archived",
                Document.source_url == source_url,
            )
        )

    def list_by_knowledge_base(
        self,
        session: Session,
        *,
        knowledge_base_id: str,
        workspace_id: str,
        offset: int,
        limit: int,
    ) -> tuple[list[Document], int]:
        query: Select[tuple[Document]] = (
            select(Document)
            .where(
                Document.knowledge_base_id == knowledge_base_id,
                Document.workspace_id == workspace_id,
                Document.status != "archived",
            )
            .order_by(Document.updated_at.desc())
        )
        items = list(session.scalars(query.offset(offset).limit(limit)))
        total = session.scalar(
            select(func.count())
            .select_from(Document)
            .where(
                Document.knowledge_base_id == knowledge_base_id,
                Document.workspace_id == workspace_id,
                Document.status != "archived",
            )
        )
        return items, int(total or 0)

    def replace_chunks(
        self,
        session: Session,
        *,
        document_id: str,
        workspace_id: str,
        chunks: list[DocumentChunk],
    ) -> None:
        # 重试同一任务时替换旧块，防止索引重复而改变检索分数。
        # 旧 Embedding 依赖原 Chunk 主键，必须先清理，才能安全地替换切分策略。
        previous_chunk_ids = select(DocumentChunk.id).where(
            DocumentChunk.document_id == document_id,
            DocumentChunk.workspace_id == workspace_id,
        )
        session.query(ChunkEmbedding).filter(
            ChunkEmbedding.workspace_id == workspace_id,
            ChunkEmbedding.document_chunk_id.in_(previous_chunk_ids),
        ).delete(synchronize_session=False)
        session.query(DocumentChunk).filter(
            DocumentChunk.document_id == document_id,
            DocumentChunk.workspace_id == workspace_id,
        ).delete()
        session.add_all(chunks)


class IngestionJobRepository:
    def create(self, session: Session, job: IngestionJob) -> IngestionJob:
        session.add(job)
        return job

    def get(self, session: Session, job_id: str, *, workspace_id: str) -> IngestionJob | None:
        return session.scalar(
            select(IngestionJob).where(
                IngestionJob.id == job_id,
                IngestionJob.workspace_id == workspace_id,
            )
        )

    def get_by_document(
        self, session: Session, document_id: str, *, workspace_id: str
    ) -> IngestionJob | None:
        return session.scalar(
            select(IngestionJob).where(
                IngestionJob.document_id == document_id,
                IngestionJob.workspace_id == workspace_id,
            )
        )


class ChunkEmbeddingRepository:
    def replace_for_document(
        self,
        session: Session,
        *,
        document_id: str,
        workspace_id: str,
        embeddings: list[ChunkEmbedding],
    ) -> None:
        chunk_ids = select(DocumentChunk.id).where(
            DocumentChunk.document_id == document_id,
            DocumentChunk.workspace_id == workspace_id,
        )
        session.query(ChunkEmbedding).filter(
            ChunkEmbedding.workspace_id == workspace_id,
            ChunkEmbedding.document_chunk_id.in_(chunk_ids),
        ).delete(synchronize_session=False)
        session.add_all(embeddings)

    def list_by_knowledge_base(
        self,
        session: Session,
        *,
        knowledge_base_id: str,
        workspace_id: str,
        embedding_revision: int,
    ) -> list[tuple[ChunkEmbedding, DocumentChunk, Document]]:
        rows = session.execute(
            select(ChunkEmbedding, DocumentChunk, Document)
            .join(DocumentChunk, DocumentChunk.id == ChunkEmbedding.document_chunk_id)
            .join(Document, Document.id == DocumentChunk.document_id)
            .where(
                ChunkEmbedding.workspace_id == workspace_id,
                DocumentChunk.workspace_id == workspace_id,
                Document.workspace_id == workspace_id,
                Document.knowledge_base_id == knowledge_base_id,
                Document.status == "indexed",
                ChunkEmbedding.embedding_revision == embedding_revision,
            )
        )
        return list(rows.tuples())


class NoteEmbeddingRepository:
    """笔记向量替换与限定范围的读取，始终通过笔记本身进行工作区隔离。"""

    def replace_for_note(
        self, session: Session, *, note_id: str, workspace_id: str, embedding: NoteEmbedding
    ) -> None:
        session.query(NoteEmbedding).filter(
            NoteEmbedding.note_id == note_id,
            NoteEmbedding.workspace_id == workspace_id,
        ).delete(synchronize_session=False)
        session.add(embedding)

    def list_by_knowledge_base(
        self,
        session: Session,
        *,
        knowledge_base_id: str,
        workspace_id: str,
        embedding_revision: int,
    ) -> list[tuple[NoteEmbedding, Note]]:
        rows = session.execute(
            select(NoteEmbedding, Note)
            .join(Note, Note.id == NoteEmbedding.note_id)
            .where(
                NoteEmbedding.workspace_id == workspace_id,
                Note.workspace_id == workspace_id,
                Note.knowledge_base_id == knowledge_base_id,
                Note.status == "active",
                NoteEmbedding.embedding_revision == embedding_revision,
            )
        )
        return list(rows.tuples())
