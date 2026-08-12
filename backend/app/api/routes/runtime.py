"""企业部署配置的安全状态接口，不返回任何密钥或原始连接串。"""

from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from app.api.schemas.runtime import (
    ExtensionCatalogResponse,
    ExtensionDescriptorResponse,
    GraphRebuildResponse,
    ModelConnectionTestResponse,
    ProviderStatusResponse,
    RebuildEmbeddingsResponse,
    RuntimeConfigurationResponse,
    TestModelConnectionRequest,
    UpdateWorkspaceModelConfigurationRequest,
    WorkspaceModelConfigurationResponse,
)
from app.application.configuration_service import ConfigurationService
from app.application.embedding_service import EmbeddingService
from app.application.extension_catalog_service import ExtensionCatalogService
from app.application.knowledge_service import KnowledgeService
from app.application.model_connectivity_service import ModelConnectivityService
from app.core.config import get_settings
from app.core.database import get_session, get_session_factory
from app.core.errors import ConfigurationError
from app.core.workspace import WorkspaceDependency
from app.domain.knowledge.models import (
    Document,
    KnowledgeBase,
    KnowledgeCommunitySummary,
    KnowledgeEntity,
    KnowledgeRelation,
)
from app.infrastructure.vector_index_service import VectorIndexService
from app.rag.embeddings import build_embedding_provider
from app.workers.ingestion import execute_graph_rebuild

router = APIRouter(tags=["Runtime Configuration"])
SessionDependency = Annotated[Session, Depends(get_session)]
SessionFactoryDependency = Annotated[sessionmaker[Session], Depends(get_session_factory)]


@router.get("/runtime/extensions", response_model=ExtensionCatalogResponse)
def list_runtime_extensions(
    workspace: WorkspaceDependency,
) -> ExtensionCatalogResponse:
    """返回当前部署已启用扩展；仅目录查询，不提供远程安装或执行入口。"""

    del workspace
    catalog = ExtensionCatalogService()
    return ExtensionCatalogResponse(
        parsers=[
            ExtensionDescriptorResponse(
                name=item.name,
                version=item.version,
                kind=item.kind,
                source_types=list(item.source_types),
            )
            for item in catalog.list_parsers()
        ],
        chunkers=[
            ExtensionDescriptorResponse(
                name=item.name,
                version=item.version,
                kind=item.kind,
                source_types=list(item.source_types),
            )
            for item in catalog.list_chunkers()
        ],
    )


@router.get("/runtime/configuration", response_model=RuntimeConfigurationResponse)
def get_runtime_configuration(
    session: SessionDependency,
    workspace: WorkspaceDependency,
) -> RuntimeConfigurationResponse:
    settings = ConfigurationService().resolve_settings(session, workspace_id=workspace.workspace_id)
    llm_configured = settings.llm_provider != "evidence_synthesis" and bool(
        settings.llm_model and settings.llm_api_key
    )
    embedding_configured = settings.embedding_provider != "hashing" and bool(
        settings.embedding_model and settings.embedding_api_key
    )
    warnings: list[str] = []
    if not llm_configured:
        warnings.append("未配置真实 LLM Provider，问答将使用本地受证据约束的降级回复。")
    if not embedding_configured:
        warnings.append("未配置语义 Embedding Provider，当前向量仅用于本地流程验证。")
    if not settings.auth_enabled:
        warnings.append("未启用 API Key 认证，当前工作区仅适合本地开发。")
    production_ready = llm_configured and embedding_configured and settings.auth_enabled
    return RuntimeConfigurationResponse(
        llm=ProviderStatusResponse(
            provider=settings.llm_provider,
            model=settings.llm_model or "未设置",
            configured=llm_configured,
            development_only=settings.llm_provider == "evidence_synthesis",
        ),
        embedding=ProviderStatusResponse(
            provider=settings.embedding_provider,
            model=settings.embedding_model or "未设置",
            configured=embedding_configured,
            development_only=settings.embedding_provider == "hashing",
        ),
        retrieval_mode=settings.retrieval_mode,
        workspace_auth_enabled=settings.auth_enabled,
        production_ready=production_ready,
        warnings=warnings,
    )


