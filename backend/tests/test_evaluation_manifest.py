"""检索评测清单的可复现性与脱敏边界测试。"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app.rag.evaluation_manifest import (
    build_cases_sha256,
    build_evaluation_manifest,
    compare_manifest_compatibility,
)
from scripts import evaluate_retrieval
from scripts.compare_retrieval_evaluations import validate_manifest_compatibility
from scripts.evaluate_retrieval import write_report


def make_settings() -> SimpleNamespace:
    """仅提供清单白名单字段，避免测试意外依赖完整运行时配置。"""

    return SimpleNamespace(
        embedding_provider="openai_compatible",
        embedding_model="text-embedding-3-small",
        embedding_dimensions=1536,
        llm_provider="openai_compatible",
        llm_model="qwen-plus",
        reranker_provider="dashscope_compatible",
        reranker_model="qwen3-rerank",
        retrieval_mode="hybrid",
        query_rewrite_enabled=True,
        query_rewrite_multi_query_enabled=True,
        query_rewrite_max_variants=4,
        query_rewrite_max_subqueries=2,
        query_rewrite_max_synonyms=2,
        reranker_enabled=True,
        reranker_candidate_limit=20,
        dynamic_top_k_enabled=True,
        dynamic_top_k_min_candidates=3,
        dynamic_top_k_max_candidates=10,
        dynamic_top_k_score_gap_threshold=0.12,
        dynamic_top_k_target_source_coverage=2,
        dynamic_top_k_budget_ratio=0.8,
        metadata_boost_enabled=True,
        metadata_title_boost=0.12,
        metadata_section_boost=0.08,
        metadata_source_type_boost=0.03,
        metadata_max_boost=0.2,
        entity_retrieval_enabled=True,
        entity_retrieval_max_entities=12,
        entity_retrieval_candidate_limit=30,
        tag_retrieval_enabled=False,
        tag_retrieval_max_tags=12,
        tag_retrieval_candidate_limit=30,
        parent_child_enabled=True,
        parent_child_window=1,
        parent_child_max_characters=2400,
        # 模拟运行时敏感字段：清单白名单不应读取或输出它们。
        llm_api_key="llm-secret",
        embedding_api_key="embedding-secret",
        reranker_api_key="reranker-secret",
        llm_base_url="https://private.example/v1",
    )


def make_manifest(
    *, cases: list[object] | None = None, embedding_revision: int = 3
) -> dict[str, object]:
    knowledge_base = SimpleNamespace(
        id="knowledge-base-1",
        index_status="ready",
        embedding_revision=embedding_revision,
        graph_status="ready",
        graph_revision=7,
    )
    return build_evaluation_manifest(
        raw_cases=cases
        or [
            {
                "id": "case-1",
                "query": "这是一条不应出现在清单中的评测问题",
                "expectedSourceTitles": ["文档 A"],
            }
        ],
        knowledge_base=knowledge_base,
        embedding_revision=embedding_revision,
        settings=make_settings(),
    )


def test_cases_digest_is_stable_and_respects_case_order() -> None:
    cases = [{"id": "1", "query": "a"}, {"id": "2", "query": "b"}]

    assert build_cases_sha256(cases) == build_cases_sha256(cases)
    assert build_cases_sha256(cases) != build_cases_sha256(list(reversed(cases)))


def test_evaluation_report_is_atomic_and_refuses_overwrite(tmp_path) -> None:
    output = tmp_path / "report.json"

    write_report(output, {"summary": {"case_count": 1}})

    assert json.loads(output.read_text(encoding="utf-8")) == {
        "summary": {"case_count": 1}
    }
    with pytest.raises(ValueError, match="拒绝覆盖"):
        write_report(output, {"summary": {"case_count": 2}})


def test_manifest_does_not_include_case_text_or_sensitive_configuration() -> None:
    manifest = make_manifest()
    serialized = str(manifest)

    assert manifest["caseCount"] == 1
    assert "这是一条不应出现在清单中的评测问题" not in serialized
    assert "llm-secret" not in serialized
    assert "embedding-secret" not in serialized
    assert "reranker-secret" not in serialized
    assert "private.example" not in serialized


def test_manifest_compatibility_rejects_different_evaluation_set_and_embedding_revision() -> None:
    baseline = make_manifest()
    changed_cases = make_manifest(cases=[{"id": "case-2", "query": "other"}])
    changed_embedding = make_manifest(embedding_revision=4)

    assert "评测集 SHA-256 不一致" in compare_manifest_compatibility(baseline, changed_cases)
    assert "向量索引版本或状态不一致" in compare_manifest_compatibility(
        baseline, changed_embedding
    )


def test_comparison_requires_compatible_manifest_unless_exploration_override() -> None:
    baseline = {"summary": {}, "manifest": make_manifest()}
    candidate = {
        "summary": {},
        "manifest": make_manifest(cases=[{"id": "case-2", "query": "other"}]),
    }

    allowed, reasons = validate_manifest_compatibility(
        baseline, candidate, allow_incompatible=False
    )
    assert allowed is False
    assert reasons == ["评测集 SHA-256 不一致"]

    allowed, reasons = validate_manifest_compatibility(
        baseline, candidate, allow_incompatible=True
    )
    assert allowed is True
    assert reasons == ["评测集 SHA-256 不一致"]


def test_evaluation_cli_establishes_workspace_scope_before_reading_knowledge_base(
    tmp_path, monkeypatch
) -> None:
    """离线 CLI 在 RLS 数据库中也应走与 HTTP 请求一致的工作区初始化。"""

    cases_path = tmp_path / "cases.json"
    cases_path.write_text(
        '[{"id":"case-1","query":"query","expectedSourceTitles":["source"]}]',
        encoding="utf-8",
    )
    workspace = SimpleNamespace(id="workspace-1")
    knowledge_base = SimpleNamespace(
        id="knowledge-base-1",
        workspace_id=workspace.id,
        index_status="ready",
        embedding_revision=1,
        graph_status="ready",
        graph_revision=1,
    )
    settings = make_settings()
    sentinel_session = object()

    class SessionFactory:
        def __call__(self):
            return self

        def __enter__(self):
            return sentinel_session

        def __exit__(self, *_args):
            return None

    monkeypatch.setattr(
        "sys.argv",
        [
            "evaluate_retrieval.py",
            "--knowledge-base-id",
            knowledge_base.id,
            "--workspace-id",
            workspace.id,
            "--cases",
            str(cases_path),
        ],
    )
    with (
        patch.object(evaluate_retrieval, "get_session_factory", return_value=SessionFactory()),
        patch.object(
            evaluate_retrieval, "ensure_workspace", return_value=workspace
        ) as ensure_scope,
        patch.object(
            evaluate_retrieval.KnowledgeService,
            "get_knowledge_base",
            return_value=knowledge_base,
        ),
        patch.object(
            evaluate_retrieval.ConfigurationService,
            "resolve_settings",
            return_value=settings,
        ),
        patch.object(
            evaluate_retrieval.ConfigurationService,
            "embedding_revision",
            return_value=1,
        ),
        patch.object(evaluate_retrieval, "validate_expected_sources"),
        patch.object(evaluate_retrieval.RetrievalService, "search", return_value=[]),
    ):
        assert evaluate_retrieval.main() == 0

    ensure_scope.assert_called_once_with(sentinel_session, workspace_id=workspace.id)


def test_evaluation_rejects_missing_expected_source_title(
    session_factory,
) -> None:
    """资产版本漂移应在发起模型调用前失败，不能污染召回基线。"""

    # 本测试只验证纯判定约束，数据库查询适配由 API/脚本集成测试覆盖。
    cases = [
        evaluate_retrieval.EvaluationCase(
            id="missing-source",
            query="不会被回显",
            expected_source_titles=("不存在的文档",),
        )
    ]
    with session_factory() as session:
        try:
            evaluate_retrieval.validate_expected_sources(
                session,
                cases=cases,
                knowledge_base_id="knowledge-base",
                workspace_id="workspace",
            )
        except ValueError as exc:
            assert "missing-source" in str(exc)
            assert "不存在的文档" in str(exc)
            assert "不会被回显" not in str(exc)
        else:
            raise AssertionError("缺失预期来源时必须拒绝评测")
