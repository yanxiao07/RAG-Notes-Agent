"""数据库归档脚本的安全契约检查。"""

from pathlib import Path

SCRIPT_PATH = (
    Path(__file__).resolve().parents[2]
    / "docker"
    / "postgres"
    / "maintenance"
    / "database_maintenance.sh"
)


def test_database_maintenance_script_keeps_backup_and_restore_guardrails() -> None:
    script = SCRIPT_PATH.read_text(encoding="utf-8")

    assert "pg_dump --format=custom" in script
    assert "pg_restore --list" in script
    assert "BACKUP_ALREADY_EXISTS" in script
    assert "rag_notes_restore_" in script
    assert "RESTORE_CONFIRMATION_REQUIRED" in script
    assert 'if [ "$RESTORE_CONFIRM" != "$target_database" ]' in script
    assert "RESTORE_TARGET_ALREADY_EXISTS" in script
    assert "DROP DATABASE IF EXISTS" in script


def test_database_maintenance_script_does_not_echo_database_password() -> None:
    script = SCRIPT_PATH.read_text(encoding="utf-8")

    assert "PGPASSWORD" in script
    assert "echo \"$PGPASSWORD\"" not in script
    assert "echo $PGPASSWORD" not in script