@router.get("/runtime/model-configuration", response_model=WorkspaceModelConfigurationResponse)
def get_model_configuration(
    session: SessionDependency,
    workspace: WorkspaceDependency,
) -> WorkspaceModelConfigurationResponse:
    service = ConfigurationService()
    settings = service.resolve_settings(session, workspace_id=workspace.workspace_id)
    stored = service.get(session, workspace_id=workspace.workspace_id)
    return WorkspaceModelConfigurationResponse(
        llm_provider=settings.llm_provider,
        llm_model=settings.llm_model,
        llm_base_url=settings.llm_base_url,
        has_llm_api_key=bool((stored and stored.llm_api_key_encrypted) or settings.llm_api_key),
        embedding_provider=settings.embedding_provider,
        embedding_model=settings.embedding_model,
        embedding_base_url=settings.embedding_base_url,
        has_embedding_api_key=bool(
            (stored and stored.embedding_api_key_encrypted) or settings.embedding_api_key
        ),
        embedding_dimensions=settings.embedding_dimensions,
        embedding_revision=service.embedding_revision(session, workspace_id=workspace.workspace_id),
        use_query_rewrite=settings.query_rewrite_enabled,
        use_query_router=settings.query_router_enabled,
        use_reranker=settings.reranker_enabled,
        reranker_provider=settings.reranker_provider,
        reranker_model=settings.reranker_model,
        reranker_base_url=settings.reranker_base_url,
        has_reranker_api_key=bool(
            (stored and stored.reranker_api_key_encrypted) or settings.reranker_api_key
        ),
        can_save_secrets=bool(get_settings().configuration_encryption_key),
    )


@router.put("/runtime/model-configuration", response_model=WorkspaceModelConfigurationResponse)
def update_model_configuration(
    payload: UpdateWorkspaceModelConfigurationRequest,
    session: SessionDependency,
    workspace: WorkspaceDependency,
) -> WorkspaceModelConfigurationResponse:
    service = ConfigurationService()
    config = service.update(
        session,
        workspace_id=workspace.workspace_id,
        llm_provider=payload.llm_provider,
        llm_model=payload.llm_model,
        llm_base_url=payload.llm_base_url,
        llm_api_key=payload.llm_api_key,
        clear_llm_api_key=payload.clear_llm_api_key,
        embedding_provider=payload.embedding_provider,
        embedding_model=payload.embedding_model,
        embedding_base_url=payload.embedding_base_url,
        embedding_api_key=payload.embedding_api_key,
        clear_embedding_api_key=payload.clear_embedding_api_key,
        embedding_dimensions=payload.embedding_dimensions,
        use_query_rewrite=payload.use_query_rewrite,
        use_query_router=payload.use_query_router,
        use_reranker=payload.use_reranker,
        reranker_provider=payload.reranker_provider,
        reranker_model=payload.reranker_model,
        reranker_base_url=payload.reranker_base_url,
        reranker_api_key=payload.reranker_api_key,
        clear_reranker_api_key=payload.clear_reranker_api_key,
    )
    return WorkspaceModelConfigurationResponse(
        llm_provider=config.llm_provider,
        llm_model=config.llm_model,
        llm_base_url=config.llm_base_url,
        has_llm_api_key=bool(config.llm_api_key_encrypted),
        embedding_provider=config.embedding_provider,
        embedding_model=config.embedding_model,
        embedding_base_url=config.embedding_base_url,
        has_embedding_api_key=bool(config.embedding_api_key_encrypted),
        embedding_dimensions=config.embedding_dimensions,
        embedding_revision=config.embedding_revision,
        use_query_rewrite=config.use_query_rewrite,
        use_query_router=config.use_query_router,
        use_reranker=config.use_reranker,
        reranker_provider=config.reranker_provider,
        reranker_model=config.reranker_model,
        reranker_base_url=config.reranker_base_url,
        has_reranker_api_key=bool(config.reranker_api_key_encrypted),
        can_save_secrets=bool(get_settings().configuration_encryption_key),
    )


