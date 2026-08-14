"""社区索引离线评测与质量门禁测试。"""

from app.rag.community_evaluation import CommunityIndexEvaluation, community_quality_gate


def evaluation(**overrides: object) -> CommunityIndexEvaluation:
    values: dict[str, object] = {
        "knowledge_base_id": "knowledge-base",
        "graph_revision": 3,
        "graph_ready": True,
        "community_count": 2,
        "member_entity_count": 4,
        "resolved_member_entity_count": 4,
        "source_chunk_count": 6,
        "resolved_source_chunk_count": 6,
        "algorithm_counts": {"louvain": 2},
        "fallback_count": 0,
    }
    values.update(overrides)
    return CommunityIndexEvaluation(**values)  # type: ignore[arg-type]


def test_community_gate_requires_full_original_asset_traceability() -> None:
    passed, reasons = community_quality_gate(
        evaluation(resolved_source_chunk_count=5),
        required_algorithm="louvain",
    )

    assert passed is False
    assert reasons == ["社区原始切块存在无法回指的记录"]


def test_community_gate_rejects_algorithm_fallback_when_algorithm_is_required() -> None:
    passed, reasons = community_quality_gate(
        evaluation(algorithm_counts={"connected_components": 2}, fallback_count=2),
        required_algorithm="louvain",
    )

    assert passed is False
    assert reasons == ["实际社区算法与要求不一致或发生回退"]


def test_community_gate_accepts_ready_louvain_snapshot() -> None:
    passed, reasons = community_quality_gate(evaluation(), required_algorithm="louvain")

    assert passed is True
    assert reasons == []
