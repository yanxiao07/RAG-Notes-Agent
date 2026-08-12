"""测试基础设施：每个测试使用独立 SQLite 数据库，避免外部服务依赖。"""

from collections.abc import Generator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app import main
from app.core.database import Base, get_session, get_session_factory
from app.main import create_app


@pytest.fixture()
def session_factory(tmp_path: Path) -> Generator[sessionmaker[Session], None, None]:
    database_path = tmp_path / "test.db"
    engine = create_engine(f"sqlite:///{database_path}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
    try:
        yield factory
    finally:
        Base.metadata.drop_all(engine)
        engine.dispose()


@pytest.fixture()
def client(session_factory: sessionmaker[Session]) -> Generator[TestClient, None, None]:
    app = create_app()
    previous_environment = main.settings.environment
    # 测试库已由 fixture 显式建表，不能让 lifespan 对开发数据库再次执行 create_all。
    main.settings.environment = "test"

    def override_session() -> Generator[Session, None, None]:
        session = session_factory()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_session] = override_session
    app.dependency_overrides[get_session_factory] = lambda: session_factory
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()
    main.settings.environment = previous_environment
