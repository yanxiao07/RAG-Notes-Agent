"""低基数、脱敏的 Prometheus 指标注册表。

该实现提供部署初期需要的 HTTP/限流指标，不把原始 URL、工作区、用户、问题、文档或
密钥写进标签。生产多副本环境应由 Prometheus 分别抓取每个实例并在查询层聚合。
"""

from __future__ import annotations

import hmac
import threading
from collections import Counter
from time import perf_counter

from fastapi import Request

from app.core.config import Settings

_HISTOGRAM_BUCKETS = (0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0)


class MetricsRegistry:
    """进程内指标累加器，所有标签由固定枚举或路由模板生成。"""

    def __init__(self) -> None:
        self._request_counts: Counter[tuple[str, str, str]] = Counter()
        self._request_durations: dict[tuple[str, str], list[float]] = {}
        self._rate_limit_rejections: Counter[tuple[str, str]] = Counter()
        self._lock = threading.Lock()

    def observe_http(
        self, *, method: str, route: str, status_code: int, duration_seconds: float
    ) -> None:
        label = (method.upper(), route, f"{status_code // 100}xx")
        duration_key = (method.upper(), route)
        with self._lock:
            self._request_counts[label] += 1
            values = self._request_durations.setdefault(duration_key, [0.0, 0.0])
            values[0] += max(duration_seconds, 0.0)
            values[1] += 1
            for bucket in _HISTOGRAM_BUCKETS:
                if duration_seconds <= bucket:
                    self._request_durations.setdefault(
                        (method.upper(), f"{route}|le={bucket}"), [0.0, 0.0]
                    )[1] += 1
            self._request_durations.setdefault(
                (method.upper(), f"{route}|le=+Inf"), [0.0, 0.0]
            )[1] += 1

    def record_rate_limit_rejection(self, *, scope: str, backend: str) -> None:
        with self._lock:
            self._rate_limit_rejections[(scope, backend)] += 1

    def render_prometheus(self) -> str:
        """输出 Prometheus Text Exposition；排序保证测试和人工审阅稳定。"""

        with self._lock:
            request_counts = dict(self._request_counts)
            request_durations = {
                key: value.copy() for key, value in self._request_durations.items()
            }
            rate_limit_rejections = dict(self._rate_limit_rejections)

        lines = [
            "# HELP rag_notes_http_requests_total "
            "HTTP requests grouped by route template and status class.",
            "# TYPE rag_notes_http_requests_total counter",
        ]
        for (method, route, status_class), count in sorted(request_counts.items()):
            lines.append(
                "rag_notes_http_requests_total"
                f'{{method="{method}",route="{route}",status_class="{status_class}"}} {count}'
            )
        lines.extend(
            [
                "# HELP rag_notes_http_request_duration_seconds "
                "HTTP request duration by route template.",
                "# TYPE rag_notes_http_request_duration_seconds histogram",
            ]
        )
        for (method, route), values in sorted(request_durations.items()):
            if "|le=" in route:
                base_route, boundary = route.split("|le=", 1)
                lines.append(
                    "rag_notes_http_request_duration_seconds_bucket"
                    f'{{method="{method}",route="{base_route}",le="{boundary}"}} {int(values[1])}'
                )
            else:
                lines.append(
                    "rag_notes_http_request_duration_seconds_sum"
                    f'{{method="{method}",route="{route}"}} {values[0]:.6f}'
                )
                lines.append(
                    "rag_notes_http_request_duration_seconds_count"
                    f'{{method="{method}",route="{route}"}} {int(values[1])}'
                )
        lines.extend(
            [
                "# HELP rag_notes_rate_limit_rejections_total "
                "Rejected requests grouped by fixed limit scope.",
                "# TYPE rag_notes_rate_limit_rejections_total counter",
            ]
        )
        for (scope, backend), count in sorted(rate_limit_rejections.items()):
            lines.append(
                "rag_notes_rate_limit_rejections_total"
                f'{{scope="{scope}",backend="{backend}"}} {count}'
            )
        return "\n".join(lines) + "\n"


_METRICS = MetricsRegistry()


def metrics_registry() -> MetricsRegistry:
    return _METRICS


def clear_metrics_registry() -> None:
    """测试前清理全局累加器，生产请求不得调用。"""

    global _METRICS
    _METRICS = MetricsRegistry()


def route_label(request: Request) -> str:
    """通过应用路由表匹配模板，避免中间件执行时尚未写入 ``scope.route``。"""

    method = request.method.upper()
    request_path = request.url.path
    for route in request.app.routes:
        # FastAPI 的包含路由在当前运行时封装为 _IncludedRouter，其模板候选不直接暴露 path。
        # 只以候选的 path_regex 和 methods 匹配，返回固定 path 模板，不使用任何路径参数值。
        candidates = getattr(route, "_effective_candidates", ())
        if isinstance(candidates, list):
            for candidate in candidates:
                candidate_path = getattr(candidate, "path", None)
                path_regex = getattr(candidate, "path_regex", None)
                methods = getattr(candidate, "methods", None)
                match_method = getattr(path_regex, "match", None)
                matches_path = bool(match_method(request_path)) if callable(match_method) else False
                if (
                    isinstance(candidate_path, str)
                    and matches_path
                    and isinstance(methods, set)
                    and method in methods
                ):
                    return candidate_path
        matcher = getattr(route, "matches", None)
        path = getattr(route, "path", None)
        if not callable(matcher) or not isinstance(path, str):
            continue
        matched = matcher(request.scope)
        if not isinstance(matched, tuple) or len(matched) != 2:
            continue
        match = matched[0]
        if getattr(match, "name", None) == "FULL":
            return path
    return "unknown"


def metrics_access_allowed(request: Request, settings: Settings) -> bool:
    """指标端点默认关闭；令牌为空时只允许显式开启后的内部网络访问。"""

    if not settings.metrics_enabled:
        return False
    if not settings.metrics_token:
        return True
    supplied = request.headers.get("X-Metrics-Token", "")
    return hmac.compare_digest(supplied, settings.metrics_token)


def observe_request(
    request: Request, *, status_code: int, started_at: float, settings: Settings
) -> None:
    """只在指标功能开启时采样，跳过指标抓取自身以避免噪声。"""

    if not settings.metrics_enabled or request.url.path == "/metrics":
        return
    metrics_registry().observe_http(
        method=request.method,
        route=route_label(request),
        status_code=status_code,
        duration_seconds=perf_counter() - started_at,
    )
