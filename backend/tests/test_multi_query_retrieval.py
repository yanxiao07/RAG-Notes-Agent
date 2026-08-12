"""多路 Query 候选融合测试。"""

from app.rag.retrieval import Evidence, fuse_query_evidence


def evidence(locator: str, score: float = 0.5) -> Evidence:
    return Evidence(
        source_type="document_chunk",
        source_id=locator,
        title="测试文档",
        content=locator,
        score=score,
        locator=locator,
    )


def test_multi_query_fusion_deduplicates_and_rewards_consensus() -> None:
    result = fuse_query_evidence(
        [
            [evidence("document:a:chunk:1"), evidence("document:b:chunk:1")],
            [evidence("document:b:chunk:1"), evidence("document:c:chunk:1")],
        ],
        limit=3,
        variant_weights=[1.0, 1.0],
    )
    assert [item.locator for item in result] == [
        "document:b:chunk:1",
        "document:a:chunk:1",
        "document:c:chunk:1",
    ]
    assert len({item.locator for item in result}) == 3
