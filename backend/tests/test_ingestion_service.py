"""入库服务测试，验证任务状态机和可重复执行语义。"""

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.application.ingestion_service import IngestionService
from app.application.knowledge_service import KnowledgeService
from app.core.errors import ProcessingError
from app.domain.knowledge.models import ChunkEmbedding, DocumentChunk


def create_knowledge_base(session: Session) -> str:
    return KnowledgeService().create_knowledge_base(session, name="研究库", description=None).id


def test_ingestion_creates_ordered_chunks(session_factory: sessionmaker[Session]) -> None:
    with session_factory() as session:
        knowledge_base_id = create_knowledge_base(session)
        service = IngestionService()
        document, job = service.create_document(
            session,
            knowledge_base_id=knowledge_base_id,
            title="研究记录",
            source_type="plain_text",
            raw_content="第一段内容。\n\n第二段内容。",
        )
        completed = service.run_job(session, job_id=job.id)

        assert completed.state == "succeeded"
        assert document.status == "indexed"
        chunks = list(session.query(DocumentChunk).filter(DocumentChunk.document_id == document.id))
        assert [chunk.ordinal for chunk in chunks] == [0]
        assert chunks[0].content == "第一段内容。\n\n第二段内容。"
        embedding = session.scalar(
            select(ChunkEmbedding).where(ChunkEmbedding.document_chunk_id == chunks[0].id)
        )
        assert embedding is not None
        assert embedding.provider_name == "hashing"
        assert embedding.dimensions == 256


def test_unknown_extension_is_rejected_before_creating_document(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as session:
        knowledge_base_id = create_knowledge_base(session)
        service = IngestionService()
        try:
            service.create_document(
                session,
                knowledge_base_id=knowledge_base_id,
                title="无效扩展",
                source_type="plain_text",
                raw_content="内容",
                parser_name="unavailable_parser",
            )
        except ProcessingError as exc:
            assert exc.code == "PROCESSING_ERROR"
        else:
            raise AssertionError("应拒绝未注册的解析器")


def test_archive_queued_document_cancels_job_and_removes_chunks(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as session:
        knowledge_base_id = create_knowledge_base(session)
        service = IngestionService()
        document, job = service.create_document(
            session,
            knowledge_base_id=knowledge_base_id,
            title="待删除.md",
            source_type="markdown",
            raw_content="# 待删除",
            parser_name="markdown",
        )

        archived = service.archive_document(session, document_id=document.id)

        assert archived.status == "archived"
        assert job.state == "cancelled"
        assert (
            session.query(DocumentChunk).filter(DocumentChunk.document_id == document.id).count()
            == 0
        )
