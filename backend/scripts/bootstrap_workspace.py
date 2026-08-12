"""初始化部署侧配置的默认工作区。

迁移只负责数据库结构，不隐式写入业务数据；Docker 启动栈在迁移完成后显式执行
本脚本，保证 API 与 Worker 共享同一个可复现的默认工作区。脚本可重复执行，已有
工作区不会被覆盖或重置。
"""

from __future__ import annotations

import json

# 导入全部 ORM 模型，确保跨模块 relationship 在独立脚本进程中完成注册。
from app.core.config import get_settings
from app.core.database import SessionLocal
from app.core.workspace import ensure_workspace
from app.domain import idempotency as _idempotency_models  # noqa: F401
from app.domain.agent import models as _agent_models  # noqa: F401
from app.domain.knowledge import models as _knowledge_models  # noqa: F401


def main() -> int:
    settings = get_settings()
    # ensure_workspace 只允许创建配置的默认工作区，并且会在创建后提交事务。
    with SessionLocal() as session:
        workspace = ensure_workspace(session, workspace_id=settings.default_workspace_id)
        print(
            json.dumps(
                {
                    "workspaceId": workspace.id,
                    "name": workspace.name,
                    "status": workspace.status,
                },
                ensure_ascii=False,
            )
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
