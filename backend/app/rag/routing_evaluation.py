"""查询路由离线评测的领域契约。

路由本身决定是否触发 RAG，因此应在不检索、不生成回答、不写入会话的条件下独立验证。
评测报告刻意不保存问题正文，避免把脱敏语料之外的用户输入带入评测工件。
"""

from __future__ import annotations

from dataclasses import dataclass
from statistics import mean

from app.rag.query_routing import QueryRoute

ALLOWED_ROUTE_MODES = frozenset({"direct", "memory", "clarify", "rag"})


@dataclass(frozen=True, slots=True)
class RoutingEvaluationCase:
    """单条人工标注的路由用例。``query`` 只在进程内参与计算。"""

    id: str
    query: str
    expected_route: str


@dataclass(frozen=True, slots=True)
class RoutingCaseResult:
    """可持久化的脱敏用例结果，不包含原始问题。"""

    case_id: str
    expected_route: str
    actual_route: str
    matched: bool
    reason: str
    router: str
    confidence: float
    cache_hit: bool


@dataclass(frozen=True, slots=True)
class RoutingEvaluationSummary:
    case_count: int
    accuracy: float
    expected_route_counts: dict[str, int]
    actual_route_counts: dict[str, int]
    route_recall: dict[str, float]


def parse_routing_cases(raw_cases: object) -> list[RoutingEvaluationCase]:
    """解析并校验版本化 JSON 用例，拒绝空集、重复 ID 和未知路由。"""

    if not isinstance(raw_cases, list) or not raw_cases:
        raise ValueError("路由评测文件必须是非空 JSON 数组")

    cases: list[RoutingEvaluationCase] = []
    case_ids: set[str] = set()
    for item in raw_cases:
        if not isinstance(item, dict):
            raise ValueError("每条路由评测用例必须是对象")
        case_id = item.get("id")
        query = item.get("query")
        expected_route = item.get("expectedRoute")
        if not isinstance(case_id, str) or not case_id.strip():
            raise ValueError("每条路由评测用例必须包含非空 id")
        if case_id in case_ids:
            raise ValueError(f"路由评测用例 id 重复: {case_id}")
        if not isinstance(query, str) or not query.strip():
            raise ValueError(f"路由评测用例 {case_id} 缺少非空 query")
        if expected_route not in ALLOWED_ROUTE_MODES:
            raise ValueError(f"路由评测用例 {case_id} 的 expectedRoute 无效")
        case_ids.add(case_id)
        cases.append(
            RoutingEvaluationCase(
                id=case_id,
                query=query,
                expected_route=expected_route,
            )
        )
    return cases


def evaluate_routing_case(case: RoutingEvaluationCase, route: QueryRoute) -> RoutingCaseResult:
    """将路由决策转换为不含问题正文的结果。"""

    return RoutingCaseResult(
        case_id=case.id,
        expected_route=case.expected_route,
        actual_route=route.mode,
        matched=route.mode == case.expected_route,
        reason=route.reason,
        router=route.router,
        confidence=route.confidence,
        cache_hit=route.cache_hit,
    )


def summarize_routing(results: list[RoutingCaseResult]) -> RoutingEvaluationSummary:
    """计算整体准确率及每种期望路由的召回率，避免大类掩盖小类退化。"""

    if not results:
        raise ValueError("routing evaluation results must not be empty")
    expected_counts = {mode: 0 for mode in sorted(ALLOWED_ROUTE_MODES)}
    actual_counts = {mode: 0 for mode in sorted(ALLOWED_ROUTE_MODES)}
    expected_matched = {mode: 0 for mode in sorted(ALLOWED_ROUTE_MODES)}
    for result in results:
        expected_counts[result.expected_route] += 1
        actual_counts[result.actual_route] = actual_counts.get(result.actual_route, 0) + 1
        if result.matched:
            expected_matched[result.expected_route] += 1
    route_recall = {
        mode: expected_matched[mode] / count if count else 0.0
        for mode, count in expected_counts.items()
    }
    return RoutingEvaluationSummary(
        case_count=len(results),
        accuracy=mean(result.matched for result in results),
        expected_route_counts=expected_counts,
        actual_route_counts=actual_counts,
        route_recall=route_recall,
    )
