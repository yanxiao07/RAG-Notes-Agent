"""add feedback knowledge drafts and evaluation cases

Revision ID: 2c3d4e5f6a7b
Revises: 1b2c3d4e5f6a
Create Date: 2026-08-11
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "2c3d4e5f6a7b"
down_revision: str | None = "1b2c3d4e5f6a"
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
        "feedback_knowledge_drafts",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("workspace_id", sa.String(length=36), nullable=False),
        sa.Column("knowledge_base_id", sa.String(length=36), nullable=False),
        sa.Column("feedback_triage_id", sa.String(length=36), nullable=False),
        sa.Column("title", sa.String(length=240), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("state", sa.String(length=20), nullable=False, server_default="pending"),
        sa.Column("reviewer_id", sa.String(length=100), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_note_id", sa.String(length=36), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["created_note_id"], ["notes.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["feedback_triage_id"], ["feedback_triage.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(["knowledge_base_id"], ["knowledge_bases.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("feedback_triage_id", name="uq_feedback_knowledge_draft_triage"),
        sa.UniqueConstraint("created_note_id"),
    )
    for name, columns in (
        ("ix_feedback_knowledge_drafts_workspace_id", ["workspace_id"]),
        ("ix_feedback_knowledge_drafts_knowledge_base_id", ["knowledge_base_id"]),
        ("ix_feedback_knowledge_drafts_feedback_triage_id", ["feedback_triage_id"]),
        ("ix_feedback_knowledge_drafts_state", ["state"]),
        ("ix_feedback_knowledge_drafts_created_note_id", ["created_note_id"]),
        (
            "ix_feedback_knowledge_drafts_review",
            ["knowledge_base_id", "state", "created_at"],
        ),
    ):
        op.create_index(name, "feedback_knowledge_drafts", columns)

    op.create_table(
        "feedback_evaluation_cases",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("workspace_id", sa.String(length=36), nullable=False),
        sa.Column("knowledge_base_id", sa.String(length=36), nullable=False),
        sa.Column("feedback_triage_id", sa.String(length=36), nullable=False),
        sa.Column("query", sa.Text(), nullable=False),
        sa.Column("expected_source_titles", sa.JSON(), nullable=False),
        sa.Column("required_keywords", sa.JSON(), nullable=False),
        sa.Column("limit", sa.Integer(), nullable=False, server_default="5"),
        sa.Column("state", sa.String(length=20), nullable=False, server_default="pending"),
        sa.Column("reviewer_id", sa.String(length=100), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["feedback_triage_id"], ["feedback_triage.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(["knowledge_base_id"], ["knowledge_bases.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("feedback_triage_id", name="uq_feedback_evaluation_case_triage"),
    )
    for name, columns in (
        ("ix_feedback_evaluation_cases_workspace_id", ["workspace_id"]),
        ("ix_feedback_evaluation_cases_knowledge_base_id", ["knowledge_base_id"]),
        ("ix_feedback_evaluation_cases_feedback_triage_id", ["feedback_triage_id"]),
        ("ix_feedback_evaluation_cases_state", ["state"]),
        (
            "ix_feedback_evaluation_cases_review",
            ["knowledge_base_id", "state", "created_at"],
        ),
    ):
        op.create_index(name, "feedback_evaluation_cases", columns)
    _enable_workspace_rls("feedback_knowledge_drafts")
    _enable_workspace_rls("feedback_evaluation_cases")


def downgrade() -> None:
    _disable_workspace_rls("feedback_evaluation_cases")
    _disable_workspace_rls("feedback_knowledge_drafts")
    for table, indexes in (
        (
            "feedback_evaluation_cases",
            (
                "ix_feedback_evaluation_cases_review",
                "ix_feedback_evaluation_cases_state",
                "ix_feedback_evaluation_cases_feedback_triage_id",
                "ix_feedback_evaluation_cases_knowledge_base_id",
                "ix_feedback_evaluation_cases_workspace_id",
            ),
        ),
        (
            "feedback_knowledge_drafts",
            (
                "ix_feedback_knowledge_drafts_review",
                "ix_feedback_knowledge_drafts_created_note_id",
                "ix_feedback_knowledge_drafts_state",
                "ix_feedback_knowledge_drafts_feedback_triage_id",
                "ix_feedback_knowledge_drafts_knowledge_base_id",
                "ix_feedback_knowledge_drafts_workspace_id",
            ),
        ),
    ):
        for index in indexes:
            op.drop_index(index, table_name=table)
        op.drop_table(table)
