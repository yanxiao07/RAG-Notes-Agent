"""add encrypted workspace model configurations

Revision ID: e9a311b2f502
Revises: cde7e41980da
Create Date: 2026-08-01 18:10:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e9a311b2f502"
down_revision: str | None = "cde7e41980da"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 每个工作区至多一份配置，密钥字段只存储 Fernet 密文。
    op.create_table(
        "workspace_model_configurations",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("workspace_id", sa.String(length=36), nullable=False),
        sa.Column("llm_provider", sa.String(length=80), nullable=False),
        sa.Column("llm_model", sa.String(length=160), nullable=False),
        sa.Column("llm_base_url", sa.String(length=500), nullable=False),
        sa.Column("llm_api_key_encrypted", sa.String(length=2000), nullable=True),
        sa.Column("embedding_provider", sa.String(length=80), nullable=False),
        sa.Column("embedding_model", sa.String(length=160), nullable=False),
        sa.Column("embedding_base_url", sa.String(length=500), nullable=False),
        sa.Column("embedding_api_key_encrypted", sa.String(length=2000), nullable=True),
        sa.Column("embedding_dimensions", sa.Integer(), nullable=False),
        sa.Column("use_reranker", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("workspace_id"),
    )
    op.create_index(
        op.f("ix_workspace_model_configurations_workspace_id"),
        "workspace_model_configurations",
        ["workspace_id"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_workspace_model_configurations_workspace_id"),
        table_name="workspace_model_configurations",
    )
    op.drop_table("workspace_model_configurations")
