"""PostgreSQL 验收脚本的 migration 契约测试。"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "verify_postgres.py"


def _load_verify_postgres_module():
    spec = importlib.util.spec_from_file_location("verify_postgres", SCRIPT_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # dataclass 在处理 postponed annotations 时会通过 sys.modules 查找模块命名空间。
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    finally:
        sys.modules.pop(spec.name, None)
    return module


def test_postgres_verification_uses_repository_migration_heads() -> None:
    module = _load_verify_postgres_module()

    # 当前只有一个主线 head；断言具体值可在新增迁移时提醒更新验收报告，
    # 而脚本本身仍会动态读取，不会因忘改常量出现误报。
    assert module._expected_migration_heads() == {"c2d3e4f5a6b7"}