@router.post(
    "/runtime/model-configuration/test/{model_kind}",
    response_model=ModelConnectionTestResponse,
)
def test_model_connection(
    model_kind: str,
    payload: TestModelConnectionRequest,
    session: SessionDependency,
    workspace: WorkspaceDependency,
) -> ModelConnectionTestResponse:
    service = ModelConnectivityService()
    if model_kind == "llm":
        provider, model, latency_ms, message = service.test_llm(
            session,
            workspace_id=workspace.workspace_id,
            provider=payload.provider,
            model=payload.model,
            base_url=payload.base_url,
            api_key=payload.api_key,
        )
    elif model_kind == "embedding":
        provider, model, latency_ms, message = service.test_embedding(
            session,
            workspace_id=workspace.workspace_id,
            provider=payload.provider,
            model=payload.model,
            base_url=payload.base_url,
            api_key=payload.api_key,
        )
    elif model_kind == "reranker":
        provider, model, latency_ms, message = service.test_reranker(
            session,
            workspace_id=workspace.workspace_id,
            provider=payload.provider,
            model=payload.model,
            base_url=payload.base_url,
            api_key=payload.api_key,
        )
    else:
        raise ConfigurationError(message="仅支持测试 LLM、Embedding 或 Reranker 连接。")
    return ModelConnectionTestResponse(
        provider=provider,
        model=model,
        latency_ms=latency_ms,
        message=message,
    )


