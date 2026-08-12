"""add workspace hybrid query router setting

Revision ID: f8e7c9d2a1b0
Revises: fa42b9d1e7c3
Create Date: 2026-08-02 16:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f8e7c9d2a1b0"
down_revision: str | None = "fa42b9d1e7c3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("workspace_model_configurations") as batch_op:
        batch_op.add_column(
            sa.Column("use_query_router", sa.Boolean(), nullable=False, server_default=sa.false())
        )


def downgrade() -> None:
    with op.batch_alter_table("workspace_model_configurations") as batch_op:
        batch_op.drop_column("use_query_router")
