"""工作区 API 输入输出模型。"""

from datetime import datetime

from pydantic import Field

from app.api.schemas.knowledge import ApiModel


class CreateWorkspaceRequest(ApiModel):
    name: str = Field(min_length=1, max_length=120)


class WorkspaceResponse(ApiModel):
    id: str
    name: str
    status: str
    created_at: datetime
    updated_at: datetime


class WorkspaceListResponse(ApiModel):
    items: list[WorkspaceResponse]


class CurrentWorkspaceIdentityResponse(ApiModel):
    workspace_id: str
    actor_id: str | None
    actor_role: str


class WorkspaceMemberResponse(ApiModel):
    id: str
    user_id: str
    email: str
    display_name: str
    role: str
    state: str
    created_at: datetime
    updated_at: datetime


class WorkspaceMemberListResponse(ApiModel):
    items: list[WorkspaceMemberResponse]


class CreateWorkspaceMemberRequest(ApiModel):
    email: str = Field(min_length=3, max_length=320)
    display_name: str = Field(min_length=1, max_length=120)
    role: str = Field(default="viewer", pattern="^(viewer|editor|approver|owner)$")


class UpdateWorkspaceMemberRequest(ApiModel):
    role: str | None = Field(default=None, pattern="^(viewer|editor|approver|owner)$")
    state: str | None = Field(default=None, pattern="^(active|disabled)$")


class WorkspaceAccessTokenResponse(ApiModel):
    id: str
    user_id: str
    label: str
    state: str
    expires_at: datetime | None
    last_used_at: datetime | None
    revoked_at: datetime | None
    created_at: datetime


class WorkspaceAccessTokenListResponse(ApiModel):
    items: list[WorkspaceAccessTokenResponse]


class CreateWorkspaceAccessTokenRequest(ApiModel):
    user_id: str = Field(min_length=1, max_length=36)
    label: str = Field(min_length=1, max_length=120)
    expires_at: datetime | None = None


class CreateWorkspaceAccessTokenResponse(WorkspaceAccessTokenResponse):
    # 原始令牌只在该响应出现一次，前端不得写入持久化状态或日志。
    access_token: str
