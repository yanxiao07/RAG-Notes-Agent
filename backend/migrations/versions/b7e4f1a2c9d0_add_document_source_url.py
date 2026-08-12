"""add source URL for web documents

Revision ID: b7e4f1a2c9d0
Revises: a4d8e1f2c3b0
Create Date: 2026-08-04 10:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b7e4f1a2c9d0"
down_revision: str | None = "a4d8e1f2c3b0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("documents", sa.Column("source_url", sa.String(length=2_000), nullable=True))
    op.create_index(
        "ix_documents_workspace_kb_source_url",
        "documents",
        ["workspace_id", "knowledge_base_id", "source_url"],
    )


def downgrade() -> None:
    op.drop_index("ix_documents_workspace_kb_source_url", table_name="documents")
    op.drop_column("documents", "source_url")
