"""用户、工作区成员和访问令牌的应用服务。"""

from datetime import UTC, datetime
from hashlib import sha256
from secrets import token_urlsafe

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.database import set_workspace_scope
from app.core.errors import ProcessingError, ResourceNotFoundError
from app.core.workspace import ensure_workspace
from app.domain.agent.models import AuditEvent
from app.domain.agent.repositories import AuditEventRepository
from app.domain.workspace import User, WorkspaceAccessToken, WorkspaceMembership

ROLE_LEVELS = {"viewer": 0, "editor": 1, "approver": 2, "owner": 3}
MEMBERSHIP_STATES = {"active", "disabled"}


def normalize_email(email: str) -> str:
    """统一邮箱比较口径，避免大小写差异创建重复身份。"""

    return email.strip().casefold()


def token_digest(raw_token: str) -> str:
    """只在内存中计算原始令牌摘要，禁止把原值传给日志或审计事件。"""

    return sha256(raw_token.encode("utf-8")).hexdigest()


class WorkspaceAccessService:
    """访问管理事务边界，路由层只负责声明 HTTP 契约。"""

    def __init__(self) -> None:
        self.audit_events = AuditEventRepository()

    def current_membership(
        self, session: Session, *, workspace_id: str, user_id: str
    ) -> WorkspaceMembership | None:
        return session.scalar(
            select(WorkspaceMembership).where(
                WorkspaceMembership.workspace_id == workspace_id,
                WorkspaceMembership.user_id == user_id,
                WorkspaceMembership.state == "active",
            )
        )

    def resolve_access_token(
        self, session: Session, *, raw_token: str
    ) -> WorkspaceAccessToken | None:
        """认证前按哈希定位令牌，并核验成员与用户仍处于启用状态。"""

        token = session.scalar(
            select(WorkspaceAccessToken).where(
                WorkspaceAccessToken.token_hash == token_digest(raw_token)
            )
        )
        if token is None or token.state != "active":
            return None
        now = datetime.now(UTC)
        if token.expires_at is not None and token.expires_at <= now:
            return None
        # 令牌定位成功后才能获得可信 workspace_id；随后立即建立 RLS 上下文，
        # 成员关系读取不会绕过租户隔离。
        set_workspace_scope(session, token.workspace_id)
        membership = self.current_membership(
            session, workspace_id=token.workspace_id, user_id=token.user_id
        )
        user = session.get(User, token.user_id)
        if membership is None or user is None or user.status != "active":
            return None
        # 每个请求都写 last_used_at 会放大读流量；分钟级采样已足够排障和追溯。
        if token.last_used_at is None or (now - token.last_used_at).total_seconds() >= 300:
            token.last_used_at = now
            session.commit()
        return token

    def list_members(
        self, session: Session, *, workspace_id: str
    ) -> list[tuple[WorkspaceMembership, User]]:
        ensure_workspace(session, workspace_id=workspace_id, create_default=False)
        return list(
            session.execute(
                select(WorkspaceMembership, User)
                .join(User, User.id == WorkspaceMembership.user_id)
                .where(WorkspaceMembership.workspace_id == workspace_id)
                .order_by(WorkspaceMembership.created_at.asc())
            ).tuples().all()
        )

    def create_member(
        self,
        session: Session,
        *,
        workspace_id: str,
        actor_id: str | None,
        email: str,
        display_name: str,
        role: str,
    ) -> tuple[WorkspaceMembership, User]:
        self._validate_role(role)
        workspace = ensure_workspace(session, workspace_id=workspace_id, create_default=False)
        normalized_email = normalize_email(email)
        user = session.scalar(select(User).where(User.email == normalized_email))
        if user is None:
            user = User(email=normalized_email, display_name=display_name.strip())
            session.add(user)
            session.flush()
        elif user.status != "active":
            raise ProcessingError(message="该用户当前已停用，不能加入工作区。")
        membership = session.scalar(
            select(WorkspaceMembership).where(
                WorkspaceMembership.workspace_id == workspace.id,
                WorkspaceMembership.user_id == user.id,
            )
        )
        if membership is not None:
            raise ProcessingError(message="该用户已是当前工作区成员。")
        membership = WorkspaceMembership(workspace_id=workspace.id, user_id=user.id, role=role)
        session.add(membership)
        # UUID default 在 flush 时生成，审计记录必须引用真实成员 ID。
        session.flush()
        self._audit(
            session,
            workspace_id=workspace.id,
            actor_id=actor_id,
            action="workspace_member_created",
            target_id=membership.id,
            payload={"role": role},
        )
        session.commit()
        session.refresh(membership)
        session.refresh(user)
        return membership, user

    def update_member(
        self,
        session: Session,
        *,
        workspace_id: str,
        actor_id: str | None,
        user_id: str,
        role: str | None,
        state: str | None,
    ) -> tuple[WorkspaceMembership, User]:
        if role is None and state is None:
            raise ProcessingError(message="至少需要修改成员角色或状态。")
        if role is not None:
            self._validate_role(role)
        if state is not None and state not in MEMBERSHIP_STATES:
            raise ProcessingError(message="成员状态不受支持。")
        ensure_workspace(session, workspace_id=workspace_id, create_default=False)
        membership = session.scalar(
            select(WorkspaceMembership)
            .where(
                WorkspaceMembership.workspace_id == workspace_id,
                WorkspaceMembership.user_id == user_id,
            )
            .with_for_update()
        )
        if membership is None:
            raise ResourceNotFoundError(details={"resource": "workspace_member"})
        next_role = role or membership.role
        next_state = state or membership.state
        if membership.role == "owner" and membership.state == "active" and (
            next_role != "owner" or next_state != "active"
        ):
            active_owner_count = session.scalar(
                select(func.count())
                .select_from(WorkspaceMembership)
                .where(
                    WorkspaceMembership.workspace_id == workspace_id,
                    WorkspaceMembership.role == "owner",
                    WorkspaceMembership.state == "active",
                )
                .with_for_update()
            )
            if active_owner_count is not None and active_owner_count <= 1:
                raise ProcessingError(message="工作区至少需要保留一名启用中的所有者。")
        membership.role = next_role
        membership.state = next_state
        user = session.get(User, membership.user_id)
        if user is None:
            raise ResourceNotFoundError(details={"resource": "user"})
        self._audit(
            session,
            workspace_id=workspace_id,
            actor_id=actor_id,
            action="workspace_member_updated",
            target_id=membership.id,
            payload={"role": membership.role, "state": membership.state},
        )
        session.commit()
        session.refresh(membership)
        return membership, user

    def list_tokens(self, session: Session, *, workspace_id: str) -> list[WorkspaceAccessToken]:
        ensure_workspace(session, workspace_id=workspace_id, create_default=False)
        return list(
            session.scalars(
                select(WorkspaceAccessToken)
                .where(WorkspaceAccessToken.workspace_id == workspace_id)
                .order_by(WorkspaceAccessToken.created_at.desc())
            )
        )

    def create_token(
        self,
        session: Session,
        *,
        workspace_id: str,
        actor_id: str | None,
        user_id: str,
        label: str,
        expires_at: datetime | None,
    ) -> tuple[WorkspaceAccessToken, str]:
        ensure_workspace(session, workspace_id=workspace_id, create_default=False)
        if expires_at is not None and expires_at <= datetime.now(UTC):
            raise ProcessingError(message="访问令牌过期时间必须晚于当前时间。")
        membership = self.current_membership(session, workspace_id=workspace_id, user_id=user_id)
        if membership is None:
            raise ProcessingError(message="只能为当前工作区的启用成员创建访问令牌。")
        raw_token = f"rna_{token_urlsafe(32)}"
        token = WorkspaceAccessToken(
            workspace_id=workspace_id,
            user_id=user_id,
            label=label.strip(),
            token_hash=token_digest(raw_token),
            expires_at=expires_at,
        )
        session.add(token)
        session.flush()
        self._audit(
            session,
            workspace_id=workspace_id,
            actor_id=actor_id,
            action="workspace_access_token_created",
            target_id=token.id,
            payload={"label": token.label, "role": membership.role},
        )
        session.commit()
        session.refresh(token)
        return token, raw_token

    def revoke_token(
        self, session: Session, *, workspace_id: str, actor_id: str | None, token_id: str
    ) -> None:
        ensure_workspace(session, workspace_id=workspace_id, create_default=False)
        token = session.scalar(
            select(WorkspaceAccessToken)
            .where(
                WorkspaceAccessToken.id == token_id,
                WorkspaceAccessToken.workspace_id == workspace_id,
            )
            .with_for_update()
        )
        if token is None:
            raise ResourceNotFoundError(details={"resource": "workspace_access_token"})
        if token.state != "revoked":
            token.state = "revoked"
            token.revoked_at = datetime.now(UTC)
            self._audit(
                session,
                workspace_id=workspace_id,
                actor_id=actor_id,
                action="workspace_access_token_revoked",
                target_id=token.id,
                payload={"label": token.label},
            )
            session.commit()

    @staticmethod
    def _validate_role(role: str) -> None:
        if role not in ROLE_LEVELS:
            raise ProcessingError(message="成员角色不受支持。")

    def _audit(
        self,
        session: Session,
        *,
        workspace_id: str,
        actor_id: str | None,
        action: str,
        target_id: str,
        payload: dict[str, str],
    ) -> None:
        self.audit_events.create(
            session,
            AuditEvent(
                workspace_id=workspace_id,
                actor_type="user",
                actor_id=actor_id,
                action=action,
                target_type="workspace_access",
                target_id=target_id,
                payload=payload,
            ),
        )
