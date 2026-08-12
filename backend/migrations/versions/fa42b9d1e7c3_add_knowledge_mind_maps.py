"""add editable knowledge mind maps

Revision ID: fa42b9d1e7c3
Revises: e89d7a2c1b45
Create Date: 2026-08-02 12:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "fa42b9d1e7c3"
down_revision: str | None = "e89d7a2c1b45"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "knowledge_mind_maps",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("workspace_id", sa.String(length=36), nullable=False),
        sa.Column("knowledge_base_id", sa.String(length=36), nullable=False),
        sa.Column("title", sa.String(length=240), nullable=False),
        sa.Column("graph", sa.JSON(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["knowledge_base_id"], ["knowledge_bases.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_knowledge_mind_maps_workspace_id", "knowledge_mind_maps", ["workspace_id"])
    op.create_index(
        "ix_knowledge_mind_maps_knowledge_base_id",
        "knowledge_mind_maps",
        ["knowledge_base_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_knowledge_mind_maps_knowledge_base_id", table_name="knowledge_mind_maps")
    op.drop_index("ix_knowledge_mind_maps_workspace_id", table_name="knowledge_mind_maps")
    op.drop_table("knowledge_mind_maps")
