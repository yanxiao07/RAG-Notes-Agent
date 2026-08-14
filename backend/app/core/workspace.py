"""请求级工作区和轻量 API Key 认证。

MVP 不引入用户中心，但保留稳定的租户边界：生产环境可用
``APP_WORKSPACE_API_KEYS`` 将 API Key 绑定到一个工作区，应用服务仍然只
接收已经解析好的 workspace_id。
"""

from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Header, Request
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.database import get_session, set_workspace_scope
from app.core.errors import (
    AuthenticationError,
    AuthorizationError,
    ResourceNotFoundError,
)
from app.domain.workspace import Workspace


@dataclass(frozen=True, slots=True)
class WorkspaceContext:
    """单次请求的租户和操作者信息。"""

    workspace_id: str
    actor_id: str | None
    # 角色必须来自数据库成员关系或部署侧 bootstrap 映射，绝不信任客户端自报 Header。
    actor_role: str


def _configured_api_keys() -> dict[str, str]:
    """解析 ``workspace_id=api_key`` 配置，忽略格式错误项并避免记录密钥。"""

    bindings: dict[str, str] = {}
    for item in get_settings().workspace_api_keys.split(","):
        if "=" not in item:
            continue
        workspace_id, api_key = (part.strip() for part in item.split("=", 1))
        if workspace_id and api_key:
            bindings[workspace_id] = api_key
    return bindings


def configured_actor_role(
    *,
    workspace_id: str,
    actor_id: str | None,
    claimed_role: str | None = None,
    trusted_role: str | None = None,
) -> str:
    """解析审批角色。

    开启认证后角色只能来自部署侧映射，不能仅凭请求头自报角色；本地开发保持 owner
    默认值，兼容不携带身份头的单工作区测试和演示。
    """

    if trusted_role is not None:
        normalized_trusted_role = trusted_role.lower()
        if normalized_trusted_role not in {"viewer", "editor", "approver", "owner"}:
            raise AuthorizationError(message="服务端解析的操作角色无效。")
        return normalized_trusted_role

    settings = get_settings()
    mappings: dict[tuple[str, str], str] = {}
    for item in settings.workspace_actor_roles.split(","):
        if "=" not in item or ":" not in item.split("=", 1)[0]:
            continue
        scope, role = (part.strip() for part in item.split("=", 1))
        target_workspace, target_actor = (part.strip() for part in scope.split(":", 1))
        normalized_role = role.lower()
        if (
            target_workspace
            and target_actor
            and normalized_role in {"viewer", "editor", "approver", "owner"}
        ):
            mappings[(target_workspace, target_actor)] = normalized_role
    configured = mappings.get((workspace_id, actor_id or ""))
    if settings.auth_enabled:
        if not actor_id:
            raise AuthenticationError(message="审批操作需要操作者身份。")
        if configured is None:
            raise AuthorizationError(message="当前操作者没有审批权限。")
        if claimed_role and claimed_role.lower() != configured:
            raise AuthorizationError(message="操作者角色与部署侧权限不一致。")
        return configured
    return (claimed_role or configured or "owner").lower()


def require_workspace_role(workspace: WorkspaceContext, *, minimum: str) -> None:
    """校验已解析上下文的最小角色，集中避免各路由出现不一致的权限表。"""

    levels = {"viewer": 0, "editor": 1, "approver": 2, "owner": 3}
    if levels.get(workspace.actor_role, -1) < levels.get(minimum, 99):
        raise AuthorizationError(message="当前角色没有执行该操作的权限。")


