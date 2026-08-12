"""与具体向量库解耦的离线检索评测指标。"""

from __future__ import annotations

from dataclasses import dataclass
from statistics import mean

from app.rag.retrieval import Evidence


@dataclass(frozen=True, slots=True)
class EvaluationCase:
    id: str
    query: str
    expected_source_titles: tuple[str, ...]
    required_keywords: tuple[str, ...] = ()
    limit: int = 5
    # grounded 评估目标文档是否召回；no_answer 评估无关问题是否能保持空候选。
    # 后者不等同于生成层的语义拒答，避免把未经标注的分数阈值伪装成可靠结论。
    expected_answerability: str = "grounded"


@dataclass(frozen=True, slots=True)
class CaseMetrics:
    case_id: str
    expected_answerability: str
    top1_hit: bool
    recall_at_k: float
    reciprocal_rank: float
    required_keyword_coverage: float
    noise_count: int
    no_answer_correct: bool | None


@dataclass(frozen=True, slots=True)
class EvaluationSummary:
    case_count: int
    top1_rate: float
    recall_at_k: float
    mrr: float
    required_keyword_coverage: float
    noise_rate: float
    grounded_case_count: int
    no_answer_case_count: int
    no_answer_correct_rate: float | None


def evaluate_case(case: EvaluationCase, evidences: list[Evidence]) -> CaseMetrics:
    """计算单条评测的召回、排序、关键词覆盖与低信息噪声指标。"""

    expected = {title.strip().lower() for title in case.expected_source_titles if title.strip()}
    retrieved_titles = [item.title.strip().lower() for item in evidences]
    if case.expected_answerability == "no_answer":
        return CaseMetrics(
            case_id=case.id,
            expected_answerability=case.expected_answerability,
            top1_hit=False,
            recall_at_k=0.0,
            reciprocal_rank=0.0,
            required_keyword_coverage=1.0,
            noise_count=sum(_is_low_information(item) for item in evidences),
            # 只有候选为空时，生成层才能稳定触发既有的“证据为空”拒答契约。
            no_answer_correct=not evidences,
        )
    matched_titles = expected.intersection(retrieved_titles)
    first_rank = next(
        (index for index, title in enumerate(retrieved_titles, start=1) if title in expected), None
    )
    evidence_text = "\n".join(item.content.lower() for item in evidences)
    keyword_hits = sum(
        keyword.strip().lower() in evidence_text
        for keyword in case.required_keywords
        if keyword.strip()
    )
    keyword_total = len([keyword for keyword in case.required_keywords if keyword.strip()])
    return CaseMetrics(
        case_id=case.id,
        expected_answerability=case.expected_answerability,
        top1_hit=bool(retrieved_titles and retrieved_titles[0] in expected),
        recall_at_k=len(matched_titles) / len(expected) if expected else 0.0,
        reciprocal_rank=1 / first_rank if first_rank else 0.0,
        required_keyword_coverage=keyword_hits / keyword_total if keyword_total else 1.0,
        noise_count=sum(_is_low_information(item) for item in evidences),
        no_answer_correct=None,
    )


def summarize(metrics: list[CaseMetrics], *, retrieved_count: int) -> EvaluationSummary:
    """聚合指标；空评测集必须由调用方拒绝，避免产生误导性的 100% 结果。"""

    if not metrics:
        raise ValueError("evaluation cases must not be empty")
    denominator = max(retrieved_count, 1)
    grounded = [item for item in metrics if item.expected_answerability == "grounded"]
    no_answer = [item for item in metrics if item.expected_answerability == "no_answer"]
    return EvaluationSummary(
        case_count=len(metrics),
        # 排序指标只统计有标准答案的用例，不能让无答案用例人为拉低召回率。
        top1_rate=mean(metric.top1_hit for metric in grounded) if grounded else 0.0,
        recall_at_k=mean(metric.recall_at_k for metric in grounded) if grounded else 0.0,
        mrr=mean(metric.reciprocal_rank for metric in grounded) if grounded else 0.0,
        required_keyword_coverage=(
            mean(metric.required_keyword_coverage for metric in grounded) if grounded else 0.0
        ),
        noise_rate=sum(metric.noise_count for metric in metrics) / denominator,
        grounded_case_count=len(grounded),
        no_answer_case_count=len(no_answer),
        no_answer_correct_rate=(
            mean(bool(metric.no_answer_correct) for metric in no_answer) if no_answer else None
        ),
    )


def _is_low_information(evidence: Evidence) -> bool:
    return not evidence.title.strip() or len("".join(evidence.content.split())) < 12
