"""入库阶段的 Embedding 生成与重建。"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.errors import ConfigurationError
from app.domain.knowledge.models import ChunkEmbedding, Document, DocumentChunk, Note, NoteEmbedding
from app.domain.knowledge.repositories import ChunkEmbeddingRepository, NoteEmbeddingRepository
from app.extensions.contracts import EmbeddingProvider
from app.rag.embeddings import build_embedding_provider


class EmbeddingService:
    """将切块与 Embedding 版本绑定，保证后续可按模型回溯和重建。"""

    def __init__(
        self,
        provider: EmbeddingProvider | None = None,
        *,
        expected_dimensions: int | None = None,
    ) -> None:
        self.provider = provider or build_embedding_provider(get_settings())
        self.expected_dimensions = expected_dimensions
        self.repository = ChunkEmbeddingRepository()
        self.note_repository = NoteEmbeddingRepository()

    def index_note(self, session: Session, *, note: Note, embedding_revision: int = 1) -> None:
        """用标题和正文共同嵌入，保证简短笔记也可按标题被语义召回。"""

        content = f"{note.title}\n{note.content}".strip()
        vector = self.provider.embed_documents([content])[0]
        self._validate_vector_dimensions([vector])
        self.note_repository.replace_for_note(
            session,
            note_id=note.id,
            workspace_id=note.workspace_id,
            embedding=NoteEmbedding(
                workspace_id=note.workspace_id,
                note_id=note.id,
                provider_name=self.provider.name,
                model_name=self.provider.model_name,
                dimensions=len(vector),
                embedding_revision=embedding_revision,
                embedding=vector,
                embedding_vector=vector,
            ),
        )
        session.flush()

    def index_document(
        self, session: Session, *, document: Document, embedding_revision: int = 1
    ) -> int:
        chunks = list(
            session.scalars(
                select(DocumentChunk)
                .where(
                    DocumentChunk.document_id == document.id,
                    DocumentChunk.workspace_id == document.workspace_id,
                )
                .order_by(DocumentChunk.ordinal.asc())
            )
        )
        vectors = self.provider.embed_documents([chunk.content for chunk in chunks])
        if len(vectors) != len(chunks):
            raise ValueError("Embedding 数量与文档切块数量不一致。")
        self._validate_vector_dimensions(vectors)
        embeddings = [
            ChunkEmbedding(
                workspace_id=document.workspace_id,
                document_chunk_id=chunk.id,
                provider_name=self.provider.name,
                model_name=self.provider.model_name,
                dimensions=len(vector),
                embedding_revision=embedding_revision,
                embedding=vector,
                embedding_vector=vector,
            )
            for chunk, vector in zip(chunks, vectors, strict=True)
        ]
        self.repository.replace_for_document(
            session,
            document_id=document.id,
            workspace_id=document.workspace_id,
            embeddings=embeddings,
        )
        session.flush()
        return len(embeddings)

    def rebuild_knowledge_base(
        self,
        session: Session,
        *,
        knowledge_base_id: str,
        workspace_id: str,
        embedding_revision: int,
    ) -> tuple[int, int]:
        documents = list(
            session.scalars(
                select(Document).where(
                    Document.knowledge_base_id == knowledge_base_id,
                    Document.workspace_id == workspace_id,
                    Document.status == "indexed",
                )
            )
        )
        chunk_count = sum(
            self.index_document(session, document=document, embedding_revision=embedding_revision)
            for document in documents
        )
        notes = list(
            session.scalars(
                select(Note).where(
                    Note.knowledge_base_id == knowledge_base_id,
                    Note.workspace_id == workspace_id,
                    Note.status == "active",
                )
            )
        )
        for note in notes:
            self.index_note(session, note=note, embedding_revision=embedding_revision)
        session.commit()
        return len(documents), chunk_count

    def _validate_vector_dimensions(self, vectors: list[list[float]]) -> None:
        """在写库前校验真实模型输出，避免暴露 pgvector 底层维度异常。"""

        dimensions = {len(vector) for vector in vectors}
        if len(dimensions) != 1:
            raise ConfigurationError(message="Embedding 模型返回了不一致的向量维度。")
        actual_dimension = dimensions.pop()
        if self.expected_dimensions is not None and actual_dimension != self.expected_dimensions:
            raise ConfigurationError(
                message="Embedding 模型实际返回维度与设置不一致，请修改模型维度后重试。",
                details={
                    "expectedDimensions": self.expected_dimensions,
                    "actualDimensions": actual_dimension,
                },
            )
