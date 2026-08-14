"""运行 GraphRAG 社区索引的脱敏结构化评测。"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.core.database import get_session_factory  # noqa: E402
from app.core.workspace import ensure_workspace  # noqa: E402
from app.rag.community_evaluation import (  # noqa: E402
    community_quality_gate,
    evaluate_community_index,
)
from scripts.evaluate_retrieval import write_report  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="评测 GraphRAG 社区索引可追溯性")
    parser.add_argument("--knowledge-base-id", required=True)
    parser.add_argument("--workspace-id", default=None)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--require-algorithm",
        choices=("connected_components", "louvain"),
        default=None,
        help="仅在已完成对应依赖与基准验证后用于强制算法门禁",
    )
    parser.add_argument("--check", action="store_true", help="门禁失败时使用非零退出码")
    arguments = parser.parse_args()
    with get_session_factory()() as session:
        workspace = ensure_workspace(session, workspace_id=arguments.workspace_id)
        evaluation = evaluate_community_index(
            session,
            knowledge_base_id=arguments.knowledge_base_id,
            workspace_id=workspace.id,
        )
    passed, reasons = community_quality_gate(
        evaluation,
        required_algorithm=arguments.require_algorithm,
    )
    report = {
        "communityIndex": evaluation.as_report(),
        "gate": {"passed": passed, "reasons": reasons},
        "qualityGateEligible": True,
    }
    write_report(arguments.output, report)
    print(json.dumps({"output": str(arguments.output), "passed": passed}, ensure_ascii=False))
    return 0 if not arguments.check or passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
