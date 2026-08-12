"""add answer feedback and triage queue

Revision ID: 1b2c3d4e5f6a
Revises: 0a1b2c3d4e5f
Create Date: 2026-08-11
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "1b2c3d4e5f6a"
down_revision: str | None = "0a1b2c3d4e5f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _enable_workspace_rls(table: str) -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    policy = f"{table}_workspace_isolation"
    op.execute(f'ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY')
    op.execute(f'ALTER TABLE "{table}" FORCE ROW LEVEL SECURITY')
    op.execute(f'DROP POLICY IF EXISTS "{policy}" ON "{table}"')
    op.execute(
        f'''CREATE POLICY "{policy}" ON "{table}"
            USING (workspace_id = current_setting('app.current_workspace_id', true))
            WITH CHECK (workspace_id = current_setting('app.current_workspace_id', true))'''
    )


def _disable_workspace_rls(table: str) -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    policy = f"{table}_workspace_isolation"
    op.execute(f'DROP POLICY IF EXISTS "{policy}" ON "{table}"')
    op.execute(f'ALTER TABLE "{table}" NO FORCE ROW LEVEL SECURITY')
    op.execute(f'ALTER TABLE "{table}" DISABLE ROW LEVEL SECURITY')


def upgrade() -> None:
    op.create_table(
        "answer_feedback",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("workspace_id", sa.String(length=36), nullable=False),
        sa.Column("knowledge_base_id", sa.String(length=36), nullable=False),
        sa.Column("assistant_message_id", sa.String(length=36), nullable=False),
        sa.Column("agent_run_id", sa.String(length=36), nullable=False),
        sa.Column("sentiment", sa.String(length=20), nullable=False),
        sa.Column("reason_code", sa.String(length=80), nullable=True),
        sa.Column("stage_event_ids", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["agent_run_id"], ["agent_runs.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["assistant_message_id"], ["conversation_messages.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(["knowledge_base_id"], ["knowledge_bases.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "workspace_id", "assistant_message_id", name="uq_answer_feedback_message"
        ),
    )
    op.create_index("ix_answer_feedback_workspace_id", "answer_feedback", ["workspace_id"])
    op.create_index(
        "ix_answer_feedback_knowledge_base_id", "answer_feedback", ["knowledge_base_id"]
    )
    op.create_index(
        "ix_answer_feedback_assistant_message_id", "answer_feedback", ["assistant_message_id"]
    )
    op.create_index("ix_answer_feedback_agent_run_id", "answer_feedback", ["agent_run_id"])
    op.create_index("ix_answer_feedback_sentiment", "answer_feedback", ["sentiment"])
    op.create_index(
        "ix_answer_feedback_kb_created", "answer_feedback", ["knowledge_base_id", "created_at"]
    )

    op.create_table(
        "feedback_triage",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("workspace_id", sa.String(length=36), nullable=False),
        sa.Column("knowledge_base_id", sa.String(length=36), nullable=False),
        sa.Column("feedback_id", sa.String(length=36), nullable=False),
        sa.Column("category", sa.String(length=80), nullable=False),
        sa.Column("state", sa.String(length=20), nullable=False, server_default="open"),
        sa.Column("resolution_target", sa.String(length=40), nullable=True),
        sa.Column("reviewer_id", sa.String(length=100), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["feedback_id"], ["answer_feedback.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["knowledge_base_id"], ["knowledge_bases.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("feedback_id", name="uq_feedback_triage_feedback"),
    )
    op.create_index("ix_feedback_triage_workspace_id", "feedback_triage", ["workspace_id"])
    op.create_index(
        "ix_feedback_triage_knowledge_base_id", "feedback_triage", ["knowledge_base_id"]
    )
    op.create_index("ix_feedback_triage_feedback_id", "feedback_triage", ["feedback_id"])
    op.create_index("ix_feedback_triage_category", "feedback_triage", ["category"])
    op.create_index("ix_feedback_triage_state", "feedback_triage", ["state"])
    op.create_index(
        "ix_feedback_triage_queue", "feedback_triage", ["knowledge_base_id", "state", "created_at"]
    )
    _enable_workspace_rls("answer_feedback")
    _enable_workspace_rls("feedback_triage")


def downgrade() -> None:
    _disable_workspace_rls("feedback_triage")
    _disable_workspace_rls("answer_feedback")
    for index_name in (
        "ix_feedback_triage_queue",
        "ix_feedback_triage_state",
        "ix_feedback_triage_category",
        "ix_feedback_triage_feedback_id",
        "ix_feedback_triage_knowledge_base_id",
        "ix_feedback_triage_workspace_id",
    ):
        op.drop_index(index_name, table_name="feedback_triage")
    op.drop_table("feedback_triage")
    for index_name in (
        "ix_answer_feedback_kb_created",
        "ix_answer_feedback_sentiment",
        "ix_answer_feedback_agent_run_id",
        "ix_answer_feedback_assistant_message_id",
        "ix_answer_feedback_knowledge_base_id",
        "ix_answer_feedback_workspace_id",
    ):
        op.drop_index(index_name, table_name="answer_feedback")
    op.drop_table("answer_feedback")
