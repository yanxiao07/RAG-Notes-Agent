"""Dynamic Top-K 的停止条件、覆盖度与固定 K 回退测试。"""

from app.rag.dynamic_top_k import DynamicTopKPolicy
from app.rag.retrieval import Evidence


def evidence(document_id: str, ordinal: int, score: float, *, size: int = 20) -> Evidence:
    return Evidence(
        source_type="document_chunk",
        source_id=f"chunk-{document_id}-{ordinal}",
        title=f"document-{document_id}",
        content="x" * size,
        score=score,
        locator=f"document:{document_id}:chunk:{ordinal}",
    )


def test_dynamic_top_k_stops_on_score_gap_after_minimum_evidence() -> None:
    selected, stats = DynamicTopKPolicy(
        minimum_candidates=3,
        maximum_candidates=8,
        score_gap_threshold=0.2,
        target_source_coverage=2,
    ).select(
        [
            evidence("a", 1, 0.98),
            evidence("a", 2, 0.95),
            evidence("a", 3, 0.93),
            evidence("a", 4, 0.62),
        ],
        requested_max_candidates=8,
        query_profile="local",
    )

    assert [item.locator for item in selected] == [
        "document:a:chunk:1",
        "document:a:chunk:2",
        "document:a:chunk:3",
    ]
    assert stats.stop_reason == "score_gap_reached"
    assert stats.boundary_score_gap == 0.31


def test_dynamic_top_k_keeps_cross_document_candidate_until_coverage_target() -> None:
    selected, stats = DynamicTopKPolicy(
        minimum_candidates=3,
        maximum_candidates=8,
        score_gap_threshold=0.2,
        target_source_coverage=2,
    ).select(
        [
            evidence("a", 1, 0.98),
            evidence("a", 2, 0.95),
            evidence("a", 3, 0.93),
            evidence("b", 1, 0.45),
        ],
        requested_max_candidates=8,
        query_profile="local",
    )

    assert len(selected) == 4
    assert stats.source_coverage == 2
    assert stats.stop_reason == "candidates_exhausted"


def test_dynamic_top_k_stops_before_exceeding_context_budget() -> None:
    selected, stats = DynamicTopKPolicy(
        minimum_candidates=3,
        maximum_candidates=8,
        score_gap_threshold=1.0,
        target_source_coverage=1,
        context_token_budget=256,
        budget_ratio=0.5,
    ).select(
        [
            evidence("a", 1, 0.98, size=30),
            evidence("a", 2, 0.95, size=30),
            evidence("a", 3, 0.93, size=30),
            evidence("a", 4, 0.92, size=600),
        ],
        requested_max_candidates=8,
        query_profile="local",
    )

    assert len(selected) == 3
    assert stats.stop_reason == "context_budget_reached"


def test_dynamic_top_k_disabled_strictly_returns_requested_fixed_limit() -> None:
    candidates = [evidence("a", ordinal, 1.0 - ordinal / 100) for ordinal in range(1, 6)]
    selected, stats = DynamicTopKPolicy(
        enabled=False,
        maximum_candidates=2,
    ).select(candidates, requested_max_candidates=4, query_profile="local")

    assert selected == candidates[:4]
    assert stats.stop_reason == "fixed_limit_fallback"