def ensure_workspace(
    session: Session, *, workspace_id: str | None = None, create_default: bool = True
) -> Workspace:
    """返回工作区；仅允许自动创建配置的默认工作区。

    这样既兼容本地 ``Base.metadata.create_all`` 的开发流程，也不会因为用户
    构造任意 ``X-Workspace-ID`` 就在数据库中产生租户。
    """

    settings = get_settings()
    target_id = workspace_id or settings.default_workspace_id
    # 在任何工作区查询之前设置 RLS 上下文；未设置的 PostgreSQL 事务默认看不到租户数据。
    set_workspace_scope(session, target_id)
    workspace = session.get(Workspace, target_id)
    if workspace is None and create_default and target_id == settings.default_workspace_id:
        workspace = Workspace(id=target_id, name=settings.default_workspace_name)
        session.add(workspace)
        try:
            session.commit()
        except IntegrityError:
            # 多个首请求可能同时初始化默认租户；竞争者回滚后读取已提交的行。
            session.rollback()
            set_workspace_scope(session, target_id)
            workspace = session.get(Workspace, target_id)
            if workspace is None:
                raise
        # 默认工作区初始化会提交事务；Session 级租户上下文需在提交后重新绑定。
        set_workspace_scope(session, target_id)
        session.refresh(workspace)
    if workspace is None or workspace.status != "active":
        raise ResourceNotFoundError(details={"resource": "workspace"})
    return workspace


def get_workspace_context(
    request: Request,
    session: Annotated[Session, Depends(get_session)],
    workspace_header: Annotated[str | None, Header(alias="X-Workspace-ID")] = None,
    api_key: Annotated[str | None, Header(alias="X-API-Key")] = None,
    actor_id: Annotated[str | None, Header(alias="X-Actor-ID")] = None,
    actor_role_header: Annotated[str | None, Header(alias="X-Actor-Role")] = None,
) -> WorkspaceContext:
    """解析并验证请求工作区。

    ``auth_enabled=false`` 时允许省略请求头，便于本地单工作区开发；开启后
    必须提供有效 API Key，且 Key 绑定的工作区不能被请求头覆盖。
    """

    del request  # 保留 Request 参数，方便未来接入认证审计和 trace context。
    settings = get_settings()
    bindings = _configured_api_keys()
    resolved_actor_id = actor_id
    resolved_actor_role: str
    if settings.auth_enabled:
        if not api_key:
            raise AuthenticationError()
        # 数据库令牌优先，静态 Key 仅用于初始 owner 引导和旧部署平滑迁移。
        # 延迟导入避免 core 与 application 在模块加载阶段形成循环依赖。
        from app.application.workspace_access_service import WorkspaceAccessService

        database_token = WorkspaceAccessService().resolve_access_token(session, raw_token=api_key)
        bound_workspace_id = database_token.workspace_id if database_token is not None else None
        if database_token is not None:
            membership = WorkspaceAccessService().current_membership(
                session, workspace_id=database_token.workspace_id, user_id=database_token.user_id
            )
            if membership is None:
                raise AuthenticationError()
            resolved_actor_id = database_token.user_id
            resolved_actor_role = membership.role
        else:
            bound_workspace_id = next(
                (
                    configured_workspace_id
                    for configured_workspace_id, configured_key in bindings.items()
                    if configured_key == api_key
                ),
                None,
            )
            # Bootstrap Key 的角色仍由部署侧映射决定；未配置时只能作为 owner 管理初始成员。
            if bound_workspace_id is None:
                resolved_actor_role = "viewer"
            elif actor_id is not None or settings.workspace_actor_roles.strip():
                resolved_actor_role = configured_actor_role(
                    workspace_id=bound_workspace_id,
                    actor_id=actor_id,
                )
            else:
                # 旧版单一 bootstrap Key 没有用户标识；仅用于建立首个 owner 成员。
                resolved_actor_role = "owner"
        if bound_workspace_id is None:
            raise AuthenticationError()
        if workspace_header and workspace_header != bound_workspace_id:
            raise AuthorizationError()
        workspace_id = bound_workspace_id
    else:
        workspace_id = workspace_header or settings.default_workspace_id
        resolved_actor_role = configured_actor_role(
            workspace_id=workspace_id,
            actor_id=actor_id,
            # 本地无认证开发可用 Header 覆盖角色以覆盖拒绝路径；生产认证分支不会读取它。
            claimed_role=actor_role_header,
        )

    ensure_workspace(
        session,
        workspace_id=workspace_id,
        create_default=not settings.auth_enabled,
    )
    return WorkspaceContext(
        workspace_id=workspace_id,
        actor_id=resolved_actor_id,
        actor_role=resolved_actor_role,
    )


WorkspaceDependency = Annotated[WorkspaceContext, Depends(get_workspace_context)]
