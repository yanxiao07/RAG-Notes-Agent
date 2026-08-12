"""网页来源定时复核 Worker。

与入库 Worker 分离，避免外部网站超时挤占解析和向量化任务。该 Worker 默认不产生网络
请求，只有 ``APP_SOURCE_VALIDATION_RECHECK_ENABLED=true`` 时才按配置周期运行。
"""

from __future__ import annotations

import argparse
import time

from app.application.source_validation_service import SourceValidationService
from app.core.config import get_settings
from app.core.database import SessionLocal
from app.core.logging import get_logger

logger = get_logger(__name__)


def run_once(*, workspace_id: str) -> int:
    """执行一个受限批次；异常被隔离，下一轮周期仍可继续。"""

    with SessionLocal() as session:
        try:
            return SourceValidationService().revalidate_due_documents(
                session,
                workspace_id=workspace_id,
            )
        except Exception:
            session.rollback()
            # 不记录 URL、正文或外部响应，只保留批次失败这一运维信号。
            logger.exception("source_validation_batch_failed")
            return 0


def main() -> int:
    settings = get_settings()
    parser = argparse.ArgumentParser(description="运行网页来源定时复核 Worker")
    parser.add_argument("--workspace-id", default=settings.default_workspace_id)
    parser.add_argument("--once", action="store_true")
    arguments = parser.parse_args()

    while True:
        run_once(workspace_id=arguments.workspace_id)
        if arguments.once:
            return 0
        time.sleep(settings.source_validation_recheck_poll_seconds)


if __name__ == "__main__":
    raise SystemExit(main())
