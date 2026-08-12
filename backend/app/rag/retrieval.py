"""证据检索接口与本地开发实现。"""

import re
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.domain.knowledge.models import Document, DocumentChunk, Note
from app.domain.knowledge.repositories import ChunkEmbeddingRepository, NoteEmbeddingRepository
from app.extensions.contracts import EmbeddingProvider
from app.rag.cache import CacheBackend, stable_cache_key
from app.rag.embeddings import build_embedding_provider, cosine_similarity
from app.rag.metadata_boost import MetadataBoostPolicy

TOKEN_PATTERN = re.compile(r"[\u4e00-\u9fff]|[a-zA-Z0-9_]{2,}")


@dataclass(frozen=True, slots=True)
class Evidence:
    """回答系统唯一允许引用的证据单元。"""

    source_type: str
    source_id: str
    title: str
    content: str
    score: float
    locator: str
    source_url: str | None = None
    # 外部来源状态与正文证据解耦：链接失效时仍可引用已存档正文，但 UI 不会将其伪装成可访问链接。
    source_validation_state: str = "not_applicable"
    source_is_approved: bool = False
    source_trust_level: str = "standard"
    governance_availability: str = "available"
    conflict_state: str = "none"


@dataclass(frozen=True, slots=True)
class RetrievalStageStats:
    """混合召回各阶段的公开统计，不包含正文或敏感配置。"""

    keyword_candidates: int = 0
    semantic_candidates: int = 0
    fused_candidates: int = 0
    metadata_boosted_candidates: int = 0


def tokenize(text: str) -> set[str]:
    return {token.lower() for token in TOKEN_PATTERN.findall(text)}


def fuse_query_evidence(
    rankings: list[list[Evidence]], *, limit: int, variant_weights: list[float] | None = None
) -> list[Evidence]:
    """融合多路 Query 的候选。

    主查询权重最高，原始问题和子查询保持较高权重，同义词作为补充；所有候选仍按
    locator 去重，避免同一切块因多个改写重复占用上下文窗口。
    """

    evidence_by_locator: dict[str, Evidence] = {}
    scores: dict[str, float] = {}
    weights = variant_weights or [1.0] * len(rankings)
    for variant_index, ranking in enumerate(rankings):
        weight = weights[variant_index] if variant_index < len(weights) else 1.0
        for rank, evidence in enumerate(ranking, start=1):
            evidence_by_locator.setdefault(evidence.locator, evidence)
            scores[evidence.locator] = scores.get(evidence.locator, 0.0) + weight / (60 + rank)
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
            source_trust_level=evidence_by_locator[locator].source_trust_level,
            governance_availability=evidence_by_locator[locator].governance_availability,
            conflict_state=evidence_by_locator[locator].conflict_state,
        )
        for locator, score in sorted(scores.items(), key=lambda item: (-item[1], item[0]))[:limit]
    ]


class LocalKeywordRetriever:
    """无外部依赖的开发检索器。

    它不试图模拟生产向量效果，只提供确定性、可测试的基线；部署时由
    PgHybridRetriever 实现同一输出模型，避免调用层修改。
    """

    name = "local_keyword"

    def __init__(self, metadata_boost: MetadataBoostPolicy | None = None) -> None:
        self.metadata_boost = metadata_boost or MetadataBoostPolicy()
        self.last_boosted_count = 0

    def retrieve(
        self,
        session: Session,
        *,
        knowledge_base_id: str,
        workspace_id: str,
        query: str,
        limit: int,
    ) -> list[Evidence]:
        self.last_boosted_count = 0
        query_tokens = tokenize(query)
        if not query_tokens:
            return []

        candidates: list[Evidence] = []
        notes = session.scalars(
            select(Note).where(
                Note.knowledge_base_id == knowledge_base_id,
                Note.workspace_id == workspace_id,
                Note.status == "active",
            )
        )
        for note in notes:
            base_score = self._score(query_tokens, f"{note.title}\n{note.content}")
            if base_score <= 0:
                continue
            score, boosted = self._score_with_metadata(
                query_tokens=query_tokens,
                base_score=base_score,
                title=note.title,
                source_type="note",
            )
            self.last_boosted_count += int(boosted)
            candidates.append(
                Evidence(
                    source_type="note",
                    source_id=note.id,
                    title=note.title,
                    content=note.content,
                    score=score,
                    locator=f"note:{note.id}",
                )
            )

        rows = session.execute(
            select(DocumentChunk, Document)
            .join(Document, Document.id == DocumentChunk.document_id)
            .where(
                Document.knowledge_base_id == knowledge_base_id,
                Document.workspace_id == workspace_id,
                Document.status == "indexed",
                DocumentChunk.workspace_id == workspace_id,
            )
        )
        for chunk, document in rows:
            base_score = self._score(query_tokens, chunk.content)
            if base_score <= 0:
                continue
            score, boosted = self._score_with_metadata(
                query_tokens=query_tokens,
                base_score=base_score,
                title=document.title,
                metadata=chunk.metadata_json,
                source_type=document.source_type,
            )
            self.last_boosted_count += int(boosted)
            candidates.append(
                Evidence(
                    source_type="document_chunk",
                    source_id=chunk.id,
                    title=document.title,
                    content=chunk.content,
                    score=score,
                    locator=f"document:{chunk.document_id}:chunk:{chunk.ordinal}",
                    source_url=document.source_url,
                    source_validation_state=document.source_validation_state,
                    source_is_approved=document.source_is_approved,
                )
            )
        return sorted(candidates, key=lambda item: (-item.score, item.locator))[:limit]

    def _score_with_metadata(
        self,
        *,
        query_tokens: set[str],
        base_score: float,
        title: str,
        metadata: dict[str, str] | None = None,
        source_type: str = "",
    ) -> tuple[float, bool]:
        return self.metadata_boost.adjust(
            base_score,
            query_tokens=query_tokens,
            title=title,
            metadata=metadata,
            source_type=source_type,
        )

    @staticmethod
    def _score(query_tokens: set[str], content: str) -> float:
        content_tokens = tokenize(content)
        if not content_tokens:
            return 0.0
        overlap = len(query_tokens & content_tokens)
        # 用查询覆盖率归一化，避免长文仅因包含更多无关词获得不合理优势。
        return overlap / len(query_tokens)


