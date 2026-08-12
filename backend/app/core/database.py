"""数据库引擎与事务依赖。仓储不得在此边界外自行提交事务。"""

from collections.abc import Generator
from typing import cast

from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.core.config import get_settings


class Base(DeclarativeBase):
    """所有 ORM 模型的声明基类。"""


def _engine_options(database_url: str) -> dict[str, object]:
    if database_url.startswith("sqlite"):
        return {"connect_args": {"check_same_thread": False}}
    return {"pool_pre_ping": True}


settings = get_settings()
engine = create_engine(settings.database_url, future=True, **_engine_options(settings.database_url))


class WorkspaceSession(Session):
    """在 commit 后恢复租户上下文的会话实现。

    SQLAlchemy 可能在 commit 后释放连接，下一次 refresh/查询会重新 checkout；
    记录在 ``Session.info`` 中的 workspace 用于自动恢复，不需要业务服务逐处补写。
    """

    def commit(self) -> None:
        workspace_id = self.info.get("workspace_id")
        super().commit()
        if isinstance(workspace_id, str) and workspace_id:
            set_workspace_scope(self, workspace_id)


SessionLocal = cast(
    sessionmaker[Session],
    sessionmaker(
        bind=engine,
        class_=WorkspaceSession,
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
    ),
)


@event.listens_for(engine, "checkout")
def _reset_workspace_context(dbapi_connection, connection_record, connection_proxy) -> None:
    """连接从池中取出前清理上一个 Session 的租户上下文。

    应用使用 session 级 ``set_config`` 以支持提交后刷新 ORM 实体；连接归还后，
    下一个 checkout 必须显式清理该配置，避免连接池复用造成跨租户可见性风险。
    """

    del connection_record, connection_proxy
    if engine.dialect.name != "postgresql":
        return
    with dbapi_connection.cursor() as cursor:
        cursor.execute("RESET app.current_workspace_id")


def get_session_factory() -> sessionmaker[Session]:
    """供后台任务获取新事务；测试可覆盖它以避免触达开发数据库。"""

    return SessionLocal


def set_workspace_scope(session: Session, workspace_id: str) -> None:
    """设置当前 SQLAlchemy Session 的数据库租户上下文。

    上下文在当前 Session 内跨 commit 保持，支持 ``commit -> refresh`` 的业务事务；
    连接池 checkout 时由 ``_reset_workspace_context`` 清理，避免连接复用时把上一个
    租户带入下一会话。SQLite 没有该能力，继续依赖应用层的 workspace 条件。
    """

    session.info["workspace_id"] = workspace_id
    bind = session.get_bind()
    if bind.dialect.name != "postgresql":
        return
    session.execute(
        text("SELECT set_config('app.current_workspace_id', :workspace_id, false)"),
        {"workspace_id": workspace_id},
    )


def get_session() -> Generator[Session, None, None]:
    """为每个 HTTP 请求提供独立会话并确保异常回滚。"""

    session = SessionLocal()
    try:
        yield session
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
