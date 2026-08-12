"""HTTP 写请求的幂等记录。

幂等记录按 workspace 隔离，保存请求指纹和已完成响应，避免客户端重试、网关重放
或网络超时造成重复创建文档、笔记和 Agent 审批操作。
"""

from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import JSON, DateTime, ForeignKey, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


def utc_now() -> datetime:
    return datetime.now(UTC)


class IdempotencyRecord(Base):
    """一次写请求的预留、完成和响应快照。"""

    __tablename__ = "idempotency_records"
    __table_args__ = (
        Index(
            "ux_idempotency_workspace_scope_key",
            "workspace_id",
            "operation_scope",
            "idempotency_key",
            unique=True,
        ),
        Index("ix_idempotency_records_expires_at", "expires_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    operation_scope: Mapped[str] = mapped_column(String(160), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    state: Mapped[str] = mapped_column(String(20), nullable=False, default="processing", index=True)
    status_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    response_json: Mapped[dict[str, object] | None] = mapped_column(JSON, nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )
