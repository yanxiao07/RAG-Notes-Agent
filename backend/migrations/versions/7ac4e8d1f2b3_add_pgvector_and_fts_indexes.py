"""add pgvector columns and PostgreSQL FTS indexes

Revision ID: 7ac4e8d1f2b3
Revises: f8e7c9d2a1b0
Create Date: 2026-08-03
"""

import os
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "7ac4e8d1f2b3"
down_revision: str | None = "f8e7c9d2a1b0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """为 PostgreSQL 增加向量列和全文索引，本地 SQLite 保留 JSON 降级列。"""

    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        # 扩展和索引只在 PostgreSQL 执行；SQLite 测试/开发环境不会被扩展语法阻断。
        dimension = _configured_dimension()
        op.execute("CREATE EXTENSION IF NOT EXISTS vector")
        op.execute("ALTER TABLE chunk_embeddings ADD COLUMN embedding_vector vector")
        op.execute("ALTER TABLE note_embeddings ADD COLUMN embedding_vector vector")
        op.execute(
            "CREATE INDEX ix_chunk_embeddings_embedding_vector_hnsw "
            f"ON chunk_embeddings USING hnsw "
            f"((embedding_vector::vector({dimension})) vector_cosine_ops)"
        )
        op.execute(
            "CREATE INDEX ix_note_embeddings_embedding_vector_hnsw "
            f"ON note_embeddings USING hnsw "
            f"((embedding_vector::vector({dimension})) vector_cosine_ops)"
        )
        op.execute(
            "CREATE INDEX ix_document_chunks_content_fts ON document_chunks "
            "USING gin (to_tsvector('simple', content))"
        )
        op.execute(
            "CREATE INDEX ix_notes_content_fts ON notes USING gin (to_tsvector('simple', content))"
        )
        return

    # SQLite 只需要兼容 ORM 的列形状；检索仍走本地 JSON + Python 余弦实现。
    op.add_column(
        "chunk_embeddings",
        sa.Column("embedding_vector", sa.JSON(), nullable=True),
    )
    op.add_column(
        "note_embeddings",
        sa.Column("embedding_vector", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    """回滚生产索引或本地兼容列，不删除原有 JSON 向量快照。"""

    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("DROP INDEX IF EXISTS ix_notes_content_fts")
        op.execute("DROP INDEX IF EXISTS ix_document_chunks_content_fts")
        op.execute("DROP INDEX IF EXISTS ix_note_embeddings_embedding_vector_hnsw")
        op.execute("DROP INDEX IF EXISTS ix_chunk_embeddings_embedding_vector_hnsw")
        op.execute("ALTER TABLE note_embeddings DROP COLUMN embedding_vector")
        op.execute("ALTER TABLE chunk_embeddings DROP COLUMN embedding_vector")
        return
    op.drop_column("note_embeddings", "embedding_vector")
    op.drop_column("chunk_embeddings", "embedding_vector")


def _configured_dimension() -> int:
    """读取迁移时的向量维度，索引表达式必须使用固定维度才能启用 HNSW。"""

    raw = os.getenv("APP_EMBEDDING_DIMENSIONS", "1536")
    try:
        dimension = int(raw)
    except ValueError as exc:
        raise RuntimeError("APP_EMBEDDING_DIMENSIONS 必须是正整数。") from exc
    if not 8 <= dimension <= 8192:
        raise RuntimeError("APP_EMBEDDING_DIMENSIONS 必须在 8 到 8192 之间。")
    return dimension
