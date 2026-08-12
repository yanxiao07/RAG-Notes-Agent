"""可编辑知识库思维导图 API Schema。"""

from datetime import datetime

from pydantic import Field

from app.api.schemas.knowledge import ApiModel


class MindMapNode(ApiModel):
    id: str = Field(min_length=1, max_length=160)
    label: str = Field(min_length=1, max_length=500)
    kind: str = Field(min_length=1, max_length=40)
    position: dict[str, float]


class MindMapEdge(ApiModel):
    id: str = Field(min_length=1, max_length=200)
    source: str = Field(min_length=1, max_length=160)
    target: str = Field(min_length=1, max_length=160)


class MindMapGraph(ApiModel):
    nodes: list[MindMapNode] = Field(max_length=200)
    edges: list[MindMapEdge] = Field(max_length=400)


class MindMapResponse(ApiModel):
    id: str
    workspace_id: str
    knowledge_base_id: str
    title: str
    graph: MindMapGraph
    version: int
    created_at: datetime
    updated_at: datetime


class UpdateMindMapRequest(ApiModel):
    title: str = Field(min_length=1, max_length=240)
    graph: MindMapGraph
    version: int = Field(ge=1)


class MindMapListResponse(ApiModel):
    items: list[MindMapResponse]
