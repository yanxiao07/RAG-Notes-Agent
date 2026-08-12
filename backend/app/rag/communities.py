"""GraphRAG 社区发现、摘要和社区级召回。

社区摘要的职责是把跨文档的实体关系压缩成可检索的导航层，而不是替代原始证据。
因此本模块写入数据库的每条摘要都会保存实体 ID 与切块 ID；问答链路最终仍回到
DocumentChunk，避免模型生成内容成为没有出处的“事实”。
"""

from __future__ import annotations

import hashlib
import re
from collections import defaultdict
from dataclasses import dataclass
from typing import TYPE_CHECKING

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.domain.knowledge.models import (
    ChunkEntityMention,
    Document,
    DocumentChunk,
    KnowledgeBase,
    KnowledgeCommunitySummary,
    KnowledgeEntity,
    KnowledgeRelation,
)
from app.rag.graph import normalize_entity_name

if TYPE_CHECKING:
    from app.rag.retrieval import Evidence


@dataclass(frozen=True, slots=True)
class CommunityBuildStats:
    """一次社区索引构建的可审计统计，不包含正文和密钥。"""

    document_count: int = 0
    entity_count: int = 0
    relation_count: int = 0
    community_count: int = 0
    graph_revision: int = 0
    extractor_provider: str = "rule"
    summary_provider: str = "deterministic-community-summary"
    summary_fallback: int = 0


@dataclass(frozen=True, slots=True)
class CommunityDraft:
    level: int
    key: str
    title: str
    member_entity_ids: tuple[str, ...]
    source_chunk_ids: tuple[str, ...]
    summary: str


class CommunitySummaryGenerator:
    """可插拔的社区摘要生成契约。

    生产环境可以传入 LLM 适配器；未配置时服务使用确定性摘要，保证离线重建可重复。
    """

    name = "deterministic-community-summary"

    def generate(self, *, title: str, chunks: list[str]) -> str:
        del title, chunks
        raise NotImplementedError


class DeterministicCommunitySummaryGenerator(CommunitySummaryGenerator):
    """不调用外部模型的稳定回退摘要器。"""

    name = "deterministic-community-summary"

    def generate(self, *, title: str, chunks: list[str]) -> str:
        compact_chunks = [re.sub(r"\s+", " ", item).strip() for item in chunks if item.strip()]
        excerpts = [item[:240].rstrip() for item in compact_chunks[:4]]
        if not excerpts:
            return f"社区主题：{title}。当前没有可展示的原始切块。"
        return f"社区主题：{title}。相关原文摘要：" + "；".join(excerpts)


class LLMCommunitySummaryGenerator(CommunitySummaryGenerator):
    """把现有问答 LLM Provider 适配为社区摘要器。

    Provider 只接收有限数量的原始切块，生成失败由上层捕获并回退确定性摘要，
    不会因为某个社区的模型超时导致整次图索引不可用。
    """

    name = "llm-community-summary"

    def __init__(self, provider: object) -> None:
        self.provider = provider

    def generate(self, *, title: str, chunks: list[str]) -> str:
        from app.extensions.contracts import ChatTurn, GroundingEvidence

        evidence = [
            GroundingEvidence(
                citation_index=index,
                title=title,
                content=content[:2_000],
                locator=f"community-source:{index}",
            )
            for index, content in enumerate(chunks[:6], start=1)
        ]
        stream_answer = getattr(self.provider, "stream_answer", None)
        if stream_answer is None:
            raise RuntimeError("社区摘要 Provider 未实现 stream_answer")
        output = "".join(
            stream_answer(
                conversation=[
                    ChatTurn(
                        role="user",
                        content=(
                            f"请根据以下原始资料，为社区“{title}”写一段 120 到 300 字的中文摘要。"
                            "只概括资料明确表达的主题、实体和关系，不要添加资料之外的事实。"
                        ),
                    )
                ],
                evidence=evidence,
                response_mode="rag",
                route_reason="community_summary",
            )
        ).strip()
        if not output:
            raise RuntimeError("社区摘要 Provider 返回空内容")
        return output[:2_000]


