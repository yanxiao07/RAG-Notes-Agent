"""知识库和笔记 HTTP 路由。"""

from typing import Annotated

from fastapi import APIRouter, Depends, Query, status
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.api.idempotency import (
    IdempotencyKeyHeader,
    begin_idempotent_request,
    complete_idempotent_request,
    release_idempotent_request,
    replay_response,
)
from app.api.schemas.knowledge import (
    ArchiveKnowledgeTagRequest,
    CreateKnowledgeBaseRequest,
    CreateKnowledgeTagRequest,
    CreateNoteRequest,
    CreateTagAssignmentRequest,
    KnowledgeBaseListResponse,
    KnowledgeBaseResponse,
    KnowledgeTagListResponse,
    KnowledgeTagResponse,
    NoteListResponse,
    NoteResponse,
    PaginationMeta,
    ReviewTagAssignmentRequest,
    TagAssignmentListResponse,
    TagAssignmentResponse,
    UpdateKnowledgeBaseRequest,
    UpdateNoteRequest,
)
from app.application.knowledge_service import KnowledgeService
from app.application.tag_governance_service import TagGovernanceService
from app.core.database import get_session
from app.core.errors import AuthorizationError
from app.core.workspace import WorkspaceDependency, configured_actor_role
from app.domain.knowledge.models import KnowledgeBase, KnowledgeTag, KnowledgeTagAssignment, Note

router = APIRouter(tags=["Knowledge"])
SessionDependency = Annotated[Session, Depends(get_session)]


def to_knowledge_base_response(knowledge_base: KnowledgeBase) -> KnowledgeBaseResponse:
    return KnowledgeBaseResponse.model_validate(knowledge_base, from_attributes=True)


def to_note_response(note: Note) -> NoteResponse:
    return NoteResponse.model_validate(note, from_attributes=True)


def to_tag_response(tag: KnowledgeTag) -> KnowledgeTagResponse:
    return KnowledgeTagResponse.model_validate(tag, from_attributes=True)


def to_tag_assignment_response(
    assignment: KnowledgeTagAssignment, tag: KnowledgeTag
) -> TagAssignmentResponse:
    return TagAssignmentResponse(
        id=assignment.id,
        workspace_id=assignment.workspace_id,
        knowledge_base_id=assignment.knowledge_base_id,
        tag_id=assignment.tag_id,
        tag_name=tag.name,
        asset_type=assignment.asset_type,
        asset_id=assignment.asset_id,
        state=assignment.state,
        source=assignment.source,
        confidence=assignment.confidence,
        reviewer_id=assignment.reviewer_id,
        reviewed_at=assignment.reviewed_at,
        created_at=assignment.created_at,
        updated_at=assignment.updated_at,
    )


def require_tag_role(workspace: WorkspaceDependency, *, minimum: str) -> None:
    levels = {"viewer": 0, "editor": 1, "approver": 2, "owner": 3}
    role = configured_actor_role(
        workspace_id=workspace.workspace_id,
        actor_id=workspace.actor_id,
    )
    if levels.get(role, -1) < levels[minimum]:
        raise AuthorizationError(message="当前角色没有标签治理权限")


@router.post(
    "/knowledge-bases", response_model=KnowledgeBaseResponse, status_code=status.HTTP_201_CREATED
)
def create_knowledge_base(
    payload: CreateKnowledgeBaseRequest,
    session: SessionDependency,
    workspace: WorkspaceDependency,
) -> KnowledgeBaseResponse:
    knowledge_base = KnowledgeService().create_knowledge_base(
        session,
        name=payload.name,
        description=payload.description,
        workspace_id=workspace.workspace_id,
    )
    return to_knowledge_base_response(knowledge_base)


@router.get("/knowledge-bases", response_model=KnowledgeBaseListResponse)
def list_knowledge_bases(
    session: SessionDependency,
    workspace: WorkspaceDependency,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=20, ge=1, le=100),
) -> KnowledgeBaseListResponse:
    items, total = KnowledgeService().list_knowledge_bases(
        session,
        workspace_id=workspace.workspace_id,
        offset=offset,
        limit=limit,
    )
    return KnowledgeBaseListResponse(
        items=[to_knowledge_base_response(item) for item in items],
        meta=PaginationMeta(offset=offset, limit=limit, total=total),
    )


