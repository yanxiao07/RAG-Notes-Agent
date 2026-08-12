"""add document governance metadata

Revision ID: fc27a8d3e4b1
Revises: 2c3d4e5f6a7b
Create Date: 2026-08-12
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "fc27a8d3e4b1"
down_revision: str | None = "2c3d4e5f6a7b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_columns = {column["name"] for column in inspector.get_columns("documents")}
    columns = (
        sa.Column(
            "source_trust_level",
            sa.String(length=20),
            nullable=False,
            server_default="standard",
        ),
        sa.Column("effective_at", sa.DateTime(timezone=True)),
        sa.Column("expires_at", sa.DateTime(timezone=True)),
        sa.Column("conflict_state", sa.String(length=20), nullable=False, server_default="none"),
        sa.Column("supersedes_document_id", sa.String(length=36), nullable=True),
        sa.Column("governance_version", sa.Integer(), nullable=False, server_default="1"),
    )
    # SQLite DDL 不能回滚。跳过已存在列，允许开发库从历史中断迁移安全恢复。
    for column in columns:
        if column.name not in existing_columns:
            op.add_column("documents", column)

    existing_indexes = {index["name"] for index in inspector.get_indexes("documents")}
    for index_name, columns in (
        ("ix_documents_source_trust_level", ["source_trust_level"]),
        ("ix_documents_conflict_state", ["conflict_state"]),
        ("ix_documents_supersedes_document_id", ["supersedes_document_id"]),
    ):
        if index_name not in existing_indexes:
            op.create_index(index_name, "documents", columns)
    # SQLite 无法通过 ALTER TABLE 增加外键；运行时已严格校验同知识库替代关系。
    if bind.dialect.name != "sqlite":
        op.create_foreign_key(
            "fk_documents_supersedes_document_id",
            "documents",
            "documents",
            ["supersedes_document_id"],
            ["id"],
            ondelete="RESTRICT",
        )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        # SQLite 删除列/外键需要表重建。开发环境允许保留历史治理列，版本降级不损坏数据。
        return
    op.drop_index("ix_documents_supersedes_document_id", table_name="documents")
    op.drop_index("ix_documents_conflict_state", table_name="documents")
    op.drop_index("ix_documents_source_trust_level", table_name="documents")
    op.drop_constraint("fk_documents_supersedes_document_id", "documents", type_="foreignkey")
    op.drop_column("documents", "governance_version")
    op.drop_column("documents", "supersedes_document_id")
    op.drop_column("documents", "conflict_state")
    op.drop_column("documents", "expires_at")
    op.drop_column("documents", "effective_at")
    op.drop_column("documents", "source_trust_level")
