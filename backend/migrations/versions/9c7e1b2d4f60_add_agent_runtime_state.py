"""add persisted agent runtime state and tool calls

Revision ID: 9c7e1b2d4f60
Revises: 8b6d2f1a4c90
Create Date: 2026-08-03
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "9c7e1b2d4f60"
down_revision: str | None = "8b6d2f1a4c90"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """为运行线程和工具调用增加可恢复的持久状态。"""

    op.add_column(
        "agent_runs",
        sa.Column("thread_id", sa.String(length=80), nullable=True),
    )
    op.add_column(
        "agent_runs",
        sa.Column("current_node", sa.String(length=80), nullable=False, server_default="start"),
    )
    op.add_column(
        "agent_runs",
        sa.Column("input_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
    )
    op.add_column("agent_runs", sa.Column("output_json", sa.JSON(), nullable=True))
    op.execute("UPDATE agent_runs SET thread_id = id WHERE thread_id IS NULL")
    op.create_index("ix_agent_runs_thread_id", "agent_runs", ["thread_id"], unique=False)
    op.create_table(
        "tool_calls",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("workspace_id", sa.String(length=36), nullable=False),
        sa.Column("agent_run_id", sa.String(length=36), nullable=False),
        sa.Column("node", sa.String(length=80), nullable=False),
        sa.Column("tool_name", sa.String(length=120), nullable=False),
        sa.Column("input_json", sa.JSON(), nullable=False),
        sa.Column("output_json", sa.JSON(), nullable=True),
        sa.Column("state", sa.String(length=30), nullable=False, server_default="running"),
        sa.Column("requires_approval", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("error_code", sa.String(length=80), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["agent_run_id"], ["agent_runs.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_tool_calls_workspace_id", "tool_calls", ["workspace_id"])
    op.create_index("ix_tool_calls_agent_run_id", "tool_calls", ["agent_run_id"])
    op.create_index("ix_tool_calls_state", "tool_calls", ["state"])
    if op.get_bind().dialect.name == "postgresql":
        op.execute('ALTER TABLE "tool_calls" ENABLE ROW LEVEL SECURITY')
        op.execute('ALTER TABLE "tool_calls" FORCE ROW LEVEL SECURITY')
        op.execute('DROP POLICY IF EXISTS "tool_calls_workspace_isolation" ON "tool_calls"')
        op.execute(
            """CREATE POLICY "tool_calls_workspace_isolation" ON "tool_calls"
               USING (workspace_id = current_setting('app.current_workspace_id', true))
               WITH CHECK (workspace_id = current_setting('app.current_workspace_id', true))"""
        )


def downgrade() -> None:
    """回滚运行状态和工具调用表，保留原有 AgentRun 主记录。"""

    if op.get_bind().dialect.name == "postgresql":
        op.execute('DROP POLICY IF EXISTS "tool_calls_workspace_isolation" ON "tool_calls"')
        op.execute('ALTER TABLE "tool_calls" NO FORCE ROW LEVEL SECURITY')
        op.execute('ALTER TABLE "tool_calls" DISABLE ROW LEVEL SECURITY')
    op.drop_index("ix_tool_calls_state", table_name="tool_calls")
    op.drop_index("ix_tool_calls_agent_run_id", table_name="tool_calls")
    op.drop_index("ix_tool_calls_workspace_id", table_name="tool_calls")
    op.drop_table("tool_calls")
    op.drop_index("ix_agent_runs_thread_id", table_name="agent_runs")
    op.drop_column("agent_runs", "output_json")
    op.drop_column("agent_runs", "input_json")
    op.drop_column("agent_runs", "current_node")
    op.drop_column("agent_runs", "thread_id")
