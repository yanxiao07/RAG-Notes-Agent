"""检索用例。知识库存在性检查在这里完成，不下沉到检索器。"""

from dataclasses import dataclass
from time import perf_counter

from sqlalchemy.orm import Session

from app.application.configuration_service import ConfigurationService
from app.application.knowledge_service import KnowledgeService
from app.core.config import Settings, get_settings
from app.core.errors import IndexRebuildRequiredError
from app.core.telemetry import set_safe_attribute, traced_span
from app.core.workspace import ensure_workspace
from app.rag.answerability import RetrievalAnswerabilityGate
from app.rag.cache import build_cache
from app.rag.communities import CommunityRetriever
from app.rag.document_governance import GovernanceFilterStats, apply_document_governance
from app.rag.dynamic_top_k import DynamicTopKPolicy, DynamicTopKStats
from app.rag.embeddings import build_embedding_provider
from app.rag.entity_retrieval import EntityRetrievalStats, EntityRetriever
from app.rag.graph import (
    GraphRetrievalStats,
    GraphRetriever,
    classify_graph_mode,
    fuse_graph_evidence,
)
from app.rag.metadata_boost import MetadataBoostPolicy
from app.rag.parent_child import ParentChildContextExpander
from app.rag.postgres_retrieval import PostgresHybridRetriever
from app.rag.query_rewrite import QueryRewritePlan, QueryRewriter
from app.rag.rerank import build_reranker
from app.rag.retrieval import (
    Evidence,
    LocalHybridRetriever,
    RetrievalStageStats,
    fuse_query_evidence,
)
from app.rag.tag_retrieval import TagRetrievalStats, TagRetriever


@dataclass(frozen=True, slots=True)
class RetrievalDiagnostics:
    """面向 UI、审计和离线分析的检索链路统计。

    只记录数量、耗时和 Provider 名称，避免把用户问题、正文或密钥写进诊断日志。
    """

    keyword_candidates: int = 0
    semantic_candidates: int = 0
    fused_candidates: int = 0
    entity_retrieval_enabled: bool = False
    entity_matched_entities: int = 0
    entity_candidates: int = 0
    entity_covered_documents: int = 0
    dual_route_fused_candidates: int = 0
    tag_retrieval_enabled: bool = False
    tag_matched_tags: int = 0
    tag_candidates: int = 0
    tag_covered_assets: int = 0
    tag_route_fused_candidates: int = 0
    metadata_boosted_candidates: int = 0
    rerank_candidates: int = 0
    final_candidates: int = 0
    dynamic_top_k_enabled: bool = False
    dynamic_top_k_profile: str = "local"
    dynamic_top_k_minimum: int = 0
    dynamic_top_k_selected: int = 0
    dynamic_top_k_source_coverage: int = 0
    dynamic_top_k_budget_tokens: int = 0
    dynamic_top_k_estimated_tokens: int = 0
    dynamic_top_k_stop_reason: str = "not_run"
    dynamic_top_k_boundary_score_gap: float | None = None
    answerability_gate_enabled: bool = False
    answerability_reason: str = "not_run"
    answerability_matched_signals: int = 0
    governance_excluded_superseded: int = 0
    governance_excluded_future_effective: int = 0
    governance_expired_candidates: int = 0
    governance_conflicted_candidates: int = 0
    governance_trust_adjusted_candidates: int = 0
    query_rewrite_ms: float = 0.0
    query_variant_count: int = 1
    query_subquery_count: int = 0
    query_synonym_count: int = 0
    query_fanout_candidates: int = 0
    hybrid_retrieval_ms: float = 0.0
    rerank_ms: float = 0.0
    context_expanded: int = 0
    context_characters: int = 0
    context_expansion_ms: float = 0.0
    graph_mode: str = "local"
    graph_matched_entities: int = 0
    graph_expanded_entities: int = 0
    graph_candidates: int = 0
    graph_covered_documents: int = 0
    matched_communities: int = 0
    community_summary_candidates: int = 0
    community_expanded_chunks: int = 0
    community_covered_documents: int = 0
    total_ms: float = 0.0


