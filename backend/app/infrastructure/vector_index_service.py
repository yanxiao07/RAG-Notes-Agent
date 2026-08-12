"""pgvector HNSW 索引的维度隔离管理。"""

from __future__ import annotations

import hashlib
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session


class VectorIndexService:
    """按工作区和维度维护可安全使用的 pgvector HNSW 索引。"""

    def ensure_hnsw_indexes(self, session: Session, *, workspace_id: str, dimension: int) -> None:
        """在显式重建时，为当前工作区和维度建立部分 HNSW 索引。

        pgvector 的 HNSW 表达式索引需要固定维度。将索引限定为工作区和
        dimensions 后，不同模型输出可共存，普通文档导入也无需执行 DDL。
        """

        if session.get_bind().dialect.name != "postgresql":
            return
        if not 8 <= dimension <= 8192:
            raise ValueError("Embedding 维度必须在 8 到 8192 之间")

        # UUID 与摘要命名共同避免将外部输入拼接为任意 SQL 标识符。
        normalized_workspace_id = str(UUID(workspace_id))
        workspace_suffix = hashlib.sha256(normalized_workspace_id.encode()).hexdigest()[:12]
        predicate = (
            f"workspace_id = '{normalized_workspace_id}' "
            f"AND dimensions = {dimension} AND embedding_vector IS NOT NULL"
        )
        for table_name, prefix in (
            ("chunk_embeddings", "ix_chunk_embeddings_hnsw"),
            ("note_embeddings", "ix_note_embeddings_hnsw"),
        ):
            index_name = f"{prefix}_{workspace_suffix}_{dimension}"
            session.execute(
                text(
                    f"CREATE INDEX IF NOT EXISTS {index_name} ON {table_name} "
                    f"USING hnsw ((embedding_vector::vector({dimension})) vector_cosine_ops) "
                    f"WHERE {predicate}"
                )
            )
