#!/bin/sh
# PostgreSQL 归档备份、校验与非破坏性恢复演练。
# 运行于 pgvector 官方镜像，宿主机无需安装 pg_dump/pg_restore。

set -eu

usage() {
  cat <<'EOF'
Usage:
  database_maintenance.sh backup
  database_maintenance.sh verify
  database_maintenance.sh restore

Environment:
  BACKUP_FILE       Required for verify/restore; backup defaults to rag-notes-<UTC>.dump.
  RESTORE_DATABASE  Required for restore; must start with rag_notes_restore_.
  RESTORE_CONFIRM   Required for restore; must equal RESTORE_DATABASE.
EOF
}

safe_file_name() {
  case "$1" in
    *"/"*|*"\\"*|""|.*|*".."*) return 1 ;;
    *.dump) return 0 ;;
    *) return 1 ;;
  esac
}

backup_file="${BACKUP_FILE:-rag-notes-$(date -u +%Y%m%dT%H%M%SZ).dump}"
if ! safe_file_name "$backup_file"; then
  echo '{"ok":false,"code":"INVALID_BACKUP_FILE"}' >&2
  exit 2
fi

archive_path="/backups/$backup_file"

backup() {
  if [ -e "$archive_path" ]; then
    echo '{"ok":false,"code":"BACKUP_ALREADY_EXISTS"}' >&2
    exit 2
  fi
  temporary_path="${archive_path}.tmp"
  trap 'rm -f "$temporary_path"' EXIT INT TERM
  # 自定义格式支持 TOC 校验和按对象恢复；密码只通过 PGPASSWORD 环境变量传入，不回显。
  pg_dump --format=custom --no-owner --no-privileges --file="$temporary_path" "$POSTGRES_DB"
  pg_restore --list "$temporary_path" >/dev/null
  mv "$temporary_path" "$archive_path"
  trap - EXIT INT TERM
  checksum=$(sha256sum "$archive_path" | awk '{print $1}')
  size=$(wc -c < "$archive_path" | tr -d ' ')
  printf '{"ok":true,"action":"backup","file":"%s","sha256":"%s","bytes":%s}\n' \
    "$backup_file" "$checksum" "$size"
}

verify() {
  if [ ! -f "$archive_path" ]; then
    echo '{"ok":false,"code":"BACKUP_NOT_FOUND"}' >&2
    exit 2
  fi
  pg_restore --list "$archive_path" >/dev/null
  checksum=$(sha256sum "$archive_path" | awk '{print $1}')
  size=$(wc -c < "$archive_path" | tr -d ' ')
  printf '{"ok":true,"action":"verify","file":"%s","sha256":"%s","bytes":%s}\n' \
    "$backup_file" "$checksum" "$size"
}

restore() {
  target_database="${RESTORE_DATABASE:-}"
  if [ ! -f "$archive_path" ]; then
    echo '{"ok":false,"code":"BACKUP_NOT_FOUND"}' >&2
    exit 2
  fi
  case "$target_database" in
    rag_notes_restore_[a-zA-Z0-9_]* ) ;;
    *)
      echo '{"ok":false,"code":"INVALID_RESTORE_TARGET"}' >&2
      exit 2
      ;;
  esac
  if [ "$RESTORE_CONFIRM" != "$target_database" ]; then
    echo '{"ok":false,"code":"RESTORE_CONFIRMATION_REQUIRED"}' >&2
    exit 2
  fi
  # 禁止恢复到源库，且目标库必须不存在，确保该命令仅用于恢复演练。
  if [ "$target_database" = "$POSTGRES_DB" ]; then
    echo '{"ok":false,"code":"RESTORE_SOURCE_TARGET_CONFLICT"}' >&2
    exit 2
  fi
  if psql --tuples-only --no-align --command \
    "SELECT 1 FROM pg_database WHERE datname = '$target_database'" postgres | grep -q '^1$'; then
    echo '{"ok":false,"code":"RESTORE_TARGET_ALREADY_EXISTS"}' >&2
    exit 2
  fi
  psql --command "CREATE DATABASE \"$target_database\"" postgres
  if ! pg_restore --no-owner --no-privileges --dbname="$target_database" "$archive_path"; then
    # 失败时只清理本次新建的演练库，不触碰源库或其他数据库。
    psql --command "DROP DATABASE IF EXISTS \"$target_database\"" postgres
    echo '{"ok":false,"code":"RESTORE_FAILED"}' >&2
    exit 1
  fi
  object_count=$(psql --tuples-only --no-align --dbname="$target_database" \
    --command "SELECT count(*) FROM information_schema.tables WHERE table_schema = 'public'" | tr -d '[:space:]')
  printf '{"ok":true,"action":"restore","file":"%s","database":"%s","publicTableCount":%s}\n' \
    "$backup_file" "$target_database" "$object_count"
}

case "${1:-}" in
  backup) backup ;;
  verify) verify ;;
  restore) restore ;;
  *) usage >&2; exit 2 ;;
esac