class RetrievalService:
    def __init__(
        self, retriever: LocalHybridRetriever | PostgresHybridRetriever | None = None
    ) -> None:
        self.retriever = retriever
        self._retriever_name = retriever.name if retriever else "local_hybrid_rrf"
        self.embedding_cache_hit = False
        self.reranker_cache_hit = False
        self.reranker_fallback = False
        self.reranker_name: str | None = None
        self.cache_backend = "disabled"
        self.rewritten_query = ""
        self.query_rewriter = "rule"
        self.query_rewrite_cache_hit = False
        self.query_rewrite_fallback = False
        self.query_plan = QueryRewritePlan(
            original_query="",
            main_query="",
            sub_queries=(),
            synonyms=(),
            queries=(),
            provider="rule",
            cache_hit=False,
            fallback=False,
        )
        self.graph_stats = GraphRetrievalStats()
        self.entity_stats = EntityRetrievalStats()
        self.tag_stats = TagRetrievalStats()
        self.community_stats = CommunityRetriever().last_stats
        self.dynamic_top_k_stats = DynamicTopKStats(
            enabled=False,
            query_profile="local",
            requested_max_candidates=0,
            minimum_candidates=0,
            selected_candidates=0,
            source_coverage=0,
            token_budget=0,
            estimated_tokens=0,
            stop_reason="not_run",
        )
        self.diagnostics = RetrievalDiagnostics()
        self.governance_stats = GovernanceFilterStats()

    @property
    def retriever_name(self) -> str:
        """返回本次服务使用的检索策略名称，避免路由依赖可空实现对象。"""

        return self._retriever_name

    def search(
        self,
        session: Session,
        *,
        knowledge_base_id: str,
        query: str,
        limit: int,
        workspace_id: str | None = None,
        settings_override: Settings | None = None,
    ) -> list[Evidence]:
        started_at = perf_counter()
        self.reranker_name = None
        self.reranker_cache_hit = False
        self.reranker_fallback = False
        resolved_workspace_id = ensure_workspace(session, workspace_id=workspace_id).id
        with traced_span(
            "rag.retrieval.search",
            enabled=get_settings().telemetry_enabled,
            attributes={"rag.requested_limit": limit},
        ) as span:
            return self._search_with_trace(
                session,
                knowledge_base_id=knowledge_base_id,
                query=query,
                limit=limit,
                resolved_workspace_id=resolved_workspace_id,
                started_at=started_at,
                span=span,
                settings_override=settings_override,
            )

    def _search_with_trace(
        self,
        session: Session,
        *,
        knowledge_base_id: str,
        query: str,
        limit: int,
        resolved_workspace_id: str,
        started_at: float,
        span,
        settings_override: Settings | None,
    ) -> list[Evidence]:
        knowledge_base = KnowledgeService().get_knowledge_base(
            session,
            knowledge_base_id,
            workspace_id=resolved_workspace_id,
        )
        embedding_revision = ConfigurationService().embedding_revision(
            session, workspace_id=resolved_workspace_id
        )
        if (
            knowledge_base.index_status != "ready"
            or knowledge_base.embedding_revision != embedding_revision
        ):
            raise IndexRebuildRequiredError(
                details={
                    "knowledgeBaseId": knowledge_base.id,
                    "indexStatus": knowledge_base.index_status,
                    "expectedEmbeddingRevision": embedding_revision,
                    "indexedEmbeddingRevision": knowledge_base.embedding_revision,
                }
            )
        # 离线 A/B 只能使用内存中的策略快照，绝不能改写工作区持久化模型配置。
        resolved_settings = settings_override or ConfigurationService().resolve_settings(
            session, workspace_id=resolved_workspace_id
        )
        cache = build_cache(resolved_settings)
        rewrite_started_at = perf_counter()
        rewrite_plan = QueryRewriter(resolved_settings, cache=cache).plan(
            query=query, workspace_id=resolved_workspace_id
        )
        rewrite_ms = (perf_counter() - rewrite_started_at) * 1000
        self.query_plan = rewrite_plan
        self.rewritten_query = rewrite_plan.main_query
        self.query_rewriter = rewrite_plan.provider
        self.query_rewrite_cache_hit = rewrite_plan.cache_hit
        self.query_rewrite_fallback = rewrite_plan.fallback
        if self.retriever is not None:
            retriever = self.retriever
        elif resolved_settings.database_url.startswith(("postgresql", "postgres")):
            # 生产 PostgreSQL 路径使用原生 FTS/pgvector；本地 SQLite 保留确定性实现。
            retriever = PostgresHybridRetriever.from_settings(
                resolved_settings,
                build_embedding_provider(resolved_settings),
                cache=cache,
            )
        else:
            retriever = LocalHybridRetriever(
                build_embedding_provider(resolved_settings),
                cache=cache,
                cache_ttl_seconds=resolved_settings.cache_default_ttl_seconds,
                metadata_boost=MetadataBoostPolicy.from_settings(resolved_settings),
            )
        self._retriever_name = retriever.name
        candidate_limit = (
            min(max(limit * 3, 8), resolved_settings.reranker_candidate_limit)
            if resolved_settings.reranker_enabled
            else limit
        )
        retrieval_started_at = perf_counter()
        variant_results: list[list[Evidence]] = []
        variant_stats: list[RetrievalStageStats] = []
        for variant in rewrite_plan.queries:
            variant_results.append(
                retriever.retrieve(
                    session,
                    knowledge_base_id=knowledge_base_id,
                    workspace_id=resolved_workspace_id,
                    query=variant,
                    limit=candidate_limit,
                    embedding_revision=embedding_revision,
                )
            )
            variant_stats.append(getattr(retriever, "last_stage_stats", RetrievalStageStats()))
        evidences = fuse_query_evidence(
            variant_results,
            limit=candidate_limit,
            variant_weights=[1.0 if index < 2 else 0.85 for index in range(len(variant_results))],
        )
        retrieval_ms = (perf_counter() - retrieval_started_at) * 1000
        stage_stats = RetrievalStageStats(
            keyword_candidates=sum(item.keyword_candidates for item in variant_stats),
            semantic_candidates=sum(item.semantic_candidates for item in variant_stats),
            fused_candidates=len({item.locator for result in variant_results for item in result}),
            metadata_boosted_candidates=sum(
                item.metadata_boosted_candidates for item in variant_stats
            ),
        )
        # 实体路径不替代通用召回：它只补充已入库实体精确命中的原始切块。
        # 使用原问题和主改写分别召回，避免改写遗漏专有名词后损失定向命中。
        entity_candidates: list[Evidence] = []
        if resolved_settings.entity_retrieval_enabled:
            entity_retriever = EntityRetriever(
                max_entities=resolved_settings.entity_retrieval_max_entities,
                max_candidates=resolved_settings.entity_retrieval_candidate_limit,
            )
            entity_rankings: list[list[Evidence]] = []
            entity_stats_by_query: list[EntityRetrievalStats] = []
            entity_queries = (query,)
            if rewrite_plan.main_query.strip() != query.strip():
                entity_queries = (query, rewrite_plan.main_query)
            for entity_query in entity_queries:
                entity_rankings.append(
                    entity_retriever.retrieve(
                        session,
                        knowledge_base_id=knowledge_base_id,
                        workspace_id=resolved_workspace_id,
                        query=entity_query,
                        limit=candidate_limit,
                    )
                )
                entity_stats_by_query.append(entity_retriever.last_stats)
            entity_candidates = fuse_query_evidence(entity_rankings, limit=candidate_limit)
            matched_entity_ids = {
                entity_id
                for stats in entity_stats_by_query
                for entity_id in stats.matched_entity_ids
            }
            covered_documents = {
                item.locator.split(":")[1]
                for item in entity_candidates
                if item.source_type == "document_chunk" and len(item.locator.split(":")) >= 2
            }
            self.entity_stats = EntityRetrievalStats(
                matched_entity_ids=tuple(sorted(matched_entity_ids)),
                matched_entities=len(matched_entity_ids),
                candidates=len(entity_candidates),
                covered_documents=len(covered_documents),
            )
            # 两路先独立排序，再按 locator 去重进行 RRF；实体空命中时通用候选原样保留。
            evidences = fuse_query_evidence(
                [evidences, entity_candidates],
                limit=candidate_limit,
            )
        else:
            self.entity_stats = EntityRetrievalStats()
        dual_route_fused_candidates = len(evidences)
        # 受控标签路径只读取审批通过的关联；它不能当作过滤条件，始终与已有候选做 RRF 融合。
        tag_candidates: list[Evidence] = []
        if resolved_settings.tag_retrieval_enabled:
            tag_retriever = TagRetriever(
                max_tags=resolved_settings.tag_retrieval_max_tags,
                max_candidates=resolved_settings.tag_retrieval_candidate_limit,
            )
            tag_rankings: list[list[Evidence]] = []
            tag_stats_by_query: list[TagRetrievalStats] = []
            tag_queries = (query,)
            if rewrite_plan.main_query.strip() != query.strip():
                tag_queries = (query, rewrite_plan.main_query)
            for tag_query in tag_queries:
                tag_rankings.append(
                    tag_retriever.retrieve(
                        session,
                        knowledge_base_id=knowledge_base_id,
                        workspace_id=resolved_workspace_id,
                        query=tag_query,
                        limit=candidate_limit,
                    )
                )
                tag_stats_by_query.append(tag_retriever.last_stats)
            tag_candidates = fuse_query_evidence(tag_rankings, limit=candidate_limit)
            matched_tag_ids = {
                tag_id for stats in tag_stats_by_query for tag_id in stats.matched_tag_ids
            }
            self.tag_stats = TagRetrievalStats(
                matched_tag_ids=tuple(sorted(matched_tag_ids)),
                matched_tags=len(matched_tag_ids),
                candidates=len(tag_candidates),
                covered_assets=len({item.locator.split(":")[1] for item in tag_candidates}),
            )
            evidences = fuse_query_evidence([evidences, tag_candidates], limit=candidate_limit)
        else:
            self.tag_stats = TagRetrievalStats()
        tag_route_fused_candidates = len(evidences)
        graph_mode = classify_graph_mode(query)
        graph_retriever = GraphRetriever()
        graph_candidates: list[Evidence] = []
        community_retriever = CommunityRetriever()
        community_candidates: list[Evidence] = []
        if graph_mode != "local":
            # 图谱只作为关系/全局问题的候选扩展，普通事实问题保持低延迟局部检索。
            graph_candidates = graph_retriever.retrieve(
                session,
                knowledge_base_id=knowledge_base_id,
                workspace_id=resolved_workspace_id,
                query=query,
                limit=candidate_limit,
                mode=graph_mode,
            )
            evidences = fuse_graph_evidence(
                evidences,
                graph_candidates,
                limit=candidate_limit,
            )
            # 全局问题先匹配社区摘要，再展开到原始切块；关系问题仍以实体一跳为主。
            if graph_mode == "global":
                community_candidates = community_retriever.retrieve(
                    session,
                    knowledge_base_id=knowledge_base_id,
                    workspace_id=resolved_workspace_id,
                    query=query,
                    limit=candidate_limit,
                    mode=graph_mode,
                )
                evidences = fuse_graph_evidence(
                    evidences,
                    community_candidates,
                    limit=candidate_limit,
                )
        self.graph_stats = graph_retriever.last_stats
        self.community_stats = community_retriever.last_stats
        evidences, self.governance_stats = apply_document_governance(
            session,
            evidences=evidences,
            workspace_id=resolved_workspace_id,
        )
        rerank_candidates = len(evidences)
        rerank_ms = 0.0
        if resolved_settings.reranker_enabled and evidences:
            reranker = build_reranker(resolved_settings, cache=cache)
            rerank_started_at = perf_counter()
            evidences = reranker.rerank(
                query=rewrite_plan.main_query, candidates=evidences, limit=limit
            )
            rerank_ms = (perf_counter() - rerank_started_at) * 1000
            self.reranker_name = reranker.name
            self.reranker_cache_hit = reranker.cache_hit
            self.reranker_fallback = reranker.used_fallback
        evidences, self.dynamic_top_k_stats = DynamicTopKPolicy.from_settings(
            resolved_settings
        ).select(
            evidences,
            requested_max_candidates=limit,
            query_profile=graph_mode,
        )
        context_expanded = 0
        context_characters = 0
        context_expansion_ms = 0.0
        if resolved_settings.parent_child_enabled and evidences:
            context_started_at = perf_counter()
            evidences, context_stats = ParentChildContextExpander(
                window=resolved_settings.parent_child_window,
                max_characters=resolved_settings.parent_child_max_characters,
            ).expand(
                session,
                evidences,
                knowledge_base_id=knowledge_base_id,
                workspace_id=resolved_workspace_id,
            )
            context_expanded = context_stats.expanded_contexts
            context_characters = context_stats.expanded_characters
            context_expansion_ms = (perf_counter() - context_started_at) * 1000
        answerability = RetrievalAnswerabilityGate.decide(
            query=query,
            evidences=evidences,
            enabled=resolved_settings.answerability_gate_enabled,
            query_profile=graph_mode,
        )
        if not answerability.is_answerable:
            # 让生成层收到空证据并走既有拒答契约；不要保留无关引用快照。
            evidences = []
        self.embedding_cache_hit = retriever.embedding_cache_hit
        self.cache_backend = cache.name if cache is not None else "disabled"
        self.diagnostics = RetrievalDiagnostics(
            keyword_candidates=stage_stats.keyword_candidates,
            semantic_candidates=stage_stats.semantic_candidates,
            fused_candidates=stage_stats.fused_candidates,
            entity_retrieval_enabled=resolved_settings.entity_retrieval_enabled,
            entity_matched_entities=self.entity_stats.matched_entities,
            entity_candidates=self.entity_stats.candidates,
            entity_covered_documents=self.entity_stats.covered_documents,
            dual_route_fused_candidates=dual_route_fused_candidates,
            tag_retrieval_enabled=resolved_settings.tag_retrieval_enabled,
            tag_matched_tags=self.tag_stats.matched_tags,
            tag_candidates=self.tag_stats.candidates,
            tag_covered_assets=self.tag_stats.covered_assets,
            tag_route_fused_candidates=tag_route_fused_candidates,
            metadata_boosted_candidates=stage_stats.metadata_boosted_candidates,
            rerank_candidates=rerank_candidates,
            final_candidates=len(evidences),
            dynamic_top_k_enabled=self.dynamic_top_k_stats.enabled,
            dynamic_top_k_profile=self.dynamic_top_k_stats.query_profile,
            dynamic_top_k_minimum=self.dynamic_top_k_stats.minimum_candidates,
            dynamic_top_k_selected=self.dynamic_top_k_stats.selected_candidates,
            dynamic_top_k_source_coverage=self.dynamic_top_k_stats.source_coverage,
            dynamic_top_k_budget_tokens=self.dynamic_top_k_stats.token_budget,
            dynamic_top_k_estimated_tokens=self.dynamic_top_k_stats.estimated_tokens,
            dynamic_top_k_stop_reason=self.dynamic_top_k_stats.stop_reason,
            dynamic_top_k_boundary_score_gap=self.dynamic_top_k_stats.boundary_score_gap,
            answerability_gate_enabled=resolved_settings.answerability_gate_enabled,
            answerability_reason=answerability.reason,
            answerability_matched_signals=answerability.matched_signals,
            governance_excluded_superseded=self.governance_stats.excluded_superseded,
            governance_excluded_future_effective=self.governance_stats.excluded_future_effective,
            governance_expired_candidates=self.governance_stats.expired_candidates,
            governance_conflicted_candidates=self.governance_stats.conflicted_candidates,
            governance_trust_adjusted_candidates=self.governance_stats.trust_adjusted_candidates,
            query_rewrite_ms=round(rewrite_ms, 2),
            query_variant_count=rewrite_plan.variant_count,
            query_subquery_count=len(rewrite_plan.sub_queries),
            query_synonym_count=len(rewrite_plan.synonyms),
            query_fanout_candidates=sum(len(result) for result in variant_results),
            hybrid_retrieval_ms=round(retrieval_ms, 2),
            rerank_ms=round(rerank_ms, 2),
            context_expanded=context_expanded,
            context_characters=context_characters,
            context_expansion_ms=round(context_expansion_ms, 2),
            graph_mode=self.graph_stats.mode,
            graph_matched_entities=self.graph_stats.matched_entities,
            graph_expanded_entities=self.graph_stats.expanded_entities,
            graph_candidates=self.graph_stats.graph_candidates,
            graph_covered_documents=self.graph_stats.covered_documents,
            matched_communities=self.community_stats.matched_communities,
            community_summary_candidates=self.community_stats.summary_candidates,
            community_expanded_chunks=self.community_stats.expanded_chunks,
            community_covered_documents=self.community_stats.covered_documents,
            total_ms=round((perf_counter() - started_at) * 1000, 2),
        )
        set_safe_attribute(span, "rag.retrieval.final_candidates", len(evidences))
        set_safe_attribute(span, "rag.retrieval.query_variant_count", rewrite_plan.variant_count)
        set_safe_attribute(span, "rag.retrieval.cache_hit", self.embedding_cache_hit)
        set_safe_attribute(span, "rag.retrieval.total_ms", self.diagnostics.total_ms)
        return evidences
