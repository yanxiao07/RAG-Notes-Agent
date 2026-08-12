"""Metadata Boost 策略的确定性测试。"""

from app.rag.metadata_boost import MetadataBoostPolicy
from app.rag.postgres_retrieval import _format_vector, _normalize_fts_score


def test_metadata_boost_is_small_explainable_and_capped() -> None:
    policy = MetadataBoostPolicy(
        title_weight=0.12,
        section_weight=0.08,
        source_type_weight=0.03,
        max_boost=0.20,
    )

    score, boosted = policy.adjust(
        0.70,
        query_tokens={"rag", "检"},
        title="RAG 检索策略",
        metadata={"section": "检索策略"},
        source_type="markdown",
    )

    assert boosted is True
    assert score == 0.86


def test_disabled_metadata_boost_keeps_original_score() -> None:
    policy = MetadataBoostPolicy(enabled=False)

    score, boosted = policy.adjust(
        0.42,
        query_tokens={"缓存"},
        title="缓存设计",
        metadata={"section": "性能"},
        source_type="markdown",
    )

    assert score == 0.42
    assert boosted is False


def test_metadata_without_query_overlap_does_not_change_score() -> None:
    policy = MetadataBoostPolicy()

    score, boosted = policy.adjust(
        0.42,
        query_tokens={"向量"},
        title="缓存设计",
        metadata={"section": "性能"},
        source_type="pdf",
    )

    assert score == 0.42
    assert boosted is False


def test_postgres_vector_bind_format_and_fts_normalization_are_stable() -> None:
    assert _format_vector([0.1, -2.5, 3.0]) == "[0.1,-2.5,3]"
    assert _normalize_fts_score(1.0) == 0.5
    assert _normalize_fts_score(None) == 0.0
