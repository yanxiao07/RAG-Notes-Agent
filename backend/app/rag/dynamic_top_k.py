"""检索结果的自适应 Top-K 选择。

该策略只缩减已排序候选，不重新计算检索分数，也不改写 Evidence 原文。它在
Rerank 之后、Parent-Child 上下文扩展之前运行，以减少低价值证据占用 Prompt
预算，同时保留固定 K 的可配置安全回退。
"""

from __future__ import annotations

from dataclasses import dataclass

from app.core.config import Settings
from app.rag.context_budget import estimate_evidence_tokens
from app.rag.retrieval import Evidence


@dataclass(frozen=True, slots=True)
class DynamicTopKStats:
    """仅包含可公开诊断的选择统计，不保存正文或查询内容。"""

    enabled: bool
    query_profile: str
    requested_max_candidates: int
    minimum_candidates: int
    selected_candidates: int
    source_coverage: int
    token_budget: int
    estimated_tokens: int
    stop_reason: str
    boundary_score_gap: float | None = None


class DynamicTopKPolicy:
    """基于分数间隔、来源覆盖和上下文预算的确定性 Evidence 选择器。"""

    def __init__(
        self,
        *,
        enabled: bool = True,
        minimum_candidates: int = 3,
        maximum_candidates: int = 12,
        score_gap_threshold: float = 0.12,
        target_source_coverage: int = 2,
        context_token_budget: int = 4_096,
        budget_ratio: float = 0.8,
    ) -> None:
        self.enabled = enabled
        self.minimum_candidates = minimum_candidates
        self.maximum_candidates = maximum_candidates
        self.score_gap_threshold = score_gap_threshold
        self.target_source_coverage = target_source_coverage
        self.context_token_budget = context_token_budget
        self.budget_ratio = budget_ratio

    @classmethod
    def from_settings(cls, settings: Settings) -> DynamicTopKPolicy:
        return cls(
            enabled=settings.dynamic_top_k_enabled,
            minimum_candidates=settings.dynamic_top_k_min_candidates,
            maximum_candidates=settings.dynamic_top_k_max_candidates,
            score_gap_threshold=settings.dynamic_top_k_score_gap_threshold,
            target_source_coverage=settings.dynamic_top_k_target_source_coverage,
            context_token_budget=settings.rag_context_max_tokens,
            budget_ratio=settings.dynamic_top_k_budget_ratio,
        )

    def select(
        self,
        candidates: list[Evidence],
        *,
        requested_max_candidates: int,
        query_profile: str,
    ) -> tuple[list[Evidence], DynamicTopKStats]:
        """按候选既有排序选择证据，并为每次停止保留确定性原因。"""

        allowed = max(1, min(requested_max_candidates, self.maximum_candidates))
        available = candidates[:allowed]
        profile_floor = {"local": 3, "multi_hop": 4, "global": 4}.get(query_profile, 3)
        minimum = min(allowed, max(self.minimum_candidates, profile_floor))
        token_budget = max(128, int(self.context_token_budget * self.budget_ratio))

        if not self.enabled:
            # 显式关闭时不再受 Dynamic Top-K 的最大值影响，严格保留调用方的固定 K。
            selected = candidates[:requested_max_candidates]
            return selected, self._stats(
                enabled=False,
                query_profile=query_profile,
                requested_max_candidates=requested_max_candidates,
                minimum_candidates=minimum,
                selected=selected,
                token_budget=token_budget,
                stop_reason="fixed_limit_fallback",
            )

        selected: list[Evidence] = []
        source_keys: set[str] = set()
        estimated_tokens = 0
        boundary_score_gap: float | None = None
        stop_reason = "candidates_exhausted"

        for candidate in available:
            candidate_tokens = estimate_evidence_tokens(candidate)
            source_key = _source_key(candidate)
            adds_coverage = source_key not in source_keys
            if len(selected) >= minimum:
                previous = selected[-1]
                score_gap = max(0.0, previous.score - candidate.score)
                if estimated_tokens + candidate_tokens > token_budget:
                    stop_reason = "context_budget_reached"
                    break
                # 当证据尚未覆盖足够来源时，允许跨来源候选越过分数断崖；这避免
                # 多跳/汇总类问题只保留同一篇文档的相邻片段。
                coverage_needed = len(source_keys) < self.target_source_coverage
                if score_gap >= self.score_gap_threshold and not (
                    coverage_needed and adds_coverage
                ):
                    stop_reason = "score_gap_reached"
                    boundary_score_gap = round(score_gap, 4)
                    break
            selected.append(candidate)
            source_keys.add(source_key)
            estimated_tokens += candidate_tokens
        else:
            if len(available) == allowed and len(candidates) > allowed:
                stop_reason = "maximum_candidates_reached"

        return selected, self._stats(
            enabled=True,
            query_profile=query_profile,
            requested_max_candidates=requested_max_candidates,
            minimum_candidates=minimum,
            selected=selected,
            token_budget=token_budget,
            estimated_tokens=estimated_tokens,
            stop_reason=stop_reason,
            boundary_score_gap=boundary_score_gap,
        )

    @staticmethod
    def _stats(
        *,
        enabled: bool,
        query_profile: str,
        requested_max_candidates: int,
        minimum_candidates: int,
        selected: list[Evidence],
        token_budget: int,
        stop_reason: str,
        estimated_tokens: int | None = None,
        boundary_score_gap: float | None = None,
    ) -> DynamicTopKStats:
        return DynamicTopKStats(
            enabled=enabled,
            query_profile=query_profile,
            requested_max_candidates=requested_max_candidates,
            minimum_candidates=minimum_candidates,
            selected_candidates=len(selected),
            source_coverage=len({_source_key(item) for item in selected}),
            token_budget=token_budget,
            estimated_tokens=(
                estimated_tokens
                if estimated_tokens is not None
                else sum(estimate_evidence_tokens(item) for item in selected)
            ),
            stop_reason=stop_reason,
            boundary_score_gap=boundary_score_gap,
        )


def _source_key(evidence: Evidence) -> str:
    """用文档/笔记作为覆盖单元，多个相邻 chunk 只计一次来源。"""

    locator_parts = evidence.locator.split(":")
    if evidence.source_type == "document_chunk" and len(locator_parts) >= 2:
        return f"document:{locator_parts[1]}"
    return f"{evidence.source_type}:{evidence.source_id}"
