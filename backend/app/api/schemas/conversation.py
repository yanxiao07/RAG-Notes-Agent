"""问答会话和 SSE 事件的 API 模型。"""

from datetime import datetime
from typing import Literal

from pydantic import Field

from app.api.schemas.knowledge import ApiModel


class CreateConversationRequest(ApiModel):
    knowledge_base_id: str
    title: str = Field(default="新建问答", min_length=1, max_length=240)


class UpdateConversationRequest(ApiModel):
    title: str = Field(min_length=1, max_length=240)


class CreateConversationMessageRequest(ApiModel):
    content: str = Field(min_length=1, max_length=20_000)
    explain_retrieval: bool = False


class CitationResponse(ApiModel):
    citation_index: int
    source_type: str
    source_id: str
    title: str
    content: str
    locator: str
    score: float
    source_url: str | None = None
    source_validation_state: str = "not_applicable"
    source_is_approved: bool = False


class ConversationResponse(ApiModel):
    id: str
    workspace_id: str
    knowledge_base_id: str
    title: str
    state: str
    created_at: datetime
    updated_at: datetime


class ConversationListResponse(ApiModel):
    items: list[ConversationResponse]


class ConversationMessageResponse(ApiModel):
    id: str
    workspace_id: str
    conversation_id: str
    role: str
    content: str
    state: str
    citations: list[CitationResponse]
    provider_name: str | None
    model_name: str | None
    created_at: datetime
    updated_at: datetime


class ConversationMessageListResponse(ApiModel):
    items: list[ConversationMessageResponse]


class SubmitAnswerFeedbackRequest(ApiModel):
    sentiment: Literal["helpful", "unhelpful"]
    reason_code: (
        Literal[
            "incorrect_answer",
            "missing_evidence",
            "irrelevant_evidence",
            "citation_problem",
            "outdated_information",
            "other",
        ]
        | None
    ) = None


class AnswerFeedbackResponse(ApiModel):
    id: str
    assistant_message_id: str
    agent_run_id: str
    sentiment: str
    reason_code: str | None
    stage_event_ids: list[str]
    created_at: datetime
    updated_at: datetime


class FeedbackTriageResponse(ApiModel):
    id: str
    feedback_id: str
    category: str
    state: str
    resolution_target: str | None
    reviewer_id: str | None
    reviewed_at: datetime | None
    created_at: datetime
    updated_at: datetime


class SubmitAnswerFeedbackResponse(ApiModel):
    feedback: AnswerFeedbackResponse
    triage: FeedbackTriageResponse | None


class FeedbackTriageListResponse(ApiModel):
    items: list[FeedbackTriageResponse]


class ReviewFeedbackTriageRequest(ApiModel):
    state: Literal["open", "in_review", "resolved", "dismissed"]
    resolution_target: Literal["knowledge_draft", "evaluation_case", "product_bug"] | None = None


class FeedbackKnowledgeDraftResponse(ApiModel):
    id: str
    feedback_triage_id: str
    knowledge_base_id: str
    title: str
    content: str
    state: Literal["pending", "approved", "rejected"]
    reviewer_id: str | None
    reviewed_at: datetime | None
    created_note_id: str | None
    created_at: datetime
    updated_at: datetime


class FeedbackKnowledgeDraftListResponse(ApiModel):
    items: list[FeedbackKnowledgeDraftResponse]


class CreateFeedbackKnowledgeDraftRequest(ApiModel):
    feedback_triage_id: str
    title: str = Field(min_length=1, max_length=240)
    content: str = Field(min_length=1, max_length=100_000)


class ReviewFeedbackKnowledgeDraftRequest(ApiModel):
    decision: Literal["approved", "rejected"]


class FeedbackEvaluationCaseResponse(ApiModel):
    id: str
    feedback_triage_id: str
    knowledge_base_id: str
    query: str
    expected_source_titles: list[str]
    required_keywords: list[str]
    limit: int
    state: Literal["pending", "approved", "rejected"]
    reviewer_id: str | None
    reviewed_at: datetime | None
    created_at: datetime
    updated_at: datetime


class FeedbackEvaluationCaseListResponse(ApiModel):
    items: list[FeedbackEvaluationCaseResponse]


class CreateFeedbackEvaluationCaseRequest(ApiModel):
    feedback_triage_id: str
    query: str = Field(min_length=1, max_length=2_000)
    expected_source_titles: list[str] = Field(min_length=1, max_length=20)
    required_keywords: list[str] = Field(default_factory=list, max_length=30)
    limit: int = Field(default=5, ge=1, le=20)


class ReviewFeedbackEvaluationCaseRequest(ApiModel):
    decision: Literal["approved", "rejected"]
