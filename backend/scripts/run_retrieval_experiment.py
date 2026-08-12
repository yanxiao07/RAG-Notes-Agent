"""运行单个 RAG 检索策略实验，且不会修改工作区配置。"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path
from time import perf_counter

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.application.configuration_service import ConfigurationService  # noqa: E402
from app.application.knowledge_service import KnowledgeService  # noqa: E402
from app.application.retrieval_service import RetrievalService  # noqa: E402
from app.core.database import get_session_factory  # noqa: E402
from app.core.workspace import ensure_workspace  # noqa: E402
from app.rag.evaluation import evaluate_case, summarize  # noqa: E402
from app.rag.evaluation_manifest import build_evaluation_manifest  # noqa: E402
from app.rag.experiment_strategy import build_experiment_strategy, strategy_snapshot  # noqa: E402
from scripts.evaluate_retrieval import (  # noqa: E402
    load_raw_cases,
    parse_cases,
    validate_expected_sources,
    write_report,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="运行不改写工作区配置的检索策略实验")
    parser.add_argument("--knowledge-base-id", required=True)
    parser.add_argument("--cases", type=Path, required=True)
    parser.add_argument(
        "--strategy",
        choices=("baseline", "rewrite", "rerank", "current"),
        required=True,
    )
    parser.add_argument("--workspace-id", default=None)
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args()

    raw_cases = load_raw_cases(arguments.cases)
    cases = parse_cases(raw_cases)
    service = RetrievalService()
    results = []
    metrics_by_case = []
    runtime_states: set[tuple[str, bool, bool, bool]] = set()
    started = perf_counter()
    with get_session_factory()() as session:
        workspace = ensure_workspace(session, workspace_id=arguments.workspace_id)
        knowledge_base = KnowledgeService().get_knowledge_base(
            session,
            arguments.knowledge_base_id,
            workspace_id=workspace.id,
        )
        validate_expected_sources(
            session,
            cases=cases,
            knowledge_base_id=knowledge_base.id,
            workspace_id=workspace.id,
        )
        configured = ConfigurationService().resolve_settings(session, workspace_id=workspace.id)
        strategy = build_experiment_strategy(arguments.strategy, configured)
        manifest = build_evaluation_manifest(
            raw_cases=raw_cases,
            knowledge_base=knowledge_base,
            embedding_revision=ConfigurationService().embedding_revision(
                session, workspace_id=workspace.id
            ),
            settings=strategy.settings,
        )
        retrieved_count = 0
        for case in cases:
            evidence = service.search(
                session,
                knowledge_base_id=knowledge_base.id,
                workspace_id=workspace.id,
                query=case.query,
                limit=case.limit,
                settings_override=strategy.settings,
            )
            retrieved_count += len(evidence)
            metrics = evaluate_case(case, evidence)
            metrics_by_case.append(metrics)
            result = asdict(metrics)
            # 仅保存执行状态，不输出任何问题、候选正文或模型原始响应。
            result["runtime"] = {
                "queryRewriter": service.query_rewriter,
                "queryRewriteFallback": service.query_rewrite_fallback,
                "reranker": service.reranker_name,
                "rerankerFallback": service.reranker_fallback,
                "rerankerCacheHit": service.reranker_cache_hit,
                "answerabilityGate": service.diagnostics.answerability_gate_enabled,
                "answerabilityReason": service.diagnostics.answerability_reason,
                "answerabilityMatchedSignals": service.diagnostics.answerability_matched_signals,
            }
            results.append(result)
            runtime_states.add(
                (
                    service.query_rewriter,
                    service.query_rewrite_fallback,
                    service.reranker_fallback,
                    service.reranker_cache_hit,
                )
            )
    report: dict[str, object] = {
        "summary": asdict(summarize(metrics_by_case, retrieved_count=retrieved_count)),
        "latencyMs": round((perf_counter() - started) * 1000),
        "manifest": manifest,
        "experiment": {
            **strategy_snapshot(strategy),
            "actualRuntimeStates": [
                {
                    "queryRewriter": state[0],
                    "queryRewriteFallback": state[1],
                    "rerankerFallback": state[2],
                    "rerankerCacheHit": state[3],
                }
                for state in sorted(runtime_states)
            ],
        },
        "cases": results,
    }
    write_report(arguments.output, report)
    print(json.dumps({"output": str(arguments.output)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
