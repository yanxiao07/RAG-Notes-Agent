"""add graph community algorithm audit metadata

Revision ID: c2d3e4f5a6b7
Revises: b1c2d3e4f5a6
Create Date: 2026-08-14
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c2d3e4f5a6b7"
down_revision: str | None = "b1c2d3e4f5a6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """记录实际社区算法与回退状态，避免将配置意图误写为运行事实。"""

    bind = op.get_bind()
    columns = {
        column["name"]
        for column in sa.inspect(bind).get_columns("knowledge_community_summaries")
    }
    if "community_algorithm" not in columns:
        op.add_column(
            "knowledge_community_summaries",
            sa.Column(
                "community_algorithm",
                sa.String(length=80),
                nullable=False,
                server_default="connected_components",
            ),
        )
    if "community_algorithm_fallback" not in columns:
        op.add_column(
            "knowledge_community_summaries",
            sa.Column(
                "community_algorithm_fallback",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            ),
        )


def downgrade() -> None:
    """SQLite 保持前向迁移兼容；生产数据库可删除新增元数据列。"""

    if op.get_bind().dialect.name == "sqlite":
        return
    op.drop_column("knowledge_community_summaries", "community_algorithm_fallback")
    op.drop_column("knowledge_community_summaries", "community_algorithm")
