"""显式授权执行可选 DeepEval 离线判分，并输出脱敏报告。"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.rag.llm_judge_evaluation import DeepEvalJudge, parse_judge_cases  # noqa: E402
from scripts.evaluate_retrieval import write_report  # noqa: E402


def load_cases(path: Path) -> list[object]:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, list):
        raise ValueError("LLM 判分文件必须是 JSON 数组。")
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description="运行可选 DeepEval 离线判分")
    parser.add_argument("--cases", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--allow-external-judge",
        action="store_true",
        help="确认当前样本已获授权且可发送给配置的外部判分模型",
    )
    parser.add_argument(
        "--require-judge",
        action="store_true",
        help="依赖缺失或任一用例判分失败时使用非零退出码",
    )
    arguments = parser.parse_args()
    if not arguments.allow_external_judge:
        raise ValueError("必须显式传入 --allow-external-judge 才会调用外部判分模型。")

    cases = parse_judge_cases(load_cases(arguments.cases))
    summary, results = DeepEvalJudge().evaluate(cases)
    # 报告只包含最小结果，不能把问题、答案、检索正文和评估员理由写入磁盘。
    report: dict[str, object] = {
        "judge": asdict(summary),
        "cases": [asdict(result) for result in results],
        "qualityGateEligible": False,
        "notice": "LLM judge 是补充观察指标，不能替代确定性引用、拒答和安全门禁。",
    }
    write_report(arguments.output, report)
    print(
        json.dumps(
            {
                "output": str(arguments.output),
                "judgeState": summary.state,
                "completedCount": summary.completed_count,
                "failedCount": summary.failed_count,
                "skippedCount": summary.skipped_count,
            },
            ensure_ascii=False,
        )
    )
    if arguments.require_judge and summary.state != "completed":
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
