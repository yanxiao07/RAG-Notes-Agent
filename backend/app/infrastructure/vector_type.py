"""跨 SQLite/ PostgreSQL 的向量列类型。

本地开发仍使用 JSON 保存确定性向量，生产 PostgreSQL 使用 pgvector 的原生
``vector`` 类型。把差异封装在 TypeDecorator 内，领域模型和入库用例无需判断数据库方言。
"""

from __future__ import annotations

from importlib import import_module
from typing import Any

from sqlalchemy.engine import Dialect
from sqlalchemy.types import JSON, TypeDecorator


class EmbeddingVectorType(TypeDecorator[list[float]]):
    """SQLite 降级为 JSON，PostgreSQL 映射为 pgvector。"""

    impl = JSON
    cache_ok = True

    def load_dialect_impl(self, dialect: Dialect) -> Any:
        if dialect.name != "postgresql":
            return dialect.type_descriptor(JSON())
        try:
            vector_module = import_module("pgvector.sqlalchemy")
        except ImportError as exc:  # pragma: no cover - 仅生产依赖缺失时触发
            raise RuntimeError(
                "PostgreSQL 检索需要安装可选依赖 pgvector，请使用 postgres extra。"
            ) from exc
        # 不在 ORM 层固定维度；应用仍通过 dimensions 和 embedding_revision 做门控。
        return dialect.type_descriptor(vector_module.Vector())
