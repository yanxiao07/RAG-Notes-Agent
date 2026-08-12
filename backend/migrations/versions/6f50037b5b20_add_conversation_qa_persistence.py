"""add conversation qa persistence

Revision ID: 6f50037b5b20
Revises: 4635ebc4d6d8
Create Date: 2026-08-01 15:55:04.761601
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "6f50037b5b20"
down_revision: str | None = "4635ebc4d6d8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if not inspector.has_table("conversations"):
        op.create_table(
            "conversations",
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("workspace_id", sa.String(length=36), nullable=False),
            sa.Column("knowledge_base_id", sa.String(length=36), nullable=False),
            sa.Column("title", sa.String(length=240), nullable=False),
            sa.Column("state", sa.String(length=20), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(
                ["knowledge_base_id"], ["knowledge_bases.id"], ondelete="RESTRICT"
            ),
            sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="RESTRICT"),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_conversations_workspace_id", "conversations", ["workspace_id"])
        op.create_index(
            "ix_conversations_knowledge_base_id", "conversations", ["knowledge_base_id"]
        )
        op.create_index("ix_conversations_state", "conversations", ["state"])

    if not inspector.has_table("conversation_messages"):
        op.create_table(
            "conversation_messages",
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("workspace_id", sa.String(length=36), nullable=False),
            sa.Column("conversation_id", sa.String(length=36), nullable=False),
            sa.Column("role", sa.String(length=20), nullable=False),
            sa.Column("content", sa.Text(), nullable=False),
            sa.Column("state", sa.String(length=20), nullable=False),
            sa.Column("citations", sa.JSON(), nullable=False),
            sa.Column("provider_name", sa.String(length=80), nullable=True),
            sa.Column("model_name", sa.String(length=120), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(["conversation_id"], ["conversations.id"], ondelete="RESTRICT"),
            sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="RESTRICT"),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index(
            "ix_conversation_messages_workspace_id", "conversation_messages", ["workspace_id"]
        )
        op.create_index(
            "ix_conversation_messages_conversation_id",
            "conversation_messages",
            ["conversation_id"],
        )
        op.create_index("ix_conversation_messages_state", "conversation_messages", ["state"])

    agent_run_columns = {column["name"] for column in inspector.get_columns("agent_runs")}
    if "conversation_id" not in agent_run_columns:
        # SQLite 需要 batch 模式重建表，才能同时添加外键和索引。
        with op.batch_alter_table("agent_runs") as batch_op:
            batch_op.add_column(sa.Column("conversation_id", sa.String(length=36), nullable=True))
            batch_op.create_index("ix_agent_runs_conversation_id", ["conversation_id"])
            batch_op.create_foreign_key(
                "fk_agent_runs_conversation_id",
                "conversations",
                ["conversation_id"],
                ["id"],
                ondelete="RESTRICT",
            )


def downgrade() -> None:
    with op.batch_alter_table("agent_runs") as batch_op:
        batch_op.drop_constraint("fk_agent_runs_conversation_id", type_="foreignkey")
        batch_op.drop_index("ix_agent_runs_conversation_id")
        batch_op.drop_column("conversation_id")
    op.drop_index("ix_conversation_messages_state", table_name="conversation_messages")
    op.drop_index("ix_conversation_messages_conversation_id", table_name="conversation_messages")
    op.drop_index("ix_conversation_messages_workspace_id", table_name="conversation_messages")
    op.drop_table("conversation_messages")
    op.drop_index("ix_conversations_state", table_name="conversations")
    op.drop_index("ix_conversations_knowledge_base_id", table_name="conversations")
    op.drop_index("ix_conversations_workspace_id", table_name="conversations")
    op.drop_table("conversations")
