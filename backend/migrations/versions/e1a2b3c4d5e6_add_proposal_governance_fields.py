"""add proposal risk, evidence snapshot and expiry

Revision ID: e1a2b3c4d5e6
Revises: d9f0a1b2c3e4
Create Date: 2026-08-05 14:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e1a2b3c4d5e6"
down_revision: str | None = "d9f0a1b2c3e4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "change_proposals",
        sa.Column("risk_level", sa.String(length=20), nullable=False, server_default="medium"),
    )
    op.add_column(
        "change_proposals",
        sa.Column("required_role", sa.String(length=40), nullable=False, server_default="approver"),
    )
    op.add_column(
        "change_proposals",
        sa.Column("evidence_snapshot", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
    )
    op.add_column("change_proposals", sa.Column("expires_at", sa.DateTime(timezone=True)))
    op.create_index("ix_change_proposals_expires_at", "change_proposals", ["expires_at"])


def downgrade() -> None:
    op.drop_index("ix_change_proposals_expires_at", table_name="change_proposals")
    op.drop_column("change_proposals", "expires_at")
    op.drop_column("change_proposals", "evidence_snapshot")
    op.drop_column("change_proposals", "required_role")
    op.drop_column("change_proposals", "risk_level")
