"""检索 API Schema。"""

from pydantic import Field

from app.api.schemas.knowledge import ApiModel


class RetrievalSearchRequest(ApiModel):
    knowledge_base_id: str
    query: str = Field(min_length=1, max_length=10_000)
    limit: int = Field(default=8, ge=1, le=30)


class EvidenceResponse(ApiModel):
    source_type: str
    source_id: str
    title: str
    content: str
    score: float
    locator: str
    source_url: str | None = None
    source_validation_state: str = "not_applicable"
    source_is_approved: bool = False
    source_trust_level: str = "standard"
    governance_availability: str = "available"
    conflict_state: str = "none"


class RetrievalDiagnosticsResponse(ApiModel):
    """不暴露正文的检索链路指标，供研究页和离线调试使用。"""

    keyword_candidates: int
    semantic_candidates: int
    fused_candidates: int
    entity_retrieval_enabled: bool
    entity_matched_entities: int
    entity_candidates: int
    entity_covered_documents: int
    dual_route_fused_candidates: int
    tag_retrieval_enabled: bool
    tag_matched_tags: int
    tag_candidates: int
    tag_covered_assets: int
    tag_route_fused_candidates: int
    metadata_boosted_candidates: int
    rerank_candidates: int
    final_candidates: int
    dynamic_top_k_enabled: bool
    dynamic_top_k_profile: str
    dynamic_top_k_minimum: int
    dynamic_top_k_selected: int
    dynamic_top_k_source_coverage: int
    dynamic_top_k_budget_tokens: int
    dynamic_top_k_estimated_tokens: int
    dynamic_top_k_stop_reason: str
    dynamic_top_k_boundary_score_gap: float | None = None
    governance_excluded_superseded: int
    governance_excluded_future_effective: int
    governance_expired_candidates: int
    governance_conflicted_candidates: int
    governance_trust_adjusted_candidates: int
    query_rewrite_ms: float
    query_variant_count: int
    query_subquery_count: int
    query_synonym_count: int
    query_fanout_candidates: int
    hybrid_retrieval_ms: float
    rerank_ms: float
    context_expanded: int
    context_characters: int
    context_expansion_ms: float
    graph_mode: str
    graph_matched_entities: int
    graph_expanded_entities: int
    graph_candidates: int
    graph_covered_documents: int
    matched_communities: int
    community_summary_candidates: int
    community_expanded_chunks: int
    community_covered_documents: int
    total_ms: float


class RetrievalSearchResponse(ApiModel):
    retriever: str
    cache_backend: str
    embedding_cache_hit: bool
    rewritten_query: str
    query_rewriter: str
    query_rewrite_cache_hit: bool
    query_rewrite_fallback: bool
    reranker: str | None
    reranker_cache_hit: bool
    reranker_fallback: bool
    diagnostics: RetrievalDiagnosticsResponse
    evidences: list[EvidenceResponse]
