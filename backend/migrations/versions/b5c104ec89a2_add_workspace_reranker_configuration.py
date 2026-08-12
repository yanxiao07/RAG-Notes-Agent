"""add workspace reranker configuration

Revision ID: b5c104ec89a2
Revises: a1f7c82b3d91
Create Date: 2026-08-01 23:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b5c104ec89a2"
down_revision: str | None = "a1f7c82b3d91"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("workspace_model_configurations") as batch_op:
        batch_op.add_column(
            sa.Column(
                "reranker_provider",
                sa.String(length=80),
                nullable=False,
                server_default="rule",
            )
        )
        batch_op.add_column(
            sa.Column("reranker_model", sa.String(length=160), nullable=False, server_default="")
        )
        batch_op.add_column(
            sa.Column("reranker_base_url", sa.String(length=500), nullable=False, server_default="")
        )
        batch_op.add_column(sa.Column("reranker_api_key_encrypted", sa.String(length=2000)))


def downgrade() -> None:
    with op.batch_alter_table("workspace_model_configurations") as batch_op:
        batch_op.drop_column("reranker_api_key_encrypted")
        batch_op.drop_column("reranker_base_url")
        batch_op.drop_column("reranker_model")
        batch_op.drop_column("reranker_provider")
