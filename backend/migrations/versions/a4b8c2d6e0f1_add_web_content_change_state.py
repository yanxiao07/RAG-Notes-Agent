"""add web content change detection state

Revision ID: a4b8c2d6e0f1
Revises: fc27a8d3e4b1
Create Date: 2026-08-12
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a4b8c2d6e0f1"
down_revision: str | None = "fc27a8d3e4b1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_columns = {column["name"] for column in inspector.get_columns("documents")}
    if "web_content_state" not in existing_columns:
        op.add_column(
            "documents",
            sa.Column(
                "web_content_state",
                sa.String(length=24),
                nullable=False,
                server_default="not_applicable",
            ),
        )
    if "web_content_checked_at" not in existing_columns:
        op.add_column("documents", sa.Column("web_content_checked_at", sa.DateTime(timezone=True)))
    existing_indexes = {index["name"] for index in inspector.get_indexes("documents")}
    if "ix_documents_web_content_state" not in existing_indexes:
        op.create_index("ix_documents_web_content_state", "documents", ["web_content_state"])


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        return
    op.drop_index("ix_documents_web_content_state", table_name="documents")
    op.drop_column("documents", "web_content_checked_at")
    op.drop_column("documents", "web_content_state")
