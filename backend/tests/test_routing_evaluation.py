"""路由离线评测的契约测试。"""

import pytest

from app.rag.query_routing import QueryRoute
from app.rag.routing_evaluation import (
    RoutingEvaluationCase,
    evaluate_routing_case,
    parse_routing_cases,
    summarize_routing,
)


def test_parse_routing_cases_rejects_duplicate_ids_and_unknown_routes() -> None:
    with pytest.raises(ValueError, match="id 重复"):
        parse_routing_cases(
            [
                {"id": "duplicate", "query": "你好", "expectedRoute": "direct"},
                {"id": "duplicate", "query": "年假", "expectedRoute": "rag"},
            ]
        )
    with pytest.raises(ValueError, match="expectedRoute 无效"):
        parse_routing_cases([{"id": "unknown", "query": "你好", "expectedRoute": "other"}])


def test_routing_summary_keeps_per_route_recall_visible() -> None:
    direct = RoutingEvaluationCase(id="direct", query="你好", expected_route="direct")
    rag = RoutingEvaluationCase(id="rag", query="年假", expected_route="rag")
    results = [
        evaluate_routing_case(direct, QueryRoute(mode="direct", reason="social")),
        evaluate_routing_case(rag, QueryRoute(mode="direct", reason="incorrect")),
    ]

    summary = summarize_routing(results)

    assert summary.accuracy == 0.5
    assert summary.route_recall["direct"] == 1.0
    assert summary.route_recall["rag"] == 0.0
    assert summary.actual_route_counts["direct"] == 2


def test_routing_result_does_not_include_query_text() -> None:
    case = RoutingEvaluationCase(
        id="sensitive-case",
        query="不应进入报告的问题",
        expected_route="rag",
    )
    result = evaluate_routing_case(case, QueryRoute(mode="rag", reason="knowledge_request"))

    assert "不应进入报告的问题" not in str(result)
