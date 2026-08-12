"""当前工作区查询接口。"""

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.schemas.workspace import WorkspaceResponse
from app.core.database import get_session
from app.core.workspace import WorkspaceDependency, ensure_workspace

router = APIRouter(tags=["Workspace"])
SessionDependency = Annotated[Session, Depends(get_session)]


@router.get("/workspace", response_model=WorkspaceResponse)
def get_current_workspace(
    session: SessionDependency,
    workspace: WorkspaceDependency,
) -> WorkspaceResponse:
    """返回请求头解析出的工作区，供前端初始化租户上下文。"""

    current = ensure_workspace(session, workspace_id=workspace.workspace_id, create_default=False)
    return WorkspaceResponse.model_validate(current, from_attributes=True)
