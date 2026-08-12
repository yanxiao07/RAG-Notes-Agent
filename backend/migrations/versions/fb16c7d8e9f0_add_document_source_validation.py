"""add document source validation metadata

Revision ID: fb16c7d8e9f0
Revises: b9c2d7e4f1a6
Create Date: 2026-08-08
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "fb16c7d8e9f0"
down_revision: str | None = "b9c2d7e4f1a6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 历史本地文件不需要访问外部来源；历史网页默认 pending，等待 Worker 或人工复核。
    op.add_column(
        "documents",
        sa.Column(
            "source_validation_state",
            sa.String(length=24),
            nullable=False,
            server_default="not_applicable",
        ),
    )
    op.add_column(
        "documents",
        sa.Column("source_is_approved", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column("documents", sa.Column("source_validated_at", sa.DateTime(timezone=True)))
    op.add_column("documents", sa.Column("source_validation_status_code", sa.Integer()))
    op.add_column("documents", sa.Column("source_redirect_url", sa.String(length=2_000)))
    op.add_column("documents", sa.Column("source_content_type", sa.String(length=255)))
    op.add_column("documents", sa.Column("source_validation_error_code", sa.String(length=80)))
    op.execute(
        "UPDATE documents SET source_validation_state = 'pending' "
        "WHERE source_type = 'webpage' AND source_url IS NOT NULL"
    )
    op.create_index(
        "ix_documents_source_validation_state", "documents", ["source_validation_state"]
    )


def downgrade() -> None:
    op.drop_index("ix_documents_source_validation_state", table_name="documents")
    op.drop_column("documents", "source_validation_error_code")
    op.drop_column("documents", "source_content_type")
    op.drop_column("documents", "source_redirect_url")
    op.drop_column("documents", "source_validation_status_code")
    op.drop_column("documents", "source_validated_at")
    op.drop_column("documents", "source_is_approved")
    op.drop_column("documents", "source_validation_state")
