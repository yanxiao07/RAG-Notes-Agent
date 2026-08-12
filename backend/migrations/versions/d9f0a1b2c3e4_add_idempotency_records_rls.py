"""enable workspace RLS for idempotency records

Revision ID: d9f0a1b2c3e4
Revises: c8f0a1b2c3d4
Create Date: 2026-08-05 10:00:00.000000
"""

from collections.abc import Sequence

from alembic import op

revision: str = "d9f0a1b2c3e4"
down_revision: str | None = "c8f0a1b2c3d4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


TABLE = "idempotency_records"
POLICY = "idempotency_records_workspace_isolation"


def upgrade() -> None:
    """为幂等响应快照补齐与其他业务表一致的强制租户隔离。"""

    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute(f'ALTER TABLE "{TABLE}" ENABLE ROW LEVEL SECURITY')
    op.execute(f'ALTER TABLE "{TABLE}" FORCE ROW LEVEL SECURITY')
    op.execute(f'DROP POLICY IF EXISTS "{POLICY}" ON "{TABLE}"')
    op.execute(
        f'''CREATE POLICY "{POLICY}" ON "{TABLE}"
            USING (workspace_id = current_setting('app.current_workspace_id', true))
            WITH CHECK (workspace_id = current_setting('app.current_workspace_id', true))'''
    )


def downgrade() -> None:
    """回滚策略但保留幂等数据和唯一约束。"""

    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute(f'DROP POLICY IF EXISTS "{POLICY}" ON "{TABLE}"')
    op.execute(f'ALTER TABLE "{TABLE}" NO FORCE ROW LEVEL SECURITY')
    op.execute(f'ALTER TABLE "{TABLE}" DISABLE ROW LEVEL SECURITY')
