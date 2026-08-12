"""add versioned GraphRAG community summaries

Revision ID: f3b4c5d6e7f8
Revises: f2a3b4c5d6e7
Create Date: 2026-08-07
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f3b4c5d6e7f8"
down_revision: str | None = "f2a3b4c5d6e7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 图谱状态与向量状态分开维护，避免图谱重建误阻断已经可用的向量索引。
    op.add_column(
        "knowledge_bases",
        sa.Column("graph_status", sa.String(length=20), nullable=False, server_default="ready"),
    )
    op.add_column(
        "knowledge_bases",
        sa.Column("graph_revision", sa.Integer(), nullable=False, server_default="1"),
    )
    op.create_index("ix_knowledge_bases_graph_status", "knowledge_bases", ["graph_status"])
    op.create_index("ix_knowledge_bases_graph_revision", "knowledge_bases", ["graph_revision"])

    op.create_table(
        "knowledge_community_summaries",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("workspace_id", sa.String(length=36), nullable=False),
        sa.Column("knowledge_base_id", sa.String(length=36), nullable=False),
        sa.Column("level", sa.Integer(), nullable=False),
        sa.Column("community_key", sa.String(length=128), nullable=False),
        sa.Column("title", sa.String(length=240), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("member_entity_ids", sa.JSON(), nullable=False),
        sa.Column("source_chunk_ids", sa.JSON(), nullable=False),
        sa.Column("graph_revision", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="active"),
        sa.Column("extractor_provider", sa.String(length=80), nullable=False),
        sa.Column("extractor_version", sa.String(length=40), nullable=False),
        sa.Column("summary_provider", sa.String(length=80), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["knowledge_base_id"], ["knowledge_bases.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "workspace_id",
            "knowledge_base_id",
            "level",
            "community_key",
            name="uq_knowledge_community_summary",
        ),
    )
    op.create_index(
        "ix_knowledge_community_summaries_workspace_id",
        "knowledge_community_summaries",
        ["workspace_id"],
    )
    op.create_index(
        "ix_knowledge_community_summaries_knowledge_base_id",
        "knowledge_community_summaries",
        ["knowledge_base_id"],
    )
    op.create_index(
        "ix_knowledge_community_summary_kb_level",
        "knowledge_community_summaries",
        ["knowledge_base_id", "level", "status"],
    )
    op.create_index(
        "ix_knowledge_community_summaries_status",
        "knowledge_community_summaries",
        ["status"],
    )
    op.create_index(
        "ix_knowledge_community_summaries_graph_revision",
        "knowledge_community_summaries",
        ["graph_revision"],
    )

    if op.get_bind().dialect.name == "postgresql":
        table = "knowledge_community_summaries"
        policy = f"{table}_workspace_isolation"
        op.execute(f'ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY')
        op.execute(f'ALTER TABLE "{table}" FORCE ROW LEVEL SECURITY')
        op.execute(f'DROP POLICY IF EXISTS "{policy}" ON "{table}"')
        op.execute(
            f'''CREATE POLICY "{policy}" ON "{table}"
                USING (workspace_id = current_setting('app.current_workspace_id', true))
                WITH CHECK (workspace_id = current_setting('app.current_workspace_id', true))'''
        )


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute(
            'DROP POLICY IF EXISTS "knowledge_community_summaries_workspace_isolation" '
            'ON "knowledge_community_summaries"'
        )
        op.execute('ALTER TABLE "knowledge_community_summaries" NO FORCE ROW LEVEL SECURITY')
        op.execute('ALTER TABLE "knowledge_community_summaries" DISABLE ROW LEVEL SECURITY')
    for index_name in (
        "ix_knowledge_community_summary_kb_level",
        "ix_knowledge_community_summaries_graph_revision",
        "ix_knowledge_community_summaries_status",
        "ix_knowledge_community_summaries_knowledge_base_id",
        "ix_knowledge_community_summaries_workspace_id",
    ):
        op.execute(sa.text(f'DROP INDEX IF EXISTS "{index_name}"'))
    op.drop_table("knowledge_community_summaries")
    op.execute(sa.text('DROP INDEX IF EXISTS "ix_knowledge_bases_graph_status"'))
    op.execute(sa.text('DROP INDEX IF EXISTS "ix_knowledge_bases_graph_revision"'))
    op.drop_column("knowledge_bases", "graph_revision")
    op.drop_column("knowledge_bases", "graph_status")
