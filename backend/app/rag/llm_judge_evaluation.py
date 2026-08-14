"""可选 LLM-as-a-judge 评测适配器。

确定性引用、拒答和安全门禁仍是项目的主质量门禁。此模块只提供 DeepEval
补充信号，并且不持久化问题、答案、检索正文或判分理由，避免把评测样本
意外扩散到报告、日志或 CI 工件中。
"""

from __future__ import annotations

from dataclasses import dataclass
from importlib import import_module
from typing import Any


@dataclass(frozen=True, slots=True)
class JudgeCase:
    """仅在当前进程内使用的已脱敏 LLM 判分样本。"""

    id: str
    input: str
    actual_output: str
    retrieval_context: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class JudgeCaseResult:
    """可写入报告的最小结果，刻意不包含模型理由或原始文本。"""

    case_id: str
    state: str
    answer_relevancy: float | None
    faithfulness: float | None
    error_code: str | None


@dataclass(frozen=True, slots=True)
class JudgeSummary:
    provider: str
    state: str
    case_count: int
    completed_count: int
    failed_count: int
    skipped_count: int
    answer_relevancy: float | None
    faithfulness: float | None


class DeepEvalJudge:
    """DeepEval 的延迟加载适配器，未安装时不影响核心 RAG 运行。"""

    provider_name = "deepeval"

    def evaluate(self, cases: list[JudgeCase]) -> tuple[JudgeSummary, list[JudgeCaseResult]]:
        runtime = self._load_runtime()
        if runtime is None:
            results = [
                JudgeCaseResult(
                    case_id=case.id,
                    state="skipped",
                    answer_relevancy=None,
                    faithfulness=None,
                    error_code="JUDGE_DEPENDENCY_UNAVAILABLE",
                )
                for case in cases
            ]
            return self._summarize(results), results

        test_case_type, answer_relevancy_metric_type, faithfulness_metric_type = runtime
        results: list[JudgeCaseResult] = []
        for case in cases:
            try:
                # 每条用例创建独立 Metric，避免可变状态或并发缓存跨用例污染分数。
                test_case = test_case_type(
                    input=case.input,
                    actual_output=case.actual_output,
                    retrieval_context=list(case.retrieval_context),
                )
                answer_metric = answer_relevancy_metric_type()
                faithfulness_metric = faithfulness_metric_type()
                answer_metric.measure(test_case)
                faithfulness_metric.measure(test_case)
                results.append(
                    JudgeCaseResult(
                        case_id=case.id,
                        state="completed",
                        answer_relevancy=self._score(answer_metric),
                        faithfulness=self._score(faithfulness_metric),
                        error_code=None,
                    )
                )
            except Exception:
                # 外部评估员失败仅反映为稳定状态码，异常与模型理由可能含有原文，不能外泄。
                results.append(
                    JudgeCaseResult(
                        case_id=case.id,
                        state="failed",
                        answer_relevancy=None,
                        faithfulness=None,
                        error_code="JUDGE_EXECUTION_FAILED",
                    )
                )
        return self._summarize(results), results

    @staticmethod
    def _load_runtime() -> tuple[type[Any], type[Any], type[Any]] | None:
        try:
            metrics = import_module("deepeval.metrics")
            test_case_module = import_module("deepeval.test_case")
            return (
                test_case_module.LLMTestCase,
                metrics.AnswerRelevancyMetric,
                metrics.FaithfulnessMetric,
            )
        except (ImportError, AttributeError):
            return None

    @staticmethod
    def _score(metric: Any) -> float:
        score = float(metric.score)
        if not 0.0 <= score <= 1.0:
            raise ValueError("judge score must be between zero and one")
        return round(score, 4)

    def _summarize(self, results: list[JudgeCaseResult]) -> JudgeSummary:
        completed = [result for result in results if result.state == "completed"]
        skipped_count = sum(result.state == "skipped" for result in results)
        failed_count = sum(result.state == "failed" for result in results)
        if completed:
            answer_relevancy = round(
                sum(result.answer_relevancy or 0.0 for result in completed) / len(completed), 4
            )
            faithfulness = round(
                sum(result.faithfulness or 0.0 for result in completed) / len(completed), 4
            )
            state = "completed" if failed_count == 0 else "completed_with_failures"
        elif skipped_count == len(results):
            answer_relevancy = None
            faithfulness = None
            state = "skipped"
        else:
            answer_relevancy = None
            faithfulness = None
            state = "failed"
        return JudgeSummary(
            provider=self.provider_name,
            state=state,
            case_count=len(results),
            completed_count=len(completed),
            failed_count=failed_count,
            skipped_count=skipped_count,
            answer_relevancy=answer_relevancy,
            faithfulness=faithfulness,
        )


def parse_judge_cases(payload: object) -> list[JudgeCase]:
    """解析本地评测样本；输入文件不得提交到仓库或上传到服务端。"""

    if not isinstance(payload, list) or not payload:
        raise ValueError("LLM 判分文件必须是非空 JSON 数组。")
    cases: list[JudgeCase] = []
    seen_ids: set[str] = set()
    for item in payload:
        if not isinstance(item, dict):
            raise ValueError("每条 LLM 判分用例必须是对象。")
        case_id = _required_text(item, "id")
        if case_id in seen_ids:
            raise ValueError("LLM 判分用例 ID 不能重复。")
        seen_ids.add(case_id)
        contexts = item.get("retrievalContext")
        if not isinstance(contexts, list) or not contexts:
            raise ValueError("retrievalContext 必须是非空字符串数组。")
        normalized_contexts = tuple(_text(value, "retrievalContext") for value in contexts)
        cases.append(
            JudgeCase(
                id=case_id,
                input=_required_text(item, "input"),
                actual_output=_required_text(item, "actualOutput"),
                retrieval_context=normalized_contexts,
            )
        )
    return cases


def _required_text(item: dict[str, object], field: str) -> str:
    value = item.get(field)
    return _text(value, field)


def _text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} 必须是非空字符串。")
    return value.strip()
