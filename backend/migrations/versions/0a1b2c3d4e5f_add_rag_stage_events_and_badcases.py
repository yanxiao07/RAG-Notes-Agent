"""add RAG stage events and deterministic badcases

Revision ID: 0a1b2c3d4e5f
Revises: f9b1c2d3e4f5
Create Date: 2026-08-11
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0a1b2c3d4e5f"
down_revision: str | None = "f9b1c2d3e4f5"
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
        "rag_stage_events",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("workspace_id", sa.String(length=36), nullable=False),
        sa.Column("knowledge_base_id", sa.String(length=36), nullable=False),
        sa.Column("agent_run_id", sa.String(length=36), nullable=False),
        sa.Column("conversation_id", sa.String(length=36), nullable=False),
        sa.Column("assistant_message_id", sa.String(length=36), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("stage", sa.String(length=40), nullable=False),
        sa.Column("state", sa.String(length=20), nullable=False, server_default="completed"),
        sa.Column("policy_version", sa.String(length=80), nullable=False),
        sa.Column("input_hash", sa.String(length=64), nullable=True),
        sa.Column("output_hash", sa.String(length=64), nullable=True),
        sa.Column("candidate_locators", sa.JSON(), nullable=False),
        sa.Column("metrics", sa.JSON(), nullable=False),
        sa.Column("error_code", sa.String(length=80), nullable=True),
        sa.Column("duration_ms", sa.Float(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["agent_run_id"], ["agent_runs.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["assistant_message_id"], ["conversation_messages.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["knowledge_base_id"], ["knowledge_bases.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("agent_run_id", "sequence", name="uq_rag_stage_event_sequence"),
    )
    op.create_index("ix_rag_stage_events_workspace_id", "rag_stage_events", ["workspace_id"])
    op.create_index(
        "ix_rag_stage_events_knowledge_base_id", "rag_stage_events", ["knowledge_base_id"]
    )
    op.create_index("ix_rag_stage_events_agent_run_id", "rag_stage_events", ["agent_run_id"])
    op.create_index(
        "ix_rag_stage_events_assistant_message_id", "rag_stage_events", ["assistant_message_id"]
    )
    op.create_index("ix_rag_stage_events_stage", "rag_stage_events", ["stage"])
    op.create_index("ix_rag_stage_events_state", "rag_stage_events", ["state"])
    op.create_index(
        "ix_rag_stage_events_run_stage", "rag_stage_events", ["agent_run_id", "stage", "sequence"]
    )
    op.create_index(
        "ix_rag_stage_events_kb_created", "rag_stage_events", ["knowledge_base_id", "created_at"]
    )

    op.create_table(
        "rag_badcases",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("workspace_id", sa.String(length=36), nullable=False),
        sa.Column("knowledge_base_id", sa.String(length=36), nullable=False),
        sa.Column("agent_run_id", sa.String(length=36), nullable=False),
        sa.Column("assistant_message_id", sa.String(length=36), nullable=False),
        sa.Column("stage_event_id", sa.String(length=36), nullable=True),
        sa.Column("category", sa.String(length=60), nullable=False),
        sa.Column("severity", sa.String(length=20), nullable=False, server_default="warning"),
        sa.Column("state", sa.String(length=20), nullable=False, server_default="open"),
        sa.Column("reason_code", sa.String(length=80), nullable=False),
        sa.Column("evidence_locators", sa.JSON(), nullable=False),
        sa.Column("details", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["agent_run_id"], ["agent_runs.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["assistant_message_id"], ["conversation_messages.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(["knowledge_base_id"], ["knowledge_bases.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["stage_event_id"], ["rag_stage_events.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("agent_run_id", "category", name="uq_rag_badcase_category"),
    )
    op.create_index("ix_rag_badcases_workspace_id", "rag_badcases", ["workspace_id"])
    op.create_index("ix_rag_badcases_knowledge_base_id", "rag_badcases", ["knowledge_base_id"])
    op.create_index("ix_rag_badcases_agent_run_id", "rag_badcases", ["agent_run_id"])
    op.create_index(
        "ix_rag_badcases_assistant_message_id", "rag_badcases", ["assistant_message_id"]
    )
    op.create_index("ix_rag_badcases_stage_event_id", "rag_badcases", ["stage_event_id"])
    op.create_index("ix_rag_badcases_category", "rag_badcases", ["category"])
    op.create_index("ix_rag_badcases_state", "rag_badcases", ["state"])
    op.create_index(
        "ix_rag_badcases_review", "rag_badcases", ["knowledge_base_id", "state", "created_at"]
    )
    _enable_workspace_rls("rag_stage_events")
    _enable_workspace_rls("rag_badcases")


def downgrade() -> None:
    _disable_workspace_rls("rag_badcases")
    _disable_workspace_rls("rag_stage_events")
    for index_name in (
        "ix_rag_badcases_review",
        "ix_rag_badcases_state",
        "ix_rag_badcases_category",
        "ix_rag_badcases_stage_event_id",
        "ix_rag_badcases_assistant_message_id",
        "ix_rag_badcases_agent_run_id",
        "ix_rag_badcases_knowledge_base_id",
        "ix_rag_badcases_workspace_id",
    ):
        op.drop_index(index_name, table_name="rag_badcases")
    op.drop_table("rag_badcases")
    for index_name in (
        "ix_rag_stage_events_kb_created",
        "ix_rag_stage_events_run_stage",
        "ix_rag_stage_events_state",
        "ix_rag_stage_events_stage",
        "ix_rag_stage_events_assistant_message_id",
        "ix_rag_stage_events_agent_run_id",
        "ix_rag_stage_events_knowledge_base_id",
        "ix_rag_stage_events_workspace_id",
    ):
        op.drop_index(index_name, table_name="rag_stage_events")
    op.drop_table("rag_stage_events")
