"""add ingestion queue leases and retry scheduling

Revision ID: b9c2d7e4f1a6
Revises: f3b4c5d6e7f8
Create Date: 2026-08-05 18:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b9c2d7e4f1a6"
down_revision: str | None = "f3b4c5d6e7f8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """为已有任务补齐可领取时间和 Worker 租约字段。"""

    op.add_column(
        "ingestion_jobs",
        sa.Column(
            "available_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    # SQLite 不允许向已有表直接添加 CURRENT_TIMESTAMP 非常量默认值；先回填历史任务，
    # 再通过 batch_alter_table 收紧约束，兼容 SQLite 和 PostgreSQL 存量库。
    op.execute(
        sa.text("UPDATE ingestion_jobs SET available_at = created_at WHERE available_at IS NULL")
    )
    with op.batch_alter_table("ingestion_jobs") as batch_op:
        batch_op.alter_column(
            "available_at",
            existing_type=sa.DateTime(timezone=True),
            nullable=False,
        )
    op.add_column(
        "ingestion_jobs",
        sa.Column("locked_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "ingestion_jobs",
        sa.Column("locked_by", sa.String(length=120), nullable=True),
    )
    op.add_column(
        "ingestion_jobs",
        sa.Column("last_error_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_ingestion_jobs_queue_schedule",
        "ingestion_jobs",
        ["workspace_id", "state", "available_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_ingestion_jobs_queue_schedule", table_name="ingestion_jobs")
    op.drop_column("ingestion_jobs", "last_error_at")
    op.drop_column("ingestion_jobs", "locked_by")
    op.drop_column("ingestion_jobs", "locked_at")
    op.drop_column("ingestion_jobs", "available_at")
