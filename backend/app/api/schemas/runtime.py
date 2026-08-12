"""非敏感运行配置状态模型。"""

from pydantic import Field

from app.api.schemas.knowledge import ApiModel


class ProviderStatusResponse(ApiModel):
    provider: str
    model: str
    configured: bool
    development_only: bool


class RuntimeConfigurationResponse(ApiModel):
    llm: ProviderStatusResponse
    embedding: ProviderStatusResponse
    retrieval_mode: str
    workspace_auth_enabled: bool
    production_ready: bool
    warnings: list[str]


class WorkspaceModelConfigurationResponse(ApiModel):
    llm_provider: str
    llm_model: str
    llm_base_url: str
    has_llm_api_key: bool
    embedding_provider: str
    embedding_model: str
    embedding_base_url: str
    has_embedding_api_key: bool
    embedding_dimensions: int
    embedding_revision: int
    use_query_rewrite: bool
    use_query_router: bool
    use_reranker: bool
    reranker_provider: str
    reranker_model: str
    reranker_base_url: str
    has_reranker_api_key: bool
    can_save_secrets: bool


class UpdateWorkspaceModelConfigurationRequest(ApiModel):
    llm_provider: str = Field(min_length=1, max_length=80)
    llm_model: str = Field(default="", max_length=160)
    llm_base_url: str = Field(default="", max_length=500)
    llm_api_key: str | None = Field(default=None, min_length=1, max_length=1000)
    clear_llm_api_key: bool = False
    embedding_provider: str = Field(min_length=1, max_length=80)
    embedding_model: str = Field(default="", max_length=160)
    embedding_base_url: str = Field(default="", max_length=500)
    embedding_api_key: str | None = Field(default=None, min_length=1, max_length=1000)
    clear_embedding_api_key: bool = False
    embedding_dimensions: int = Field(default=1536, ge=8, le=8192)
    use_query_rewrite: bool = False
    use_query_router: bool = False
    use_reranker: bool = False
    reranker_provider: str = Field(default="rule", min_length=1, max_length=80)
    reranker_model: str = Field(default="", max_length=160)
    reranker_base_url: str = Field(default="", max_length=500)
    reranker_api_key: str | None = Field(default=None, min_length=1, max_length=1000)
    clear_reranker_api_key: bool = False


class TestModelConnectionRequest(ApiModel):
    """仅用于一次性连通测试的参数，不会写入工作区配置。"""

    provider: str = Field(min_length=1, max_length=80)
    model: str = Field(default="", max_length=160)
    base_url: str = Field(default="", max_length=500)
    api_key: str | None = Field(default=None, min_length=1, max_length=1000)


class ModelConnectionTestResponse(ApiModel):
    provider: str
    model: str
    latency_ms: int
    message: str


class RebuildEmbeddingsResponse(ApiModel):
    document_count: int
    chunk_count: int
    embedding_revision: int
    index_status: str


class GraphRebuildResponse(ApiModel):
    """图谱/社区重建任务状态；不返回摘要正文，正文只能通过证据接口读取。"""

    state: str
    document_count: int
    entity_count: int
    relation_count: int
    community_count: int
    graph_revision: int
    extractor_provider: str
    summary_provider: str
    summary_fallback: int


class ExtensionDescriptorResponse(ApiModel):
    name: str
    version: str
    kind: str
    source_types: list[str]


class ExtensionCatalogResponse(ApiModel):
    parsers: list[ExtensionDescriptorResponse]
    chunkers: list[ExtensionDescriptorResponse]
