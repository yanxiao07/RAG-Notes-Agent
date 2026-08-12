"""运行可重复的检索延迟基准，不输出问题和证据正文。

该脚本用于比较 SQLite 本地检索与 PostgreSQL/pgvector 生产检索的 P50/P95，
并记录缓存命中、检索器和诊断阶段耗时。它不是压力生成器，生产压测应在隔离环境
通过 k6/Locust 发送受控请求，避免把测试流量混入用户会话。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from statistics import mean
from time import perf_counter
from typing import Any

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.application.retrieval_service import RetrievalService  # noqa: E402
from app.core.database import get_session_factory  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="运行 RAG 检索延迟基准")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--queries", type=Path, help="JSON 字符串数组或包含 query 字段的对象数组")
    source.add_argument("--query", action="append", help="单条查询；可重复传入")
    parser.add_argument("--knowledge-base-id", required=True)
    parser.add_argument("--workspace-id", default=None)
    parser.add_argument("--limit", type=int, default=5)
    parser.add_argument("--iterations", type=int, default=3)
    parser.add_argument("--warmup", type=int, default=1)
    parser.add_argument("--output", type=Path, default=None)
    arguments = parser.parse_args()
    if arguments.limit < 1 or arguments.iterations < 1 or arguments.warmup < 0:
        raise SystemExit("limit 和 iterations 必须大于 0，warmup 不能小于 0")
    queries = _load_queries(arguments.queries, arguments.query)
    result = _run_benchmark(
        queries,
        knowledge_base_id=arguments.knowledge_base_id,
        workspace_id=arguments.workspace_id,
        limit=arguments.limit,
        iterations=arguments.iterations,
        warmup=arguments.warmup,
    )
    serialized = json.dumps(result, ensure_ascii=False, indent=2)
    if arguments.output:
        arguments.output.write_text(serialized + "\n", encoding="utf-8")
    print(serialized)
    return 0


def _load_queries(path: Path | None, inline: list[str] | None) -> list[str]:
    if inline:
        return [query.strip() for query in inline if query.strip()]
    if path is None:
        raise ValueError("必须提供 queries 文件或 query 参数")
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise ValueError("queries 文件必须是数组")
    queries: list[str] = []
    for item in raw:
        if isinstance(item, str) and item.strip():
            queries.append(item.strip())
        elif (
            isinstance(item, dict) and isinstance(item.get("query"), str) and item["query"].strip()
        ):
            queries.append(item["query"].strip())
    if not queries:
        raise ValueError("queries 文件没有有效查询")
    return queries


def _run_benchmark(
    queries: list[str],
    *,
    knowledge_base_id: str,
    workspace_id: str | None,
    limit: int,
    iterations: int,
    warmup: int,
) -> dict[str, Any]:
    service = RetrievalService()
    session_factory = get_session_factory()
    with session_factory() as session:
        for _ in range(warmup):
            for query in queries:
                service.search(
                    session,
                    knowledge_base_id=knowledge_base_id,
                    workspace_id=workspace_id,
                    query=query,
                    limit=limit,
                )
        samples: list[float] = []
        cache_hits = 0
        retrievers: set[str] = set()
        cache_backends: set[str] = set()
        diagnostics: list[dict[str, float]] = []
        for _ in range(iterations):
            for query in queries:
                started = perf_counter()
                service.search(
                    session,
                    knowledge_base_id=knowledge_base_id,
                    workspace_id=workspace_id,
                    query=query,
                    limit=limit,
                )
                samples.append(round((perf_counter() - started) * 1000, 3))
                cache_hits += int(service.embedding_cache_hit)
                retrievers.add(service.retriever_name)
                cache_backends.add(service.cache_backend)
                diagnostics.append(
                    {
                        "retrievalMs": service.diagnostics.hybrid_retrieval_ms,
                        "rerankMs": service.diagnostics.rerank_ms,
                        "totalMs": service.diagnostics.total_ms,
                    }
                )
    return {
        "queryCount": len(queries),
        "iterations": iterations,
        "sampleCount": len(samples),
        "latencyMs": _latency_summary(samples),
        "retrievers": sorted(retrievers),
        "cacheBackends": sorted(cache_backends),
        "embeddingCacheHitRate": round(cache_hits / max(len(samples), 1), 4),
        "diagnosticsMs": {
            key: round(mean(item[key] for item in diagnostics), 3)
            for key in ("retrievalMs", "rerankMs", "totalMs")
        },
    }


def _latency_summary(samples: list[float]) -> dict[str, float]:
    if not samples:
        raise ValueError("benchmark samples must not be empty")
    ordered = sorted(samples)
    return {
        "min": ordered[0],
        "mean": round(mean(ordered), 3),
        "p50": _percentile(ordered, 0.50),
        "p95": _percentile(ordered, 0.95),
        "max": ordered[-1],
    }


def _percentile(ordered: list[float], quantile: float) -> float:
    """使用线性插值，样本少时仍能稳定比较候选方案。"""

    if not ordered or not 0 <= quantile <= 1:
        raise ValueError("invalid percentile input")
    position = (len(ordered) - 1) * quantile
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = position - lower
    return round(ordered[lower] + (ordered[upper] - ordered[lower]) * fraction, 3)


if __name__ == "__main__":
    raise SystemExit(main())