@router.get("/knowledge-bases/{knowledge_base_id}", response_model=KnowledgeBaseResponse)
def get_knowledge_base(
    knowledge_base_id: str,
    session: SessionDependency,
    workspace: WorkspaceDependency,
) -> KnowledgeBaseResponse:
    return to_knowledge_base_response(
        KnowledgeService().get_knowledge_base(
            session,
            knowledge_base_id,
            workspace_id=workspace.workspace_id,
        )
    )


@router.patch("/knowledge-bases/{knowledge_base_id}", response_model=KnowledgeBaseResponse)
def update_knowledge_base(
    knowledge_base_id: str,
    payload: UpdateKnowledgeBaseRequest,
    session: SessionDependency,
    workspace: WorkspaceDependency,
) -> KnowledgeBaseResponse:
    knowledge_base = KnowledgeService().update_knowledge_base(
        session,
        knowledge_base_id=knowledge_base_id,
        name=payload.name,
        description=payload.description,
        workspace_id=workspace.workspace_id,
    )
    return to_knowledge_base_response(knowledge_base)


@router.delete("/knowledge-bases/{knowledge_base_id}", response_model=KnowledgeBaseResponse)
def archive_knowledge_base(
    knowledge_base_id: str,
    session: SessionDependency,
    workspace: WorkspaceDependency,
) -> KnowledgeBaseResponse:
    knowledge_base = KnowledgeService().archive_knowledge_base(
        session,
        knowledge_base_id=knowledge_base_id,
        workspace_id=workspace.workspace_id,
    )
    return to_knowledge_base_response(knowledge_base)