@router.post(
    "/knowledge-bases/{knowledge_base_id}/embeddings/rebuild",
    response_model=RebuildEmbeddingsResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def rebuild_embeddings(
    knowledge_base_id: str,
    session: SessionDependency,
    workspace: WorkspaceDependency,
) -> RebuildEmbeddingsResponse:
    knowledge_base = KnowledgeService().get_knowledge_base(
        session,
        knowledge_base_id,
        workspace_id=workspace.workspace_id,
    )
    configuration_service = ConfigurationService()
    embedding_revision = configuration_service.embedding_revision(
        session, workspace_id=workspace.workspace_id
    )
    knowledge_base.index_status = "building"
    session.commit()
    try:
        resolved_settings = configuration_service.resolve_settings(
            session, workspace_id=workspace.workspace_id
        )
        embedding_provider = build_embedding_provider(resolved_settings)
        # 仅在用户明确发起重建时创建新维度的 HNSW 部分索引，避免导入路径持有 DDL 锁。
        VectorIndexService().ensure_hnsw_indexes(
            session,
            workspace_id=workspace.workspace_id,
            dimension=resolved_settings.embedding_dimensions,
        )
        document_count, chunk_count = EmbeddingService(
            provider=embedding_provider,
            expected_dimensions=resolved_settings.embedding_dimensions,
        ).rebuild_knowledge_base(
            session,
            knowledge_base_id=knowledge_base_id,
            workspace_id=workspace.workspace_id,
            embedding_revision=embedding_revision,
        )
        knowledge_base.embedding_revision = embedding_revision
        knowledge_base.index_status = "ready"
        session.commit()
    except Exception:
        session.rollback()
        knowledge_base = KnowledgeService().get_knowledge_base(
            session, knowledge_base_id, workspace_id=workspace.workspace_id
        )
        knowledge_base.index_status = "stale"
        session.commit()
        raise
    return RebuildEmbeddingsResponse(
        document_count=document_count,
        chunk_count=chunk_count,
        embedding_revision=embedding_revision,
        index_status="ready",
    )


def _graph_rebuild_response(
    session: Session, knowledge_base: KnowledgeBase, *, state: str | None = None
) -> GraphRebuildResponse:
    """统一构造图索引状态，避免状态接口和重建接口字段漂移。"""

    entity_count = session.scalar(
        select(func.count())
        .select_from(KnowledgeEntity)
        .where(
            KnowledgeEntity.workspace_id == knowledge_base.workspace_id,
            KnowledgeEntity.knowledge_base_id == knowledge_base.id,
        )
    )
    relation_count = session.scalar(
        select(func.count())
        .select_from(KnowledgeRelation)
        .where(
            KnowledgeRelation.workspace_id == knowledge_base.workspace_id,
            KnowledgeRelation.knowledge_base_id == knowledge_base.id,
        )
    )
    community_count = session.scalar(
        select(func.count())
        .select_from(KnowledgeCommunitySummary)
        .where(
            KnowledgeCommunitySummary.workspace_id == knowledge_base.workspace_id,
            KnowledgeCommunitySummary.knowledge_base_id == knowledge_base.id,
            KnowledgeCommunitySummary.status == "active",
        )
    )
    document_count = session.scalar(
        select(func.count())
        .select_from(Document)
        .where(
            Document.workspace_id == knowledge_base.workspace_id,
            Document.knowledge_base_id == knowledge_base.id,
            Document.status == "indexed",
        )
    )
    latest_summary = session.scalar(
        select(KnowledgeCommunitySummary)
        .where(
            KnowledgeCommunitySummary.workspace_id == knowledge_base.workspace_id,
            KnowledgeCommunitySummary.knowledge_base_id == knowledge_base.id,
            KnowledgeCommunitySummary.status == "active",
        )
        .order_by(KnowledgeCommunitySummary.graph_revision.desc())
        .limit(1)
    )
    summary_fallback = 0
    if latest_summary is not None and latest_summary.extractor_provider == "llm_graph_extractor":
        summary_fallback = int(
            session.scalar(
                select(func.count())
                .select_from(KnowledgeCommunitySummary)
                .where(
                    KnowledgeCommunitySummary.workspace_id == knowledge_base.workspace_id,
                    KnowledgeCommunitySummary.knowledge_base_id == knowledge_base.id,
                    KnowledgeCommunitySummary.graph_revision == knowledge_base.graph_revision,
                    KnowledgeCommunitySummary.status == "active",
                    KnowledgeCommunitySummary.summary_provider == "deterministic-community-summary",
                )
            )
            or 0
        )
    return GraphRebuildResponse(
        state=state or knowledge_base.graph_status,
        document_count=int(document_count or 0),
        entity_count=int(entity_count or 0),
        relation_count=int(relation_count or 0),
        community_count=int(community_count or 0),
        graph_revision=knowledge_base.graph_revision,
        extractor_provider=latest_summary.extractor_provider if latest_summary else "rule",
        summary_provider=(
            latest_summary.summary_provider if latest_summary else "deterministic-community-summary"
        ),
        summary_fallback=summary_fallback,
    )


@router.post(
    "/knowledge-bases/{knowledge_base_id}/graph/rebuild",
    response_model=GraphRebuildResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def rebuild_graph(
    knowledge_base_id: str,
    background_tasks: BackgroundTasks,
    session: SessionDependency,
    session_factory: SessionFactoryDependency,
    workspace: WorkspaceDependency,
) -> GraphRebuildResponse:
    """异步重建实体、关系和多层社区摘要。"""

    knowledge_base = KnowledgeService().get_knowledge_base(
        session,
        knowledge_base_id,
        workspace_id=workspace.workspace_id,
    )
    if knowledge_base.graph_status == "building":
        return _graph_rebuild_response(session, knowledge_base)
    knowledge_base.graph_status = "building"
    session.commit()
    background_tasks.add_task(
        execute_graph_rebuild,
        knowledge_base_id,
        workspace.workspace_id,
        session_factory,
    )
    return _graph_rebuild_response(session, knowledge_base, state="building")


@router.get(
    "/knowledge-bases/{knowledge_base_id}/graph/status",
    response_model=GraphRebuildResponse,
)
def graph_status(
    knowledge_base_id: str,
    session: SessionDependency,
    workspace: WorkspaceDependency,
) -> GraphRebuildResponse:
    knowledge_base = KnowledgeService().get_knowledge_base(
        session,
        knowledge_base_id,
        workspace_id=workspace.workspace_id,
    )
    return _graph_rebuild_response(session, knowledge_base)
