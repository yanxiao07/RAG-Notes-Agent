"""验证 PostgreSQL/pgvector/RLS 生产前置条件。

脚本只输出检查项、数量和错误类型，不输出数据库连接串、用户问题或文档正文。
它不会修改数据库；RLS 演练仅在显式传入两个工作区后执行只读查询。
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy import inspect, text
from sqlalchemy.engine import Connection, Engine

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.core.config import get_settings  # noqa: E402

REQUIRED_TABLES = {
    "workspaces",
    "knowledge_bases",
    "notes",
    "documents",
    "document_chunks",
    "chunk_embeddings",
    "note_embeddings",
    "idempotency_records",
}
RLS_TABLES = REQUIRED_TABLES | {
    "ingestion_jobs",
    "workspace_model_configurations",
    "conversations",
    "conversation_messages",
    "agent_runs",
    "change_proposals",
    "audit_events",
    "knowledge_mind_maps",
}
REQUIRED_INDEXES = {
    "ix_chunk_embeddings_embedding_vector_hnsw",
    "ix_note_embeddings_embedding_vector_hnsw",
    "ix_document_chunks_content_fts",
    "ix_notes_content_fts",
}
# 与当前迁移链末端保持同步；新增迁移必须同时更新此验收契约并重跑报告。
EXPECTED_MIGRATION_HEAD = "b9c2d7e4f1a6"


@dataclass(frozen=True, slots=True)
class CheckResult:
    name: str
    ok: bool
    details: dict[str, Any]


def main() -> int:
    parser = argparse.ArgumentParser(description="验证 PostgreSQL、pgvector 和 workspace RLS")
    parser.add_argument("--workspace-id", help="RLS 演练使用的工作区 ID")
    parser.add_argument("--probe-workspace-id", help="RLS 演练使用的第二个工作区 ID")
    parser.add_argument(
        "--allow-superuser",
        action="store_true",
        help="允许使用 PostgreSQL superuser；不建议生产使用",
    )
    arguments = parser.parse_args()
    settings = get_settings()
    if not settings.database_url.startswith(("postgresql", "postgres")):
        return _print_report(
            [
                CheckResult(
                    "database_dialect",
                    False,
                    {"message": "APP_DATABASE_URL 不是 PostgreSQL，未执行生产验收"},
                )
            ],
            exit_code=2,
        )

    checks: list[CheckResult] = []
    try:
        # 将数据库模块延迟到 try 内导入，缺少 psycopg 时也输出结构化失败报告。
        from app.core.database import engine as application_engine

        engine = application_engine
        with engine.connect() as connection:
            checks.extend(_schema_checks(connection))
            checks.append(_superuser_check(connection, allow=arguments.allow_superuser))
            if arguments.workspace_id and arguments.probe_workspace_id:
                checks.extend(
                    _rls_probe(
                        engine,
                        workspace_id=arguments.workspace_id,
                        probe_workspace_id=arguments.probe_workspace_id,
                    )
                )
            else:
                checks.append(
                    CheckResult(
                        "rls_probe",
                        False,
                        {"message": "需要同时传入 --workspace-id 和 --probe-workspace-id"},
                    )
                )
    except Exception as exc:
        checks.append(
            CheckResult(
                "database_connection",
                False,
                {"errorType": type(exc).__name__, "message": "无法连接或读取 PostgreSQL"},
            )
        )
    return _print_report(checks, exit_code=0 if all(item.ok for item in checks) else 2)


def _schema_checks(connection: Connection) -> list[CheckResult]:
    inspector = inspect(connection)
    tables = set(inspector.get_table_names(schema="public"))
    checks = [
        CheckResult(
            "required_tables",
            tables >= REQUIRED_TABLES,
            {"missing": sorted(REQUIRED_TABLES - tables)},
        )
    ]
    migration_head = connection.scalar(text("SELECT version_num FROM alembic_version LIMIT 1"))
    checks.append(
        CheckResult(
            "migration_head",
            str(migration_head) == EXPECTED_MIGRATION_HEAD,
            {"current": str(migration_head) if migration_head else None},
        )
    )
    extension = bool(
        connection.scalar(
            text("SELECT EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'vector')")
        )
    )
    checks.append(CheckResult("pgvector_extension", extension, {}))
    indexes = {
        str(row[0])
        for row in connection.execute(
            text("SELECT indexname FROM pg_indexes WHERE schemaname = 'public'")
        )
    }
    checks.append(
        CheckResult(
            "production_indexes",
            indexes >= REQUIRED_INDEXES,
            {"missing": sorted(REQUIRED_INDEXES - indexes)},
        )
    )
    vector_columns = {
        str(row[0])
        for row in connection.execute(
            text(
                "SELECT table_name FROM information_schema.columns "
                "WHERE table_schema = 'public' AND column_name = 'embedding_vector'"
            )
        )
    }
    checks.append(
        CheckResult(
            "native_vector_columns",
            {"chunk_embeddings", "note_embeddings"} <= vector_columns,
            {"missing": sorted({"chunk_embeddings", "note_embeddings"} - vector_columns)},
        )
    )
    rls_rows = connection.execute(
        text(
            "SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity "
            "FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE n.nspname = 'public' AND c.relname = ANY(CAST(:tables AS text[]))"
        ),
        {"tables": list(RLS_TABLES)},
    ).all()
    rls_by_table = {str(row[0]): (bool(row[1]), bool(row[2])) for row in rls_rows}
    missing_rls = sorted(table for table in RLS_TABLES if rls_by_table.get(table) != (True, True))
    checks.append(CheckResult("forced_rls", not missing_rls, {"missing": missing_rls}))
    policies = {
        str(row[0])
        for row in connection.execute(
            text(
                "SELECT policyname FROM pg_policies "
                "WHERE schemaname = 'public' AND tablename = ANY(CAST(:tables AS text[]))"
            ),
            {"tables": list(RLS_TABLES)},
        )
    }
    expected_policies = {f"{table}_workspace_isolation" for table in RLS_TABLES}
    checks.append(
        CheckResult(
            "workspace_policies",
            expected_policies <= policies,
            {"missing": sorted(expected_policies - policies)},
        )
    )
    return checks


def _superuser_check(connection: Connection, *, allow: bool) -> CheckResult:
    row = connection.execute(
        text("SELECT current_user, rolsuper FROM pg_roles WHERE rolname = current_user")
    ).one()
    is_superuser = bool(row[1])
    return CheckResult(
        "non_superuser_role",
        allow or not is_superuser,
        {"role": str(row[0]), "isSuperuser": is_superuser},
    )


def _rls_probe(engine: Engine, *, workspace_id: str, probe_workspace_id: str) -> list[CheckResult]:
    counts = {
        workspace_id: _visible_workspace_count(engine, workspace_id),
        probe_workspace_id: _visible_workspace_count(engine, probe_workspace_id),
    }
    visible = counts[workspace_id] == 1 and counts[probe_workspace_id] == 1
    cross_scope_hidden = _visible_document_count(engine, workspace_id, probe_workspace_id) == 0
    no_context_hidden = _visible_without_context(engine) == 0
    return [
        CheckResult("rls_workspace_visibility", visible, {"visibleCounts": counts}),
        CheckResult(
            "rls_cross_workspace_hidden",
            cross_scope_hidden,
            {"message": "当前工作区不可读取另一个工作区的文档"},
        ),
        CheckResult(
            "rls_requires_context",
            no_context_hidden,
            {"message": "未绑定 workspace 上下文时业务表不可读"},
        ),
    ]


def _visible_workspace_count(engine: Engine, workspace_id: str) -> int:
    with engine.connect() as connection, connection.begin():
        connection.execute(
            text("SELECT set_config('app.current_workspace_id', :workspace_id, true)"),
            {"workspace_id": workspace_id},
        )
        return int(
            connection.scalar(
                text("SELECT count(*) FROM workspaces WHERE id = :workspace_id"),
                {"workspace_id": workspace_id},
            )
            or 0
        )


def _visible_document_count(engine: Engine, workspace_id: str, document_workspace_id: str) -> int:
    with engine.connect() as connection, connection.begin():
        connection.execute(
            text("SELECT set_config('app.current_workspace_id', :workspace_id, true)"),
            {"workspace_id": workspace_id},
        )
        return int(
            connection.scalar(
                text("SELECT count(*) FROM documents WHERE workspace_id = :workspace_id"),
                {"workspace_id": document_workspace_id},
            )
            or 0
        )


def _visible_without_context(engine: Engine) -> int:
    with engine.connect() as connection, connection.begin():
        connection.execute(text("SELECT set_config('app.current_workspace_id', '', true)"))
        return int(connection.scalar(text("SELECT count(*) FROM documents")) or 0)


def _print_report(checks: list[CheckResult], *, exit_code: int) -> int:
    print(
        json.dumps(
            {
                "ok": all(check.ok for check in checks),
                "checks": [
                    {"name": check.name, "ok": check.ok, "details": check.details}
                    for check in checks
                ],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
