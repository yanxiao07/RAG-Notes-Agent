"""知识库 API 输入输出模型。"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


def _to_camel(value: str) -> str:
    head, *tail = value.split("_")
    return head + "".join(part.title() for part in tail)


class ApiModel(BaseModel):
    model_config = ConfigDict(populate_by_name=True, alias_generator=lambda value: _to_camel(value))


class CreateKnowledgeBaseRequest(ApiModel):
    name: str = Field(min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=1000)


class UpdateKnowledgeBaseRequest(ApiModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=1000)


class KnowledgeBaseResponse(ApiModel):
    id: str
    workspace_id: str
    name: str
    description: str | None
    status: str
    embedding_revision: int
    index_status: str
    graph_revision: int
    graph_status: str
    created_at: datetime
    updated_at: datetime


class CreateNoteRequest(ApiModel):
    title: str = Field(min_length=1, max_length=240)
    content: str = Field(default="", max_length=500_000)


class UpdateNoteRequest(ApiModel):
    title: str | None = Field(default=None, min_length=1, max_length=240)
    content: str | None = Field(default=None, max_length=500_000)
    version: int = Field(ge=1)


class NoteResponse(ApiModel):
    id: str
    workspace_id: str
    knowledge_base_id: str
    title: str
    content: str
    version: int
    status: str
    created_at: datetime
    updated_at: datetime


class PaginationMeta(ApiModel):
    offset: int
    limit: int
    total: int


class KnowledgeBaseListResponse(ApiModel):
    items: list[KnowledgeBaseResponse]
    meta: PaginationMeta


class NoteListResponse(ApiModel):
    items: list[NoteResponse]
    meta: PaginationMeta


class CreateKnowledgeTagRequest(ApiModel):
    name: str = Field(min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=500)


class ArchiveKnowledgeTagRequest(ApiModel):
    version: int = Field(ge=1)


class KnowledgeTagResponse(ApiModel):
    id: str
    workspace_id: str
    knowledge_base_id: str
    name: str
    description: str | None
    state: str
    version: int
    created_at: datetime
    updated_at: datetime


class KnowledgeTagListResponse(ApiModel):
    items: list[KnowledgeTagResponse]


class CreateTagAssignmentRequest(ApiModel):
    tag_id: str
    asset_type: str = Field(pattern="^(document|note)$")
    asset_id: str


class TagAssignmentResponse(ApiModel):
    id: str
    workspace_id: str
    knowledge_base_id: str
    tag_id: str
    tag_name: str
    asset_type: str
    asset_id: str
    state: str
    source: str
    confidence: float | None
    reviewer_id: str | None
    reviewed_at: datetime | None
    created_at: datetime
    updated_at: datetime


class TagAssignmentListResponse(ApiModel):
    items: list[TagAssignmentResponse]


class ReviewTagAssignmentRequest(ApiModel):
    decision: str = Field(pattern="^(approved|rejected)$")
