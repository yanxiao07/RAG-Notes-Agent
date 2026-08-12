"""会话问答 API：普通资源查询与 SSE 生成流分离。"""

import json
from collections.abc import Iterator, Mapping
from typing import Annotated

from fastapi import APIRouter, Depends, Query, status
from fastapi.responses import JSONResponse, StreamingResponse
from sqlalchemy.orm import Session

from app.api.idempotency import (
    IdempotencyKeyHeader,
    begin_idempotent_request,
    complete_idempotent_request,
    release_idempotent_request,
    replay_response,
)
from app.api.schemas.conversation import (
    AnswerFeedbackResponse,
    CitationResponse,
    ConversationListResponse,
    ConversationMessageListResponse,
    ConversationMessageResponse,
    ConversationResponse,
    CreateConversationMessageRequest,
    CreateConversationRequest,
    CreateFeedbackEvaluationCaseRequest,
    CreateFeedbackKnowledgeDraftRequest,
    FeedbackEvaluationCaseListResponse,
    FeedbackEvaluationCaseResponse,
    FeedbackKnowledgeDraftListResponse,
    FeedbackKnowledgeDraftResponse,
    FeedbackTriageListResponse,
    FeedbackTriageResponse,
    ReviewFeedbackEvaluationCaseRequest,
    ReviewFeedbackKnowledgeDraftRequest,
    ReviewFeedbackTriageRequest,
    SubmitAnswerFeedbackRequest,
    SubmitAnswerFeedbackResponse,
    UpdateConversationRequest,
)
from app.application.conversation_service import ConversationService, PreparedAnswer
from app.application.feedback_learning_service import FeedbackLearningService
from app.application.feedback_service import FeedbackService
from app.core.database import get_session
from app.core.errors import AppError, AuthorizationError
from app.core.logging import get_logger
from app.core.workspace import WorkspaceDependency, configured_actor_role
from app.domain.agent.models import (
    AnswerFeedback,
    Conversation,
    ConversationMessage,
    FeedbackEvaluationCase,
    FeedbackKnowledgeDraft,
    FeedbackTriage,
)

router = APIRouter(tags=["Conversations"])
logger = get_logger(__name__)
SessionDependency = Annotated[Session, Depends(get_session)]


def to_conversation_response(conversation: Conversation) -> ConversationResponse:
    return ConversationResponse.model_validate(conversation, from_attributes=True)


def to_message_response(message: ConversationMessage) -> ConversationMessageResponse:
    return ConversationMessageResponse.model_validate(message, from_attributes=True)


def to_feedback_response(feedback: AnswerFeedback) -> AnswerFeedbackResponse:
    return AnswerFeedbackResponse.model_validate(feedback, from_attributes=True)


def to_triage_response(triage: FeedbackTriage) -> FeedbackTriageResponse:
    return FeedbackTriageResponse.model_validate(triage, from_attributes=True)


def to_knowledge_draft_response(draft: FeedbackKnowledgeDraft) -> FeedbackKnowledgeDraftResponse:
    return FeedbackKnowledgeDraftResponse.model_validate(draft, from_attributes=True)


def to_evaluation_case_response(case: FeedbackEvaluationCase) -> FeedbackEvaluationCaseResponse:
    return FeedbackEvaluationCaseResponse.model_validate(case, from_attributes=True)


def require_triage_role(workspace: WorkspaceDependency) -> None:
    role = configured_actor_role(workspace_id=workspace.workspace_id, actor_id=workspace.actor_id)
    if role not in {"approver", "owner"}:
        raise AuthorizationError(message="当前角色没有反馈分诊权限。")


