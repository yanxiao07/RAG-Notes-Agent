"""add embedding index revisions and knowledge base state

Revision ID: a1f7c82b3d91
Revises: f05c2d4e9b66
Create Date: 2026-08-01 22:10:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a1f7c82b3d91"
down_revision: str | None = "f05c2d4e9b66"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 旧数据默认归属第 1 个嵌入配置版本，后续模型切换才会使其失效。
    with op.batch_alter_table("workspace_model_configurations") as batch_op:
        batch_op.add_column(
            sa.Column("embedding_revision", sa.Integer(), nullable=False, server_default="1")
        )
    with op.batch_alter_table("knowledge_bases") as batch_op:
        batch_op.add_column(
            sa.Column("embedding_revision", sa.Integer(), nullable=False, server_default="1")
        )
        batch_op.add_column(
            sa.Column("index_status", sa.String(length=20), nullable=False, server_default="ready")
        )
        batch_op.create_index("ix_knowledge_bases_index_status", ["index_status"])
    with op.batch_alter_table("chunk_embeddings") as batch_op:
        batch_op.add_column(
            sa.Column("embedding_revision", sa.Integer(), nullable=False, server_default="1")
        )
        batch_op.create_index("ix_chunk_embeddings_embedding_revision", ["embedding_revision"])


def downgrade() -> None:
    with op.batch_alter_table("chunk_embeddings") as batch_op:
        batch_op.drop_index("ix_chunk_embeddings_embedding_revision")
        batch_op.drop_column("embedding_revision")
    with op.batch_alter_table("knowledge_bases") as batch_op:
        batch_op.drop_index("ix_knowledge_bases_index_status")
        batch_op.drop_column("index_status")
        batch_op.drop_column("embedding_revision")
    with op.batch_alter_table("workspace_model_configurations") as batch_op:
        batch_op.drop_column("embedding_revision")
