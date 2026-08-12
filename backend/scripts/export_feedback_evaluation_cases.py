"""把人工批准的反馈评测用例显式导出为待审阅的 JSON 文件。"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from uuid import uuid4

# 支持从 backend 目录直接执行，符合现有评测脚本的调用方式。
BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.application.feedback_evaluation_export_service import (  # noqa: E402
    FeedbackEvaluationExportService,
)
from app.core.database import get_session_factory  # noqa: E402
from app.core.workspace import ensure_workspace  # noqa: E402


def write_export_file(
    output: Path, payload: list[dict[str, object]], *, overwrite: bool
) -> str:
    """原子写入导出文件；默认拒绝覆盖，避免未审阅基线被静默替换。"""

    if not output.parent.is_dir():
        raise ValueError(f"输出目录不存在：{output.parent}")
    if output.exists() and not overwrite:
        raise FileExistsError(f"输出文件已存在：{output}；如确认覆盖请显式传入 --overwrite")
    serialized = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    temporary = output.with_name(f".{output.name}.{uuid4().hex}.tmp")
    try:
        temporary.write_text(serialized, encoding="utf-8")
        temporary.replace(output)
    finally:
        if temporary.exists():
            temporary.unlink()
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description="导出已批准的反馈回归评测用例")
    parser.add_argument("--knowledge-base-id", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--workspace-id", default=None)
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="显式允许覆盖已有文件；默认拒绝覆盖以保留人工审阅边界",
    )
    arguments = parser.parse_args()
    session_factory = get_session_factory()
    with session_factory() as session:
        # CLI 不经过 FastAPI 依赖注入，需自行设置 RLS 工作区上下文。
        workspace = ensure_workspace(session, workspace_id=arguments.workspace_id)
        payload = FeedbackEvaluationExportService().export_payload(
            session,
            knowledge_base_id=arguments.knowledge_base_id,
            workspace_id=workspace.id,
        )
    digest = write_export_file(arguments.output, payload, overwrite=arguments.overwrite)
    # 控制台只记录数量、文件名和内容哈希，避免把评测问题回显到操作日志。
    print(
        json.dumps(
            {
                "caseCount": len(payload),
                "output": str(arguments.output),
                "sha256": digest,
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