@router.post(
    "/conversations",
    response_model=ConversationResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_conversation(
    payload: CreateConversationRequest,
    session: SessionDependency,
    workspace: WorkspaceDependency,
) -> ConversationResponse:
    conversation = ConversationService().create_conversation(
        session,
        knowledge_base_id=payload.knowledge_base_id,
        title=payload.title,
        workspace_id=workspace.workspace_id,
    )
    return to_conversation_response(conversation)


@router.get(
    "/knowledge-bases/{knowledge_base_id}/conversations",
    response_model=ConversationListResponse,
)
def list_conversations(
    knowledge_base_id: str,
    session: SessionDependency,
    workspace: WorkspaceDependency,
    limit: int = Query(default=30, ge=1, le=100),
) -> ConversationListResponse:
    conversations = ConversationService().list_conversations(
        session,
        knowledge_base_id=knowledge_base_id,
        workspace_id=workspace.workspace_id,
        limit=limit,
    )
    return ConversationListResponse(
        items=[to_conversation_response(item) for item in conversations]
    )


@router.patch("/conversations/{conversation_id}", response_model=ConversationResponse)
def update_conversation(
    conversation_id: str,
    payload: UpdateConversationRequest,
    session: SessionDependency,
    workspace: WorkspaceDependency,
) -> ConversationResponse:
    conversation = ConversationService().update_conversation(
        session,
        conversation_id=conversation_id,
        title=payload.title,
        workspace_id=workspace.workspace_id,
    )
    return to_conversation_response(conversation)


@router.delete("/conversations/{conversation_id}", response_model=ConversationResponse)
def archive_conversation(
    conversation_id: str,
    session: SessionDependency,
    workspace: WorkspaceDependency,
) -> ConversationResponse:
    conversation = ConversationService().archive_conversation(
        session,
        conversation_id=conversation_id,
        workspace_id=workspace.workspace_id,
    )
    return to_conversation_response(conversation)


@router.get(
    "/conversations/{conversation_id}/messages",
    response_model=ConversationMessageListResponse,
)
def list_messages(
    conversation_id: str,
    session: SessionDependency,
    workspace: WorkspaceDependency,
) -> ConversationMessageListResponse:
    messages = ConversationService().list_messages(
        session,
        conversation_id=conversation_id,
        workspace_id=workspace.workspace_id,
    )
    return ConversationMessageListResponse(items=[to_message_response(item) for item in messages])


@router.put(
    "/conversation-messages/{assistant_message_id}/feedback",
    response_model=SubmitAnswerFeedbackResponse,
)
def submit_answer_feedback(
    assistant_message_id: str,
    payload: SubmitAnswerFeedbackRequest,
    session: SessionDependency,
    workspace: WorkspaceDependency,
) -> SubmitAnswerFeedbackResponse:
    feedback, triage = FeedbackService().submit(
        session,
        assistant_message_id=assistant_message_id,
        sentiment=payload.sentiment,
        reason_code=payload.reason_code,
        workspace_id=workspace.workspace_id,
    )
    return SubmitAnswerFeedbackResponse(
        feedback=to_feedback_response(feedback),
        triage=to_triage_response(triage) if triage is not None else None,
    )


@router.get(
    "/knowledge-bases/{knowledge_base_id}/feedback-triage",
    response_model=FeedbackTriageListResponse,
)
def list_feedback_triage(
    knowledge_base_id: str,
    session: SessionDependency,
    workspace: WorkspaceDependency,
    state_filter: str | None = Query(default=None, alias="state"),
    limit: int = Query(default=50, ge=1, le=100),
) -> FeedbackTriageListResponse:
    items = FeedbackService().list_triage(
        session,
        knowledge_base_id=knowledge_base_id,
        state=state_filter,
        workspace_id=workspace.workspace_id,
        limit=limit,
    )
    return FeedbackTriageListResponse(items=[to_triage_response(item) for item in items])


@router.patch("/feedback-triage/{triage_id}", response_model=FeedbackTriageResponse)
def review_feedback_triage(
    triage_id: str,
    payload: ReviewFeedbackTriageRequest,
    session: SessionDependency,
    workspace: WorkspaceDependency,
) -> FeedbackTriageResponse:
    require_triage_role(workspace)
    triage = FeedbackService().review_triage(
        session,
        triage_id=triage_id,
        state=payload.state,
        resolution_target=payload.resolution_target,
        reviewer_id=workspace.actor_id,
        workspace_id=workspace.workspace_id,
    )
    return to_triage_response(triage)


@router.get(
    "/knowledge-bases/{knowledge_base_id}/feedback-knowledge-drafts",
    response_model=FeedbackKnowledgeDraftListResponse,
)
def list_feedback_knowledge_drafts(
    knowledge_base_id: str,
    session: SessionDependency,
    workspace: WorkspaceDependency,
    state_filter: str | None = Query(default=None, alias="state"),
    limit: int = Query(default=50, ge=1, le=100),
) -> FeedbackKnowledgeDraftListResponse:
    items = FeedbackLearningService().list_knowledge_drafts(
        session,
        knowledge_base_id=knowledge_base_id,
        state=state_filter,
        workspace_id=workspace.workspace_id,
        limit=limit,
    )
    return FeedbackKnowledgeDraftListResponse(
        items=[to_knowledge_draft_response(item) for item in items]
    )


@router.post(
    "/feedback-knowledge-drafts",
    response_model=FeedbackKnowledgeDraftResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_feedback_knowledge_draft(
    payload: CreateFeedbackKnowledgeDraftRequest,
    session: SessionDependency,
    workspace: WorkspaceDependency,
    idempotency_key: IdempotencyKeyHeader = None,
) -> FeedbackKnowledgeDraftResponse | JSONResponse:
    context = begin_idempotent_request(
        session,
        workspace_id=workspace.workspace_id,
        operation_scope="feedback:knowledge-draft:create",
        idempotency_key=idempotency_key,
        request_payload=payload.model_dump(mode="json"),
    )
    replay = replay_response(context)
    if replay is not None:
        return replay
    try:
        draft = FeedbackLearningService().create_knowledge_draft(
            session,
            feedback_triage_id=payload.feedback_triage_id,
            title=payload.title,
            content=payload.content,
            actor_id=workspace.actor_id,
            workspace_id=workspace.workspace_id,
            commit=context is None,
        )
        response = to_knowledge_draft_response(draft)
        complete_idempotent_request(session, context, response, status_code=status.HTTP_201_CREATED)
        return response
    except Exception:
        release_idempotent_request(session, context)
        raise


@router.post(
    "/feedback-knowledge-drafts/{draft_id}/review",
    response_model=FeedbackKnowledgeDraftResponse,
)
def review_feedback_knowledge_draft(
    draft_id: str,
    payload: ReviewFeedbackKnowledgeDraftRequest,
    session: SessionDependency,
    workspace: WorkspaceDependency,
    idempotency_key: IdempotencyKeyHeader = None,
) -> FeedbackKnowledgeDraftResponse | JSONResponse:
    context = begin_idempotent_request(
        session,
        workspace_id=workspace.workspace_id,
        operation_scope="feedback:knowledge-draft:review",
        idempotency_key=idempotency_key,
        request_payload={"draftId": draft_id, **payload.model_dump(mode="json")},
    )
    replay = replay_response(context)
    if replay is not None:
        return replay
    try:
        draft = FeedbackLearningService().review_knowledge_draft(
            session,
            draft_id=draft_id,
            decision=payload.decision,
            actor_id=workspace.actor_id,
            workspace_id=workspace.workspace_id,
            commit=context is None,
        )
        response = to_knowledge_draft_response(draft)
        complete_idempotent_request(session, context, response, status_code=status.HTTP_200_OK)
        return response
    except Exception:
        release_idempotent_request(session, context)
        raise


@router.get(
    "/knowledge-bases/{knowledge_base_id}/feedback-evaluation-cases",
    response_model=FeedbackEvaluationCaseListResponse,
)
def list_feedback_evaluation_cases(
    knowledge_base_id: str,
    session: SessionDependency,
    workspace: WorkspaceDependency,
    state_filter: str | None = Query(default=None, alias="state"),
    limit: int = Query(default=50, ge=1, le=100),
) -> FeedbackEvaluationCaseListResponse:
    items = FeedbackLearningService().list_evaluation_cases(
        session,
        knowledge_base_id=knowledge_base_id,
        state=state_filter,
        workspace_id=workspace.workspace_id,
        limit=limit,
    )
    return FeedbackEvaluationCaseListResponse(
        items=[to_evaluation_case_response(item) for item in items]
    )


@router.post(
    "/feedback-evaluation-cases",
    response_model=FeedbackEvaluationCaseResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_feedback_evaluation_case(
    payload: CreateFeedbackEvaluationCaseRequest,
    session: SessionDependency,
    workspace: WorkspaceDependency,
    idempotency_key: IdempotencyKeyHeader = None,
) -> FeedbackEvaluationCaseResponse | JSONResponse:
    context = begin_idempotent_request(
        session,
        workspace_id=workspace.workspace_id,
        operation_scope="feedback:evaluation-case:create",
        idempotency_key=idempotency_key,
        request_payload=payload.model_dump(mode="json"),
    )
    replay = replay_response(context)
    if replay is not None:
        return replay
    try:
        case = FeedbackLearningService().create_evaluation_case(
            session,
            feedback_triage_id=payload.feedback_triage_id,
            query=payload.query,
            expected_source_titles=payload.expected_source_titles,
            required_keywords=payload.required_keywords,
            limit=payload.limit,
            actor_id=workspace.actor_id,
            workspace_id=workspace.workspace_id,
            commit=context is None,
        )
        response = to_evaluation_case_response(case)
        complete_idempotent_request(session, context, response, status_code=status.HTTP_201_CREATED)
        return response
    except Exception:
        release_idempotent_request(session, context)
        raise


@router.post(
    "/feedback-evaluation-cases/{case_id}/review",
    response_model=FeedbackEvaluationCaseResponse,
)
def review_feedback_evaluation_case(
    case_id: str,
    payload: ReviewFeedbackEvaluationCaseRequest,
    session: SessionDependency,
    workspace: WorkspaceDependency,
    idempotency_key: IdempotencyKeyHeader = None,
) -> FeedbackEvaluationCaseResponse | JSONResponse:
    context = begin_idempotent_request(
        session,
        workspace_id=workspace.workspace_id,
        operation_scope="feedback:evaluation-case:review",
        idempotency_key=idempotency_key,
        request_payload={"caseId": case_id, **payload.model_dump(mode="json")},
    )
    replay = replay_response(context)
    if replay is not None:
        return replay
    try:
        case = FeedbackLearningService().review_evaluation_case(
            session,
            case_id=case_id,
            decision=payload.decision,
            actor_id=workspace.actor_id,
            workspace_id=workspace.workspace_id,
            commit=context is None,
        )
        response = to_evaluation_case_response(case)
        complete_idempotent_request(session, context, response, status_code=status.HTTP_200_OK)
        return response
    except Exception:
        release_idempotent_request(session, context)
        raise


@router.post("/conversations/{conversation_id}/messages")
def create_message(
    conversation_id: str,
    payload: CreateConversationMessageRequest,
    session: SessionDependency,
    workspace: WorkspaceDependency,
) -> StreamingResponse:
    service = ConversationService()
    prepared = service.prepare_answer(
        session,
        conversation_id=conversation_id,
        content=payload.content,
        explain_retrieval=payload.explain_retrieval,
        workspace_id=workspace.workspace_id,
    )
    return StreamingResponse(
        _answer_events(service, session, prepared),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def _answer_events(
    service: ConversationService, session: Session, prepared: PreparedAnswer
) -> Iterator[str]:
    """将模型增量与引用分开推送，客户端无需猜测文本中的引用边界。"""

    yield _sse_event(
        "started",
        {
            "conversationId": prepared.conversation.id,
            "assistantMessageId": prepared.assistant_message.id,
            "provider": prepared.provider.name,
            "model": prepared.provider.model_name,
            "route": prepared.route.mode,
            "routeReason": prepared.route.reason,
            "routeRouter": prepared.route.router,
            "routeConfidence": prepared.route.confidence,
            "routeCacheHit": prepared.route.cache_hit,
        },
    )
    if prepared.explain_retrieval:
        for item in service.explain_trace(prepared):
            yield _sse_event("trace", item)
    for citation in prepared.assistant_message.citations:
        # SSE 与 REST 共用公开 Schema，避免客户端在流式阶段收到内部 snake_case 字段。
        yield _sse_event(
            "citation",
            CitationResponse.model_validate(citation).model_dump(mode="json", by_alias=True),
        )

    fragments: list[str] = []
    try:
        for fragment in service.stream_answer(prepared):
            fragments.append(fragment)
            yield _sse_event("delta", {"text": fragment})
        completed = service.complete_answer(session, prepared=prepared, content="".join(fragments))
        yield _sse_event(
            "completed",
            to_message_response(completed).model_dump(mode="json", by_alias=True),
        )
    except AppError as exc:
        service.fail_answer(
            session,
            prepared=prepared,
            message=exc.message,
            error_code=exc.code,
        )
        yield _sse_event("error", {"code": exc.code, "message": exc.message})
    except Exception:
        logger.exception(
            "conversation_stream_failed",
            workspace_id=prepared.conversation.workspace_id,
            conversation_id=prepared.conversation.id,
            message_id=prepared.assistant_message.id,
        )
        service.fail_answer(
            session,
            prepared=prepared,
            message="unexpected_error",
            error_code="MODEL_UNAVAILABLE",
        )
        yield _sse_event(
            "error",
            {"code": "MODEL_UNAVAILABLE", "message": "问答模型暂时不可用，请稍后重试。"},
        )


def _sse_event(event: str, payload: Mapping[str, object]) -> str:
    return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"
