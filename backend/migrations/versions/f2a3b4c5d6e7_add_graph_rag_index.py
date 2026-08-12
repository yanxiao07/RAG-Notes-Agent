"""add GraphRAG-lite entity and relation index

Revision ID: f2a3b4c5d6e7
Revises: e1a2b3c4d5e6
Create Date: 2026-08-06
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f2a3b4c5d6e7"
down_revision: str | None = "e1a2b3c4d5e6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

GRAPH_TABLES = ("knowledge_entities", "chunk_entity_mentions", "knowledge_relations")


def upgrade() -> None:
    op.create_table(
        "knowledge_entities",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("workspace_id", sa.String(length=36), nullable=False),
        sa.Column("knowledge_base_id", sa.String(length=36), nullable=False),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("normalized_name", sa.String(length=160), nullable=False),
        sa.Column("entity_type", sa.String(length=40), nullable=False),
        sa.Column("mention_count", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["knowledge_base_id"], ["knowledge_bases.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "workspace_id",
            "knowledge_base_id",
            "normalized_name",
            name="uq_knowledge_entity_name",
        ),
    )
    op.create_index("ix_knowledge_entities_workspace_id", "knowledge_entities", ["workspace_id"])
    op.create_index(
        "ix_knowledge_entities_knowledge_base_id",
        "knowledge_entities",
        ["knowledge_base_id"],
    )
    op.create_index(
        "ix_knowledge_entities_kb_name",
        "knowledge_entities",
        ["knowledge_base_id", "normalized_name"],
    )

    op.create_table(
        "chunk_entity_mentions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("workspace_id", sa.String(length=36), nullable=False),
        sa.Column("document_chunk_id", sa.String(length=36), nullable=False),
        sa.Column("entity_id", sa.String(length=36), nullable=False),
        sa.Column("mention_count", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["document_chunk_id"], ["document_chunks.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["entity_id"], ["knowledge_entities.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "workspace_id", "document_chunk_id", "entity_id", name="uq_chunk_entity_mention"
        ),
    )
    op.create_index(
        "ix_chunk_entity_mentions_workspace_id", "chunk_entity_mentions", ["workspace_id"]
    )
    op.create_index(
        "ix_chunk_entity_mentions_document_chunk_id",
        "chunk_entity_mentions",
        ["document_chunk_id"],
    )
    op.create_index(
        "ix_chunk_entity_mentions_entity", "chunk_entity_mentions", ["entity_id", "workspace_id"]
    )
    op.create_index("ix_chunk_entity_mentions_entity_id", "chunk_entity_mentions", ["entity_id"])

    op.create_table(
        "knowledge_relations",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("workspace_id", sa.String(length=36), nullable=False),
        sa.Column("knowledge_base_id", sa.String(length=36), nullable=False),
        sa.Column("document_chunk_id", sa.String(length=36), nullable=False),
        sa.Column("source_entity_id", sa.String(length=36), nullable=False),
        sa.Column("target_entity_id", sa.String(length=36), nullable=False),
        sa.Column("relation_type", sa.String(length=60), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["document_chunk_id"], ["document_chunks.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["knowledge_base_id"], ["knowledge_bases.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["source_entity_id"], ["knowledge_entities.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["target_entity_id"], ["knowledge_entities.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "workspace_id",
            "document_chunk_id",
            "source_entity_id",
            "target_entity_id",
            "relation_type",
            name="uq_knowledge_relation_evidence",
        ),
    )
    for index_name, columns in (
        ("ix_knowledge_relations_workspace_id", ["workspace_id"]),
        ("ix_knowledge_relations_knowledge_base_id", ["knowledge_base_id"]),
        ("ix_knowledge_relations_document_chunk_id", ["document_chunk_id"]),
        ("ix_knowledge_relations_source", ["source_entity_id", "workspace_id"]),
        ("ix_knowledge_relations_target", ["target_entity_id", "workspace_id"]),
    ):
        op.create_index(index_name, "knowledge_relations", columns)
    op.create_index(
        "ix_knowledge_relations_source_entity_id", "knowledge_relations", ["source_entity_id"]
    )
    op.create_index(
        "ix_knowledge_relations_target_entity_id", "knowledge_relations", ["target_entity_id"]
    )

    if op.get_bind().dialect.name == "postgresql":
        for table in GRAPH_TABLES:
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
        for table in reversed(GRAPH_TABLES):
            policy = f"{table}_workspace_isolation"
            op.execute(f'DROP POLICY IF EXISTS "{policy}" ON "{table}"')
            op.execute(f'ALTER TABLE "{table}" NO FORCE ROW LEVEL SECURITY')
            op.execute(f'ALTER TABLE "{table}" DISABLE ROW LEVEL SECURITY')
    for index_name in (
        "ix_knowledge_relations_target",
        "ix_knowledge_relations_source",
        "ix_knowledge_relations_target_entity_id",
        "ix_knowledge_relations_source_entity_id",
        "ix_knowledge_relations_document_chunk_id",
        "ix_knowledge_relations_knowledge_base_id",
        "ix_knowledge_relations_workspace_id",
        "ix_chunk_entity_mentions_entity",
        "ix_chunk_entity_mentions_entity_id",
        "ix_chunk_entity_mentions_document_chunk_id",
        "ix_chunk_entity_mentions_workspace_id",
        "ix_knowledge_entities_kb_name",
        "ix_knowledge_entities_knowledge_base_id",
        "ix_knowledge_entities_workspace_id",
    ):
        # SQLite 的失败迁移可能已经删除部分索引，IF EXISTS 让回滚保持幂等。
        op.execute(sa.text(f'DROP INDEX IF EXISTS "{index_name}"'))
    op.drop_table("knowledge_relations")
    op.drop_table("chunk_entity_mentions")
    op.drop_table("knowledge_entities")
