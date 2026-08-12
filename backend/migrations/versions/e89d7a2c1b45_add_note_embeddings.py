"""add semantic embeddings for curated notes

Revision ID: e89d7a2c1b45
Revises: d38a61f3c0e7
Create Date: 2026-08-02 10:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e89d7a2c1b45"
down_revision: str | None = "d38a61f3c0e7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "note_embeddings",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("workspace_id", sa.String(length=36), nullable=False),
        sa.Column("note_id", sa.String(length=36), nullable=False),
        sa.Column("provider_name", sa.String(length=80), nullable=False),
        sa.Column("model_name", sa.String(length=120), nullable=False),
        sa.Column("dimensions", sa.Integer(), nullable=False),
        sa.Column("embedding_revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("embedding", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["note_id"], ["notes.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("note_id"),
    )
    op.create_index("ix_note_embeddings_workspace_id", "note_embeddings", ["workspace_id"])
    op.create_index("ix_note_embeddings_note_id", "note_embeddings", ["note_id"], unique=True)
    op.create_index(
        "ix_note_embeddings_embedding_revision", "note_embeddings", ["embedding_revision"]
    )
    # 历史笔记没有向量，必须在重建后才能声称当前知识库索引完整。
    op.get_bind().execute(
        sa.text(
            "UPDATE knowledge_bases SET index_status = 'stale' "
            "WHERE status = 'active' AND EXISTS ("
            "SELECT 1 FROM notes WHERE notes.knowledge_base_id = knowledge_bases.id "
            "AND notes.status = 'active')"
        )
    )


def downgrade() -> None:
    op.drop_index("ix_note_embeddings_embedding_revision", table_name="note_embeddings")
    op.drop_index("ix_note_embeddings_note_id", table_name="note_embeddings")
    op.drop_index("ix_note_embeddings_workspace_id", table_name="note_embeddings")
    op.drop_table("note_embeddings")
