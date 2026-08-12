"""文档与入库任务 API Schema。"""

from datetime import datetime

from pydantic import Field

from app.api.schemas.knowledge import ApiModel, PaginationMeta


class CreateDocumentRequest(ApiModel):
    knowledge_base_id: str
    title: str = Field(min_length=1, max_length=240)
    source_type: str = Field(default="plain_text", min_length=1, max_length=40)
    content: str = Field(min_length=1, max_length=2_000_000)
    parser: str = Field(default="plain_text", min_length=1, max_length=80)
    chunker: str = Field(default="structured", min_length=1, max_length=80)


class CreateUrlDocumentRequest(ApiModel):
    """网页导入请求；抓取在后台 Worker 执行，避免阻塞 HTTP 请求。"""

    knowledge_base_id: str
    url: str = Field(min_length=8, max_length=2_000)
    title: str | None = Field(default=None, max_length=240)
    chunker: str = Field(default="structured", min_length=1, max_length=80)


class DocumentResponse(ApiModel):
    id: str
    workspace_id: str
    knowledge_base_id: str
    title: str
    source_type: str
    source_url: str | None
    source_validation_state: str
    source_is_approved: bool
    source_validated_at: datetime | None
    source_validation_status_code: int | None
    source_redirect_url: str | None
    source_content_type: str | None
    source_validation_error_code: str | None
    status: str
    created_at: datetime
    updated_at: datetime


class DocumentDetailResponse(DocumentResponse):
    """文档阅读器使用的详情响应；列表接口不会返回完整正文。"""

    raw_content: str


class IngestionJobResponse(ApiModel):
    id: str
    workspace_id: str
    document_id: str
    state: str
    attempts: int
    available_at: datetime
    locked_at: datetime | None
    locked_by: str | None
    last_error_at: datetime | None
    error_code: str | None
    error_message: str | None
    config_snapshot: dict[str, str]
    created_at: datetime
    updated_at: datetime


class CreateDocumentResponse(ApiModel):
    document: DocumentResponse
    ingestion_job: IngestionJobResponse


class DocumentListResponse(ApiModel):
    items: list[DocumentResponse]
    meta: PaginationMeta


class RechunkKnowledgeBaseResponse(ApiModel):
    document_count: int
    state: str
