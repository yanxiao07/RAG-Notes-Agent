"""知识库思维导图 HTTP 路由。"""

from typing import Annotated

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.api.schemas.mind_map import (
    MindMapListResponse,
    MindMapResponse,
    UpdateMindMapRequest,
)
from app.application.mind_map_service import MindMapService
from app.core.database import get_session
from app.core.workspace import WorkspaceDependency
from app.domain.knowledge.models import KnowledgeMindMap

router = APIRouter(tags=["Knowledge Mind Maps"])
SessionDependency = Annotated[Session, Depends(get_session)]


def to_response(mind_map: KnowledgeMindMap) -> MindMapResponse:
    return MindMapResponse.model_validate(mind_map, from_attributes=True)


@router.get("/knowledge-bases/{knowledge_base_id}/mind-maps", response_model=MindMapListResponse)
def list_mind_maps(
    knowledge_base_id: str, session: SessionDependency, workspace: WorkspaceDependency
) -> MindMapListResponse:
    items = MindMapService().list_maps(
        session, knowledge_base_id=knowledge_base_id, workspace_id=workspace.workspace_id
    )
    return MindMapListResponse(items=[to_response(item) for item in items])


@router.post(
    "/knowledge-bases/{knowledge_base_id}/mind-maps/generate",
    response_model=MindMapResponse,
    status_code=status.HTTP_201_CREATED,
)
def generate_mind_map(
    knowledge_base_id: str, session: SessionDependency, workspace: WorkspaceDependency
) -> MindMapResponse:
    return to_response(
        MindMapService().generate(
            session, knowledge_base_id=knowledge_base_id, workspace_id=workspace.workspace_id
        )
    )


@router.get("/mind-maps/{mind_map_id}", response_model=MindMapResponse)
def get_mind_map(
    mind_map_id: str, session: SessionDependency, workspace: WorkspaceDependency
) -> MindMapResponse:
    return to_response(
        MindMapService().get_map(
            session, mind_map_id=mind_map_id, workspace_id=workspace.workspace_id
        )
    )


@router.put("/mind-maps/{mind_map_id}", response_model=MindMapResponse)
def update_mind_map(
    mind_map_id: str,
    payload: UpdateMindMapRequest,
    session: SessionDependency,
    workspace: WorkspaceDependency,
) -> MindMapResponse:
    return to_response(
        MindMapService().update(
            session,
            mind_map_id=mind_map_id,
            title=payload.title,
            graph=payload.graph.model_dump(by_alias=True),
            expected_version=payload.version,
            workspace_id=workspace.workspace_id,
        )
    )