class CommunitySummaryService:
    """构建、替换和失效处理社区摘要。"""

    def rebuild(
        self,
        session: Session,
        *,
        knowledge_base_id: str,
        workspace_id: str,
        summary_generator: CommunitySummaryGenerator | None = None,
        extractor_provider: str = "rule",
        extractor_version: str = "v1",
    ) -> CommunityBuildStats:
        knowledge_base = session.scalar(
            select(KnowledgeBase).where(
                KnowledgeBase.id == knowledge_base_id,
                KnowledgeBase.workspace_id == workspace_id,
            )
        )
        if knowledge_base is None:
            raise ValueError("知识库不存在或不属于当前工作区")

        entities = list(
            session.scalars(
                select(KnowledgeEntity).where(
                    KnowledgeEntity.workspace_id == workspace_id,
                    KnowledgeEntity.knowledge_base_id == knowledge_base_id,
                )
            )
        )
        relations = list(
            session.scalars(
                select(KnowledgeRelation).where(
                    KnowledgeRelation.workspace_id == workspace_id,
                    KnowledgeRelation.knowledge_base_id == knowledge_base_id,
                )
            )
        )
        documents = list(
            session.scalars(
                select(Document).where(
                    Document.workspace_id == workspace_id,
                    Document.knowledge_base_id == knowledge_base_id,
                    Document.status == "indexed",
                )
            )
        )

        # 版本递增后旧摘要立即失效；事务提交前检索仍会受到 graph_status=building 保护。
        knowledge_base.graph_status = "building"
        knowledge_base.graph_revision = (knowledge_base.graph_revision or 0) + 1
        revision = knowledge_base.graph_revision
        session.execute(
            delete(KnowledgeCommunitySummary).where(
                KnowledgeCommunitySummary.workspace_id == workspace_id,
                KnowledgeCommunitySummary.knowledge_base_id == knowledge_base_id,
            )
        )

        drafts = self._discover_communities(
            session,
            entities=entities,
            relations=relations,
            workspace_id=workspace_id,
        )
        generator = summary_generator or DeterministicCommunitySummaryGenerator()
        fallback_count = 0
        for draft in drafts:
            chunks = self._load_source_chunks(session, draft.source_chunk_ids, workspace_id)
            try:
                summary = generator.generate(
                    title=draft.title, chunks=[item.content for item in chunks]
                )
                provider_name = generator.name
            except Exception:
                # 单个社区失败只回退当前社区，保留其他社区的模型摘要。
                summary = DeterministicCommunitySummaryGenerator().generate(
                    title=draft.title, chunks=[item.content for item in chunks]
                )
                provider_name = DeterministicCommunitySummaryGenerator.name
                fallback_count += 1
            session.add(
                KnowledgeCommunitySummary(
                    workspace_id=workspace_id,
                    knowledge_base_id=knowledge_base_id,
                    level=draft.level,
                    community_key=draft.key,
                    title=draft.title,
                    summary=summary,
                    member_entity_ids=list(draft.member_entity_ids),
                    source_chunk_ids=list(draft.source_chunk_ids),
                    graph_revision=revision,
                    status="active",
                    extractor_provider=extractor_provider,
                    extractor_version=extractor_version,
                    summary_provider=provider_name,
                )
            )
        knowledge_base.graph_status = "ready"
        session.flush()
        return CommunityBuildStats(
            document_count=len(documents),
            entity_count=len(entities),
            relation_count=len(relations),
            community_count=len(drafts),
            graph_revision=revision,
            extractor_provider=extractor_provider,
            summary_provider=generator.name,
            summary_fallback=fallback_count,
        )

    def invalidate(self, session: Session, *, knowledge_base_id: str, workspace_id: str) -> None:
        """文档归档或切块替换时删除旧摘要，避免引用已不存在的切块。"""

        session.execute(
            delete(KnowledgeCommunitySummary).where(
                KnowledgeCommunitySummary.workspace_id == workspace_id,
                KnowledgeCommunitySummary.knowledge_base_id == knowledge_base_id,
            )
        )
        knowledge_base = session.scalar(
            select(KnowledgeBase).where(
                KnowledgeBase.id == knowledge_base_id,
                KnowledgeBase.workspace_id == workspace_id,
            )
        )
        if knowledge_base is not None:
            knowledge_base.graph_status = "stale"

    def _discover_communities(
        self,
        session: Session,
        *,
        entities: list[KnowledgeEntity],
        relations: list[KnowledgeRelation],
        workspace_id: str,
    ) -> list[CommunityDraft]:
        parent = {entity.id: entity.id for entity in entities}

        def find(node: str) -> str:
            while parent[node] != node:
                parent[node] = parent[parent[node]]
                node = parent[node]
            return node

        def union(left: str, right: str) -> None:
            left_root, right_root = find(left), find(right)
            if left_root != right_root:
                parent[right_root] = left_root

        for relation in relations:
            # 共现边只用于弱连接；极低置信度边不应把整个知识库合并成一个社区。
            if (
                relation.confidence >= 0.40
                and relation.source_entity_id in parent
                and relation.target_entity_id in parent
            ):
                union(relation.source_entity_id, relation.target_entity_id)

        groups: dict[str, list[KnowledgeEntity]] = defaultdict(list)
        for entity in entities:
            groups[find(entity.id)].append(entity)

        # 关系证据先按并查集根节点聚合，避免为每个社区重复扫描全部关系。
        relation_chunks_by_root: dict[str, set[str]] = defaultdict(set)
        for relation in relations:
            if relation.source_entity_id in parent and relation.target_entity_id in parent:
                relation_chunks_by_root[find(relation.source_entity_id)].add(
                    relation.document_chunk_id
                )

        mention_rows = list(
            session.scalars(
                select(ChunkEntityMention).where(
                    ChunkEntityMention.workspace_id == workspace_id,
                )
            )
        )
        mentions_by_entity: dict[str, set[str]] = defaultdict(set)
        for mention in mention_rows:
            mentions_by_entity[mention.entity_id].add(mention.document_chunk_id)

        drafts: list[CommunityDraft] = []
        for root, members in groups.items():
            members.sort(key=lambda item: (-item.mention_count, item.name.casefold()))
            member_ids = tuple(item.id for item in members)
            chunk_ids = set()
            for member in members:
                chunk_ids.update(mentions_by_entity.get(member.id, set()))
            chunk_ids.update(relation_chunks_by_root.get(root, set()))
            ordered_chunks = tuple(sorted(chunk_ids)[:80])
            title = "、".join(item.name[:40] for item in members[:4]) or "未命名社区"
            key = hashlib.sha256("|".join(sorted(member_ids)).encode()).hexdigest()[:32]
            drafts.append(
                CommunityDraft(
                    level=0,
                    key=key,
                    title=title,
                    member_entity_ids=member_ids,
                    source_chunk_ids=ordered_chunks,
                    summary="",
                )
            )

        # Level 1 是跨 level-0 社区的全局摘要，专门服务“整体/趋势/归纳”问题。
        if len(drafts) > 1:
            all_member_ids = tuple(
                sorted({item for draft in drafts for item in draft.member_entity_ids})
            )
            all_chunk_ids = tuple(
                sorted({item for draft in drafts for item in draft.source_chunk_ids})[:120]
            )
            title = "知识库全局主题"
            key = hashlib.sha256("|".join(all_member_ids).encode()).hexdigest()[:32]
            drafts.append(
                CommunityDraft(
                    level=1,
                    key=key,
                    title=title,
                    member_entity_ids=all_member_ids,
                    source_chunk_ids=all_chunk_ids,
                    summary="",
                )
            )
        return drafts

    @staticmethod
    def _load_source_chunks(
        session: Session, chunk_ids: tuple[str, ...], workspace_id: str
    ) -> list[DocumentChunk]:
        if not chunk_ids:
            return []
        return list(
            session.scalars(
                select(DocumentChunk)
                .where(
                    DocumentChunk.workspace_id == workspace_id,
                    DocumentChunk.id.in_(chunk_ids),
                )
                .order_by(DocumentChunk.ordinal.asc())
            )
        )


