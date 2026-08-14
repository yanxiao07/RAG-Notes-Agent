"""add database-backed workspace users, memberships and access tokens

Revision ID: b1c2d3e4f5a6
Revises: a4b8c2d6e0f1
Create Date: 2026-08-14
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b1c2d3e4f5a6"
down_revision: str | None = "a4b8c2d6e0f1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """创建身份与授权基础表，并仅对工作区成员关系启用 RLS。"""

    op.create_table(
        "users",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("display_name", sa.String(length=120), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="active"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("email", name="uq_users_email"),
    )
    op.create_index("ix_users_email", "users", ["email"])
    op.create_index("ix_users_status", "users", ["status"])

    op.create_table(
        "workspace_memberships",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("workspace_id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("role", sa.String(length=20), nullable=False, server_default="viewer"),
        sa.Column("state", sa.String(length=20), nullable=False, server_default="active"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("workspace_id", "user_id", name="ux_workspace_memberships"),
    )
    for column in ("workspace_id", "user_id", "role", "state"):
        op.create_index(f"ix_workspace_memberships_{column}", "workspace_memberships", [column])

    op.create_table(
        "workspace_access_tokens",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("workspace_id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("label", sa.String(length=120), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("state", sa.String(length=20), nullable=False, server_default="active"),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("token_hash", name="uq_workspace_access_tokens_token_hash"),
    )
    for column in ("workspace_id", "user_id", "token_hash", "state"):
        op.create_index(f"ix_workspace_access_tokens_{column}", "workspace_access_tokens", [column])

    if op.get_bind().dialect.name != "postgresql":
        return
    # users 是跨工作区身份目录；访问令牌必须先用于认证才能获知 workspace_id。
    # 因此二者不使用 RLS。成员关系已获得工作区上下文，强制隔离。
    op.execute('ALTER TABLE "workspace_memberships" ENABLE ROW LEVEL SECURITY')
    op.execute('ALTER TABLE "workspace_memberships" FORCE ROW LEVEL SECURITY')
    op.execute(
        '''CREATE POLICY "workspace_memberships_workspace_isolation" ON "workspace_memberships"
           USING (workspace_id = current_setting('app.current_workspace_id', true))
           WITH CHECK (workspace_id = current_setting('app.current_workspace_id', true))'''
    )


def downgrade() -> None:
    """回滚仅移除本阶段新增的认证授权表。"""

    if op.get_bind().dialect.name == "postgresql":
        op.execute(
            'DROP POLICY IF EXISTS "workspace_memberships_workspace_isolation" '
            'ON "workspace_memberships"'
        )
        op.execute('ALTER TABLE "workspace_memberships" NO FORCE ROW LEVEL SECURITY')
        op.execute('ALTER TABLE "workspace_memberships" DISABLE ROW LEVEL SECURITY')
    op.drop_table("workspace_access_tokens")
    op.drop_table("workspace_memberships")
    op.drop_table("users")
