"""写请求幂等控制服务。

服务只负责记录生命周期，不参与具体领域写入。预留记录单独提交，业务事务完成后
再写入响应快照；业务异常会删除 processing 记录，允许客户端安全重试。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.errors import (
    IdempotencyConflictError,
    IdempotencyInProgressError,
    InvalidIdempotencyKeyError,
)
from app.core.logging import get_logger
from app.domain.idempotency import IdempotencyRecord

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class IdempotencyContext:
    """本次请求的幂等上下文；``replay`` 不为空时无需再次执行领域操作。"""

    record_id: str
    workspace_id: str
    operation_scope: str
    idempotency_key: str
    request_hash: str
    replay: bool = False
    response_json: dict[str, object] | None = None
    status_code: int | None = None


class IdempotencyService:
    """实现 24 小时请求体去重和响应重放。"""

    def __init__(self, *, ttl_seconds: int = 86_400) -> None:
        self.ttl_seconds = ttl_seconds

    def start(
        self,
        session: Session,
        *,
        workspace_id: str,
        operation_scope: str,
        idempotency_key: str | None,
        request_payload: Any,
    ) -> IdempotencyContext | None:
        """预留幂等键；未提供请求头时返回 ``None``，保持向后兼容。"""

        key = self._validate_key(idempotency_key)
        if key is None:
            return None
        request_hash = hash_request_payload(request_payload)
        now = datetime.now(UTC)
        session.execute(
            delete(IdempotencyRecord).where(
                IdempotencyRecord.expires_at <= now,
                IdempotencyRecord.workspace_id == workspace_id,
            )
        )
        existing = session.scalar(
            select(IdempotencyRecord).where(
                IdempotencyRecord.workspace_id == workspace_id,
                IdempotencyRecord.operation_scope == operation_scope,
                IdempotencyRecord.idempotency_key == key,
            )
        )
        if existing is not None:
            return self._existing_context(existing, request_hash)

        record = IdempotencyRecord(
            workspace_id=workspace_id,
            idempotency_key=key,
            operation_scope=operation_scope,
            request_hash=request_hash,
            state="processing",
            expires_at=now + timedelta(seconds=self.ttl_seconds),
        )
        session.add(record)
        try:
            session.commit()
        except IntegrityError:
            session.rollback()
            existing = session.scalar(
                select(IdempotencyRecord).where(
                    IdempotencyRecord.workspace_id == workspace_id,
                    IdempotencyRecord.operation_scope == operation_scope,
                    IdempotencyRecord.idempotency_key == key,
                )
            )
            if existing is None:
                raise
            return self._existing_context(existing, request_hash)
        session.refresh(record)
        logger.info(
            "idempotency_reserved",
            workspace_id=workspace_id,
            operation_scope=operation_scope,
            request_hash=request_hash,
        )
        return IdempotencyContext(
            record_id=record.id,
            workspace_id=workspace_id,
            operation_scope=operation_scope,
            idempotency_key=key,
            request_hash=request_hash,
        )

    def complete(
        self,
        session: Session,
        context: IdempotencyContext | None,
        *,
        status_code: int,
        response_json: dict[str, object],
    ) -> None:
        """将业务响应写入快照；调用方随后提交整个业务事务。"""

        if context is None or context.replay:
            return
        record = session.get(IdempotencyRecord, context.record_id)
        if record is None:
            raise IdempotencyInProgressError(message="幂等记录已失效，请重试")
        if record.request_hash != context.request_hash or record.state != "processing":
            raise IdempotencyConflictError()
        record.state = "completed"
        record.status_code = status_code
        record.response_json = response_json
        logger.info(
            "idempotency_completed",
            workspace_id=context.workspace_id,
            operation_scope=context.operation_scope,
            request_hash=context.request_hash,
            status_code=status_code,
        )

    def release(self, session: Session, context: IdempotencyContext | None) -> None:
        """业务失败时释放预留，避免失败请求永久阻塞同一个幂等键。"""

        if context is None or context.replay:
            return
        try:
            # 业务层可能因约束异常使 Session 进入 failed 状态，先回滚才能安全释放预留。
            session.rollback()
            record = session.get(IdempotencyRecord, context.record_id)
            if record is not None and record.state == "processing":
                session.delete(record)
            session.commit()
            logger.info(
                "idempotency_released",
                workspace_id=context.workspace_id,
                operation_scope=context.operation_scope,
                request_hash=context.request_hash,
            )
        except Exception:
            session.rollback()
            raise

    @staticmethod
    def _validate_key(value: str | None) -> str | None:
        if value is None:
            return None
        key = value.strip()
        if not key or len(key) > 255:
            raise InvalidIdempotencyKeyError()
        return key

    @staticmethod
    def _existing_context(record: IdempotencyRecord, request_hash: str) -> IdempotencyContext:
        if record.request_hash != request_hash:
            logger.warning(
                "idempotency_conflict",
                workspace_id=record.workspace_id,
                operation_scope=record.operation_scope,
                request_hash=request_hash,
            )
            raise IdempotencyConflictError()
        if record.state == "completed":
            logger.info(
                "idempotency_replayed",
                workspace_id=record.workspace_id,
                operation_scope=record.operation_scope,
                request_hash=request_hash,
            )
            return IdempotencyContext(
                record_id=record.id,
                workspace_id=record.workspace_id,
                operation_scope=record.operation_scope,
                idempotency_key=record.idempotency_key,
                request_hash=request_hash,
                replay=True,
                response_json=record.response_json or {},
                status_code=record.status_code or 200,
            )
        raise IdempotencyInProgressError()


def hash_request_payload(payload: Any) -> str:
    """对结构化请求做稳定哈希，不把原始内容写入日志或幂等表。"""

    serialized = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()
