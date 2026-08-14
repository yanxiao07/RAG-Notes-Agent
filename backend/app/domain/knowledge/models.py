"""知识库领域的 ORM 持久化模型。"""

from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import (
    JSON,
    Boolean,
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
from app.domain.workspace import Workspace
from app.infrastructure.vector_type import EmbeddingVectorType


def utc_now() -> datetime:
    return datetime.now(UTC)


class TimestampMixin:
    """统一使用 UTC 时间，避免各表独立实现时间字段。"""

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )


class KnowledgeBase(TimestampMixin, Base):
    __tablename__ = "knowledge_bases"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active")
    # 只有 ready 且版本一致的知识库允许走语义检索，防止切换模型后混用旧向量。
    embedding_revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    index_status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="ready", index=True
    )
    # 图谱和向量索引是两个独立生命周期。图谱重建期间禁止使用旧社区摘要，
    # graph_revision 用于回答链路校验摘要是否与当前实体关系一致。
    graph_status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="ready", index=True
    )
    graph_revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1, index=True)
    notes: Mapped[list["Note"]] = relationship(back_populates="knowledge_base")
    documents: Mapped[list["Document"]] = relationship(back_populates="knowledge_base")
    workspace: Mapped[Workspace] = relationship(back_populates="knowledge_bases")


