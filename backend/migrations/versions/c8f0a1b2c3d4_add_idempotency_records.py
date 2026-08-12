"""add persistent idempotency records

Revision ID: c8f0a1b2c3d4
Revises: b7e4f1a2c9d0
Create Date: 2026-08-04 15:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c8f0a1b2c3d4"
down_revision: str | None = "b7e4f1a2c9d0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "idempotency_records",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("workspace_id", sa.String(length=36), nullable=False),
        sa.Column("idempotency_key", sa.String(length=255), nullable=False),
        sa.Column("operation_scope", sa.String(length=160), nullable=False),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column("state", sa.String(length=20), nullable=False),
        sa.Column("status_code", sa.Integer(), nullable=True),
        sa.Column("response_json", sa.JSON(), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ux_idempotency_workspace_scope_key",
        "idempotency_records",
        ["workspace_id", "operation_scope", "idempotency_key"],
        unique=True,
    )
    op.create_index("ix_idempotency_records_workspace_id", "idempotency_records", ["workspace_id"])
    op.create_index("ix_idempotency_records_state", "idempotency_records", ["state"])
    op.create_index("ix_idempotency_records_expires_at", "idempotency_records", ["expires_at"])


def downgrade() -> None:
    op.drop_index("ix_idempotency_records_expires_at", table_name="idempotency_records")
    op.drop_index("ix_idempotency_records_state", table_name="idempotency_records")
    op.drop_index("ix_idempotency_records_workspace_id", table_name="idempotency_records")
    op.drop_index("ux_idempotency_workspace_scope_key", table_name="idempotency_records")
    op.drop_table("idempotency_records")