@router.post(
    "/knowledge-bases/{knowledge_base_id}/tags",
    response_model=KnowledgeTagResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_knowledge_tag(
    knowledge_base_id: str,
    payload: CreateKnowledgeTagRequest,
    session: SessionDependency,
    workspace: WorkspaceDependency,
) -> KnowledgeTagResponse:
    require_tag_role(workspace, minimum="editor")
    tag = TagGovernanceService().create_tag(
        session,
        knowledge_base_id=knowledge_base_id,
        name=payload.name,
        description=payload.description,
        workspace_id=workspace.workspace_id,
    )
    return to_tag_response(tag)


@router.get(
    "/knowledge-bases/{knowledge_base_id}/tags",
    response_model=KnowledgeTagListResponse,
)
def list_knowledge_tags(
    knowledge_base_id: str,
    session: SessionDependency,
    workspace: WorkspaceDependency,
    include_archived: bool = Query(default=False),
) -> KnowledgeTagListResponse:
    tags = TagGovernanceService().list_tags(
        session,
        knowledge_base_id=knowledge_base_id,
        workspace_id=workspace.workspace_id,
        include_archived=include_archived,
    )
    return KnowledgeTagListResponse(items=[to_tag_response(tag) for tag in tags])


@router.delete("/knowledge-tags/{tag_id}", response_model=KnowledgeTagResponse)
def archive_knowledge_tag(
    tag_id: str,
    payload: ArchiveKnowledgeTagRequest,
    session: SessionDependency,
    workspace: WorkspaceDependency,
) -> KnowledgeTagResponse:
    require_tag_role(workspace, minimum="editor")
    tag = TagGovernanceService().archive_tag(
        session,
        tag_id=tag_id,
        expected_version=payload.version,
        workspace_id=workspace.workspace_id,
    )
    return to_tag_response(tag)


@router.post(
    "/knowledge-bases/{knowledge_base_id}/tag-assignments",
    response_model=TagAssignmentResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_tag_assignment(
    knowledge_base_id: str,
    payload: CreateTagAssignmentRequest,
    session: SessionDependency,
    workspace: WorkspaceDependency,
) -> TagAssignmentResponse:
    require_tag_role(workspace, minimum="editor")
    service = TagGovernanceService()
    assignment = service.propose_assignment(
        session,
        knowledge_base_id=knowledge_base_id,
        tag_id=payload.tag_id,
        asset_type=payload.asset_type,
        asset_id=payload.asset_id,
        source="manual",
        confidence=1.0,
        workspace_id=workspace.workspace_id,
    )
    tag = next(
        tag
        for tag in service.list_tags(
            session,
            knowledge_base_id=knowledge_base_id,
            workspace_id=workspace.workspace_id,
            include_archived=True,
        )
        if tag.id == assignment.tag_id
    )
    return to_tag_assignment_response(assignment, tag)


@router.get(
    "/knowledge-bases/{knowledge_base_id}/tag-assignments",
    response_model=TagAssignmentListResponse,
)
def list_tag_assignments(
    knowledge_base_id: str,
    session: SessionDependency,
    workspace: WorkspaceDependency,
    state_filter: str | None = Query(default=None, alias="state"),
) -> TagAssignmentListResponse:
    assignments = TagGovernanceService().list_assignments(
        session,
        knowledge_base_id=knowledge_base_id,
        state=state_filter,
        workspace_id=workspace.workspace_id,
    )
    return TagAssignmentListResponse(
        items=[to_tag_assignment_response(assignment, tag) for assignment, tag in assignments]
    )


@router.post("/tag-assignments/{assignment_id}/review", response_model=TagAssignmentResponse)
def review_tag_assignment(
    assignment_id: str,
    payload: ReviewTagAssignmentRequest,
    session: SessionDependency,
    workspace: WorkspaceDependency,
) -> TagAssignmentResponse:
    require_tag_role(workspace, minimum="approver")
    service = TagGovernanceService()
    assignment = service.review_assignment(
        session,
        assignment_id=assignment_id,
        decision=payload.decision,
        reviewer_id=workspace.actor_id,
        workspace_id=workspace.workspace_id,
    )
    tag = next(
        tag
        for tag in service.list_tags(
            session,
            knowledge_base_id=assignment.knowledge_base_id,
            workspace_id=workspace.workspace_id,
            include_archived=True,
        )
        if tag.id == assignment.tag_id
    )
    return to_tag_assignment_response(assignment, tag)


@router.post(
    "/knowledge-bases/{knowledge_base_id}/notes",
    response_model=NoteResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_note(
    knowledge_base_id: str,
    payload: CreateNoteRequest,
    session: SessionDependency,
    workspace: WorkspaceDependency,
    idempotency_key: IdempotencyKeyHeader = None,
) -> NoteResponse | JSONResponse:
    context = begin_idempotent_request(
        session,
        workspace_id=workspace.workspace_id,
        operation_scope="notes:create",
        idempotency_key=idempotency_key,
        request_payload={
            "knowledgeBaseId": knowledge_base_id,
            **payload.model_dump(mode="json"),
        },
    )
    replay = replay_response(context)
    if replay is not None:
        return replay
    try:
        note = KnowledgeService().create_note(
            session,
            knowledge_base_id=knowledge_base_id,
            title=payload.title,
            content=payload.content,
            workspace_id=workspace.workspace_id,
            commit=context is None,
        )
        response = to_note_response(note)
        complete_idempotent_request(session, context, response, status_code=status.HTTP_201_CREATED)
        return response
    except Exception:
        release_idempotent_request(session, context)
        raise


@router.get("/knowledge-bases/{knowledge_base_id}/notes", response_model=NoteListResponse)
def list_notes(
    knowledge_base_id: str,
    session: SessionDependency,
    workspace: WorkspaceDependency,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=20, ge=1, le=100),
) -> NoteListResponse:
    items, total = KnowledgeService().list_notes(
        session,
        knowledge_base_id=knowledge_base_id,
        workspace_id=workspace.workspace_id,
        offset=offset,
        limit=limit,
    )
    return NoteListResponse(
        items=[to_note_response(item) for item in items],
        meta=PaginationMeta(offset=offset, limit=limit, total=total),
    )


@router.patch("/notes/{note_id}", response_model=NoteResponse)
def update_note(
    note_id: str,
    payload: UpdateNoteRequest,
    session: SessionDependency,
    workspace: WorkspaceDependency,
    idempotency_key: IdempotencyKeyHeader = None,
) -> NoteResponse | JSONResponse:
    context = begin_idempotent_request(
        session,
        workspace_id=workspace.workspace_id,
        operation_scope="notes:update",
        idempotency_key=idempotency_key,
        request_payload={"noteId": note_id, **payload.model_dump(mode="json")},
    )
    replay = replay_response(context)
    if replay is not None:
        return replay
    try:
        note = KnowledgeService().update_note(
            session,
            note_id=note_id,
            title=payload.title,
            content=payload.content,
            expected_version=payload.version,
            workspace_id=workspace.workspace_id,
            commit=context is None,
        )
        response = to_note_response(note)
        complete_idempotent_request(session, context, response, status_code=status.HTTP_200_OK)
        return response
    except Exception:
        release_idempotent_request(session, context)
        raise
