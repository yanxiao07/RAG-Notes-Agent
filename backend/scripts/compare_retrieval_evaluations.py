"""比较两次离线 RAG 评测结果，输出可审计的指标增量。"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Mapping
from pathlib import Path

# 与评测脚本保持一致：支持在 backend 目录中直接运行该 CLI。
BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.rag.evaluation_manifest import compare_manifest_compatibility  # noqa: E402

METRICS = (
    "top1_rate",
    "recall_at_k",
    "mrr",
    "required_keyword_coverage",
    "noise_rate",
    "no_answer_correct_rate",
)
POSITIVE_METRICS = (
    "top1_rate",
    "recall_at_k",
    "mrr",
    "required_keyword_coverage",
    "no_answer_correct_rate",
)


def load_report(path: Path) -> dict[str, object]:
    # PowerShell 的 UTF-8 输出可能带 BOM，评测对比应兼容常见 CI/本地写入方式。
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict) or not isinstance(payload.get("summary"), dict):
        raise ValueError(f"{path} 不是有效的检索评测结果。")
    return payload


def validate_manifest_compatibility(
    baseline: dict[str, object], candidate: dict[str, object], *, allow_incompatible: bool
) -> tuple[bool, list[str]]:
    """默认拒绝无法归因的比较；显式豁免只能用于人工探索，不能作为质量门禁。"""

    baseline_manifest = baseline.get("manifest")
    candidate_manifest = candidate.get("manifest")
    if not isinstance(baseline_manifest, dict) or not isinstance(candidate_manifest, dict):
        reasons = ["缺少评测清单，无法验证结果可比性"]
    else:
        reasons = compare_manifest_compatibility(baseline_manifest, candidate_manifest)
    return (not reasons or allow_incompatible), reasons


def compare_reports(baseline: dict[str, object], candidate: dict[str, object]) -> dict[str, object]:
    """生成可序列化的指标差值，供 CLI、CI 和后续报告复用。"""

    baseline_summary = baseline["summary"]
    candidate_summary = candidate["summary"]
    if not isinstance(baseline_summary, Mapping) or not isinstance(candidate_summary, Mapping):
        raise ValueError("评测结果缺少 summary 对象。")
    comparison: dict[str, object] = {
        metric: {
            "baseline": baseline_summary.get(metric),
            "candidate": candidate_summary.get(metric),
            "delta": round(
                as_float(candidate_summary.get(metric)) - as_float(baseline_summary.get(metric)),
                4,
            ),
        }
        for metric in METRICS
    }
    baseline_latency = as_float(baseline.get("latencyMs"))
    candidate_latency = as_float(candidate.get("latencyMs"))
    comparison["latency_ms"] = {
        "baseline": baseline_latency,
        "candidate": candidate_latency,
        "delta": round(candidate_latency - baseline_latency, 2),
    }
    return comparison


def as_float(value: object) -> float:
    """评测报告来自 JSON，非数值字段统一按 0 处理而非让 CLI 类型错误中断。"""

    return float(value) if isinstance(value, (int, float, str)) else 0.0


def evaluate_quality_gate(
    comparison: dict[str, object], *, max_latency_regression_ms: float = 0.0
) -> tuple[bool, list[str]]:
    """检查质量门禁：至少一项质量提升，噪声不恶化，延迟不超过允许上限。"""

    reasons: list[str] = []
    deltas = {
        metric: as_float(metric_comparison.get("delta"))
        for metric in METRICS
        if isinstance(metric_comparison := comparison.get(metric), Mapping)
    }
    if not any(deltas.get(metric, 0.0) > 0 for metric in POSITIVE_METRICS):
        reasons.append("Top1/Recall@K/MRR/关键词覆盖/拒答正确率均未提升")
    if deltas.get("noise_rate", 0.0) > 0:
        reasons.append("噪声率上升")
    latency = comparison.get("latency_ms")
    if isinstance(latency, Mapping) and as_float(latency.get("delta")) > max_latency_regression_ms:
        reasons.append(
            f"延迟回归 {as_float(latency.get('delta')):.2f} ms，超过允许上限 "
            f"{max_latency_regression_ms:.2f} ms"
        )
    return not reasons, reasons


def main() -> int:
    parser = argparse.ArgumentParser(description="比较 RAG 基线与候选方案")
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument(
        "--check",
        action="store_true",
        help="启用质量门禁，失败时以非零状态退出，适合 CI",
    )
    parser.add_argument(
        "--allow-incompatible-manifest",
        action="store_true",
        help="仅用于人工探索：允许比较不同评测对象，结果会被标记为不可作为质量门禁",
    )
    parser.add_argument(
        "--max-latency-regression-ms",
        type=float,
        default=0.0,
        help="允许的端到端延迟回归上限，默认不允许回归",
    )
    arguments = parser.parse_args()
    baseline = load_report(arguments.baseline)
    candidate = load_report(arguments.candidate)
    manifests_compatible, manifest_reasons = validate_manifest_compatibility(
        baseline,
        candidate,
        allow_incompatible=arguments.allow_incompatible_manifest,
    )
    comparison = compare_reports(baseline, candidate)
    comparison["manifest"] = {
        "compatible": not manifest_reasons,
        "reasons": manifest_reasons,
        "qualityGateEligible": not manifest_reasons,
        "overrideUsed": bool(manifest_reasons and arguments.allow_incompatible_manifest),
    }
    if not manifests_compatible:
        comparison["gate"] = {"passed": False, "reasons": manifest_reasons}
        print(json.dumps(comparison, ensure_ascii=False, indent=2))
        return 1
    passed, reasons = evaluate_quality_gate(
        comparison,
        max_latency_regression_ms=arguments.max_latency_regression_ms,
    )
    if manifest_reasons:
        # 显式豁免也不可通过 CI 质量门禁，防止不同比较对象被误写为性能提升。
        passed = False
        reasons = [*manifest_reasons, "已使用不兼容清单豁免，结果不可作为质量门禁"]
    comparison["gate"] = {"passed": passed, "reasons": reasons}
    print(json.dumps(comparison, ensure_ascii=False, indent=2))
    return 0 if not arguments.check or passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
