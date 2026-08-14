"""当前工作区查询接口。"""

from typing import Annotated

from fastapi import APIRouter, Depends, Response, status
from sqlalchemy.orm import Session

from app.api.schemas.workspace import (
    CreateWorkspaceAccessTokenRequest,
    CreateWorkspaceAccessTokenResponse,
    CreateWorkspaceMemberRequest,
    CurrentWorkspaceIdentityResponse,
    UpdateWorkspaceMemberRequest,
    WorkspaceAccessTokenListResponse,
    WorkspaceAccessTokenResponse,
    WorkspaceMemberListResponse,
    WorkspaceMemberResponse,
    WorkspaceResponse,
)
from app.application.workspace_access_service import WorkspaceAccessService
from app.core.database import get_session
from app.core.workspace import WorkspaceDependency, ensure_workspace, require_workspace_role
from app.domain.workspace import User, WorkspaceAccessToken, WorkspaceMembership

router = APIRouter(tags=["Workspace"])
SessionDependency = Annotated[Session, Depends(get_session)]


def to_member_response(member: WorkspaceMembership, user: User) -> WorkspaceMemberResponse:
    return WorkspaceMemberResponse(
        id=member.id,
        user_id=user.id,
        email=user.email,
        display_name=user.display_name,
        role=member.role,
        state=member.state,
        created_at=member.created_at,
        updated_at=member.updated_at,
    )


def to_token_response(token: WorkspaceAccessToken) -> WorkspaceAccessTokenResponse:
    return WorkspaceAccessTokenResponse.model_validate(token, from_attributes=True)


@router.get("/workspace", response_model=WorkspaceResponse)
def get_current_workspace(
    session: SessionDependency,
    workspace: WorkspaceDependency,
) -> WorkspaceResponse:
    """返回请求头解析出的工作区，供前端初始化租户上下文。"""

    current = ensure_workspace(session, workspace_id=workspace.workspace_id, create_default=False)
    return WorkspaceResponse.model_validate(current, from_attributes=True)


@router.get("/workspace/identity", response_model=CurrentWorkspaceIdentityResponse)
def get_current_workspace_identity(
    workspace: WorkspaceDependency,
) -> CurrentWorkspaceIdentityResponse:
    """返回服务器解析后的身份，客户端不得自行推导或缓存角色。"""

    return CurrentWorkspaceIdentityResponse(
        workspace_id=workspace.workspace_id,
        actor_id=workspace.actor_id,
        actor_role=workspace.actor_role,
    )


@router.get("/workspace/members", response_model=WorkspaceMemberListResponse)
def list_workspace_members(
    session: SessionDependency, workspace: WorkspaceDependency
) -> WorkspaceMemberListResponse:
    require_workspace_role(workspace, minimum="owner")
    items = WorkspaceAccessService().list_members(session, workspace_id=workspace.workspace_id)
    return WorkspaceMemberListResponse(
        items=[to_member_response(member, user) for member, user in items]
    )


@router.post(
    "/workspace/members",
    response_model=WorkspaceMemberResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_workspace_member(
    payload: CreateWorkspaceMemberRequest,
    session: SessionDependency,
    workspace: WorkspaceDependency,
) -> WorkspaceMemberResponse:
    require_workspace_role(workspace, minimum="owner")
    member, user = WorkspaceAccessService().create_member(
        session,
        workspace_id=workspace.workspace_id,
        actor_id=workspace.actor_id,
        email=payload.email,
        display_name=payload.display_name,
        role=payload.role,
    )
    return to_member_response(member, user)


@router.patch("/workspace/members/{user_id}", response_model=WorkspaceMemberResponse)
def update_workspace_member(
    user_id: str,
    payload: UpdateWorkspaceMemberRequest,
    session: SessionDependency,
    workspace: WorkspaceDependency,
) -> WorkspaceMemberResponse:
    require_workspace_role(workspace, minimum="owner")
    member, user = WorkspaceAccessService().update_member(
        session,
        workspace_id=workspace.workspace_id,
        actor_id=workspace.actor_id,
        user_id=user_id,
        role=payload.role,
        state=payload.state,
    )
    return to_member_response(member, user)


@router.get("/workspace/access-tokens", response_model=WorkspaceAccessTokenListResponse)
def list_workspace_access_tokens(
    session: SessionDependency, workspace: WorkspaceDependency
) -> WorkspaceAccessTokenListResponse:
    require_workspace_role(workspace, minimum="owner")
    items = WorkspaceAccessService().list_tokens(session, workspace_id=workspace.workspace_id)
    return WorkspaceAccessTokenListResponse(items=[to_token_response(item) for item in items])


@router.post(
    "/workspace/access-tokens",
    response_model=CreateWorkspaceAccessTokenResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_workspace_access_token(
    payload: CreateWorkspaceAccessTokenRequest,
    session: SessionDependency,
    workspace: WorkspaceDependency,
) -> CreateWorkspaceAccessTokenResponse:
    require_workspace_role(workspace, minimum="owner")
    token, raw_token = WorkspaceAccessService().create_token(
        session,
        workspace_id=workspace.workspace_id,
        actor_id=workspace.actor_id,
        user_id=payload.user_id,
        label=payload.label,
        expires_at=payload.expires_at,
    )
    return CreateWorkspaceAccessTokenResponse(
        **to_token_response(token).model_dump(), access_token=raw_token
    )


@router.delete("/workspace/access-tokens/{token_id}", status_code=status.HTTP_204_NO_CONTENT)
def revoke_workspace_access_token(
    token_id: str,
    session: SessionDependency,
    workspace: WorkspaceDependency,
) -> Response:
    require_workspace_role(workspace, minimum="owner")
    WorkspaceAccessService().revoke_token(
        session,
        workspace_id=workspace.workspace_id,
        actor_id=workspace.actor_id,
        token_id=token_id,
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)
