"""执行版本化 RAG 评测集，并输出可供 CI 比较的 JSON 指标。"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from dataclasses import asdict
from pathlib import Path
from time import perf_counter

from sqlalchemy import select

# 允许从 backend 目录直接执行本脚本，而不要求调用方手动设置 PYTHONPATH。
BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.application.configuration_service import ConfigurationService  # noqa: E402
from app.application.knowledge_service import KnowledgeService  # noqa: E402
from app.application.retrieval_service import RetrievalService  # noqa: E402
from app.core.database import get_session_factory  # noqa: E402
from app.core.workspace import ensure_workspace  # noqa: E402
from app.domain.knowledge.models import Document, Note  # noqa: E402
from app.rag.evaluation import EvaluationCase, evaluate_case, summarize  # noqa: E402
from app.rag.evaluation_manifest import build_evaluation_manifest  # noqa: E402


def write_report(path: Path, report: dict[str, object]) -> None:
    """原子写入纯 JSON 报告，避免运行日志污染可供 CI 比较的评测工件。"""

    if path.exists():
        raise ValueError(f"评测报告已存在，拒绝覆盖: {path}")
    if not path.parent.is_dir():
        raise ValueError(f"评测报告目录不存在: {path.parent}")
    serialized = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    temporary_path: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary_file:
            temporary_path = temporary_file.name
            temporary_file.write(serialized)
        os.replace(temporary_path, path)
    except Exception:
        if temporary_path:
            Path(temporary_path).unlink(missing_ok=True)
        raise


def load_raw_cases(path: Path) -> list[object]:
    raw_cases = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(raw_cases, list) or not raw_cases:
        raise ValueError("评测文件必须是非空 JSON 数组")
    return raw_cases


def parse_cases(raw_cases: list[object]) -> list[EvaluationCase]:
    cases: list[EvaluationCase] = []
    for item in raw_cases:
        if not isinstance(item, dict):
            raise ValueError("每条评测用例必须是对象")
        expected_titles = item.get("expectedSourceTitles")
        answerability = str(item.get("expectedAnswerability", "grounded"))
        if answerability not in {"grounded", "no_answer"}:
            raise ValueError("expectedAnswerability 仅支持 grounded 或 no_answer")
        if expected_titles is None:
            expected_titles = []
        if not isinstance(expected_titles, list):
            raise ValueError("expectedSourceTitles 必须是数组")
        if answerability == "grounded" and not expected_titles:
            raise ValueError("grounded 用例必须声明 expectedSourceTitles")
        if answerability == "no_answer" and expected_titles:
            raise ValueError("no_answer 用例不得声明 expectedSourceTitles")
        cases.append(
            EvaluationCase(
                id=str(item["id"]),
                query=str(item["query"]),
                expected_source_titles=tuple(str(title) for title in expected_titles),
                required_keywords=tuple(str(value) for value in item.get("requiredKeywords", [])),
                limit=int(item.get("limit", 5)),
                expected_answerability=answerability,
            )
        )
    return cases


def validate_expected_sources(
    session, *, cases: list[EvaluationCase], knowledge_base_id: str, workspace_id: str
) -> None:
    """拒绝与当前资产漂移的评测集，防止缺文档被错误归因成检索退化。"""

    document_titles = set(
        session.scalars(
            select(Document.title).where(
                Document.workspace_id == workspace_id,
                Document.knowledge_base_id == knowledge_base_id,
                Document.status == "indexed",
            )
        )
    )
    note_titles = set(
        session.scalars(
            select(Note.title).where(
                Note.workspace_id == workspace_id,
                Note.knowledge_base_id == knowledge_base_id,
                Note.status == "active",
            )
        )
    )
    available_titles = document_titles | note_titles
    grounded_cases = [case for case in cases if case.expected_answerability == "grounded"]
    missing_by_case = {
        case.id: sorted(set(case.expected_source_titles) - available_titles)
        for case in grounded_cases
    }
    missing_by_case = {case_id: titles for case_id, titles in missing_by_case.items() if titles}
    if missing_by_case:
        # 仅输出标注的来源标题和稳定用例 ID，不回显问题或任何证据正文。
        raise ValueError(f"评测集预期来源未在当前知识库可检索资产中找到: {missing_by_case}")


def main() -> int:
    parser = argparse.ArgumentParser(description="运行知识库检索离线评测")
    parser.add_argument("--knowledge-base-id", required=True)
    parser.add_argument("--cases", type=Path, required=True)
    parser.add_argument("--workspace-id", default=None)
    parser.add_argument("--output", type=Path, default=None, help="原子写入纯 JSON 评测报告")
    arguments = parser.parse_args()
    raw_cases = load_raw_cases(arguments.cases)
    cases = parse_cases(raw_cases)
    service = RetrievalService()
    session_factory = get_session_factory()
    results = []
    metrics = []
    retrieved_count = 0
    started = perf_counter()
    retriever_names: set[str] = set()
    cache_backends: set[str] = set()
    embedding_cache_hits = 0
    with session_factory() as session:
        # PostgreSQL 开启 RLS 时，离线脚本也必须显式建立租户上下文；不能依赖 API 请求依赖项。
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
        resolved_settings = ConfigurationService().resolve_settings(
            session, workspace_id=knowledge_base.workspace_id
        )
        manifest = build_evaluation_manifest(
            raw_cases=raw_cases,
            knowledge_base=knowledge_base,
            embedding_revision=ConfigurationService().embedding_revision(
                session, workspace_id=knowledge_base.workspace_id
            ),
            settings=resolved_settings,
        )
        for case in cases:
            evidences = service.search(
                session,
                knowledge_base_id=arguments.knowledge_base_id,
                workspace_id=workspace.id,
                query=case.query,
                limit=case.limit,
            )
            retrieved_count += len(evidences)
            retriever_names.add(service.retriever_name)
            cache_backends.add(service.cache_backend)
            embedding_cache_hits += int(service.embedding_cache_hit)
            case_metrics = evaluate_case(case, evidences)
            metrics.append(case_metrics)
            case_result = asdict(case_metrics)
            # 每条用例附带不含正文的诊断，方便比较 Metadata Boost、缓存和阶段延迟。
            case_result["diagnostics"] = asdict(service.diagnostics)
            results.append(case_result)
    summary = summarize(metrics, retrieved_count=retrieved_count)
    report: dict[str, object] = {
        "summary": asdict(summary),
        "latencyMs": round((perf_counter() - started) * 1000),
        "runtime": {
            "retrievers": sorted(retriever_names),
            "cacheBackends": sorted(cache_backends),
            "embeddingCacheHits": embedding_cache_hits,
        },
        "manifest": manifest,
        "cases": results,
    }
    if arguments.output:
        write_report(arguments.output, report)
        # 输出路径不含语料正文和查询，供自动化任务定位可审计工件。
        print(json.dumps({"output": str(arguments.output)}, ensure_ascii=False))
    else:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
