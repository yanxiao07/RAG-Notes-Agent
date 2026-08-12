"""持久入库队列的租约、退避和死信状态机测试。"""

from datetime import UTC, timedelta

import pytest

from app.application.ingestion_service import IngestionService
from app.application.knowledge_service import KnowledgeService
from app.core.errors import ProcessingError
from app.core.workspace import ensure_workspace
from app.domain.knowledge.models import utc_now
from app.workers.polling import reclaim_stale_jobs


def _create_broken_job(session):
    knowledge_base = KnowledgeService().create_knowledge_base(
        session, name="队列测试库", description=None
    )
    document, job = IngestionService().create_document(
        session,
        knowledge_base_id=knowledge_base.id,
        title="队列失败.md",
        source_type="markdown",
        raw_content="# 队列失败",
        parser_name="markdown",
    )
    job.config_snapshot = {**job.config_snapshot, "parser": "missing-parser"}
    session.commit()
    return document, job


def test_failed_ingestion_is_scheduled_and_eventually_dead_lettered(session_factory) -> None:
    with session_factory() as session:
        document, job = _create_broken_job(session)
        service = IngestionService()

        for expected_state in ("failed", "failed", "dead_letter"):
            with pytest.raises(ProcessingError):
                service.run_job(session, job_id=job.id)
            session.refresh(job)
            session.refresh(document)
            assert job.state == expected_state
            assert job.last_error_at is not None
            assert job.locked_at is None
            assert job.available_at >= job.last_error_at
            assert document.status == "failed"

        retried = service.retry_document(session, document_id=document.id)
        assert retried.state == "queued"
        assert retried.attempts == 0
        assert retried.last_error_at is None
        assert retried.locked_at is None


def test_reclaim_stale_worker_lease_requeues_job(session_factory) -> None:
    with session_factory() as session:
        document, job = _create_broken_job(session)
        workspace_id = ensure_workspace(session).id
        now = utc_now()
        job.state = "running"
        job.locked_at = now - timedelta(hours=1)
        job.locked_by = "crashed-worker"
        document.status = "processing"
        session.commit()

        reclaimed = reclaim_stale_jobs(
            session,
            workspace_id=workspace_id,
            now=now,
            lease_seconds=60,
            max_attempts=3,
        )

        assert reclaimed == 1
        session.refresh(job)
        session.refresh(document)
        assert job.state == "failed"
        assert job.error_code == "WORKER_LEASE_EXPIRED"
        assert job.locked_at is None
        assert job.locked_by is None
        assert job.available_at.replace(tzinfo=UTC) == now
        assert document.status == "failed"
