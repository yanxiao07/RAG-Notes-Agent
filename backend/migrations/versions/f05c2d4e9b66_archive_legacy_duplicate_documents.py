"""archive legacy duplicate documents

Revision ID: f05c2d4e9b66
Revises: d18c7a9a1504
Create Date: 2026-08-01 20:15:00.000000
"""

import hashlib
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f05c2d4e9b66"
down_revision: str | None = "d18c7a9a1504"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 历史数据可能在内容哈希引入前重复上传：保留优先级最高的版本，其余仅归档。
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
    duplicate_ids: list[str] = []
    for row in rows:
        content_hash = hashlib.sha256(row["raw_content"].encode("utf-8")).hexdigest()
        scope = (row["workspace_id"], row["knowledge_base_id"], content_hash)
        if scope in seen:
            duplicate_ids.append(row["id"])
        else:
            seen.add(scope)
    if duplicate_ids:
        op.get_bind().execute(
            sa.text(
                "UPDATE documents SET status = 'archived' WHERE id IN :document_ids"
            ).bindparams(sa.bindparam("document_ids", expanding=True)),
            {"document_ids": duplicate_ids},
        )


def downgrade() -> None:
    # 归档是数据清理决策，降级不应猜测哪些记录原本是重复项。
    pass
