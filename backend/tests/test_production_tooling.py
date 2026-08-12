"""生产验收脚本的纯函数契约测试。"""

import json

import pytest

from scripts.benchmark_retrieval import _latency_summary, _load_queries, _percentile


def test_benchmark_query_loader_accepts_strings_and_objects(tmp_path) -> None:
    path = tmp_path / "queries.json"
    path.write_text(
        json.dumps(["  第一条  ", {"id": "q2", "query": "第二条"}, {"query": ""}]),
        encoding="utf-8",
    )

    assert _load_queries(path, None) == ["第一条", "第二条"]
    assert _load_queries(None, ["  内联查询 ", ""]) == ["内联查询"]


def test_benchmark_percentiles_are_stable_for_small_samples() -> None:
    assert _latency_summary([1, 2, 3, 4, 5]) == {
        "min": 1,
        "mean": 3,
        "p50": 3.0,
        "p95": 4.8,
        "max": 5,
    }
    assert _percentile([10, 20], 0.95) == 19.5


def test_benchmark_rejects_invalid_percentile() -> None:
    with pytest.raises(ValueError):
        _percentile([], 0.5)
    with pytest.raises(ValueError):
        _percentile([1], 1.1)
