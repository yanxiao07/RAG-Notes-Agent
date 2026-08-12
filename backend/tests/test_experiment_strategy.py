"""检索实验策略只能派生内存快照。"""

import pytest

from app.core.config import Settings
from app.rag.experiment_strategy import build_experiment_strategy, strategy_snapshot


def test_experiment_strategy_does_not_mutate_configured_settings() -> None:
    configured = Settings(query_rewrite_enabled=False, reranker_enabled=False)

    rewrite = build_experiment_strategy("rewrite", configured)
    rerank = build_experiment_strategy("rerank", configured)

    assert configured.query_rewrite_enabled is False
    assert configured.reranker_enabled is False
    assert rewrite.settings.query_rewrite_enabled is True
    assert rewrite.settings.reranker_enabled is False
    assert rerank.settings.query_rewrite_enabled is False
    assert rerank.settings.reranker_enabled is True
    snapshot = strategy_snapshot(rerank)
    requested = snapshot["requested"]
    assert isinstance(requested, dict)
    assert requested["reranker"] is True


def test_unknown_experiment_strategy_is_rejected() -> None:
    with pytest.raises(ValueError, match="未知检索实验策略"):
        build_experiment_strategy("unsupported", Settings())
