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
    source_trust_level: str
    effective_at: datetime | None
    expires_at: datetime | None
    conflict_state: str
    supersedes_document_id: str | None
    governance_version: int
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


class UpdateDocumentGovernanceRequest(ApiModel):
    """资料时效、可信度与替代关系只接受受控枚举，避免浏览器写入任意策略状态。"""

    source_trust_level: str = Field(pattern="^(verified|standard|unverified)$")
    effective_at: datetime | None = None
    expires_at: datetime | None = None
    conflict_state: str = Field(pattern="^(none|conflicted)$")
    supersedes_document_id: str | None = Field(default=None, max_length=36)
    governance_version: int = Field(ge=1)


class RechunkKnowledgeBaseResponse(ApiModel):
    document_count: int
    state: str
