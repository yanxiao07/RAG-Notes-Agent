"""评测质量门禁的确定性测试。"""

from scripts.compare_retrieval_evaluations import evaluate_quality_gate


def make_comparison(
    *,
    top1_delta: float = 0.0,
    recall_delta: float = 0.0,
    mrr_delta: float = 0.0,
    keyword_delta: float = 0.0,
    noise_delta: float = 0.0,
    latency_delta: float = 0.0,
) -> dict[str, object]:
    return {
        "top1_rate": {"delta": top1_delta},
        "recall_at_k": {"delta": recall_delta},
        "mrr": {"delta": mrr_delta},
        "required_keyword_coverage": {"delta": keyword_delta},
        "noise_rate": {"delta": noise_delta},
        "latency_ms": {"delta": latency_delta},
    }


def test_quality_gate_requires_improvement_without_noise_or_latency_regression() -> None:
    passed, reasons = evaluate_quality_gate(
        make_comparison(mrr_delta=0.1),
    )

    assert passed is True
    assert reasons == []


def test_quality_gate_rejects_noise_and_latency_regression() -> None:
    passed, reasons = evaluate_quality_gate(
        make_comparison(top1_delta=0.1, noise_delta=0.01, latency_delta=12.0),
        max_latency_regression_ms=5.0,
    )

    assert passed is False
    assert reasons == ["噪声率上升", "延迟回归 12.00 ms，超过允许上限 5.00 ms"]
