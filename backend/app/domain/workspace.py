"""工作区领域模型。

工作区是所有业务数据的租户根。请求层解析工作区后，应用服务必须把该值
传递到仓储查询，避免仅依赖客户端传入的资源 ID 导致跨租户数据泄露。
"""

from datetime import UTC, datetime
from typing import TYPE_CHECKING
from uuid import uuid4

from sqlalchemy import Boolean, DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base

if TYPE_CHECKING:
    from app.domain.knowledge.models import KnowledgeBase


def utc_now() -> datetime:
    return datetime.now(UTC)


class Workspace(Base):
    __tablename__ = "workspaces"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active", index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )

    knowledge_bases: Mapped[list["KnowledgeBase"]] = relationship(back_populates="workspace")


class WorkspaceModelConfiguration(Base):
    """工作区的 Provider 选择和加密后的密钥，不向 API 返回密文。"""

    __tablename__ = "workspace_model_configurations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="RESTRICT"), nullable=False, unique=True, index=True
    )
    llm_provider: Mapped[str] = mapped_column(
        String(80), nullable=False, default="evidence_synthesis"
    )
    llm_model: Mapped[str] = mapped_column(String(160), nullable=False, default="")
    llm_base_url: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    llm_api_key_encrypted: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    embedding_provider: Mapped[str] = mapped_column(String(80), nullable=False, default="hashing")
    embedding_model: Mapped[str] = mapped_column(String(160), nullable=False, default="hashing-256")
    embedding_base_url: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    embedding_api_key_encrypted: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    embedding_dimensions: Mapped[int] = mapped_column(nullable=False, default=256)
    # 每次影响向量语义空间的配置变更都会递增。仅密钥轮换不需要重建索引。
    embedding_revision: Mapped[int] = mapped_column(nullable=False, default=1)
    use_query_rewrite: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # 只让 LLM Router 处理规则无法确定的灰区，系统策略仍由代码优先执行。
    use_query_router: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    use_reranker: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    reranker_provider: Mapped[str] = mapped_column(String(80), nullable=False, default="rule")
    reranker_model: Mapped[str] = mapped_column(String(160), nullable=False, default="")
    reranker_base_url: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    reranker_api_key_encrypted: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )
