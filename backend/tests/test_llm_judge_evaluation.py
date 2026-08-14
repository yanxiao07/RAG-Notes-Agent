"""可选 DeepEval 判分适配器的边界测试。"""

from __future__ import annotations

import pytest

from app.rag.llm_judge_evaluation import DeepEvalJudge, JudgeCase, parse_judge_cases
from scripts import evaluate_llm_judge


def make_case() -> JudgeCase:
    return JudgeCase(
        id="case-1",
        input="已脱敏问题",
        actual_output="已脱敏回答",
        retrieval_context=("已脱敏证据",),
    )


def test_missing_optional_dependency_is_explicitly_skipped(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(DeepEvalJudge, "_load_runtime", staticmethod(lambda: None))

    summary, results = DeepEvalJudge().evaluate([make_case()])

    assert summary.state == "skipped"
    assert results[0].error_code == "JUDGE_DEPENDENCY_UNAVAILABLE"
    assert results[0].answer_relevancy is None


def test_deepeval_results_are_aggregated_without_persisting_reason(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeTestCase:
        def __init__(self, **values: object) -> None:
            self.values = values

    class FakeAnswerMetric:
        score = 0.9
        reason = "这段理由不能进入报告"

        def measure(self, _case: FakeTestCase) -> None:
            return None

    class FakeFaithfulnessMetric:
        score = 0.8
        reason = "同样不能进入报告"

        def measure(self, _case: FakeTestCase) -> None:
            return None

    monkeypatch.setattr(
        DeepEvalJudge,
        "_load_runtime",
        staticmethod(lambda: (FakeTestCase, FakeAnswerMetric, FakeFaithfulnessMetric)),
    )

    summary, results = DeepEvalJudge().evaluate([make_case()])

    assert summary.state == "completed"
    assert summary.answer_relevancy == 0.9
    assert summary.faithfulness == 0.8
    assert results[0].state == "completed"
    assert not hasattr(results[0], "reason")


def test_invalid_judge_case_contract_is_rejected() -> None:
    with pytest.raises(ValueError, match="retrievalContext"):
        parse_judge_cases(
            [
                {
                    "id": "case-1",
                    "input": "question",
                    "actualOutput": "answer",
                    "retrievalContext": [],
                }
            ]
        )


def test_cli_requires_explicit_external_judge_authorization(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    monkeypatch.setattr(
        "sys.argv",
        [
            "evaluate_llm_judge.py",
            "--cases",
            str(tmp_path / "authorized-cases.json"),
            "--output",
            str(tmp_path / "report.json"),
        ],
    )

    with pytest.raises(ValueError, match="allow-external-judge"):
        evaluate_llm_judge.main()
