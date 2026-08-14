"""GraphRAG 社区索引的确定性离线质量门禁。"""

from __future__ import annotations

from dataclasses import asdict, dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.knowledge.models import (
    DocumentChunk,
    KnowledgeBase,
    KnowledgeCommunitySummary,
    KnowledgeEntity,
)


@dataclass(frozen=True, slots=True)
class CommunityIndexEvaluation:
    """只保存可审计计数与算法元数据，不能包含摘要或原始切块正文。"""

    knowledge_base_id: str
    graph_revision: int
    graph_ready: bool
    community_count: int
    member_entity_count: int
    resolved_member_entity_count: int
    source_chunk_count: int
    resolved_source_chunk_count: int
    algorithm_counts: dict[str, int]
    fallback_count: int

    @property
    def member_entity_coverage(self) -> float:
        if not self.member_entity_count:
            return 0.0
        return self.resolved_member_entity_count / self.member_entity_count

    @property
    def source_chunk_coverage(self) -> float:
        if not self.source_chunk_count:
            return 0.0
        return self.resolved_source_chunk_count / self.source_chunk_count

    def as_report(self) -> dict[str, object]:
        report = asdict(self)
        report["memberEntityCoverage"] = round(self.member_entity_coverage, 4)
        report["sourceChunkCoverage"] = round(self.source_chunk_coverage, 4)
        return report


def evaluate_community_index(
    session: Session, *, knowledge_base_id: str, workspace_id: str
) -> CommunityIndexEvaluation:
    """验证当前图谱版本的导航记录均可回指到工作区内的原始资产。"""

    knowledge_base = session.scalar(
        select(KnowledgeBase).where(
            KnowledgeBase.id == knowledge_base_id,
            KnowledgeBase.workspace_id == workspace_id,
        )
    )
    if knowledge_base is None:
        raise ValueError("知识库不存在或不属于当前工作区。")
    summaries = list(
        session.scalars(
            select(KnowledgeCommunitySummary).where(
                KnowledgeCommunitySummary.workspace_id == workspace_id,
                KnowledgeCommunitySummary.knowledge_base_id == knowledge_base_id,
                KnowledgeCommunitySummary.graph_revision == knowledge_base.graph_revision,
                KnowledgeCommunitySummary.status == "active",
            )
        )
    )
    member_ids = {
        entity_id for summary in summaries for entity_id in summary.member_entity_ids
    }
    chunk_ids = {
        chunk_id for summary in summaries for chunk_id in summary.source_chunk_ids
    }
    resolved_member_ids = set(
        session.scalars(
            select(KnowledgeEntity.id).where(
                KnowledgeEntity.workspace_id == workspace_id,
                KnowledgeEntity.knowledge_base_id == knowledge_base_id,
                KnowledgeEntity.id.in_(member_ids),
            )
        )
    ) if member_ids else set()
    resolved_chunk_ids = set(
        session.scalars(
            select(DocumentChunk.id).where(
                DocumentChunk.workspace_id == workspace_id,
                DocumentChunk.id.in_(chunk_ids),
            )
        )
    ) if chunk_ids else set()
    algorithm_counts: dict[str, int] = {}
    for summary in summaries:
        algorithm_counts[summary.community_algorithm] = (
            algorithm_counts.get(summary.community_algorithm, 0) + 1
        )
    return CommunityIndexEvaluation(
        knowledge_base_id=knowledge_base.id,
        graph_revision=knowledge_base.graph_revision,
        graph_ready=knowledge_base.graph_status == "ready",
        community_count=len(summaries),
        member_entity_count=len(member_ids),
        resolved_member_entity_count=len(resolved_member_ids),
        source_chunk_count=len(chunk_ids),
        resolved_source_chunk_count=len(resolved_chunk_ids),
        algorithm_counts=dict(sorted(algorithm_counts.items())),
        fallback_count=sum(summary.community_algorithm_fallback for summary in summaries),
    )


def community_quality_gate(
    evaluation: CommunityIndexEvaluation, *, required_algorithm: str | None = None
) -> tuple[bool, list[str]]:
    """对可追溯性和实际算法做硬约束，不将主观摘要质量伪装成确定性结论。"""

    reasons: list[str] = []
    if not evaluation.graph_ready:
        reasons.append("图谱索引未处于 ready 状态")
    if evaluation.community_count == 0:
        reasons.append("当前图谱版本没有有效社区")
    if evaluation.member_entity_coverage < 1.0:
        reasons.append("社区成员实体存在无法回指的记录")
    if evaluation.source_chunk_coverage < 1.0:
        reasons.append("社区原始切块存在无法回指的记录")
    if required_algorithm and set(evaluation.algorithm_counts) != {required_algorithm}:
        reasons.append("实际社区算法与要求不一致或发生回退")
    return not reasons, reasons
