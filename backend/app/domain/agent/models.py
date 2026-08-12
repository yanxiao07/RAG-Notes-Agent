"""Agent 写操作的持久化状态模型。"""

from datetime import datetime
from uuid import uuid4

from sqlalchemy import (
    JSON,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.domain.knowledge.models import TimestampMixin


class Conversation(TimestampMixin, Base):
    """面向知识库的可追溯问答会话。"""

    __tablename__ = "conversations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    knowledge_base_id: Mapped[str] = mapped_column(
        ForeignKey("knowledge_bases.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    title: Mapped[str] = mapped_column(String(240), nullable=False, default="新建问答")
    state: Mapped[str] = mapped_column(String(20), nullable=False, default="active", index=True)
    messages: Mapped[list["ConversationMessage"]] = relationship(
        back_populates="conversation",
        order_by="ConversationMessage.created_at",
    )


class ConversationMessage(TimestampMixin, Base):
    """会话消息及其生成时使用的引用快照。"""

    __tablename__ = "conversation_messages"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    conversation_id: Mapped[str] = mapped_column(
        ForeignKey("conversations.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False, default="")
    state: Mapped[str] = mapped_column(String(20), nullable=False, default="completed", index=True)
    citations: Mapped[list[dict[str, str | float | int]]] = mapped_column(
        JSON, nullable=False, default=list
    )
    provider_name: Mapped[str | None] = mapped_column(String(80), nullable=True)
    model_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    conversation: Mapped[Conversation] = relationship(back_populates="messages")


class AgentRun(TimestampMixin, Base):
    __tablename__ = "agent_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    knowledge_base_id: Mapped[str] = mapped_column(
        ForeignKey("knowledge_bases.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    conversation_id: Mapped[str | None] = mapped_column(
        ForeignKey("conversations.id", ondelete="RESTRICT"), nullable=True, index=True
    )
    state: Mapped[str] = mapped_column(String(30), nullable=False, default="running", index=True)
    policy_version: Mapped[str] = mapped_column(String(40), nullable=False, default="v1")
    thread_id: Mapped[str | None] = mapped_column(
        String(80), nullable=True, default=lambda: str(uuid4()), index=True
    )
    current_node: Mapped[str] = mapped_column(String(80), nullable=False, default="start")
    input_json: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False, default=dict)
    output_json: Mapped[dict[str, object] | None] = mapped_column(JSON, nullable=True)


class AgentCheckpoint(TimestampMixin, Base):
    """Agent 图节点快照。

    快照是可恢复性和审计的边界，只允许保存经过运行时脱敏的结构化字段。
    原始文档正文、API Key 和模型隐式推理过程永远不进入此表。
    """

    __tablename__ = "agent_checkpoints"
    __table_args__ = (
        UniqueConstraint("agent_run_id", "sequence", name="uq_agent_checkpoint_sequence"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    agent_run_id: Mapped[str] = mapped_column(
        ForeignKey("agent_runs.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    thread_id: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    node: Mapped[str] = mapped_column(String(80), nullable=False)
    state_json: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False, default=dict)
    state_checksum: Mapped[str] = mapped_column(String(64), nullable=False)


class ChangeProposal(TimestampMixin, Base):
    __tablename__ = "change_proposals"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    agent_run_id: Mapped[str] = mapped_column(
        ForeignKey("agent_runs.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    knowledge_base_id: Mapped[str] = mapped_column(
        ForeignKey("knowledge_bases.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    action: Mapped[str] = mapped_column(String(80), nullable=False)
    payload: Mapped[dict[str, str]] = mapped_column(JSON, nullable=False)
    rationale: Mapped[str] = mapped_column(Text, nullable=False)
    state: Mapped[str] = mapped_column(String(20), nullable=False, default="pending", index=True)
    risk_level: Mapped[str] = mapped_column(String(20), nullable=False, default="medium")
    required_role: Mapped[str] = mapped_column(String(40), nullable=False, default="approver")
    # 只保存证据定位摘要，不保存证据正文，避免审批快照扩大敏感数据暴露面。
    evidence_snapshot: Mapped[list[dict[str, object]]] = mapped_column(
        JSON, nullable=False, default=list
    )
    expires_at: Mapped[datetime | None] = mapped_column(nullable=True, index=True)


class AuditEvent(TimestampMixin, Base):
    __tablename__ = "audit_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    actor_type: Mapped[str] = mapped_column(String(30), nullable=False)
    actor_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    action: Mapped[str] = mapped_column(String(80), nullable=False)
    target_type: Mapped[str] = mapped_column(String(80), nullable=False)
    target_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    payload: Mapped[dict[str, str]] = mapped_column(JSON, nullable=False, default=dict)


class AgentToolCall(TimestampMixin, Base):
    """Agent 每次工具调用的可审计快照。

    输入和输出只保存结构化摘要，工具实现不得把完整文档、密钥或模型隐式思维写入这里。
    """

    __tablename__ = "tool_calls"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    agent_run_id: Mapped[str] = mapped_column(
        ForeignKey("agent_runs.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    node: Mapped[str] = mapped_column(String(80), nullable=False)
    tool_name: Mapped[str] = mapped_column(String(120), nullable=False)
    input_json: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False, default=dict)
    output_json: Mapped[dict[str, object] | None] = mapped_column(JSON, nullable=True)
    state: Mapped[str] = mapped_column(String(30), nullable=False, default="running", index=True)
    requires_approval: Mapped[bool] = mapped_column(nullable=False, default=False)
    error_code: Mapped[str | None] = mapped_column(String(80), nullable=True)


class RagStageEvent(TimestampMixin, Base):
    """RAG 链路的脱敏阶段事件。

    事件只记录可复跑和质量归因所需的哈希、定位符和统计值。用户问题、证据正文、Prompt、
    API Key 与模型隐式推理均不允许写入该表。
    """

    __tablename__ = "rag_stage_events"
    __table_args__ = (
        UniqueConstraint("agent_run_id", "sequence", name="uq_rag_stage_event_sequence"),
        Index("ix_rag_stage_events_run_stage", "agent_run_id", "stage", "sequence"),
        Index("ix_rag_stage_events_kb_created", "knowledge_base_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    knowledge_base_id: Mapped[str] = mapped_column(
        ForeignKey("knowledge_bases.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    agent_run_id: Mapped[str] = mapped_column(
        ForeignKey("agent_runs.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    conversation_id: Mapped[str] = mapped_column(
        ForeignKey("conversations.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    assistant_message_id: Mapped[str] = mapped_column(
        ForeignKey("conversation_messages.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    stage: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    state: Mapped[str] = mapped_column(String(20), nullable=False, default="completed", index=True)
    policy_version: Mapped[str] = mapped_column(String(80), nullable=False)
    input_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    output_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    candidate_locators: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    metrics: Mapped[dict[str, str | int | float | bool]] = mapped_column(
        JSON, nullable=False, default=dict
    )
    error_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    duration_ms: Mapped[float | None] = mapped_column(Float, nullable=True)


class RagBadcase(TimestampMixin, Base):
    """由阶段事件确定性推导出的待复核质量问题。"""

    __tablename__ = "rag_badcases"
    __table_args__ = (
        UniqueConstraint("agent_run_id", "category", name="uq_rag_badcase_category"),
        Index("ix_rag_badcases_review", "knowledge_base_id", "state", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    knowledge_base_id: Mapped[str] = mapped_column(
        ForeignKey("knowledge_bases.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    agent_run_id: Mapped[str] = mapped_column(
        ForeignKey("agent_runs.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    assistant_message_id: Mapped[str] = mapped_column(
        ForeignKey("conversation_messages.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    stage_event_id: Mapped[str | None] = mapped_column(
        ForeignKey("rag_stage_events.id", ondelete="RESTRICT"), nullable=True, index=True
    )
    category: Mapped[str] = mapped_column(String(60), nullable=False, index=True)
    severity: Mapped[str] = mapped_column(String(20), nullable=False, default="warning")
    state: Mapped[str] = mapped_column(String(20), nullable=False, default="open", index=True)
    reason_code: Mapped[str] = mapped_column(String(80), nullable=False)
    evidence_locators: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    details: Mapped[dict[str, str | int | float | bool]] = mapped_column(
        JSON, nullable=False, default=dict
    )


class AnswerFeedback(TimestampMixin, Base):
    """用户对单条回答的结构化反馈，不复制回答或提问正文。"""

    __tablename__ = "answer_feedback"
    __table_args__ = (
        UniqueConstraint("workspace_id", "assistant_message_id", name="uq_answer_feedback_message"),
        Index("ix_answer_feedback_kb_created", "knowledge_base_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    knowledge_base_id: Mapped[str] = mapped_column(
        ForeignKey("knowledge_bases.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    assistant_message_id: Mapped[str] = mapped_column(
        ForeignKey("conversation_messages.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    agent_run_id: Mapped[str] = mapped_column(
        ForeignKey("agent_runs.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    sentiment: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    reason_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    # 反馈关联的是当时的阶段事件 ID，不复制问题、答案或事件中的 locator 以减少冗余暴露。
    stage_event_ids: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)


class FeedbackTriage(TimestampMixin, Base):
    """无帮助反馈的待分诊队列，结论只使用预定义分类与目标类型。"""

    __tablename__ = "feedback_triage"
    __table_args__ = (
        UniqueConstraint("feedback_id", name="uq_feedback_triage_feedback"),
        Index("ix_feedback_triage_queue", "knowledge_base_id", "state", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    knowledge_base_id: Mapped[str] = mapped_column(
        ForeignKey("knowledge_bases.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    feedback_id: Mapped[str] = mapped_column(
        ForeignKey("answer_feedback.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    category: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    state: Mapped[str] = mapped_column(String(20), nullable=False, default="open", index=True)
    resolution_target: Mapped[str | None] = mapped_column(String(40), nullable=True)
    reviewer_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class FeedbackKnowledgeDraft(TimestampMixin, Base):
    """由已完成分诊创建的知识草稿，批准前绝不进入检索知识层。"""

    __tablename__ = "feedback_knowledge_drafts"
    __table_args__ = (
        UniqueConstraint("feedback_triage_id", name="uq_feedback_knowledge_draft_triage"),
        Index("ix_feedback_knowledge_drafts_review", "knowledge_base_id", "state", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    knowledge_base_id: Mapped[str] = mapped_column(
        ForeignKey("knowledge_bases.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    feedback_triage_id: Mapped[str] = mapped_column(
        ForeignKey("feedback_triage.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    title: Mapped[str] = mapped_column(String(240), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    state: Mapped[str] = mapped_column(String(20), nullable=False, default="pending", index=True)
    reviewer_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_note_id: Mapped[str | None] = mapped_column(
        ForeignKey("notes.id", ondelete="RESTRICT"), nullable=True, unique=True, index=True
    )


class FeedbackEvaluationCase(TimestampMixin, Base):
    """由已完成分诊创建的回归评测草稿。

    该表是受控评测集合的运行时来源。批准并不直接改写仓库中的 JSON 基线，
    必须经显式导出、代码审阅和版本控制后才可进入离线质量门禁。
    """

    __tablename__ = "feedback_evaluation_cases"
    __table_args__ = (
        UniqueConstraint("feedback_triage_id", name="uq_feedback_evaluation_case_triage"),
        Index("ix_feedback_evaluation_cases_review", "knowledge_base_id", "state", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    knowledge_base_id: Mapped[str] = mapped_column(
        ForeignKey("knowledge_bases.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    feedback_triage_id: Mapped[str] = mapped_column(
        ForeignKey("feedback_triage.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    query: Mapped[str] = mapped_column(Text, nullable=False)
    expected_source_titles: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    required_keywords: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    limit: Mapped[int] = mapped_column(Integer, nullable=False, default=5)
    state: Mapped[str] = mapped_column(String(20), nullable=False, default="pending", index=True)
    reviewer_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
