"""工作区领域模型。

工作区是所有业务数据的租户根。请求层解析工作区后，应用服务必须把该值
传递到仓储查询，避免仅依赖客户端传入的资源 ID 导致跨租户数据泄露。
"""

from datetime import UTC, datetime
from typing import TYPE_CHECKING
from uuid import uuid4

from sqlalchemy import Boolean, DateTime, ForeignKey, String, UniqueConstraint
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


class User(Base):
    """平台用户的最小身份档案。

    本阶段不实现密码、注册或第三方 SSO；该表只承载可审计的主体身份，
    防止把可变的展示名或客户端 Header 当作授权依据。
    """

    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    email: Mapped[str] = mapped_column(String(320), nullable=False, unique=True, index=True)
    display_name: Mapped[str] = mapped_column(String(120), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active", index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )


class WorkspaceMembership(Base):
    """用户在单个工作区中的授权关系，角色只能由服务端维护。"""

    __tablename__ = "workspace_memberships"
    __table_args__ = (UniqueConstraint("workspace_id", "user_id", name="ux_workspace_memberships"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    role: Mapped[str] = mapped_column(String(20), nullable=False, default="viewer", index=True)
    state: Mapped[str] = mapped_column(String(20), nullable=False, default="active", index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )


class WorkspaceAccessToken(Base):
    """工作区访问令牌的不可逆凭据记录。

    原始令牌仅在创建响应中返回一次；后续认证只比较 SHA-256 摘要。
    该表有意不启用 RLS：认证发生在获得 workspace_id 之前，必须先定位
    令牌。它不提供任何业务查询接口，且查询条件始终是完整令牌哈希。
    """

    __tablename__ = "workspace_access_tokens"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    label: Mapped[str] = mapped_column(String(120), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    state: Mapped[str] = mapped_column(String(20), nullable=False, default="active", index=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )


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
