#!/bin/sh
set -eu

: "${POSTGRES_APP_USER:?POSTGRES_APP_USER must be set}"
: "${POSTGRES_APP_PASSWORD:?POSTGRES_APP_PASSWORD must be set}"

# 初始化阶段由 bootstrap 管理员创建扩展和应用角色；后续迁移/API/Worker
# 使用非超级用户，避免应用进程拥有绕过 RLS 的能力。
psql_host_args=""
if [ -n "${PGHOST:-}" ]; then
  psql_host_args="--host=$PGHOST"
fi

psql $psql_host_args \
  --username "$POSTGRES_USER" \
  --dbname "$POSTGRES_DB" \
  --set=app_user="$POSTGRES_APP_USER" \
  --set=app_password="$POSTGRES_APP_PASSWORD" \
  --set=app_db="$POSTGRES_DB" <<'SQL'
CREATE EXTENSION IF NOT EXISTS vector;

SELECT format(
    CASE
        WHEN EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :'app_user')
            THEN 'ALTER ROLE %I LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT PASSWORD %L'
        ELSE 'CREATE ROLE %I LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT PASSWORD %L'
    END,
    :'app_user',
    :'app_password'
) \gexec

ALTER DATABASE :"app_db" OWNER TO :"app_user";
ALTER SCHEMA public OWNER TO :"app_user";
GRANT CONNECT ON DATABASE :"app_db" TO :"app_user";
GRANT USAGE, CREATE ON SCHEMA public TO :"app_user";

-- 旧数据卷中的业务对象仍可能属于初始化管理员，逐表转移所有权。
SELECT format('ALTER TABLE %I.%I OWNER TO %I', schemaname, tablename, :'app_user')
FROM pg_tables
WHERE schemaname = 'public'
\gexec
SELECT format('ALTER SEQUENCE %I.%I OWNER TO %I', sequence_schema, sequence_name, :'app_user')
FROM information_schema.sequences
WHERE sequence_schema = 'public'
\gexec
GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA public TO :"app_user";
GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public TO :"app_user";
SQL
