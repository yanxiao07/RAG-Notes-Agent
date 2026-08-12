"""执行脱敏查询路由离线评测，不触发检索、回答生成或会话写入。"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
from dataclasses import asdict
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.application.configuration_service import ConfigurationService  # noqa: E402
from app.core.config import Settings, get_settings  # noqa: E402
from app.core.database import get_session_factory  # noqa: E402
from app.core.workspace import ensure_workspace  # noqa: E402
from app.rag.query_routing import HybridQueryRouter, RuleQueryRouter  # noqa: E402
from app.rag.routing_evaluation import (  # noqa: E402
    evaluate_routing_case,
    parse_routing_cases,
    summarize_routing,
)


def write_report(path: Path, report: dict[str, object]) -> None:
    """原子写入报告，并拒绝覆盖已有的可审计工件。"""

    if path.exists():
        raise ValueError(f"评测报告已存在，拒绝覆盖: {path}")
    if not path.parent.is_dir():
        raise ValueError(f"评测报告目录不存在: {path.parent}")
    temporary_path: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as file:
            temporary_path = file.name
            file.write(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
        os.replace(temporary_path, path)
    except Exception:
        if temporary_path:
            Path(temporary_path).unlink(missing_ok=True)
        raise


def load_raw_cases(path: Path) -> list[object]:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, list):
        raise ValueError("路由评测文件必须是 JSON 数组")
    return payload


def resolve_hybrid_settings(workspace_id: str | None) -> tuple[Settings, str]:
    """读取工作区持久化配置，使离线评测与实际问答使用同一模型策略。"""

    with get_session_factory()() as session:
        workspace = ensure_workspace(session, workspace_id=workspace_id)
        settings = ConfigurationService().resolve_settings(session, workspace_id=workspace.id)
        return settings, workspace.id


def main() -> int:
    parser = argparse.ArgumentParser(description="运行查询路由离线评测")
    parser.add_argument("--cases", type=Path, required=True)
    parser.add_argument("--router", choices=("rule", "hybrid"), default="rule")
    parser.add_argument("--workspace-id", default=None, help="仅 hybrid LLM 路由的缓存命名空间")
    parser.add_argument("--output", type=Path, default=None, help="原子写入纯 JSON 评测报告")
    arguments = parser.parse_args()

    raw_cases = load_raw_cases(arguments.cases)
    cases = parse_routing_cases(raw_cases)
    if arguments.router == "rule":
        router = RuleQueryRouter()
        results = [evaluate_routing_case(case, router.route(case.query)) for case in cases]
        settings = get_settings()
        workspace_id = None
    else:
        settings, workspace_id = resolve_hybrid_settings(arguments.workspace_id)
        router = HybridQueryRouter()
        results = [
            evaluate_routing_case(
                case,
                router.route(
                    case.query,
                    settings=settings,
                    workspace_id=workspace_id,
                ),
            )
            for case in cases
        ]

    # 报告仅使用评测文件哈希和脱敏结果；不输出 query、Prompt 或模型请求内容。
    report: dict[str, object] = {
        "summary": asdict(summarize_routing(results)),
        "manifest": {
            "caseSetSha256": hashlib.sha256(
                json.dumps(raw_cases, ensure_ascii=False, sort_keys=True).encode("utf-8")
            ).hexdigest(),
            "routerMode": arguments.router,
            "routerEnabled": settings.query_router_enabled,
            "workspaceConfigured": arguments.router == "hybrid",
            "llmModel": settings.llm_model if arguments.router == "hybrid" else None,
        },
        "cases": [asdict(result) for result in results],
    }
    if arguments.output:
        write_report(arguments.output, report)
        print(json.dumps({"output": str(arguments.output)}, ensure_ascii=False))
    else:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
