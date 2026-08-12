"""add document content hash deduplication

Revision ID: d18c7a9a1504
Revises: e9a311b2f502
Create Date: 2026-08-01 19:40:00.000000
"""

import hashlib
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d18c7a9a1504"
down_revision: str | None = "e9a311b2f502"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("documents", sa.Column("content_hash", sa.String(length=64), nullable=True))

    # 优先保留已入库的历史文档；旧重复项保持 NULL，避免迁移因遗留重复数据失败。
    rows = (
        op.get_bind()
        .execute(
            sa.text(
                """
            SELECT id, workspace_id, knowledge_base_id, raw_content
            FROM documents
            ORDER BY CASE status WHEN 'indexed' THEN 0 ELSE 1 END, created_at ASC
            """
            )
        )
        .mappings()
    )
    seen: set[tuple[str, str, str]] = set()
    for row in rows:
        content_hash = hashlib.sha256(row["raw_content"].encode("utf-8")).hexdigest()
        scope = (row["workspace_id"], row["knowledge_base_id"], content_hash)
        if scope in seen:
            continue
        op.get_bind().execute(
            sa.text("UPDATE documents SET content_hash = :content_hash WHERE id = :id"),
            {"content_hash": content_hash, "id": row["id"]},
        )
        seen.add(scope)

    op.create_index(
        "ux_documents_workspace_kb_content_hash",
        "documents",
        ["workspace_id", "knowledge_base_id", "content_hash"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("ux_documents_workspace_kb_content_hash", table_name="documents")
    op.drop_column("documents", "content_hash")