@dataclass(frozen=True, slots=True)
class CommunityRetrievalStats:
    matched_communities: int = 0
    summary_candidates: int = 0
    expanded_chunks: int = 0
    covered_documents: int = 0


class CommunityRetriever:
    """社区摘要匹配器；命中后展开原始切块，不把摘要直接作为 Evidence。"""

    def __init__(self, *, max_communities: int = 8, max_chunks: int = 60) -> None:
        self.max_communities = max_communities
        self.max_chunks = max_chunks
        self.last_stats = CommunityRetrievalStats()

    def retrieve(
        self,
        session: Session,
        *,
        knowledge_base_id: str,
        workspace_id: str,
        query: str,
        limit: int,
        mode: str,
    ) -> list[Evidence]:
        from app.rag.retrieval import Evidence

        summaries = list(
            session.scalars(
                select(KnowledgeCommunitySummary)
                .join(
                    KnowledgeBase,
                    KnowledgeBase.id == KnowledgeCommunitySummary.knowledge_base_id,
                )
                .where(
                    KnowledgeCommunitySummary.workspace_id == workspace_id,
                    KnowledgeCommunitySummary.knowledge_base_id == knowledge_base_id,
                    KnowledgeCommunitySummary.status == "active",
                    KnowledgeBase.graph_status == "ready",
                    KnowledgeCommunitySummary.graph_revision == KnowledgeBase.graph_revision,
                )
                .order_by(KnowledgeCommunitySummary.level.desc())
            )
        )
        query_tokens = self._tokenize_query(query)
        ranked: list[tuple[float, KnowledgeCommunitySummary]] = []
        for summary in summaries:
            haystack = normalize_entity_name(f"{summary.title} {summary.summary}")
            overlap = sum(1 for token in query_tokens if token in haystack)
            score = float(overlap) + (0.25 if summary.level > 0 and mode == "global" else 0.0)
            if mode == "global" and not query_tokens:
                score = 0.1
            if score > 0 or mode == "global":
                ranked.append((score, summary))
        ranked.sort(key=lambda item: (-item[0], item[1].level, item[1].community_key))
        selected = ranked[: self.max_communities]
        chunk_ids = [
            chunk_id for _, summary in selected for chunk_id in list(summary.source_chunk_ids)
        ][: self.max_chunks]
        if not chunk_ids:
            self.last_stats = CommunityRetrievalStats(
                matched_communities=len(selected), summary_candidates=len(summaries)
            )
            return []
        rows = session.execute(
            select(DocumentChunk, Document)
            .join(Document, Document.id == DocumentChunk.document_id)
            .where(
                DocumentChunk.workspace_id == workspace_id,
                DocumentChunk.id.in_(chunk_ids),
                Document.workspace_id == workspace_id,
                Document.knowledge_base_id == knowledge_base_id,
                Document.status == "indexed",
            )
        )
        ranked_chunks: list[Evidence] = []
        chunk_rank = {chunk_id: index for index, chunk_id in enumerate(chunk_ids)}
        for chunk, document in rows:
            rank_score = 1.0 / (1 + chunk_rank.get(chunk.id, len(chunk_ids)))
            ranked_chunks.append(
                Evidence(
                    source_type="document_chunk",
                    source_id=chunk.id,
                    title=document.title,
                    content=chunk.content,
                    score=rank_score,
                    locator=f"document:{document.id}:chunk:{chunk.ordinal}",
                    source_url=document.source_url,
                    source_validation_state=document.source_validation_state,
                    source_is_approved=document.source_is_approved,
                )
            )
        ranked_chunks.sort(key=lambda item: (-item.score, item.locator))
        self.last_stats = CommunityRetrievalStats(
            matched_communities=len(selected),
            summary_candidates=len(summaries),
            expanded_chunks=len(ranked_chunks),
            covered_documents=len({item.locator.split(":")[1] for item in ranked_chunks}),
        )
        return ranked_chunks[:limit]

    @staticmethod
    def _tokenize_query(query: str) -> set[str]:
        """同时覆盖英文词和中文二/三元片段，避免中文查询被当成一个超长 token。"""

        normalized = normalize_entity_name(query)
        tokens = {
            token
            for token in re.findall(r"[a-z0-9_./:-]{2,}|[\u4e00-\u9fff]+", normalized)
            if len(token) >= 2
        }
        for run in re.findall(r"[\u4e00-\u9fff]+", normalized):
            tokens.update(run[index : index + 2] for index in range(len(run) - 1))
            tokens.update(run[index : index + 3] for index in range(len(run) - 2))
        return tokens
