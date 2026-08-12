"""文档提交与任务状态查询路由。"""

import hashlib
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, Query, UploadFile, status
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session, sessionmaker

from app.api.idempotency import (
    IdempotencyKeyHeader,
    begin_idempotent_request,
    complete_idempotent_request,
    release_idempotent_request,
    replay_response,
)
from app.api.schemas.ingestion import (
    CreateDocumentRequest,
    CreateDocumentResponse,
    CreateUrlDocumentRequest,
    DocumentDetailResponse,
    DocumentListResponse,
    DocumentResponse,
    IngestionJobResponse,
    RechunkKnowledgeBaseResponse,
)
from app.api.schemas.knowledge import PaginationMeta
from app.application.ingestion_service import IngestionService
from app.application.source_validation_service import (
    SourceValidationService,
    execute_source_validation,
)
from app.core.config import get_settings
from app.core.database import get_session, get_session_factory
from app.core.errors import ProcessingError, ResourceNotFoundError
from app.core.workspace import WorkspaceDependency
from app.domain.knowledge.models import Document, IngestionJob
from app.domain.knowledge.repositories import IngestionJobRepository
from app.workers.ingestion import execute_ingestion_job, execute_rechunk_knowledge_base

router = APIRouter(tags=["Ingestion"])
SessionDependency = Annotated[Session, Depends(get_session)]
SessionFactoryDependency = Annotated[sessionmaker[Session], Depends(get_session_factory)]

SUPPORTED_UPLOAD_TYPES = {
    ".txt": "text/plain",
    ".md": "text/markdown",
    ".markdown": "text/markdown",
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}


def to_document_response(document: Document) -> DocumentResponse:
    return DocumentResponse.model_validate(document, from_attributes=True)


def to_job_response(job: IngestionJob) -> IngestionJobResponse:
    return IngestionJobResponse.model_validate(job, from_attributes=True)


def dispatch_ingestion_job(
    background_tasks: BackgroundTasks,
    *,
    job_id: str,
    session_factory: sessionmaker[Session],
    workspace_id: str,
) -> None:
    """按部署模式派发入库任务；轮询 Worker 模式下 API 不再重复执行。"""

    if get_settings().ingestion_dispatch_mode == "poll":
        return
    background_tasks.add_task(execute_ingestion_job, job_id, session_factory, workspace_id)


@router.post(
    "/documents", response_model=CreateDocumentResponse, status_code=status.HTTP_202_ACCEPTED
)
def create_document(
    payload: CreateDocumentRequest,
    background_tasks: BackgroundTasks,
    session: SessionDependency,
    workspace: WorkspaceDependency,
    session_factory: SessionFactoryDependency,
    idempotency_key: IdempotencyKeyHeader = None,
) -> CreateDocumentResponse | JSONResponse:
    context = begin_idempotent_request(
        session,
        workspace_id=workspace.workspace_id,
        operation_scope="documents:create",
        idempotency_key=idempotency_key,
        request_payload=payload.model_dump(mode="json"),
    )
    replay = replay_response(context)
    if replay is not None:
        return replay
    try:
        document, job = IngestionService().create_document(
            session,
            knowledge_base_id=payload.knowledge_base_id,
            title=payload.title,
            source_type=payload.source_type,
            raw_content=payload.content,
            parser_name=payload.parser,
            chunker_name=payload.chunker,
            workspace_id=workspace.workspace_id,
            commit=context is None,
        )
        response = CreateDocumentResponse(
            document=to_document_response(document), ingestion_job=to_job_response(job)
        )
        complete_idempotent_request(
            session, context, response, status_code=status.HTTP_202_ACCEPTED
        )
        dispatch_ingestion_job(
            background_tasks,
            job_id=job.id,
            session_factory=session_factory,
            workspace_id=workspace.workspace_id,
        )
        return response
    except Exception:
        release_idempotent_request(session, context)
        raise


