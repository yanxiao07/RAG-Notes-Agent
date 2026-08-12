"""make HNSW indexes dimension safe

Revision ID: c2e7d9a4b8f1
Revises: fb16c7d8e9f0
Create Date: 2026-08-08
"""

import os
from collections.abc import Sequence

from alembic import op

revision: str = "c2e7d9a4b8f1"
down_revision: str | None = "fb16c7d8e9f0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """用默认维度的部分索引替代会拒绝其他维度写入的旧全局索引。"""

    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    dimension = _configured_dimension()
    for table_name, index_name in (
        ("chunk_embeddings", "ix_chunk_embeddings_embedding_vector_hnsw"),
        ("note_embeddings", "ix_note_embeddings_embedding_vector_hnsw"),
    ):
        op.execute(f"DROP INDEX IF EXISTS {index_name}")
        op.execute(
            f"CREATE INDEX {index_name} ON {table_name} USING hnsw "
            f"((embedding_vector::vector({dimension})) vector_cosine_ops) "
            f"WHERE dimensions = {dimension} AND embedding_vector IS NOT NULL"
        )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    dimension = _configured_dimension()
    for table_name, index_name in (
        ("chunk_embeddings", "ix_chunk_embeddings_embedding_vector_hnsw"),
        ("note_embeddings", "ix_note_embeddings_embedding_vector_hnsw"),
    ):
        op.execute(f"DROP INDEX IF EXISTS {index_name}")
        op.execute(
            f"CREATE INDEX {index_name} ON {table_name} USING hnsw "
            f"((embedding_vector::vector({dimension})) vector_cosine_ops)"
        )


def _configured_dimension() -> int:
    raw = os.getenv("APP_EMBEDDING_DIMENSIONS", "1536")
    try:
        dimension = int(raw)
    except ValueError as exc:
        raise RuntimeError("APP_EMBEDDING_DIMENSIONS 必须是正整数。") from exc
    if not 8 <= dimension <= 8192:
        raise RuntimeError("APP_EMBEDDING_DIMENSIONS 必须在 8 到 8192 之间。")
    return dimension
