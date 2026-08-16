"""轻量级入库轮询 Worker。

这是 Docker 开发/验收环境的无额外队列依赖实现。Worker 固定绑定一个工作区，使用
``FOR UPDATE SKIP LOCKED`` 领取到期任务，提交短租约后再用独立 Session 执行；过期租约
会被回收，失败任务按指数退避重试，超过上限进入 ``dead_letter``。
"""

from __future__ import annotations

import argparse
import os
import socket
import time
from collections.abc import Iterator
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from datetime import timedelta

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.application.ingestion_service import IngestionService
from app.core.config import get_settings
from app.core.database import SessionLocal
from app.core.logging import get_logger
from app.core.workspace import ensure_workspace
from app.domain.knowledge.models import IngestionJob, utc_now

logger = get_logger(__name__)


def iter_pending_jobs(session: Session, *, workspace_id: str, now=None) -> Iterator[IngestionJob]:
    """按到期时间领取当前工作区任务；多 Worker 时跳过已锁定行。"""

    effective_now = now or utc_now()
    statement = (
        select(IngestionJob)
        .where(
            IngestionJob.workspace_id == workspace_id,
            IngestionJob.available_at <= effective_now,
            or_(IngestionJob.state == "queued", IngestionJob.state == "failed"),
        )
        .order_by(IngestionJob.available_at.asc(), IngestionJob.created_at.asc())
        .limit(1)
        .with_for_update(skip_locked=True)
    )
    job = session.scalar(statement)
    if job is not None:
        yield job


def reclaim_stale_jobs(
    session: Session,
    *,
    workspace_id: str,
    now=None,
    lease_seconds: int,
    max_attempts: int,
) -> int:
    """回收 Worker 崩溃后遗留的 running 任务，避免任务永久卡住。"""

    effective_now = now or utc_now()
    cutoff = effective_now - timedelta(seconds=lease_seconds)
    statement = select(IngestionJob).where(
        IngestionJob.workspace_id == workspace_id,
        IngestionJob.state.in_(("claimed", "running")),
        IngestionJob.locked_at.is_not(None),
        IngestionJob.locked_at < cutoff,
    )
    stale_jobs = list(session.scalars(statement))
    for job in stale_jobs:
        job.state = "dead_letter" if job.attempts >= max_attempts else "failed"
        job.error_code = "WORKER_LEASE_EXPIRED"
        job.error_message = "Worker 租约已过期，任务已重新排队。"
        job.last_error_at = effective_now
        job.available_at = effective_now
        job.locked_at = None
        job.locked_by = None
        job.document.status = "failed"
    if stale_jobs:
        session.commit()
    return len(stale_jobs)


def _default_worker_id() -> str:
    return f"{socket.gethostname()}:{os.getpid()}"


def claim_next_job(*, workspace_id: str, worker_id: str) -> str | None:
    """短事务领取一个任务，并将其置为 ``claimed`` 防止其他 Worker 重复领取。"""

    settings = get_settings()
    with SessionLocal() as claim_session:
        ensure_workspace(claim_session, workspace_id=workspace_id, create_default=False)
        reclaim_stale_jobs(
            claim_session,
            workspace_id=workspace_id,
            lease_seconds=settings.ingestion_lease_seconds,
            max_attempts=settings.ingestion_max_attempts,
        )
        job = next(iter_pending_jobs(claim_session, workspace_id=workspace_id), None)
        if job is None:
            claim_session.rollback()
            return None
        # SKIP LOCKED 仅在本事务内有效，提交前必须修改状态，才能使租约跨事务生效。
        job.state = "claimed"
        job.locked_at = utc_now()
        job.locked_by = worker_id
        claim_session.commit()
        # 仅记录任务与 Worker 标识，供并发验收和运维排障使用，不记录文档标题或正文。
        logger.info("polling_ingestion_job_claimed", job_id=job.id, worker_id=worker_id)
        return job.id


def _execute_claimed_job(*, job_id: str, workspace_id: str, worker_id: str) -> None:
    """每个并行任务使用独立 Session，避免 SQLAlchemy Session 跨线程共享。"""

    with SessionLocal() as work_session:
        try:
            IngestionService().run_job(
                work_session,
                job_id=job_id,
                workspace_id=workspace_id,
                worker_id=worker_id,
            )
        except Exception:
            logger.exception(
                "polling_ingestion_job_failed",
                job_id=job_id,
                worker_id=worker_id,
            )


def run_once(*, workspace_id: str, worker_id: str | None = None) -> bool:
    """同步执行一个已领取任务，保留给单次维护命令和回归测试使用。"""

    effective_worker_id = worker_id or _default_worker_id()
    job_id = claim_next_job(workspace_id=workspace_id, worker_id=effective_worker_id)
    if job_id is None:
        return False
    _execute_claimed_job(
        job_id=job_id,
        workspace_id=workspace_id,
        worker_id=effective_worker_id,
    )
    return True


def run_available_jobs(*, workspace_id: str, worker_id: str, concurrency: int) -> int:
    """以有界线程池处理当前可领取任务，空闲时立即返回给轮询循环。

    每个槽位只领取一个任务，完成后才继续领取，避免在模型网关或数据库变慢时无限
    抢占任务。多进程/多容器的唯一性仍由 ``claimed`` 状态和 PostgreSQL 行锁保证。
    """

    processed = 0
    pending: set[Future[None]] = set()
    with ThreadPoolExecutor(max_workers=concurrency, thread_name_prefix="ingestion") as executor:
        while True:
            while len(pending) < concurrency:
                job_id = claim_next_job(workspace_id=workspace_id, worker_id=worker_id)
                if job_id is None:
                    break
                pending.add(
                    executor.submit(
                        _execute_claimed_job,
                        job_id=job_id,
                        workspace_id=workspace_id,
                        worker_id=worker_id,
                    )
                )
                processed += 1
            if not pending:
                return processed
            done, pending = wait(pending, return_when=FIRST_COMPLETED)
            for future in done:
                future.result()


def main() -> int:
    parser = argparse.ArgumentParser(description="运行 RAG Notes Agent 入库轮询 Worker")
    parser.add_argument("--workspace-id", default=get_settings().default_workspace_id)
    parser.add_argument("--interval-seconds", type=float, default=2.0)
    parser.add_argument("--worker-id", default=_default_worker_id())
    parser.add_argument(
        "--concurrency",
        type=int,
        default=get_settings().ingestion_worker_concurrency,
        help="单个 Worker 进程的并行入库任务数。",
    )
    parser.add_argument("--once", action="store_true")
    arguments = parser.parse_args()
    if arguments.interval_seconds <= 0:
        raise SystemExit("interval-seconds 必须大于 0")
    if arguments.concurrency <= 0:
        raise SystemExit("concurrency 必须大于 0")
    while True:
        handled = run_available_jobs(
            workspace_id=arguments.workspace_id,
            worker_id=arguments.worker_id,
            concurrency=arguments.concurrency,
        )
        if arguments.once:
            return 0
        if not handled:
            time.sleep(arguments.interval_seconds)


if __name__ == "__main__":
    raise SystemExit(main())
