"""离线评测指标的确定性测试。"""

import pytest

from app.rag.evaluation import EvaluationCase, evaluate_case, summarize
from app.rag.retrieval import Evidence


def make_evidence(title: str, content: str) -> Evidence:
    return Evidence("document_chunk", title, title, content, 1.0, title)


def test_evaluation_calculates_ranking_and_coverage_metrics() -> None:
    case = EvaluationCase(
        id="rag-cache",
        query="缓存如何优化 RAG",
        expected_source_titles=("缓存设计", "向量检索"),
        required_keywords=("Redis", "TTL"),
    )
    metrics = evaluate_case(
        case,
        [
            make_evidence("向量检索", "向量检索使用 Redis 缓存并设置 TTL。"),
            make_evidence("缓存设计", "缓存策略。"),
        ],
    )
    assert metrics.top1_hit is True
    assert metrics.recall_at_k == 1.0
    assert metrics.reciprocal_rank == 1.0
    assert metrics.required_keyword_coverage == 1.0


def test_empty_evaluation_summary_is_rejected() -> None:
    with pytest.raises(ValueError, match="must not be empty"):
        summarize([], retrieved_count=0)


def test_no_answer_case_requires_empty_retrieval_and_does_not_affect_recall() -> None:
    case = EvaluationCase(
        id="no-answer",
        query="知识库不存在的问题",
        expected_source_titles=(),
        expected_answerability="no_answer",
    )
    rejected = evaluate_case(case, [])
    false_positive = evaluate_case(case, [make_evidence("无关文档", "无关内容")])

    summary = summarize([rejected, false_positive], retrieved_count=1)

    assert rejected.no_answer_correct is True
    assert false_positive.no_answer_correct is False
    assert summary.grounded_case_count == 0
    assert summary.no_answer_correct_rate == 0.5
