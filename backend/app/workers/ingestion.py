"""可由任务队列包装的入库 Worker 函数。"""

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.agent.llm import build_llm_provider
from app.application.configuration_service import ConfigurationService
from app.application.ingestion_service import IngestionService
from app.core.database import SessionLocal
from app.core.logging import get_logger
from app.domain.knowledge.models import Document, KnowledgeBase
from app.rag.communities import CommunitySummaryService, LLMCommunitySummaryGenerator
from app.rag.graph import GraphIndexService, build_graph_extractor

logger = get_logger(__name__)


def execute_ingestion_job(
    job_id: str,
    session_factory: sessionmaker[Session] = SessionLocal,
    workspace_id: str | None = None,
) -> None:
    """执行单个任务；Celery/Dramatiq 接入时只需调用此函数。"""

    with session_factory() as session:
        try:
            job = IngestionService().run_job(session, job_id=job_id, workspace_id=workspace_id)
            logger.info(
                "ingestion_job_finished", job_id=job.id, state=job.state, attempts=job.attempts
            )
        except Exception:
            logger.exception("ingestion_job_failed", job_id=job_id)
            raise


def execute_rechunk_knowledge_base(
    knowledge_base_id: str,
    workspace_id: str,
    session_factory: sessionmaker[Session] = SessionLocal,
) -> None:
    """重切分任务与 HTTP 请求解耦，未来可原样迁移到 Celery/Dramatiq。"""

    with session_factory() as session:
        service = IngestionService()
        try:
            document_count, chunk_count = service.rechunk_knowledge_base(
                session,
                knowledge_base_id=knowledge_base_id,
                workspace_id=workspace_id,
            )
            logger.info(
                "knowledge_base_rechunk_finished",
                knowledge_base_id=knowledge_base_id,
                document_count=document_count,
                chunk_count=chunk_count,
            )
        except Exception:
            logger.exception("knowledge_base_rechunk_failed", knowledge_base_id=knowledge_base_id)
            service.mark_rechunk_failed(
                session,
                knowledge_base_id=knowledge_base_id,
                workspace_id=workspace_id,
            )


def execute_graph_rebuild(
    knowledge_base_id: str,
    workspace_id: str,
    session_factory: sessionmaker[Session] = SessionLocal,
) -> None:
    """重建实体、关系和社区摘要；适配轮询 Worker 时无需修改业务逻辑。"""

    with session_factory() as session:
        try:
            knowledge_base = session.get(KnowledgeBase, knowledge_base_id)
            if knowledge_base is None or knowledge_base.workspace_id != workspace_id:
                raise ValueError("知识库不存在或不属于当前工作区")
            documents = list(
                session.scalars(
                    select(Document).where(
                        Document.knowledge_base_id == knowledge_base_id,
                        Document.workspace_id == workspace_id,
                        Document.status == "indexed",
                    )
                )
            )
            settings = ConfigurationService().resolve_settings(session, workspace_id=workspace_id)
            graph_service = GraphIndexService(extractor=build_graph_extractor(settings))
            for document in documents:
                graph_service.index_document(
                    session,
                    document_id=document.id,
                    workspace_id=workspace_id,
                    knowledge_base_id=knowledge_base_id,
                )
            summary_generator = None
            if settings.llm_provider == "openai_compatible" and settings.llm_api_key:
                try:
                    summary_generator = LLMCommunitySummaryGenerator(build_llm_provider(settings))
                except Exception:
                    summary_generator = None
            CommunitySummaryService().rebuild(
                session,
                knowledge_base_id=knowledge_base_id,
                workspace_id=workspace_id,
                summary_generator=summary_generator,
                extractor_provider=getattr(graph_service.extractor, "name", "rule"),
                extractor_version=getattr(graph_service.extractor, "version", "v1"),
            )
            session.commit()
            logger.info("knowledge_graph_rebuild_finished", knowledge_base_id=knowledge_base_id)
        except Exception:
            session.rollback()
            knowledge_base = session.get(KnowledgeBase, knowledge_base_id)
            if knowledge_base is not None and knowledge_base.workspace_id == workspace_id:
                knowledge_base.graph_status = "stale"
                session.commit()
            logger.exception("knowledge_graph_rebuild_failed", knowledge_base_id=knowledge_base_id)
            raise
