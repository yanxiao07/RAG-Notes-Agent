"""PostgreSQL + pgvector + FTS 的生产检索实现。

该模块只依赖 ``Evidence``、缓存和 Embedding 契约，应用服务无需知道 SQL 方言。
PostgreSQL 路径使用 FTS 与 pgvector 分别召回，再复用同一 RRF 和 Metadata Boost
策略；任何部署没有 PostgreSQL 时，工厂仍会选择本地确定性实现。
"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.extensions.contracts import EmbeddingProvider
from app.rag.cache import CacheBackend, stable_cache_key
from app.rag.metadata_boost import MetadataBoostPolicy
from app.rag.retrieval import Evidence, RetrievalStageStats, tokenize


class PostgresHybridRetriever:
    """使用 PostgreSQL FTS、pgvector HNSW 和 RRF 的生产检索器。"""

    name = "postgres_pgvector_fts_rrf"

    def __init__(
        self,
        embedding_provider: EmbeddingProvider,
        *,
        cache: CacheBackend | None = None,
        cache_ttl_seconds: int = 900,
        metadata_boost: MetadataBoostPolicy | None = None,
    ) -> None:
        self.embedding_provider = embedding_provider
        self.cache = cache
        self.cache_ttl_seconds = cache_ttl_seconds
        self.metadata_boost = metadata_boost or MetadataBoostPolicy()
        self.embedding_cache_hit = False
        self.last_stage_stats = RetrievalStageStats()
        self._last_semantic_boosted_count = 0

    @classmethod
    def from_settings(
        cls,
        settings: Settings,
        embedding_provider: EmbeddingProvider,
        *,
        cache: CacheBackend | None,
    ) -> PostgresHybridRetriever:
        """根据配置装配生产检索器，集中管理运行时策略依赖。"""

        return cls(
            embedding_provider,
            cache=cache,
            cache_ttl_seconds=settings.cache_default_ttl_seconds,
            metadata_boost=MetadataBoostPolicy.from_settings(settings),
        )

    def retrieve(
        self,
        session: Session,
        *,
        knowledge_base_id: str,
        workspace_id: str,
        query: str,
        limit: int,
        embedding_revision: int = 1,
    ) -> list[Evidence]:
        lexical = self._keyword_retrieve(
            session,
            knowledge_base_id=knowledge_base_id,
            workspace_id=workspace_id,
            query=query,
            limit=limit * 3,
        )
        semantic = self._semantic_retrieve(
            session,
            knowledge_base_id=knowledge_base_id,
            workspace_id=workspace_id,
            query=query,
            limit=limit * 3,
            embedding_revision=embedding_revision,
        )
        fused = self._rrf_fuse(lexical, semantic, limit=limit)
        self.last_stage_stats = RetrievalStageStats(
            keyword_candidates=len(lexical),
            semantic_candidates=len(semantic),
            fused_candidates=len({item.locator for item in (*lexical, *semantic)}),
            metadata_boosted_candidates=(
                self._last_lexical_boosted_count + self._last_semantic_boosted_count
            ),
        )
        return fused

    def _keyword_retrieve(
        self,
        session: Session,
        *,
        knowledge_base_id: str,
        workspace_id: str,
        query: str,
        limit: int,
    ) -> list[Evidence]:
        """通过 FTS 召回正文，标题和章节加权交给统一 Metadata 策略。"""

        statement = text(
            """
            SELECT source_type, source_id, title, content, locator, score, source_url,
                   source_validation_state, source_is_approved, metadata_json
            FROM (
                SELECT
                    'document_chunk' AS source_type,
                    dc.id AS source_id,
                    d.title AS title,
                    dc.content AS content,
                    CONCAT('document:', dc.document_id, ':chunk:', dc.ordinal) AS locator,
                    ts_rank_cd(
                        to_tsvector('simple', COALESCE(dc.content, '')),
                        websearch_to_tsquery('simple', :query)
                    ) AS score,
                    d.source_url AS source_url,
                    d.source_validation_state AS source_validation_state,
                    d.source_is_approved AS source_is_approved,
                    dc.metadata_json AS metadata_json
                FROM document_chunks dc
                JOIN documents d ON d.id = dc.document_id
                WHERE dc.workspace_id = :workspace_id
                  AND d.workspace_id = :workspace_id
                  AND d.knowledge_base_id = :knowledge_base_id
                  AND d.status = 'indexed'
                  AND to_tsvector('simple', COALESCE(dc.content, ''))
                      @@ websearch_to_tsquery('simple', :query)
                UNION ALL
                SELECT
                    'note' AS source_type,
                    n.id AS source_id,
                    n.title AS title,
                    n.content AS content,
                    CONCAT('note:', n.id) AS locator,
                    ts_rank_cd(
                        to_tsvector('simple', COALESCE(n.content, '')),
                        websearch_to_tsquery('simple', :query)
                    ) AS score,
                    NULL::text AS source_url,
                    'not_applicable'::text AS source_validation_state,
                    false AS source_is_approved,
                    '{}'::json AS metadata_json
                FROM notes n
                WHERE n.workspace_id = :workspace_id
                  AND n.knowledge_base_id = :knowledge_base_id
                  AND n.status = 'active'
                  AND to_tsvector('simple', COALESCE(n.content, ''))
                      @@ websearch_to_tsquery('simple', :query)
            ) candidates
            ORDER BY score DESC, locator ASC
            LIMIT :limit
            """
        )
        rows = session.execute(
            statement,
            {
                "workspace_id": workspace_id,
                "knowledge_base_id": knowledge_base_id,
                "query": query,
                "limit": limit,
            },
        ).mappings()
        query_tokens = tokenize(query)
        self._last_lexical_boosted_count = 0
        evidences: list[Evidence] = []
        for row in rows:
            base_score = _normalize_fts_score(row["score"])
            metadata = _metadata(row["metadata_json"])
            score, boosted = self.metadata_boost.adjust(
                base_score,
                query_tokens=query_tokens,
                title=str(row["title"] or ""),
                metadata=metadata,
                source_type=str(row["source_type"] or ""),
            )
            self._last_lexical_boosted_count += int(boosted)
            evidences.append(
                Evidence(
                    source_type=str(row["source_type"]),
                    source_id=str(row["source_id"]),
                    title=str(row["title"] or ""),
                    content=str(row["content"] or ""),
                    score=score,
                    locator=str(row["locator"]),
                    source_url=str(row["source_url"]) if row["source_url"] else None,
                    source_validation_state=str(row["source_validation_state"]),
                    source_is_approved=bool(row["source_is_approved"]),
                )
            )
        return evidences

    def _semantic_retrieve(
        self,
        session: Session,
        *,
        knowledge_base_id: str,
        workspace_id: str,
        query: str,
        limit: int,
        embedding_revision: int,
    ) -> list[Evidence]:
        """通过 pgvector 余弦距离召回，向量列由迁移和 Embedding 双写维护。"""

        self._last_semantic_boosted_count = 0
        query_vector = self._embed_query(
            query=query,
            workspace_id=workspace_id,
            embedding_revision=embedding_revision,
        )
        vector_dimension = len(query_vector)
        # 维度来自当前 Embedding Provider 的实际输出，仅允许内部整数进入 SQL 模板。
        vector_distance = (
            f"embedding_vector::vector({vector_dimension}) <=> "
            f"CAST(:query_vector AS vector({vector_dimension}))"
        )
        statement = text(
            f"""
            SELECT source_type, source_id, title, content, locator, score, source_url,
                   source_validation_state, source_is_approved, metadata_json
            FROM (
                SELECT
                    'document_chunk' AS source_type,
                    dc.id AS source_id,
                    d.title AS title,
                    dc.content AS content,
                    CONCAT('document:', dc.document_id, ':chunk:', dc.ordinal) AS locator,
                    1 - ({vector_distance}) AS score,
                    d.source_url AS source_url,
                    d.source_validation_state AS source_validation_state,
                    d.source_is_approved AS source_is_approved,
                    dc.metadata_json AS metadata_json
                FROM chunk_embeddings ce
                JOIN document_chunks dc ON dc.id = ce.document_chunk_id
                JOIN documents d ON d.id = dc.document_id
                WHERE ce.workspace_id = :workspace_id
                  AND dc.workspace_id = :workspace_id
                  AND d.workspace_id = :workspace_id
                  AND d.knowledge_base_id = :knowledge_base_id
                  AND d.status = 'indexed'
                  AND ce.embedding_revision = :embedding_revision
                  AND ce.dimensions = :dimensions
                  AND ce.embedding_vector IS NOT NULL
                UNION ALL
                SELECT
                    'note' AS source_type,
                    n.id AS source_id,
                    n.title AS title,
                    n.content AS content,
                    CONCAT('note:', n.id) AS locator,
                    1 - ({vector_distance}) AS score,
                    NULL::text AS source_url,
                    'not_applicable'::text AS source_validation_state,
                    false AS source_is_approved,
                    '{{}}'::json AS metadata_json
                FROM note_embeddings ne
                JOIN notes n ON n.id = ne.note_id
                WHERE ne.workspace_id = :workspace_id
                  AND n.workspace_id = :workspace_id
                  AND n.knowledge_base_id = :knowledge_base_id
                  AND n.status = 'active'
                  AND ne.embedding_revision = :embedding_revision
                  AND ne.dimensions = :dimensions
                  AND ne.embedding_vector IS NOT NULL
            ) candidates
            ORDER BY score DESC, locator ASC
            LIMIT :limit
            """
        )
        rows = session.execute(
            statement,
            {
                "workspace_id": workspace_id,
                "knowledge_base_id": knowledge_base_id,
                "query_vector": _format_vector(query_vector),
                "embedding_revision": embedding_revision,
                "dimensions": len(query_vector),
                "limit": limit,
            },
        ).mappings()
        query_tokens = tokenize(query)
        evidences: list[Evidence] = []
        for row in rows:
            base_score = min(max(float(row["score"] or 0.0), 0.0), 1.0)
            # 元数据不能把没有语义相似度的候选凭空加入结果集。
            if base_score <= 0:
                continue
            metadata = _metadata(row["metadata_json"])
            score, boosted = self.metadata_boost.adjust(
                base_score,
                query_tokens=query_tokens,
                title=str(row["title"] or ""),
                metadata=metadata,
                source_type=str(row["source_type"] or ""),
            )
            self._last_semantic_boosted_count += int(boosted)
            evidences.append(
                Evidence(
                    source_type=str(row["source_type"]),
                    source_id=str(row["source_id"]),
                    title=str(row["title"] or ""),
                    content=str(row["content"] or ""),
                    score=score,
                    locator=str(row["locator"]),
                    source_url=str(row["source_url"]) if row["source_url"] else None,
                    source_validation_state=str(row["source_validation_state"]),
                    source_is_approved=bool(row["source_is_approved"]),
                )
            )
        return evidences

    def _embed_query(
        self, *, query: str, workspace_id: str, embedding_revision: int
    ) -> list[float]:
        """共享查询向量缓存语义，缓存异常不能阻断真实检索。"""

        if self.cache is None:
            self.embedding_cache_hit = False
            return self.embedding_provider.embed_query(query)
        cache_key = stable_cache_key(
            "query_embedding",
            workspace_id,
            self.embedding_provider.name,
            self.embedding_provider.model_name,
            str(embedding_revision),
            query.strip().lower(),
        )
        cached = self.cache.get_json(cache_key)
        if isinstance(cached, list) and all(isinstance(value, (float, int)) for value in cached):
            self.embedding_cache_hit = True
            return [float(value) for value in cached]
        vector = self.embedding_provider.embed_query(query)
        self.embedding_cache_hit = False
        self.cache.set_json(cache_key, vector, ttl_seconds=self.cache_ttl_seconds)
        return vector

    @staticmethod
    def _rrf_fuse(
        lexical: list[Evidence], semantic: list[Evidence], *, limit: int
    ) -> list[Evidence]:
        evidence_by_locator: dict[str, Evidence] = {}
        scores: dict[str, float] = {}
        for ranking in (lexical, semantic):
            for rank, evidence in enumerate(ranking, start=1):
                evidence_by_locator.setdefault(evidence.locator, evidence)
                scores[evidence.locator] = scores.get(evidence.locator, 0.0) + 1 / (60 + rank)
        if not scores:
            return []
        maximum = max(scores.values())
        return [
            Evidence(
                source_type=evidence_by_locator[locator].source_type,
                source_id=evidence_by_locator[locator].source_id,
                title=evidence_by_locator[locator].title,
                content=evidence_by_locator[locator].content,
                score=score / maximum,
                locator=locator,
                source_url=evidence_by_locator[locator].source_url,
                source_validation_state=evidence_by_locator[locator].source_validation_state,
                source_is_approved=evidence_by_locator[locator].source_is_approved,
            )
            for locator, score in sorted(scores.items(), key=lambda item: (-item[1], item[0]))[
                :limit
            ]
        ]


def _normalize_fts_score(value: Any) -> float:
    """将 PostgreSQL ts_rank_cd 压缩到和向量分数一致的 0..1 区间。"""

    score = max(float(value or 0.0), 0.0)
    return score / (1.0 + score)


def _metadata(value: Any) -> dict[str, str]:
    """兼容 psycopg 返回 dict 或 JSON 字符串的两种结果形态。"""

    if isinstance(value, dict):
        return {str(key): str(item) for key, item in value.items()}
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {}
        if isinstance(parsed, dict):
            return {str(key): str(item) for key, item in parsed.items()}
    return {}


def _format_vector(vector: list[float]) -> str:
    """使用 pgvector 文本格式绑定查询向量，避免把供应商对象传入 SQL。"""

    return "[" + ",".join(f"{value:.12g}" for value in vector) + "]"
