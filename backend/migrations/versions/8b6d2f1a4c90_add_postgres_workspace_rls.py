"""add PostgreSQL workspace row-level security

Revision ID: 8b6d2f1a4c90
Revises: 7ac4e8d1f2b3
Create Date: 2026-08-03
"""

from collections.abc import Sequence

from alembic import op

revision: str = "8b6d2f1a4c90"
down_revision: str | None = "7ac4e8d1f2b3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


WORKSPACE_SCOPED_TABLES = (
    "knowledge_bases",
    "notes",
    "note_embeddings",
    "knowledge_mind_maps",
    "documents",
    "ingestion_jobs",
    "document_chunks",
    "chunk_embeddings",
    "workspace_model_configurations",
    "conversations",
    "conversation_messages",
    "agent_runs",
    "change_proposals",
    "audit_events",
)


def upgrade() -> None:
    """启用强制 RLS；没有 Session 级租户上下文时任何业务表默认返回空集。"""

    if op.get_bind().dialect.name != "postgresql":
        return
    for table in WORKSPACE_SCOPED_TABLES:
        policy = f"{table}_workspace_isolation"
        op.execute(f'ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY')
        op.execute(f'ALTER TABLE "{table}" FORCE ROW LEVEL SECURITY')
        op.execute(f'DROP POLICY IF EXISTS "{policy}" ON "{table}"')
        op.execute(
            f'''CREATE POLICY "{policy}" ON "{table}"
                USING (workspace_id = current_setting('app.current_workspace_id', true))
                WITH CHECK (workspace_id = current_setting('app.current_workspace_id', true))'''
        )

    # 工作区根表没有 workspace_id，使用主键和同一个上下文表达租户边界。
    op.execute('ALTER TABLE "workspaces" ENABLE ROW LEVEL SECURITY')
    op.execute('ALTER TABLE "workspaces" FORCE ROW LEVEL SECURITY')
    op.execute('DROP POLICY IF EXISTS "workspaces_workspace_isolation" ON "workspaces"')
    op.execute(
        """CREATE POLICY "workspaces_workspace_isolation" ON "workspaces"
           USING (id = current_setting('app.current_workspace_id', true))
           WITH CHECK (id = current_setting('app.current_workspace_id', true))"""
    )


def downgrade() -> None:
    """回滚时关闭强制策略，但不删除业务数据或上下文函数。"""

    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute('DROP POLICY IF EXISTS "workspaces_workspace_isolation" ON "workspaces"')
    op.execute('ALTER TABLE "workspaces" NO FORCE ROW LEVEL SECURITY')
    op.execute('ALTER TABLE "workspaces" DISABLE ROW LEVEL SECURITY')
    for table in WORKSPACE_SCOPED_TABLES:
        policy = f"{table}_workspace_isolation"
        op.execute(f'DROP POLICY IF EXISTS "{policy}" ON "{table}"')
        op.execute(f'ALTER TABLE "{table}" NO FORCE ROW LEVEL SECURITY')
        op.execute(f'ALTER TABLE "{table}" DISABLE ROW LEVEL SECURITY')
