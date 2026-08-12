"""把历史 JSON 向量安全回填到 PostgreSQL pgvector 列。

回填按工作区执行，以适配启用 FORCE RLS 后的最小权限连接；脚本不会尝试关闭 RLS，
也不会打印正文、向量或连接串。失败时事务整体回滚，便于重新执行。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from sqlalchemy import text

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.core.config import get_settings  # noqa: E402
from app.core.database import get_session_factory, set_workspace_scope  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="回填 PostgreSQL pgvector 原生向量列")
    parser.add_argument("--workspace-id", required=True, help="本次回填的工作区 ID")
    parser.add_argument("--dimensions", type=int, default=None, help="目标向量维度")
    parser.add_argument("--dry-run", action="store_true", help="只统计待回填数量，不修改数据")
    arguments = parser.parse_args()
    settings = get_settings()
    if not settings.database_url.startswith(("postgresql", "postgres")):
        raise SystemExit("回填脚本仅允许连接 PostgreSQL，当前数据库配置不是 PostgreSQL。")
    dimensions = arguments.dimensions or settings.embedding_dimensions
    if not 8 <= dimensions <= 8192:
        raise SystemExit("目标向量维度必须在 8 到 8192 之间。")

    with get_session_factory()() as session:
        set_workspace_scope(session, arguments.workspace_id)
        counts = _pending_counts(session, arguments.workspace_id)
        result = {
            "workspaceId": arguments.workspace_id,
            "dimensions": dimensions,
            "dryRun": arguments.dry_run,
            "pendingChunkEmbeddings": counts["chunks"],
            "pendingNoteEmbeddings": counts["notes"],
        }
        if arguments.dry_run:
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0
        chunk_count = _backfill_table(
            session,
            table="chunk_embeddings",
            workspace_id=arguments.workspace_id,
            dimensions=dimensions,
        )
        note_count = _backfill_table(
            session,
            table="note_embeddings",
            workspace_id=arguments.workspace_id,
            dimensions=dimensions,
        )
        session.commit()
        result.update(
            {"backfilledChunkEmbeddings": chunk_count, "backfilledNoteEmbeddings": note_count}
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def _pending_counts(session, workspace_id: str) -> dict[str, int]:
    """统计同一工作区中尚未填充原生向量的行。"""

    result: dict[str, int] = {}
    for key, table in (("chunks", "chunk_embeddings"), ("notes", "note_embeddings")):
        result[key] = int(
            session.scalar(
                text(
                    f"SELECT count(*) FROM {table} "
                    "WHERE workspace_id = :workspace_id AND embedding_vector IS NULL"
                ),
                {"workspace_id": workspace_id},
            )
            or 0
        )
    return result


def _backfill_table(session, *, table: str, workspace_id: str, dimensions: int) -> int:
    """只回填目标维度，其他维度留给新的索引版本处理。"""

    result = session.execute(
        text(
            f"UPDATE {table} SET embedding_vector = embedding::text::vector "
            "WHERE workspace_id = :workspace_id AND embedding_vector IS NULL "
            "AND dimensions = :dimensions"
        ),
        {"workspace_id": workspace_id, "dimensions": dimensions},
    )
    return int(result.rowcount or 0)


if __name__ == "__main__":
    raise SystemExit(main())
