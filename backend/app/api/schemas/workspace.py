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
