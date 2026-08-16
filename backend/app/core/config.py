"""应用配置只从环境变量读取，避免配置散落在业务代码中。"""

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """运行时配置；`APP_` 前缀避免污染其他项目的环境变量。"""

    model_config = SettingsConfigDict(env_file=".env", env_prefix="APP_", extra="ignore")

    app_name: str = "RAG Notes Agent"
    environment: str = "development"
    database_url: str = "sqlite:///./rag_notes_agent.db"
    log_level: str = "INFO"
    cors_origins: str = "http://localhost:5173"
    max_upload_bytes: int = 25 * 1024 * 1024
    # 入库任务可由请求内 BackgroundTasks 执行，或交给独立轮询 Worker；生产 Compose 使用 poll。
    ingestion_dispatch_mode: str = "background"
    ingestion_max_attempts: int = Field(default=3, ge=1, le=20)
    ingestion_lease_seconds: int = Field(default=900, ge=30, le=86_400)
    ingestion_retry_base_seconds: float = Field(default=5.0, ge=0.5, le=3_600)
    ingestion_retry_max_seconds: float = Field(default=300.0, ge=1.0, le=86_400)
    # 单个 Worker 进程允许并行处理的文档数。任务领取仍由数据库租约保证唯一性。
    ingestion_worker_concurrency: int = Field(default=1, ge=1, le=32)
    # 生产审批角色映射：workspace_id:actor_id=owner,workspace_id:actor_id=approver。
    workspace_actor_roles: str = ""
    agent_proposal_ttl_seconds: int = Field(default=86_400, ge=300, le=2_592_000)
    # Bounded Agentic RAG 只对关系/全局问题进行有限步只读检索，所有预算由服务端强制。
    agentic_rag_enabled: bool = True
    agentic_rag_max_steps: int = Field(default=3, ge=1, le=6)
    agentic_rag_min_evidence: int = Field(default=3, ge=1, le=20)
    agentic_rag_token_budget: int = Field(default=6_000, ge=256, le=32_768)
    agentic_rag_max_latency_ms: int = Field(default=12_000, ge=500, le=120_000)
    # 网页导入在 Worker 中执行；默认仅允许 HTTPS，并限制连接/响应规模，降低 SSRF 和资源耗尽风险。
    web_import_timeout_seconds: float = Field(default=15.0, ge=1.0, le=120.0)
    web_import_max_bytes: int = Field(default=5 * 1024 * 1024, ge=16_384, le=50 * 1024 * 1024)
    web_import_max_redirects: int = Field(default=3, ge=0, le=8)
    web_import_allow_http: bool = False
    # 来源校验不参与正文抓取和问答请求。网页完成入库后由 Worker 复核其可访问性，
    # 防止后续失效链接在引用中仍被误标为可用来源。
    source_validation_enabled: bool = True
    source_validation_timeout_seconds: float = Field(default=10.0, ge=1.0, le=60.0)
    source_validation_approved_domains: str = ""
    # 定时复核是独立 Worker 的低频维护任务，默认关闭，避免开发环境和未授权公网环境产生意外外连。
    # 启用后仅检查已到复核周期的网页资料，失败只更新来源健康元数据，不影响已存档正文和检索索引。
    source_validation_recheck_enabled: bool = False
    source_validation_recheck_interval_hours: int = Field(default=168, ge=1, le=8_760)
    source_validation_recheck_batch_size: int = Field(default=20, ge=1, le=200)
    source_validation_recheck_poll_seconds: float = Field(default=300.0, ge=5.0, le=86_400.0)
    # 变更检测会额外抓取到期网页正文；默认关闭，且只写状态，绝不静默替换已入库版本。
    web_content_change_detection_enabled: bool = False
    # 开发环境使用固定默认工作区；生产环境应通过 API Key 显式绑定工作区。
    default_workspace_id: str = "00000000-0000-0000-0000-000000000001"
    default_workspace_name: str = "默认工作区"
    auth_enabled: bool = False
    # 格式：workspace_id=api_key,workspace_id_2=api_key_2。API Key 只从环境变量读取。
    workspace_api_keys: str = ""
    # 默认提供证据摘要，配置兼容 OpenAI 的服务后自动切换为真实模型流。
    llm_provider: str = "evidence_synthesis"
    llm_model: str = ""
    llm_api_key: str = ""
    llm_base_url: str = "https://api.openai.com/v1"
    llm_timeout_seconds: float = 60.0
    # 所有外部模型调用共享进程级并发闸门和指数退避策略。
    model_max_concurrency: int = Field(default=4, ge=1, le=64)
    model_acquire_timeout_seconds: float = Field(default=3.0, ge=0.1, le=60.0)
    # 多 Worker 部署时优先用 Redis 共享模型配额；Redis 故障时保留进程级闸门以保障可用性。
    model_distributed_concurrency_enabled: bool = True
    model_distributed_lease_seconds: int = Field(default=300, ge=30, le=7_200)
    model_retry_attempts: int = Field(default=2, ge=0, le=5)
    model_retry_base_seconds: float = Field(default=0.5, ge=0.1, le=30.0)
    model_retry_max_seconds: float = Field(default=8.0, ge=0.5, le=120.0)
    llm_max_output_tokens: int = Field(default=1_024, ge=64, le=16_384)
    # Embedding 密钥独立于聊天模型密钥；生产环境必须选择受控的语义模型。
    embedding_provider: str = "hashing"
    embedding_model: str = "hashing-256"
    embedding_api_key: str = ""
    embedding_base_url: str = "https://api.openai.com/v1"
    embedding_dimensions: int = 256
    # 对齐常见 OpenAI 兼容网关的输入上限；Provider 在内部按此值分批，保持原始顺序。
    embedding_batch_size: int = Field(default=25, ge=1, le=256)
    # Chunker 由部署侧白名单启停；浏览器只能选择已启用实现，不能上传或执行第三方插件代码。
    enabled_chunkers: str = "structured,paragraph"
    retrieval_mode: str = "hybrid"
    # Rewrite 复用问答 LLM 配置，失败时原样检索，不能阻断知识库问答。
    query_rewrite_enabled: bool = False
    # 多路改写默认开启，但始终保留原问题作为一路召回，避免 LLM 改写丢失专有名词。
    query_rewrite_multi_query_enabled: bool = True
    query_rewrite_max_variants: int = Field(default=5, ge=1, le=8)
    query_rewrite_max_subqueries: int = Field(default=3, ge=0, le=5)
    query_rewrite_max_synonyms: int = Field(default=3, ge=0, le=5)
    query_rewrite_timeout_seconds: float = Field(default=12.0, ge=1, le=60)
    query_rewrite_max_length: int = Field(default=256, ge=32, le=1_000)
    # 智能路由只处理规则无法确定的灰区问题；高置信度系统策略始终优先。
    query_router_enabled: bool = False
    query_router_timeout_seconds: float = Field(default=4.0, ge=1, le=30)
    query_router_confidence_threshold: float = Field(default=0.85, ge=0.5, le=1.0)
    # 子块负责召回，最终候选补充同章节相邻块；限制长度避免 Prompt 膨胀。
    parent_child_enabled: bool = True
    parent_child_window: int = Field(default=1, ge=0, le=3)
    parent_child_max_characters: int = Field(default=2_400, ge=256, le=12_000)
    # 发送给模型的证据独立于引用快照限额；超限时只截断模型副本，不修改原始证据。
    rag_context_max_tokens: int = Field(default=4_096, ge=128, le=32_768)
    # Dynamic Top-K 只在已排序证据上做缩减；关闭后严格退回请求的固定 K。
    dynamic_top_k_enabled: bool = True
    dynamic_top_k_min_candidates: int = Field(default=3, ge=1, le=30)
    dynamic_top_k_max_candidates: int = Field(default=12, ge=1, le=30)
    dynamic_top_k_score_gap_threshold: float = Field(default=0.12, ge=0.0, le=1.0)
    dynamic_top_k_target_source_coverage: int = Field(default=2, ge=1, le=10)
    dynamic_top_k_budget_ratio: float = Field(default=0.8, ge=0.25, le=1.0)
    # 局部问题的候选若完全不包含问题中的有效实体/短语，则清空证据以触发可靠拒答。
    # 图谱关系和全局问题豁免，避免中间实体未出现在原问题时被错误过滤。
    answerability_gate_enabled: bool = True
    # 定向实体召回与通用 Hybrid 召回独立取候选，随后 RRF 融合；关闭后保留原有单路链路。
    entity_retrieval_enabled: bool = True
    entity_retrieval_max_entities: int = Field(default=12, ge=1, le=100)
    entity_retrieval_candidate_limit: int = Field(default=30, ge=1, le=100)
    # 业务标签只在审批后参与定向候选召回。默认关闭，须经离线评测确认收益后再启用。
    tag_retrieval_enabled: bool = False
    tag_retrieval_max_tags: int = Field(default=12, ge=1, le=100)
    tag_retrieval_candidate_limit: int = Field(default=30, ge=1, le=100)
    # 图谱 LLM 抽取默认关闭，显式开启后才会在入库/重建阶段调用工作区配置的问答模型。
    # 关闭时仍使用规则抽取和社区摘要，保证本地开发可重复且不产生意外模型费用。
    graph_llm_extraction_enabled: bool = False
    graph_llm_extraction_timeout_seconds: float = Field(default=20.0, ge=2.0, le=120.0)
    graph_llm_extraction_max_chars: int = Field(default=6_000, ge=500, le=20_000)
    # 社区发现默认保持确定性连通分量。生产可显式切换 Louvain，但依赖缺失或算法失败时
    # 必须回退并在图谱状态中暴露，不能将回退结果误标为生产图谱算法。
    graph_community_algorithm: str = "connected_components"
    graph_community_louvain_resolution: float = Field(default=1.0, ge=0.1, le=5.0)
    graph_community_min_relation_confidence: float = Field(default=0.40, ge=0.0, le=1.0)
    # 元数据只做轻量排序加权，不改变工作区边界；权重纳入评测以避免暗中掩盖召回退化。
    metadata_boost_enabled: bool = True
    metadata_title_boost: float = Field(default=0.12, ge=0.0, le=1.0)
    metadata_section_boost: float = Field(default=0.08, ge=0.0, le=1.0)
    metadata_source_type_boost: float = Field(default=0.03, ge=0.0, le=1.0)
    metadata_max_boost: float = Field(default=0.20, ge=0.0, le=1.0)
    reranker_enabled: bool = False
    reranker_provider: str = "rule"
    reranker_model: str = ""
    reranker_api_key: str = ""
    reranker_base_url: str = "https://dashscope.aliyuncs.com/compatible-api/v1"
    reranker_candidate_limit: int = Field(default=20, ge=2, le=100)
    reranker_timeout_seconds: float = Field(default=20.0, ge=1, le=120)
    # Redis 是生产缓存首选；未配置、依赖缺失或连接故障时自动回退为单进程内存缓存。
    cache_enabled: bool = True
    redis_url: str = ""
    redis_key_prefix: str = "rag-notes-agent"
    cache_default_ttl_seconds: int = Field(default=900, ge=1, le=86_400)
    cache_local_max_entries: int = Field(default=512, ge=32, le=20_000)
    # API 限流使用固定窗口；Redis 不可用时回退进程内计数，避免入口治理反向拖垮服务。
    rate_limit_enabled: bool = True
    rate_limit_window_seconds: int = Field(default=60, ge=1, le=3_600)
    rate_limit_api_requests: int = Field(default=1_000, ge=1, le=100_000)
    rate_limit_write_requests: int = Field(default=240, ge=1, le=100_000)
    rate_limit_expensive_requests: int = Field(default=60, ge=1, le=100_000)
    rate_limit_agent_requests: int = Field(default=30, ge=1, le=100_000)
    # 指标默认关闭，避免在开发/公网环境意外暴露运行画像；Token 只从部署 Secret 读取。
    metrics_enabled: bool = False
    metrics_token: str = ""
    # OTLP 默认关闭；Trace 禁止采集请求体和知识库正文，Collector 地址只从部署环境读取。
    telemetry_enabled: bool = False
    telemetry_otlp_endpoint: str = ""
    telemetry_service_name: str = "rag-notes-agent"
    telemetry_sample_ratio: float = Field(default=1.0, ge=0.0, le=1.0)
    allow_local_development_providers: bool = True
    allow_user_model_configuration: bool = True
    # Fernet 密钥只存在部署环境；缺失时拒绝持久化用户密钥，不能退回明文存储。
    configuration_encryption_key: str = ""

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    @property
    def enabled_chunker_names(self) -> set[str]:
        return {name.strip() for name in self.enabled_chunkers.split(",") if name.strip()}


@lru_cache
def get_settings() -> Settings:
    """缓存配置实例，测试可通过 cache_clear 显式刷新。"""

    return Settings()
