"""add controlled business tag governance

Revision ID: f9b1c2d3e4f5
Revises: c2e7d9a4b8f1, f3b4c5d6e7f8
Create Date: 2026-08-09
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f9b1c2d3e4f5"
down_revision: tuple[str, str] = ("c2e7d9a4b8f1", "f3b4c5d6e7f8")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _enable_workspace_rls(table: str) -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    policy = f"{table}_workspace_isolation"
    op.execute(f'ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY')
    op.execute(f'ALTER TABLE "{table}" FORCE ROW LEVEL SECURITY')
    op.execute(f'DROP POLICY IF EXISTS "{policy}" ON "{table}"')
    op.execute(
        f'''CREATE POLICY "{policy}" ON "{table}"
            USING (workspace_id = current_setting('app.current_workspace_id', true))
            WITH CHECK (workspace_id = current_setting('app.current_workspace_id', true))'''
    )


def _disable_workspace_rls(table: str) -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    policy = f"{table}_workspace_isolation"
    op.execute(f'DROP POLICY IF EXISTS "{policy}" ON "{table}"')
    op.execute(f'ALTER TABLE "{table}" NO FORCE ROW LEVEL SECURITY')
    op.execute(f'ALTER TABLE "{table}" DISABLE ROW LEVEL SECURITY')


def upgrade() -> None:
    op.create_table(
        "knowledge_tags",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("workspace_id", sa.String(length=36), nullable=False),
        sa.Column("knowledge_base_id", sa.String(length=36), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("normalized_name", sa.String(length=120), nullable=False),
        sa.Column("description", sa.String(length=500), nullable=True),
        sa.Column("state", sa.String(length=20), nullable=False, server_default="active"),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["knowledge_base_id"], ["knowledge_bases.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "workspace_id",
            "knowledge_base_id",
            "normalized_name",
            name="uq_knowledge_tag_name",
        ),
    )
    op.create_index("ix_knowledge_tags_workspace_id", "knowledge_tags", ["workspace_id"])
    op.create_index("ix_knowledge_tags_knowledge_base_id", "knowledge_tags", ["knowledge_base_id"])
    op.create_index("ix_knowledge_tags_state", "knowledge_tags", ["state"])
    op.create_index("ix_knowledge_tags_kb_state", "knowledge_tags", ["knowledge_base_id", "state"])

    op.create_table(
        "knowledge_tag_assignments",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("workspace_id", sa.String(length=36), nullable=False),
        sa.Column("knowledge_base_id", sa.String(length=36), nullable=False),
        sa.Column("tag_id", sa.String(length=36), nullable=False),
        sa.Column("asset_type", sa.String(length=20), nullable=False),
        sa.Column("asset_id", sa.String(length=36), nullable=False),
        sa.Column("state", sa.String(length=20), nullable=False, server_default="pending"),
        sa.Column("source", sa.String(length=30), nullable=False, server_default="manual"),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("reviewer_id", sa.String(length=100), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["knowledge_base_id"], ["knowledge_bases.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["tag_id"], ["knowledge_tags.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "workspace_id",
            "knowledge_base_id",
            "tag_id",
            "asset_type",
            "asset_id",
            name="uq_knowledge_tag_assignment",
        ),
    )
    op.create_index(
        "ix_knowledge_tag_assignments_workspace_id", "knowledge_tag_assignments", ["workspace_id"]
    )
    op.create_index(
        "ix_knowledge_tag_assignments_knowledge_base_id",
        "knowledge_tag_assignments",
        ["knowledge_base_id"],
    )
    op.create_index("ix_knowledge_tag_assignments_tag_id", "knowledge_tag_assignments", ["tag_id"])
    op.create_index("ix_knowledge_tag_assignments_state", "knowledge_tag_assignments", ["state"])
    op.create_index(
        "ix_knowledge_tag_assignments_review",
        "knowledge_tag_assignments",
        ["knowledge_base_id", "state", "created_at"],
    )
    op.create_index(
        "ix_knowledge_tag_assignments_asset",
        "knowledge_tag_assignments",
        ["asset_type", "asset_id", "workspace_id"],
    )
    _enable_workspace_rls("knowledge_tags")
    _enable_workspace_rls("knowledge_tag_assignments")


def downgrade() -> None:
    _disable_workspace_rls("knowledge_tag_assignments")
    _disable_workspace_rls("knowledge_tags")
    for index_name in (
        "ix_knowledge_tag_assignments_asset",
        "ix_knowledge_tag_assignments_review",
        "ix_knowledge_tag_assignments_state",
        "ix_knowledge_tag_assignments_tag_id",
        "ix_knowledge_tag_assignments_knowledge_base_id",
        "ix_knowledge_tag_assignments_workspace_id",
    ):
        op.drop_index(index_name, table_name="knowledge_tag_assignments")
    op.drop_table("knowledge_tag_assignments")
    for index_name in (
        "ix_knowledge_tags_kb_state",
        "ix_knowledge_tags_state",
        "ix_knowledge_tags_knowledge_base_id",
        "ix_knowledge_tags_workspace_id",
    ):
        op.drop_index(index_name, table_name="knowledge_tags")
    op.drop_table("knowledge_tags")
