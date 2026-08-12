"""受控业务标签定向召回。

标签不是自由文本过滤器：本模块只读取已审批的 ``KnowledgeTagAssignment``，并把匹配
标签关联的原始文档切块或笔记作为候选证据。它不直接生成答案，也不取代通用 Hybrid
召回；调用方必须使用 RRF 将两条路径融合，保证标签未命中时仍可正常召回。
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.application.tag_governance_service import normalize_tag_name
from app.domain.knowledge.models import (
    Document,
    DocumentChunk,
    KnowledgeTag,
    KnowledgeTagAssignment,
    Note,
)
from app.rag.retrieval import Evidence, tokenize


@dataclass(frozen=True, slots=True)
class TagRetrievalStats:
    """受控标签召回的脱敏统计，用于诊断和离线评测。"""

    matched_tag_ids: tuple[str, ...] = ()
    matched_tags: int = 0
    candidates: int = 0
    covered_assets: int = 0


class TagRetriever:
    """以已批准的业务标签精确定位原始知识资产。"""

    def __init__(self, *, max_tags: int = 12, max_candidates: int = 30) -> None:
        self.max_tags = max_tags
        self.max_candidates = max_candidates
        self.last_stats = TagRetrievalStats()

    def retrieve(
        self,
        session: Session,
        *,
        knowledge_base_id: str,
        workspace_id: str,
        query: str,
        limit: int,
    ) -> list[Evidence]:
        """根据 Query 中出现的受控标签读取已审核关联，严格限制租户边界。"""

        normalized_query = normalize_tag_name(query)
        query_tokens = tokenize(query)
        tags = list(
            session.scalars(
                select(KnowledgeTag)
                .where(
                    KnowledgeTag.workspace_id == workspace_id,
                    KnowledgeTag.knowledge_base_id == knowledge_base_id,
                    KnowledgeTag.state == "active",
                )
                .order_by(KnowledgeTag.name.asc())
                .limit(self.max_tags * 4)
            )
        )
        matched_tags = [
            tag
            for tag in tags
            if tag.normalized_name in normalized_query or tag.normalized_name in query_tokens
        ][: self.max_tags]
        if not matched_tags:
            self.last_stats = TagRetrievalStats()
            return []

        assignments = list(
            session.scalars(
                select(KnowledgeTagAssignment).where(
                    KnowledgeTagAssignment.workspace_id == workspace_id,
                    KnowledgeTagAssignment.knowledge_base_id == knowledge_base_id,
                    KnowledgeTagAssignment.tag_id.in_([tag.id for tag in matched_tags]),
                    KnowledgeTagAssignment.state == "approved",
                )
            )
        )
        if not assignments:
            self.last_stats = TagRetrievalStats(
                matched_tag_ids=tuple(tag.id for tag in matched_tags),
                matched_tags=len(matched_tags),
            )
            return []

        document_ids = {
            assignment.asset_id for assignment in assignments if assignment.asset_type == "document"
        }
        note_ids = {
            assignment.asset_id for assignment in assignments if assignment.asset_type == "note"
        }
        evidences = self._document_evidences(
            session,
            document_ids=document_ids,
            knowledge_base_id=knowledge_base_id,
            workspace_id=workspace_id,
        )
        evidences.extend(
            self._note_evidences(
                session,
                note_ids=note_ids,
                knowledge_base_id=knowledge_base_id,
                workspace_id=workspace_id,
            )
        )
        selected = self._select_balanced(
            evidences,
            candidate_limit=min(self.max_candidates, max(limit, limit * 3)),
        )
        self.last_stats = TagRetrievalStats(
            matched_tag_ids=tuple(tag.id for tag in matched_tags),
            matched_tags=len(matched_tags),
            candidates=len(selected),
            covered_assets=len({item.locator.split(":")[1] for item in selected}),
        )
        return selected

    @staticmethod
    def _select_balanced(evidences: list[Evidence], *, candidate_limit: int) -> list[Evidence]:
        """优先覆盖不同标签资产，避免长文所有切块挤占定向路径预算。"""

        buckets: dict[str, list[Evidence]] = {}
        for evidence in sorted(evidences, key=lambda item: (item.locator, item.title)):
            asset_id = evidence.locator.split(":")[1]
            buckets.setdefault(asset_id, []).append(evidence)
        selected: list[Evidence] = []
        depth = 0
        while len(selected) < candidate_limit:
            added = False
            for asset_id in sorted(buckets):
                if depth < len(buckets[asset_id]):
                    selected.append(buckets[asset_id][depth])
                    added = True
                    if len(selected) == candidate_limit:
                        break
            if not added:
                break
            depth += 1
        return selected

    @staticmethod
    def _document_evidences(
        session: Session,
        *,
        document_ids: set[str],
        knowledge_base_id: str,
        workspace_id: str,
    ) -> list[Evidence]:
        if not document_ids:
            return []
        rows = session.execute(
            select(DocumentChunk, Document)
            .join(Document, Document.id == DocumentChunk.document_id)
            .where(
                DocumentChunk.workspace_id == workspace_id,
                Document.workspace_id == workspace_id,
                Document.knowledge_base_id == knowledge_base_id,
                Document.id.in_(document_ids),
                Document.status == "indexed",
            )
            .order_by(Document.id.asc(), DocumentChunk.ordinal.asc())
        )
        return [
            Evidence(
                source_type="document_chunk",
                source_id=chunk.id,
                title=document.title,
                content=chunk.content,
                score=1.0,
                locator=f"document:{document.id}:chunk:{chunk.ordinal}",
                source_url=document.source_url,
                source_validation_state=document.source_validation_state,
                source_is_approved=document.source_is_approved,
            )
            for chunk, document in rows
        ]

    @staticmethod
    def _note_evidences(
        session: Session,
        *,
        note_ids: set[str],
        knowledge_base_id: str,
        workspace_id: str,
    ) -> list[Evidence]:
        if not note_ids:
            return []
        notes = session.scalars(
            select(Note)
            .where(
                Note.workspace_id == workspace_id,
                Note.knowledge_base_id == knowledge_base_id,
                Note.id.in_(note_ids),
                Note.status == "active",
            )
            .order_by(Note.title.asc(), Note.id.asc())
        )
        return [
            Evidence(
                source_type="note",
                source_id=note.id,
                title=note.title,
                content=note.content,
                score=1.0,
                locator=f"note:{note.id}",
            )
            for note in notes
        ]
