"""向量索引服务的方言降级与输入边界测试。"""

import pytest
from sqlalchemy.orm import Session, sessionmaker

from app.infrastructure.vector_index_service import VectorIndexService


def test_vector_index_service_is_noop_for_sqlite(session_factory: sessionmaker[Session]) -> None:
    with session_factory() as session:
        VectorIndexService().ensure_hnsw_indexes(
            session,
            workspace_id="00000000-0000-0000-0000-000000000001",
            dimension=1536,
        )


def test_vector_index_service_validates_dimension_before_postgres_ddl(
    session_factory: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with session_factory() as session:
        monkeypatch.setattr(session.get_bind().dialect, "name", "postgresql")
        with pytest.raises(ValueError, match="Embedding"):
            VectorIndexService().ensure_hnsw_indexes(
                session,
                workspace_id="00000000-0000-0000-0000-000000000001",
                dimension=7,
            )
