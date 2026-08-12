"""persist resumable Agent Runtime checkpoints

Revision ID: a4d8e1f2c3b0
Revises: 9c7e1b2d4f60
Create Date: 2026-08-03
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a4d8e1f2c3b0"
down_revision: str | None = "9c7e1b2d4f60"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """为每个 Runtime 节点增加可校验、可恢复的结构化状态快照。"""

    op.create_table(
        "agent_checkpoints",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("workspace_id", sa.String(length=36), nullable=False),
        sa.Column("agent_run_id", sa.String(length=36), nullable=False),
        sa.Column("thread_id", sa.String(length=80), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("node", sa.String(length=80), nullable=False),
        sa.Column("state_json", sa.JSON(), nullable=False),
        sa.Column("state_checksum", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["agent_run_id"], ["agent_runs.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("agent_run_id", "sequence", name="uq_agent_checkpoint_sequence"),
    )
    op.create_index("ix_agent_checkpoints_workspace_id", "agent_checkpoints", ["workspace_id"])
    op.create_index("ix_agent_checkpoints_agent_run_id", "agent_checkpoints", ["agent_run_id"])
    op.create_index("ix_agent_checkpoints_thread_id", "agent_checkpoints", ["thread_id"])
    if op.get_bind().dialect.name == "postgresql":
        op.execute('ALTER TABLE "agent_checkpoints" ENABLE ROW LEVEL SECURITY')
        op.execute('ALTER TABLE "agent_checkpoints" FORCE ROW LEVEL SECURITY')
        op.execute(
            'DROP POLICY IF EXISTS "agent_checkpoints_workspace_isolation" ON "agent_checkpoints"'
        )
        op.execute(
            """CREATE POLICY "agent_checkpoints_workspace_isolation" ON "agent_checkpoints"
               USING (workspace_id = current_setting('app.current_workspace_id', true))
               WITH CHECK (workspace_id = current_setting('app.current_workspace_id', true))"""
        )


def downgrade() -> None:
    """删除 Runtime 快照表。"""

    if op.get_bind().dialect.name == "postgresql":
        op.execute(
            'DROP POLICY IF EXISTS "agent_checkpoints_workspace_isolation" ON "agent_checkpoints"'
        )
        op.execute('ALTER TABLE "agent_checkpoints" NO FORCE ROW LEVEL SECURITY')
        op.execute('ALTER TABLE "agent_checkpoints" DISABLE ROW LEVEL SECURITY')
    op.drop_index("ix_agent_checkpoints_thread_id", table_name="agent_checkpoints")
    op.drop_index("ix_agent_checkpoints_agent_run_id", table_name="agent_checkpoints")
    op.drop_index("ix_agent_checkpoints_workspace_id", table_name="agent_checkpoints")
    op.drop_table("agent_checkpoints")