class LocalHybridRetriever:
    """本地开发的向量 + 关键词混合召回。

    使用 RRF 融合两个独立排序，避免直接相加不同量纲的相似度。生产 PostgreSQL
    部署会由 pgvector/FTS 检索器替换，但调用方仍依赖同一 Evidence 输出。
    """

    name = "local_hybrid_rrf"

    def __init__(
        self,
        embedding_provider: EmbeddingProvider | None = None,
        *,
        cache: CacheBackend | None = None,
        cache_ttl_seconds: int | None = None,
        metadata_boost: MetadataBoostPolicy | None = None,
    ) -> None:
        self.metadata_boost = metadata_boost or MetadataBoostPolicy()
        self.keyword_retriever = LocalKeywordRetriever(self.metadata_boost)
        self.embedding_provider = embedding_provider or build_embedding_provider(get_settings())
        self.embedding_repository = ChunkEmbeddingRepository()
        self.note_embedding_repository = NoteEmbeddingRepository()
        self.cache = cache
        self.cache_ttl_seconds = cache_ttl_seconds or get_settings().cache_default_ttl_seconds
        self.embedding_cache_hit = False
        self.last_stage_stats = RetrievalStageStats()
        self._last_semantic_boosted_count = 0

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
        lexical = self.keyword_retriever.retrieve(
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
        fused_locator_count = len({item.locator for item in (*lexical, *semantic)})
        self.last_stage_stats = RetrievalStageStats(
            keyword_candidates=len(lexical),
            semantic_candidates=len(semantic),
            # 记录截断前的唯一候选数，才能判断 RRF 是否真正扩大了候选覆盖。
            fused_candidates=fused_locator_count,
            metadata_boosted_candidates=(
                self.keyword_retriever.last_boosted_count + self._last_semantic_boosted_count
            ),
        )
        return fused

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
        self._last_semantic_boosted_count = 0
        query_vector = self._embed_query(
            query=query,
            workspace_id=workspace_id,
            embedding_revision=embedding_revision,
        )
        query_tokens = tokenize(query)
        candidates: list[Evidence] = []
        for embedding, chunk, document in self.embedding_repository.list_by_knowledge_base(
            session,
            knowledge_base_id=knowledge_base_id,
            workspace_id=workspace_id,
            embedding_revision=embedding_revision,
        ):
            if embedding.dimensions != len(query_vector):
                continue
            score = cosine_similarity(query_vector, embedding.embedding)
            if score <= 0:
                continue
            score, boosted = self.metadata_boost.adjust(
                score,
                query_tokens=query_tokens,
                title=document.title,
                metadata=chunk.metadata_json,
                source_type=document.source_type,
            )
            self._last_semantic_boosted_count += int(boosted)
            candidates.append(
                Evidence(
                    source_type="document_chunk",
                    source_id=chunk.id,
                    title=document.title,
                    content=chunk.content,
                    score=score,
                    locator=f"document:{chunk.document_id}:chunk:{chunk.ordinal}",
                    source_url=document.source_url,
                    source_validation_state=document.source_validation_state,
                    source_is_approved=document.source_is_approved,
                )
            )
        for embedding, note in self.note_embedding_repository.list_by_knowledge_base(
            session,
            knowledge_base_id=knowledge_base_id,
            workspace_id=workspace_id,
            embedding_revision=embedding_revision,
        ):
            if embedding.dimensions != len(query_vector):
                continue
            score = cosine_similarity(query_vector, embedding.embedding)
            if score <= 0:
                continue
            score, boosted = self.metadata_boost.adjust(
                score,
                query_tokens=query_tokens,
                title=note.title,
                source_type="note",
            )
            self._last_semantic_boosted_count += int(boosted)
            candidates.append(
                Evidence(
                    source_type="note",
                    source_id=note.id,
                    title=note.title,
                    content=note.content,
                    score=score,
                    locator=f"note:{note.id}",
                )
            )
        return sorted(candidates, key=lambda item: (-item.score, item.locator))[:limit]

    def _embed_query(
        self, *, query: str, workspace_id: str, embedding_revision: int
    ) -> list[float]:
        """缓存查询向量，避免相同问题重复消耗 Embedding API 配额。"""

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
                source_trust_level=evidence_by_locator[locator].source_trust_level,
                governance_availability=evidence_by_locator[locator].governance_availability,
                conflict_state=evidence_by_locator[locator].conflict_state,
            )
            for locator, score in sorted(scores.items(), key=lambda item: (-item[1], item[0]))[
                :limit
            ]
        ]