@router.post(
    "/documents/url",
    response_model=CreateDocumentResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def create_url_document(
    payload: CreateUrlDocumentRequest,
    background_tasks: BackgroundTasks,
    session: SessionDependency,
    session_factory: SessionFactoryDependency,
    workspace: WorkspaceDependency,
    idempotency_key: IdempotencyKeyHeader = None,
) -> CreateDocumentResponse | JSONResponse:
    """创建网页入库任务；抓取在独立 Worker Session 中执行。"""

    context = begin_idempotent_request(
        session,
        workspace_id=workspace.workspace_id,
        operation_scope="documents:create-url",
        idempotency_key=idempotency_key,
        request_payload=payload.model_dump(mode="json"),
    )
    replay = replay_response(context)
    if replay is not None:
        return replay
    try:
        document, job = IngestionService().create_url_document(
            session,
            knowledge_base_id=payload.knowledge_base_id,
            url=payload.url,
            title=payload.title,
            chunker_name=payload.chunker,
            workspace_id=workspace.workspace_id,
            commit=context is None,
        )
        response = CreateDocumentResponse(
            document=to_document_response(document), ingestion_job=to_job_response(job)
        )
        complete_idempotent_request(
            session, context, response, status_code=status.HTTP_202_ACCEPTED
        )
        dispatch_ingestion_job(
            background_tasks,
            job_id=job.id,
            session_factory=session_factory,
            workspace_id=workspace.workspace_id,
        )
        return response
    except Exception:
        release_idempotent_request(session, context)
        raise


@router.post(
    "/documents/upload",
    response_model=CreateDocumentResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def upload_document(
    background_tasks: BackgroundTasks,
    session: SessionDependency,
    session_factory: SessionFactoryDependency,
    workspace: WorkspaceDependency,
    knowledge_base_id: Annotated[str, Form()],
    file: Annotated[UploadFile, File(description="TXT、Markdown、PDF 或 DOCX 文件")],
    chunker: Annotated[str, Form()] = "structured",
    idempotency_key: IdempotencyKeyHeader = None,
) -> CreateDocumentResponse | JSONResponse:
    filename = file.filename or "uploaded-document"
    content_type = SUPPORTED_UPLOAD_TYPES.get(Path(filename).suffix.lower())
    if content_type is None:
        raise ProcessingError(message="仅支持 TXT、Markdown、PDF 和 DOCX 文件。")

    max_upload_bytes = get_settings().max_upload_bytes
    content = await file.read(max_upload_bytes + 1)
    if len(content) > max_upload_bytes:
        raise ProcessingError(message="文件超过 25 MB 上传限制。")

    context = begin_idempotent_request(
        session,
        workspace_id=workspace.workspace_id,
        operation_scope="documents:upload",
        idempotency_key=idempotency_key,
        request_payload={
            "knowledgeBaseId": knowledge_base_id,
            "filename": filename,
            "contentType": content_type,
            "contentHash": hashlib.sha256(content).hexdigest(),
        },
    )
    replay = replay_response(context)
    if replay is not None:
        return replay
    try:
        document, job = IngestionService().create_uploaded_document(
            session,
            knowledge_base_id=knowledge_base_id,
            filename=filename,
            content_type=content_type,
            content=content,
            chunker_name=chunker,
            workspace_id=workspace.workspace_id,
            commit=context is None,
        )
        response = CreateDocumentResponse(
            document=to_document_response(document), ingestion_job=to_job_response(job)
        )
        complete_idempotent_request(
            session, context, response, status_code=status.HTTP_202_ACCEPTED
        )
        dispatch_ingestion_job(
            background_tasks,
            job_id=job.id,
            session_factory=session_factory,
            workspace_id=workspace.workspace_id,
        )
        return response
    except Exception:
        release_idempotent_request(session, context)
        raise


@router.get("/knowledge-bases/{knowledge_base_id}/documents", response_model=DocumentListResponse)
def list_documents(
    knowledge_base_id: str,
    session: SessionDependency,
    workspace: WorkspaceDependency,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=20, ge=1, le=100),
) -> DocumentListResponse:
    items, total = IngestionService().list_documents(
        session,
        knowledge_base_id=knowledge_base_id,
        workspace_id=workspace.workspace_id,
        offset=offset,
        limit=limit,
    )
    return DocumentListResponse(
        items=[to_document_response(item) for item in items],
        meta=PaginationMeta(offset=offset, limit=limit, total=total),
    )


@router.delete("/documents/{document_id}", response_model=DocumentResponse)
def archive_document(
    document_id: str,
    session: SessionDependency,
    workspace: WorkspaceDependency,
) -> DocumentResponse:
    """归档已导入文档，并同步移除其切块和向量索引。"""

    document = IngestionService().archive_document(
        session,
        document_id=document_id,
        workspace_id=workspace.workspace_id,
    )
    return to_document_response(document)


@router.post("/documents/{document_id}/source-validation", response_model=DocumentResponse)
def revalidate_document_source(
    document_id: str,
    background_tasks: BackgroundTasks,
    session: SessionDependency,
    session_factory: SessionFactoryDependency,
    workspace: WorkspaceDependency,
) -> DocumentResponse:
    """请求异步复核网页来源；请求线程只更新 pending 状态。"""

    document = SourceValidationService().mark_pending(
        session,
        document_id=document_id,
        workspace_id=workspace.workspace_id,
    )
    background_tasks.add_task(
        execute_source_validation,
        document.id,
        workspace.workspace_id,
        session_factory,
    )
    return to_document_response(document)


@router.get("/documents/{document_id}", response_model=DocumentDetailResponse)
def get_document(
    document_id: str,
    session: SessionDependency,
    workspace: WorkspaceDependency,
) -> DocumentDetailResponse:
    """按需读取原始解析文本，供文档阅读器和后续定位功能使用。"""

    document = IngestionService().get_document(
        session,
        document_id=document_id,
        workspace_id=workspace.workspace_id,
    )
    return DocumentDetailResponse.model_validate(document, from_attributes=True)


@router.post(
    "/knowledge-bases/{knowledge_base_id}/documents/rechunk",
    response_model=RechunkKnowledgeBaseResponse,
)
def rechunk_knowledge_base(
    knowledge_base_id: str,
    background_tasks: BackgroundTasks,
    session: SessionDependency,
    session_factory: SessionFactoryDependency,
    workspace: WorkspaceDependency,
) -> RechunkKnowledgeBaseResponse:
    document_count = IngestionService().start_rechunk_knowledge_base(
        session,
        knowledge_base_id=knowledge_base_id,
        workspace_id=workspace.workspace_id,
    )
    background_tasks.add_task(
        execute_rechunk_knowledge_base,
        knowledge_base_id,
        workspace.workspace_id,
        session_factory,
    )
    return RechunkKnowledgeBaseResponse(
        document_count=document_count,
        state="building",
    )


@router.get("/ingestion-jobs/{job_id}", response_model=IngestionJobResponse)
def get_ingestion_job(
    job_id: str,
    session: SessionDependency,
    workspace: WorkspaceDependency,
) -> IngestionJobResponse:
    job = IngestionJobRepository().get(session, job_id, workspace_id=workspace.workspace_id)
    if job is None:
        raise ResourceNotFoundError(details={"resource": "ingestion_job"})
    return to_job_response(job)


@router.post(
    "/documents/{document_id}/retry",
    response_model=IngestionJobResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def retry_document_ingestion(
    document_id: str,
    background_tasks: BackgroundTasks,
    session: SessionDependency,
    session_factory: SessionFactoryDependency,
    workspace: WorkspaceDependency,
) -> IngestionJobResponse:
    job = IngestionService().retry_document(
        session,
        document_id=document_id,
        workspace_id=workspace.workspace_id,
    )
    dispatch_ingestion_job(
        background_tasks,
        job_id=job.id,
        session_factory=session_factory,
        workspace_id=workspace.workspace_id,
    )
    return to_job_response(job)
