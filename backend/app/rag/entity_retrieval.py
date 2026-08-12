"""实体定向召回。

该模块只使用入库阶段已生成的实体倒排索引定位原始 DocumentChunk，不把实体名称或
关系摘要当作回答证据。它与通用 Hybrid 召回独立运行，由调用方使用 RRF 融合，从而
在实体未命中或抽取质量不足时保留通用召回兜底。
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.knowledge.models import (
    ChunkEntityMention,
    Document,
    DocumentChunk,
    KnowledgeEntity,
)
from app.rag.graph import normalize_entity_name
from app.rag.retrieval import Evidence, tokenize


@dataclass(frozen=True, slots=True)
class EntityRetrievalStats:
    """实体定向路径的脱敏计数，用于诊断和离线质量评估。"""

    # 仅供同一次服务调用聚合多个 Query 变体，API 只暴露最终计数。
    matched_entity_ids: tuple[str, ...] = ()
    matched_entities: int = 0
    candidates: int = 0
    covered_documents: int = 0


class EntityRetriever:
    """以实体倒排索引召回原始切块，不进行图谱关系扩展。"""

    def __init__(self, *, max_entities: int = 12, max_candidates: int = 30) -> None:
        self.max_entities = max_entities
        self.max_candidates = max_candidates
        self.last_stats = EntityRetrievalStats()

    def retrieve(
        self,
        session: Session,
        *,
        knowledge_base_id: str,
        workspace_id: str,
        query: str,
        limit: int,
    ) -> list[Evidence]:
        """根据原始查询命中的实体定位切块，严格限定工作区和知识库边界。"""

        entities = list(
            session.scalars(
                select(KnowledgeEntity)
                .where(
                    KnowledgeEntity.workspace_id == workspace_id,
                    KnowledgeEntity.knowledge_base_id == knowledge_base_id,
                )
                .order_by(KnowledgeEntity.mention_count.desc(), KnowledgeEntity.name.asc())
                .limit(self.max_entities * 4)
            )
        )
        normalized_query = normalize_entity_name(query)
        query_tokens = tokenize(query)
        matched = [
            entity
            for entity in entities
            if entity.normalized_name in normalized_query or entity.normalized_name in query_tokens
        ][: self.max_entities]
        if not matched:
            self.last_stats = EntityRetrievalStats()
            return []

        matched_ids = {entity.id for entity in matched}
        rows = session.execute(
            select(ChunkEntityMention, DocumentChunk, Document)
            .join(DocumentChunk, DocumentChunk.id == ChunkEntityMention.document_chunk_id)
            .join(Document, Document.id == DocumentChunk.document_id)
            .where(
                ChunkEntityMention.workspace_id == workspace_id,
                ChunkEntityMention.entity_id.in_(matched_ids),
                DocumentChunk.workspace_id == workspace_id,
                Document.workspace_id == workspace_id,
                Document.knowledge_base_id == knowledge_base_id,
                Document.status == "indexed",
            )
        )

        # 同一切块可命中多个实体，保留其最高实体置信分，避免重复占用候选预算。
        grouped: dict[str, tuple[float, DocumentChunk, Document]] = {}
        for mention, chunk, document in rows:
            score = 2.0 + min(mention.mention_count, 4) * 0.1
            current = grouped.get(chunk.id)
            if current is None or score > current[0]:
                grouped[chunk.id] = (score, chunk, document)

        selected = sorted(
            grouped.values(), key=lambda item: (-item[0], item[1].ordinal, item[1].id)
        )[: min(self.max_candidates, max(limit, limit * 3))]
        evidences = [
            Evidence(
                source_type="document_chunk",
                source_id=chunk.id,
                title=document.title,
                content=chunk.content,
                score=score,
                locator=f"document:{document.id}:chunk:{chunk.ordinal}",
                source_url=document.source_url,
                source_validation_state=document.source_validation_state,
                source_is_approved=document.source_is_approved,
            )
            for score, chunk, document in selected
        ]
        self.last_stats = EntityRetrievalStats(
            matched_entity_ids=tuple(sorted(matched_ids)),
            matched_entities=len(matched_ids),
            candidates=len(evidences),
            covered_documents=len({document.id for _, _, document in selected}),
        )
        return evidences
