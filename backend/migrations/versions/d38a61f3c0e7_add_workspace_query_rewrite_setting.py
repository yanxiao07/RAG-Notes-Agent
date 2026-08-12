"""add workspace query rewrite setting

Revision ID: d38a61f3c0e7
Revises: b5c104ec89a2
Create Date: 2026-08-02 09:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d38a61f3c0e7"
down_revision: str | None = "b5c104ec89a2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("workspace_model_configurations") as batch_op:
        batch_op.add_column(
            sa.Column("use_query_rewrite", sa.Boolean(), nullable=False, server_default=sa.false())
        )


def downgrade() -> None:
    with op.batch_alter_table("workspace_model_configurations") as batch_op:
        batch_op.drop_column("use_query_rewrite")