class Note(TimestampMixin, Base):
    __tablename__ = "notes"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    knowledge_base_id: Mapped[str] = mapped_column(
        ForeignKey("knowledge_bases.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    title: Mapped[str] = mapped_column(String(240), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False, default="")
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active")
    knowledge_base: Mapped[KnowledgeBase] = relationship(back_populates="notes")


class NoteEmbedding(TimestampMixin, Base):
    """手工笔记的独立向量快照，避免伪造文档切块或丢失笔记版本语义。"""

    __tablename__ = "note_embeddings"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    note_id: Mapped[str] = mapped_column(
        ForeignKey("notes.id", ondelete="RESTRICT"), nullable=False, unique=True, index=True
    )
    provider_name: Mapped[str] = mapped_column(String(80), nullable=False)
    model_name: Mapped[str] = mapped_column(String(120), nullable=False)
    dimensions: Mapped[int] = mapped_column(Integer, nullable=False)
    embedding_revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1, index=True)
    embedding: Mapped[list[float]] = mapped_column(JSON, nullable=False)
    # PostgreSQL 使用此列做 pgvector HNSW 检索；JSON 列继续保留给本地降级和迁移回填。
    embedding_vector: Mapped[list[float] | None] = mapped_column(
        EmbeddingVectorType(), nullable=True
    )


class KnowledgeMindMap(TimestampMixin, Base):
    """知识库的可编辑思维导图快照，图结构由用户而非模型输出最终决定。"""

    __tablename__ = "knowledge_mind_maps"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    knowledge_base_id: Mapped[str] = mapped_column(
        ForeignKey("knowledge_bases.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    title: Mapped[str] = mapped_column(String(240), nullable=False)
    graph: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class Document(TimestampMixin, Base):
    __tablename__ = "documents"
    __table_args__ = (
        Index(
            "ux_documents_workspace_kb_content_hash",
            "workspace_id",
            "knowledge_base_id",
            "content_hash",
            unique=True,
        ),
        Index(
            "ix_documents_workspace_kb_source_url",
            "workspace_id",
            "knowledge_base_id",
            "source_url",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    knowledge_base_id: Mapped[str] = mapped_column(
        ForeignKey("knowledge_bases.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    title: Mapped[str] = mapped_column(String(240), nullable=False)
    source_type: Mapped[str] = mapped_column(String(40), nullable=False, default="plain_text")
    # 网页文档保留规范化 URL，便于引用回链、去重和后续刷新；本地文件为空。
    source_url: Mapped[str | None] = mapped_column(String(2_000), nullable=True)
    # 网页正文一经入库仍可作为历史证据使用；来源校验只记录外部链接当前健康度，
    # 不因临时网络错误删除已索引的正文或切块。
    source_validation_state: Mapped[str] = mapped_column(
        String(24), nullable=False, default="not_applicable", index=True
    )
    source_is_approved: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    source_validated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    source_validation_status_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source_redirect_url: Mapped[str | None] = mapped_column(String(2_000), nullable=True)
    source_content_type: Mapped[str | None] = mapped_column(String(255), nullable=True)
    source_validation_error_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    # 内容变更检测只比较清洗后的摘要，不保存外部新正文，也不会静默覆盖已入库版本。
    web_content_state: Mapped[str] = mapped_column(
        String(24), nullable=False, default="not_applicable", index=True
    )
    web_content_checked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # 治理字段由人工或受控流程维护，不能从 URL、模型输出或文本相似度自动推断。
    source_trust_level: Mapped[str] = mapped_column(
        String(20), nullable=False, default="standard", index=True
    )
    effective_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    conflict_state: Mapped[str] = mapped_column(
        String(20), nullable=False, default="none", index=True
    )
    supersedes_document_id: Mapped[str | None] = mapped_column(
        ForeignKey("documents.id", ondelete="RESTRICT"), nullable=True, index=True
    )
    governance_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    raw_content: Mapped[str] = mapped_column(Text, nullable=False)
    # 对解析后的标准文本计算 SHA-256；同库重复文件名或不同文件名的相同内容都会命中。
    content_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="queued", index=True)
    knowledge_base: Mapped[KnowledgeBase] = relationship(back_populates="documents")
    ingestion_jobs: Mapped[list["IngestionJob"]] = relationship(back_populates="document")
    chunks: Mapped[list["DocumentChunk"]] = relationship(back_populates="document")


class IngestionJob(TimestampMixin, Base):
    __tablename__ = "ingestion_jobs"
    __table_args__ = (
        Index(
            "ix_ingestion_jobs_queue_schedule",
            "workspace_id",
            "state",
            "available_at",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    document_id: Mapped[str] = mapped_column(
        ForeignKey("documents.id", ondelete="RESTRICT"), nullable=False, unique=True
    )
    state: Mapped[str] = mapped_column(String(20), nullable=False, default="queued", index=True)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # 任务只有到达 available_at 才允许被 Worker 领取，失败重试因此不会形成忙等。
    available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    # 租约字段用于进程崩溃后的任务回收；locked_by 记录实例而不是用户身份。
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    locked_by: Mapped[str | None] = mapped_column(String(120), nullable=True)
    last_error_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    error_message: Mapped[str | None] = mapped_column(String(500), nullable=True)
    config_snapshot: Mapped[dict[str, str]] = mapped_column(JSON, nullable=False)
    document: Mapped[Document] = relationship(back_populates="ingestion_jobs")


class DocumentChunk(TimestampMixin, Base):
    __tablename__ = "document_chunks"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    document_id: Mapped[str] = mapped_column(
        ForeignKey("documents.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    metadata_json: Mapped[dict[str, str]] = mapped_column(JSON, nullable=False, default=dict)
    document: Mapped[Document] = relationship(back_populates="chunks")


class KnowledgeTag(TimestampMixin, Base):
    """知识库内受控业务标签词表。

    标签名称经过规范化后唯一；删除采用 archived 状态，避免历史提议和审计记录失去语义。
    """

    __tablename__ = "knowledge_tags"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "knowledge_base_id",
            "normalized_name",
            name="uq_knowledge_tag_name",
        ),
        Index("ix_knowledge_tags_kb_state", "knowledge_base_id", "state"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    knowledge_base_id: Mapped[str] = mapped_column(
        ForeignKey("knowledge_bases.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    normalized_name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str | None] = mapped_column(String(500), nullable=True)
    state: Mapped[str] = mapped_column(String(20), nullable=False, default="active", index=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class KnowledgeTagAssignment(TimestampMixin, Base):
    """文档或笔记的标签提议与审批状态。

    使用 asset_type + asset_id 保持一张统一的审批队列；服务层必须校验目标资产的工作区和
    知识库归属，不能把多态 ID 当作绕过租户边界的通道。
    """

    __tablename__ = "knowledge_tag_assignments"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "knowledge_base_id",
            "tag_id",
            "asset_type",
            "asset_id",
            name="uq_knowledge_tag_assignment",
        ),
        Index("ix_knowledge_tag_assignments_review", "knowledge_base_id", "state", "created_at"),
        Index("ix_knowledge_tag_assignments_asset", "asset_type", "asset_id", "workspace_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    knowledge_base_id: Mapped[str] = mapped_column(
        ForeignKey("knowledge_bases.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    tag_id: Mapped[str] = mapped_column(
        ForeignKey("knowledge_tags.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    asset_type: Mapped[str] = mapped_column(String(20), nullable=False)
    asset_id: Mapped[str] = mapped_column(String(36), nullable=False)
    state: Mapped[str] = mapped_column(String(20), nullable=False, default="pending", index=True)
    source: Mapped[str] = mapped_column(String(30), nullable=False, default="manual")
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    reviewer_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ChunkEmbedding(TimestampMixin, Base):
    """切块的 Embedding 快照；生产环境会迁移到 pgvector 索引。"""

    __tablename__ = "chunk_embeddings"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    document_chunk_id: Mapped[str] = mapped_column(
        ForeignKey("document_chunks.id", ondelete="RESTRICT"),
        nullable=False,
        unique=True,
        index=True,
    )
    provider_name: Mapped[str] = mapped_column(String(80), nullable=False)
    model_name: Mapped[str] = mapped_column(String(120), nullable=False)
    dimensions: Mapped[int] = mapped_column(Integer, nullable=False)
    embedding_revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1, index=True)
    embedding: Mapped[list[float]] = mapped_column(JSON, nullable=False)
    # 双写期间 JSON 是可回滚快照，原生向量列供生产检索器使用。
    embedding_vector: Mapped[list[float] | None] = mapped_column(
        EmbeddingVectorType(), nullable=True
    )


class KnowledgeEntity(TimestampMixin, Base):
    """知识库实体索引。

    实体是 GraphRAG-lite 的稳定节点，名称经过规范化后用于查询匹配；正文仍保留在
    DocumentChunk 中，避免把模型生成的摘要误当成事实来源。
    """

    __tablename__ = "knowledge_entities"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "knowledge_base_id",
            "normalized_name",
            name="uq_knowledge_entity_name",
        ),
        Index("ix_knowledge_entities_kb_name", "knowledge_base_id", "normalized_name"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    knowledge_base_id: Mapped[str] = mapped_column(
        ForeignKey("knowledge_bases.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    normalized_name: Mapped[str] = mapped_column(String(160), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(40), nullable=False, default="concept")
    mention_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class ChunkEntityMention(TimestampMixin, Base):
    """切块与实体的倒排关联，供局部关系检索快速定位原始证据。"""

    __tablename__ = "chunk_entity_mentions"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "document_chunk_id",
            "entity_id",
            name="uq_chunk_entity_mention",
        ),
        Index("ix_chunk_entity_mentions_entity", "entity_id", "workspace_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    document_chunk_id: Mapped[str] = mapped_column(
        ForeignKey("document_chunks.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    entity_id: Mapped[str] = mapped_column(
        ForeignKey("knowledge_entities.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    mention_count: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class KnowledgeRelation(TimestampMixin, Base):
    """实体关系边；每条边必须绑定一个原始切块作为证据。"""

    __tablename__ = "knowledge_relations"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "document_chunk_id",
            "source_entity_id",
            "target_entity_id",
            "relation_type",
            name="uq_knowledge_relation_evidence",
        ),
        Index("ix_knowledge_relations_source", "source_entity_id", "workspace_id"),
        Index("ix_knowledge_relations_target", "target_entity_id", "workspace_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    knowledge_base_id: Mapped[str] = mapped_column(
        ForeignKey("knowledge_bases.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    document_chunk_id: Mapped[str] = mapped_column(
        ForeignKey("document_chunks.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    source_entity_id: Mapped[str] = mapped_column(
        ForeignKey("knowledge_entities.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    target_entity_id: Mapped[str] = mapped_column(
        ForeignKey("knowledge_entities.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    relation_type: Mapped[str] = mapped_column(String(60), nullable=False, default="co_occurs")
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.5)


class KnowledgeCommunitySummary(TimestampMixin, Base):
    """GraphRAG 社区摘要快照。

    summary 只是检索导航和全局问题的压缩表示，member/source 两组 ID 始终保留，
    让最终 Evidence 可以回到原始切块，避免把模型生成的摘要误当成事实来源。
    """

    __tablename__ = "knowledge_community_summaries"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "knowledge_base_id",
            "level",
            "community_key",
            name="uq_knowledge_community_summary",
        ),
        Index(
            "ix_knowledge_community_summary_kb_level",
            "knowledge_base_id",
            "level",
            "status",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    knowledge_base_id: Mapped[str] = mapped_column(
        ForeignKey("knowledge_bases.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    level: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    community_key: Mapped[str] = mapped_column(String(128), nullable=False)
    title: Mapped[str] = mapped_column(String(240), nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    # JSON 在 SQLite 中保持开发可用；PostgreSQL 迁移后可进一步升级为 JSONB。
    member_entity_ids: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    source_chunk_ids: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    graph_revision: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active", index=True)
    extractor_provider: Mapped[str] = mapped_column(String(80), nullable=False, default="rule")
    extractor_version: Mapped[str] = mapped_column(String(40), nullable=False, default="v1")
    summary_provider: Mapped[str] = mapped_column(
        String(80), nullable=False, default="deterministic-community-summary"
    )
    # 记录实际执行的社区算法而非期望配置，便于离线评测识别依赖缺失后的回退。
    community_algorithm: Mapped[str] = mapped_column(
        String(80), nullable=False, default="connected_components"
    )
    community_algorithm_fallback: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
