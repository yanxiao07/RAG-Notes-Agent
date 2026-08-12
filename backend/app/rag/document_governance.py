"""文档时效、可信度与冲突状态的检索策略。

策略只使用人工维护的结构化治理字段，不从文档正文、URL 或模型输出推测权威性。
已被替代或尚未生效的资料在查询层排除；过期、冲突资料保留为可追溯历史候选，
但不能与当前有效资料获得相同排序待遇。
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime
from enum import StrEnum

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.knowledge.models import Document
from app.rag.retrieval import Evidence


class GovernanceAvailability(StrEnum):
    AVAILABLE = "available"
    FUTURE_EFFECTIVE = "future_effective"
    EXPIRED = "expired"


def availability(
    *, effective_at: datetime | None, expires_at: datetime | None
) -> GovernanceAvailability:
    """判断资料当前是否可作为现行依据；时间一律转换为 UTC 再比较。"""

    now = datetime.now(UTC)
    if effective_at is not None and _as_utc(effective_at) > now:
        return GovernanceAvailability.FUTURE_EFFECTIVE
    if expires_at is not None and _as_utc(expires_at) <= now:
        return GovernanceAvailability.EXPIRED
    return GovernanceAvailability.AVAILABLE


def adjust_score(
    score: float,
    *,
    source_trust_level: str,
    effective_at: datetime | None,
    expires_at: datetime | None,
    conflict_state: str,
) -> tuple[float, GovernanceAvailability, bool]:
    """在同一召回集合内小幅调整排序，不用治理字段凭空制造候选。"""

    state = availability(effective_at=effective_at, expires_at=expires_at)
    if state is GovernanceAvailability.FUTURE_EFFECTIVE:
        return 0.0, state, True
    adjustment = {"verified": 0.06, "standard": 0.0, "unverified": -0.04}.get(
        source_trust_level,
        0.0,
    )
    if state is GovernanceAvailability.EXPIRED:
        adjustment -= 0.12
    if conflict_state == "conflicted":
        adjustment -= 0.08
    adjusted = min(max(score + adjustment, 0.0), 1.0)
    return adjusted, state, adjusted != score


def _as_utc(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


@dataclass(frozen=True, slots=True)
class GovernanceFilterStats:
    excluded_superseded: int = 0
    excluded_future_effective: int = 0
    expired_candidates: int = 0
    conflicted_candidates: int = 0
    trust_adjusted_candidates: int = 0


def apply_document_governance(
    session: Session,
    *,
    evidences: list[Evidence],
    workspace_id: str,
) -> tuple[list[Evidence], GovernanceFilterStats]:
    """在所有召回路径融合后统一执行资料治理，避免定向/图谱路径绕过规则。"""

    document_ids = {_document_id(item) for item in evidences}
    document_ids.discard(None)
    if not document_ids:
        return evidences, GovernanceFilterStats()
    documents = {
        document.id: document
        for document in session.scalars(
            select(Document).where(
                Document.workspace_id == workspace_id,
                Document.id.in_(document_ids),
            )
        )
    }
    superseded_ids = set(
        session.scalars(
            select(Document.supersedes_document_id).where(
                Document.workspace_id == workspace_id,
                Document.status != "archived",
                Document.supersedes_document_id.is_not(None),
            )
        )
    )
    selected: list[Evidence] = []
    excluded_superseded = 0
    excluded_future = 0
    expired = 0
    conflicted = 0
    trust_adjusted = 0
    for evidence in evidences:
        document_id = _document_id(evidence)
        document = documents.get(document_id) if document_id else None
        if document is None:
            selected.append(evidence)
            continue
        if document.id in superseded_ids:
            excluded_superseded += 1
            continue
        adjusted_score, state, adjusted = adjust_score(
            evidence.score,
            source_trust_level=document.source_trust_level,
            effective_at=document.effective_at,
            expires_at=document.expires_at,
            conflict_state=document.conflict_state,
        )
        if state is GovernanceAvailability.FUTURE_EFFECTIVE:
            excluded_future += 1
            continue
        expired += int(state is GovernanceAvailability.EXPIRED)
        conflicted += int(document.conflict_state == "conflicted")
        trust_adjusted += int(adjusted)
        selected.append(
            replace(
                evidence,
                score=adjusted_score,
                source_trust_level=document.source_trust_level,
                governance_availability=state.value,
                conflict_state=document.conflict_state,
            )
        )
    return (
        sorted(selected, key=lambda item: (-item.score, item.locator)),
        GovernanceFilterStats(
            excluded_superseded=excluded_superseded,
            excluded_future_effective=excluded_future,
            expired_candidates=expired,
            conflicted_candidates=conflicted,
            trust_adjusted_candidates=trust_adjusted,
        ),
    )


def _document_id(evidence: Evidence) -> str | None:
    if evidence.source_type != "document_chunk":
        return None
    parts = evidence.locator.split(":")
    return parts[1] if len(parts) >= 4 and parts[0] == "document" else None
