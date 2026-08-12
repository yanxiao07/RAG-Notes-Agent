"""文档入库用例。Worker 和命令行任务调用同一服务，保证处理语义一致。"""

import hashlib
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agent.llm import build_llm_provider
from app.application.configuration_service import ConfigurationService
from app.application.embedding_service import EmbeddingService
from app.application.knowledge_service import KnowledgeService
from app.application.source_validation_service import SourceValidationService
from app.application.tag_governance_service import TagGovernanceService
from app.application.web_import_service import fetch_web_page, normalize_web_url
from app.core.config import get_settings
from app.core.errors import (
    DuplicateResourceError,
    ProcessingError,
    ResourceNotFoundError,
    VersionConflictError,
)
from app.core.logging import get_logger
from app.core.workspace import ensure_workspace
from app.domain.agent.models import AuditEvent
from app.domain.agent.repositories import AuditEventRepository
from app.domain.knowledge.models import (
    ChunkEmbedding,
    Document,
    DocumentChunk,
    IngestionJob,
    utc_now,
)
from app.domain.knowledge.repositories import DocumentRepository, IngestionJobRepository
from app.extensions.builtin import build_builtin_registry
from app.extensions.registry import ExtensionNotFoundError, ExtensionRegistry
from app.rag.communities import CommunitySummaryService, LLMCommunitySummaryGenerator
from app.rag.embeddings import build_embedding_provider
from app.rag.graph import GraphIndexService, build_graph_extractor
from app.security.content_sanitization import sanitize_knowledge_content

FILE_TYPE_CONFIG: dict[str, tuple[str, str]] = {
    "text/plain": ("plain_text", "utf8_text"),
    # Typora 文档必须走 Markdown 专用解析器，保留围栏代码、Front Matter 和 HTML 行。
    "text/markdown": ("markdown", "markdown"),
    "application/pdf": ("pdf", "pdf"),
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ("docx", "docx"),
}

logger = get_logger(__name__)


class IngestionService:
    def __init__(
        self,
        *,
        registry: ExtensionRegistry | None = None,
        document_repository: DocumentRepository | None = None,
        job_repository: IngestionJobRepository | None = None,
    ) -> None:
        try:
            self.registry = registry or build_builtin_registry()
        except ValueError as exc:
            raise ProcessingError(message="当前部署的入库扩展配置无效。") from exc
        self.document_repository = document_repository or DocumentRepository()
        self.job_repository = job_repository or IngestionJobRepository()
        self.audit_repository = AuditEventRepository()

    def create_document(
        self,
        session: Session,
        *,
        knowledge_base_id: str,
        title: str,
        source_type: str,
        raw_content: str,
        parser_name: str = "plain_text",
        chunker_name: str = "structured",
        source_url: str | None = None,
        workspace_id: str | None = None,
        commit: bool = True,
    ) -> tuple[Document, IngestionJob]:
        resolved_workspace_id = ensure_workspace(session, workspace_id=workspace_id).id
        KnowledgeService().get_knowledge_base(
            session,
            knowledge_base_id,
            workspace_id=resolved_workspace_id,
        )
        # 在提交任务时确认扩展存在，将错误前置到用户可理解的请求阶段。
        try:
            parser = self.registry.get_parser(parser_name)
            chunker = self.registry.get_chunker(chunker_name)
        except ExtensionNotFoundError as exc:
            raise ProcessingError(message="指定的入库扩展未启用。") from exc

        # 脱敏发生在内容指纹、持久化、切块与 Embedding 之前，避免凭证进入任何下游索引。
        raw_content = sanitize_knowledge_content(raw_content).content

        content_hash = (
            None if source_url else hashlib.sha256(raw_content.encode("utf-8")).hexdigest()
        )
        existing = (
            self.document_repository.get_by_source_url(
                session,
                knowledge_base_id=knowledge_base_id,
                workspace_id=resolved_workspace_id,
                source_url=source_url,
            )
            if source_url
            else self.document_repository.get_by_content_hash(
                session,
                knowledge_base_id=knowledge_base_id,
                workspace_id=resolved_workspace_id,
                content_hash=content_hash or "",
            )
        )
        if existing is not None:
            raise DuplicateResourceError(
                message="相同内容已在当前知识库中导入，请使用已有文档或重试其入库任务。",
                details={"documentId": existing.id, "status": existing.status},
            )

        document = Document(
            workspace_id=resolved_workspace_id,
            knowledge_base_id=knowledge_base_id,
            title=title,
            source_type=source_type,
            source_url=source_url,
            # 本地文件没有外部 URL；网页在完成入库后由 Worker 独立写入校验结果。
            source_validation_state="pending" if source_type == "webpage" else "not_applicable",
            web_content_state="unchecked" if source_type == "webpage" else "not_applicable",
            raw_content=raw_content,
            content_hash=content_hash,
            status="queued",
        )
        job = IngestionJob(
            workspace_id=resolved_workspace_id,
            document=document,
            config_snapshot={
                "parser": parser.name,
                "parserVersion": parser.version,
                "chunker": chunker.name,
                "chunkerVersion": chunker.version,
            },
        )
        self.document_repository.create(session, document)
        self.job_repository.create(session, job)
        session.flush()
        if commit:
            session.commit()
            session.refresh(document)
            session.refresh(job)
        return document, job

    def create_url_document(
        self,
        session: Session,
        *,
        knowledge_base_id: str,
        url: str,
        title: str | None = None,
        chunker_name: str = "structured",
        workspace_id: str | None = None,
        commit: bool = True,
    ) -> tuple[Document, IngestionJob]:
        """创建网页入库任务；实际抓取和正文提取由 Worker 执行。"""

        normalized_url = normalize_web_url(url)
        fallback_title = normalized_url.rstrip("/").rsplit("/", 1)[-1] or normalized_url
        return self.create_document(
            session,
            knowledge_base_id=knowledge_base_id,
            title=(title or fallback_title)[:240],
            source_type="webpage",
            raw_content="",
            parser_name="plain_text",
            chunker_name=chunker_name,
            source_url=normalized_url,
            workspace_id=workspace_id,
            commit=commit,
        )

    def create_uploaded_document(
        self,
        session: Session,
        *,
        knowledge_base_id: str,
        filename: str,
        content_type: str,
        content: bytes,
        chunker_name: str = "structured",
        workspace_id: str | None = None,
        commit: bool = True,
    ) -> tuple[Document, IngestionJob]:
        """解析上传文件为标准文本，再复用同一入库任务与切块链路。"""

        try:
            source_type, parser_name = FILE_TYPE_CONFIG[content_type]
            parser = self.registry.get_file_parser(parser_name)
        except KeyError as exc:
            raise ProcessingError(message="暂不支持该文件类型。") from exc
        except ExtensionNotFoundError as exc:
            raise ProcessingError(message="该文件类型的解析器未启用。") from exc

        try:
            parsed = parser.parse_bytes(title=filename, content=content)
        except Exception as exc:
            raise ProcessingError(message="无法解析该文件，请确认文件未损坏且未加密。") from exc
        if not parsed.text.strip():
            raise ProcessingError(message="文件中没有可索引的文本内容。")

        document, job = self.create_document(
            session,
            knowledge_base_id=knowledge_base_id,
            title=filename,
            source_type=source_type,
            raw_content=parsed.text,
            chunker_name=chunker_name,
            workspace_id=workspace_id,
            commit=commit,
        )
        return document, job

    def list_documents(
        self,
        session: Session,
        *,
        knowledge_base_id: str,
        workspace_id: str | None = None,
        offset: int,
        limit: int,
    ) -> tuple[list[Document], int]:
        resolved_workspace_id = ensure_workspace(session, workspace_id=workspace_id).id
        KnowledgeService().get_knowledge_base(
            session,
            knowledge_base_id,
            workspace_id=resolved_workspace_id,
        )
        return self.document_repository.list_by_knowledge_base(
            session,
            knowledge_base_id=knowledge_base_id,
            workspace_id=resolved_workspace_id,
            offset=offset,
            limit=limit,
        )

    def get_document(
        self, session: Session, *, document_id: str, workspace_id: str | None = None
    ) -> Document:
        """读取文档原始解析文本，工作区边界由仓储查询强制执行。"""

        resolved_workspace_id = ensure_workspace(session, workspace_id=workspace_id).id
        document = self.document_repository.get(
            session, document_id, workspace_id=resolved_workspace_id
        )
        if document is None:
            raise ResourceNotFoundError(details={"resource": "document"})
        return document

    def update_document_governance(
        self,
        session: Session,
        *,
        document_id: str,
        source_trust_level: str,
        effective_at: datetime | None,
        expires_at: datetime | None,
        conflict_state: str,
        supersedes_document_id: str | None,
        expected_version: int,
        workspace_id: str | None = None,
        actor_id: str = "workspace-user",
    ) -> Document:
        """更新人工治理元数据，并建立同知识库内可审计的替代链。"""

        resolved_workspace_id = ensure_workspace(session, workspace_id=workspace_id).id
        document = self.document_repository.get(
            session, document_id, workspace_id=resolved_workspace_id
        )
        if document is None or document.status == "archived":
            raise ResourceNotFoundError(details={"resource": "document"})
        if document.governance_version != expected_version:
            raise VersionConflictError(details={"resource": "document_governance"})
        if effective_at and expires_at and effective_at >= expires_at:
            raise ProcessingError(message="资料生效时间必须早于到期时间。")
        if supersedes_document_id == document.id:
            raise ProcessingError(message="文档不能替代自身。")
        if supersedes_document_id:
            predecessor = self.document_repository.get(
                session, supersedes_document_id, workspace_id=resolved_workspace_id
            )
            if (
                predecessor is None
                or predecessor.status == "archived"
                or predecessor.knowledge_base_id != document.knowledge_base_id
            ):
                raise ProcessingError(message="被替代文档必须属于当前知识库且处于活动状态。")

        document.source_trust_level = source_trust_level
        document.effective_at = effective_at
        document.expires_at = expires_at
        document.conflict_state = conflict_state
        document.supersedes_document_id = supersedes_document_id
        document.governance_version += 1
        self.audit_repository.create(
            session,
            AuditEvent(
                workspace_id=resolved_workspace_id,
                actor_type="user",
                actor_id=actor_id,
                action="document_governance_updated",
                target_type="document",
                target_id=document.id,
                payload={
                    "trustLevel": source_trust_level,
                    "conflictState": conflict_state,
                    "hasEffectiveAt": str(effective_at is not None).lower(),
                    "hasExpiresAt": str(expires_at is not None).lower(),
                    "hasSupersedes": str(supersedes_document_id is not None).lower(),
                    "governanceVersion": str(document.governance_version),
                },
            ),
        )
        session.commit()
        session.refresh(document)
        return document

    def retry_document(
        self, session: Session, *, document_id: str, workspace_id: str | None = None
    ) -> IngestionJob:
        """将失败任务重新排队，保留原始文档和审计链路，避免用户重复上传。"""

        resolved_workspace_id = ensure_workspace(session, workspace_id=workspace_id).id
        document = self.document_repository.get(
            session, document_id, workspace_id=resolved_workspace_id
        )
        if document is None:
            raise ResourceNotFoundError(details={"resource": "document"})
        job = self.job_repository.get_by_document(
            session, document_id, workspace_id=resolved_workspace_id
        )
        if job is None:
            raise ResourceNotFoundError(details={"resource": "ingestion_job"})
        if job.state not in {"failed", "dead_letter"}:
            raise ProcessingError(
                message="仅失败或进入死信的入库任务可以重试。", details={"state": job.state}
            )
        job.state = "queued"
        job.attempts = 0
        job.available_at = utc_now()
        job.locked_at = None
        job.locked_by = None
        job.last_error_at = None
        job.error_code = None
        job.error_message = None
        document.status = "queued"
        session.commit()
        session.refresh(job)
        return job

    def archive_document(
        self,
        session: Session,
        *,
        document_id: str,
        workspace_id: str | None = None,
        commit: bool = True,
    ) -> Document:
        """归档文档并清理检索索引，保留原始内容以便审计和后续恢复。

        正在处理的任务不允许删除，避免 Worker 在另一个事务中提交新切块后
        把已经归档的文档重新标记为 indexed。
        """

        resolved_workspace_id = ensure_workspace(session, workspace_id=workspace_id).id
        document = self.document_repository.get(
            session, document_id, workspace_id=resolved_workspace_id
        )
        if document is None or document.status == "archived":
            raise ResourceNotFoundError(details={"resource": "document"})
        job = self.job_repository.get_by_document(
            session, document_id, workspace_id=resolved_workspace_id
        )
        if document.status == "processing" or (job is not None and job.state == "running"):
            raise ProcessingError(message="文档正在处理中，请等待任务完成后再删除。")

        # 先删图索引再删切块，确保实体关系不会遗留指向已归档文档的脏边。
        GraphIndexService().delete_document(
            session,
            document_id=document.id,
            workspace_id=resolved_workspace_id,
        )
        # 删除向量和切块，但保留 documents 行，确保来源状态仍可审计。
        self.document_repository.replace_chunks(
            session,
            document_id=document.id,
            workspace_id=resolved_workspace_id,
            chunks=[],
        )
        document.status = "archived"
        # 释放唯一内容哈希，使归档后可以重新上传同一份文件。
        document.content_hash = None
        if job is not None and job.state in {"queued", "failed", "dead_letter"}:
            job.state = "cancelled"
            job.error_code = None
            job.error_message = None
        # 显式 flush 让待删除的实体、切块在社区查询前可见，避免归档文档残留在摘要快照中。
        session.flush()
        # 归档后立即为剩余活动文档重建社区；单文档知识库会自然得到空社区。
        CommunitySummaryService().rebuild(
            session,
            knowledge_base_id=document.knowledge_base_id,
            workspace_id=resolved_workspace_id,
        )
        if commit:
            session.commit()
            session.refresh(document)
        return document

    def rechunk_knowledge_base(
        self, session: Session, *, knowledge_base_id: str, workspace_id: str | None = None
    ) -> tuple[int, int]:
        """在事务外调用 Embedding，随后使用短写事务替换切分和索引。"""

        resolved_workspace_id = ensure_workspace(session, workspace_id=workspace_id).id
        KnowledgeService().get_knowledge_base(
            session, knowledge_base_id, workspace_id=resolved_workspace_id
        )
        documents = [
            (
                document.id,
                document.title,
                document.source_type,
                sanitize_knowledge_content(document.raw_content).content,
            )
            for document in session.scalars(
                select(Document).where(
                    Document.workspace_id == resolved_workspace_id,
                    Document.knowledge_base_id == knowledge_base_id,
                    Document.status.in_(("indexed", "processing")),
                )
            )
        ]
        sanitized_content_by_document_id = {
            document_id: raw_content for document_id, _, _, raw_content in documents
        }
        # 结束读取事务后再访问模型服务，避免 SQLite 在网络抖动时锁住其他请求。
        session.commit()
        chunker = self.registry.get_chunker("structured")
        settings = ConfigurationService().resolve_settings(
            session, workspace_id=resolved_workspace_id
        )
        embedding_provider = build_embedding_provider(settings)
        embedding_revision = ConfigurationService().embedding_revision(
            session, workspace_id=resolved_workspace_id
        )
        prepared_documents: list[tuple[str, list[DocumentChunk], list[list[float]], str, str]] = []
        try:
            for document_id, title, source_type, raw_content in documents:
                parser_name = "markdown" if source_type == "markdown" else "plain_text"
                parser = self.registry.get_parser(parser_name)
                parsed = parser.parse(title=title, content=raw_content)
                drafts = chunker.chunk(parsed)
                if not drafts:
                    raise ProcessingError(message=f"文档“{title}”没有可用于重建的有效文本。")
                vectors = embedding_provider.embed_documents([draft.content for draft in drafts])
                if len(vectors) != len(drafts):
                    raise ProcessingError(message=f"文档“{title}”的 Embedding 返回数量不完整。")
                if any(len(vector) != settings.embedding_dimensions for vector in vectors):
                    raise ProcessingError(
                        message=f"文档“{title}”的 Embedding 实际维度与当前设置不一致。"
                    )
                prepared_documents.append(
                    (
                        document_id,
                        [
                            DocumentChunk(
                                workspace_id=resolved_workspace_id,
                                document_id=document_id,
                                ordinal=draft.ordinal,
                                content=draft.content,
                                metadata_json=draft.metadata,
                            )
                            for draft in drafts
                        ],
                        vectors,
                        parser.name,
                        parser.version,
                    )
                )
        except Exception as exc:
            if isinstance(exc, ProcessingError):
                raise
            raise ProcessingError(message="重建文档切分与索引失败。") from exc

        try:
            chunk_count = 0
            for document_id, chunks, vectors, parser_name, parser_version in prepared_documents:
                document = self.document_repository.get(
                    session, document_id, workspace_id=resolved_workspace_id
                )
                if document is None:
                    raise ResourceNotFoundError(details={"resource": "document"})
                # 旧资产的受控修复：重切分同时替换原文，之后切块和向量不会保留旧凭证。
                document.raw_content = sanitized_content_by_document_id[document_id]
                GraphIndexService().delete_document(
                    session,
                    document_id=document.id,
                    workspace_id=resolved_workspace_id,
                )
                self.document_repository.replace_chunks(
                    session,
                    document_id=document.id,
                    workspace_id=resolved_workspace_id,
                    chunks=chunks,
                )
                job = self.job_repository.get_by_document(
                    session, document.id, workspace_id=resolved_workspace_id
                )
                if job is not None:
                    job.config_snapshot = {
                        **job.config_snapshot,
                        "parser": parser_name,
                        "parserVersion": parser_version,
                        "chunker": chunker.name,
                        "chunkerVersion": chunker.version,
                    }
                session.flush()
                persisted_chunks = list(
                    session.scalars(
                        select(DocumentChunk)
                        .where(
                            DocumentChunk.workspace_id == resolved_workspace_id,
                            DocumentChunk.document_id == document.id,
                        )
                        .order_by(DocumentChunk.ordinal.asc())
                    )
                )
                GraphIndexService().index_document(
                    session,
                    document_id=document.id,
                    workspace_id=resolved_workspace_id,
                    knowledge_base_id=knowledge_base_id,
                    chunks=persisted_chunks,
                )
                session.add_all(
                    ChunkEmbedding(
                        workspace_id=resolved_workspace_id,
                        document_chunk_id=chunk.id,
                        provider_name=embedding_provider.name,
                        model_name=embedding_provider.model_name,
                        dimensions=len(vector),
                        embedding_revision=embedding_revision,
                        embedding=vector,
                        embedding_vector=vector,
                    )
                    for chunk, vector in zip(persisted_chunks, vectors, strict=True)
                )
                document.status = "indexed"
                chunk_count += len(persisted_chunks)
            knowledge_base = KnowledgeService().get_knowledge_base(
                session, knowledge_base_id, workspace_id=resolved_workspace_id
            )
            # 所有文档完成图索引后一次性构建社区，避免在重切分过程中重复生成摘要。
            CommunitySummaryService().rebuild(
                session,
                knowledge_base_id=knowledge_base_id,
                workspace_id=resolved_workspace_id,
            )
            knowledge_base.index_status = "ready"
            session.commit()
        except Exception as exc:
            session.rollback()
            if isinstance(exc, ProcessingError):
                raise
            raise ProcessingError(message="重建文档切分与索引失败。") from exc
        return len(documents), chunk_count

    def start_rechunk_knowledge_base(
        self, session: Session, *, knowledge_base_id: str, workspace_id: str | None = None
    ) -> int:
        """将长耗时的切分重建转换为后台任务，前台仅负责切换到 building 状态。"""

        resolved_workspace_id = ensure_workspace(session, workspace_id=workspace_id).id
        knowledge_base = KnowledgeService().get_knowledge_base(
            session, knowledge_base_id, workspace_id=resolved_workspace_id
        )
        if knowledge_base.index_status == "building":
            raise ProcessingError(message="当前知识库正在重建切分与索引，请等待任务完成。")
        documents = list(
            session.scalars(
                select(Document).where(
                    Document.workspace_id == resolved_workspace_id,
                    Document.knowledge_base_id == knowledge_base_id,
                    Document.status == "indexed",
                )
            )
        )
        for document in documents:
            document.status = "processing"
        knowledge_base.index_status = "building"
        # 重切分期间切块 ID 会变化，先禁止社区检索并清理旧摘要，防止短暂引用旧块。
        CommunitySummaryService().invalidate(
            session,
            knowledge_base_id=knowledge_base_id,
            workspace_id=resolved_workspace_id,
        )
        session.commit()
        return len(documents)

    def mark_rechunk_failed(
        self, session: Session, *, knowledge_base_id: str, workspace_id: str
    ) -> None:
        """后台任务失败后保留旧资料，但禁止混用不完整的新索引。"""

        knowledge_base = KnowledgeService().get_knowledge_base(
            session, knowledge_base_id, workspace_id=workspace_id
        )
        knowledge_base.index_status = "stale"
        knowledge_base.graph_status = "stale"
        session.query(Document).filter(
            Document.workspace_id == workspace_id,
            Document.knowledge_base_id == knowledge_base_id,
            Document.status == "processing",
        ).update({Document.status: "failed"}, synchronize_session=False)
        session.commit()

    def run_job(
        self,
        session: Session,
        *,
        job_id: str,
        workspace_id: str | None = None,
        worker_id: str | None = None,
    ) -> IngestionJob:
        """执行一个任务。队列 Worker 只负责领取任务，业务状态机在这里维护。"""

        resolved_workspace_id = workspace_id
        if resolved_workspace_id is None:
            # Worker 没有 HTTP 请求上下文，从任务自身的租户字段恢复 scope。
            raw_job = session.get(IngestionJob, job_id)
            resolved_workspace_id = raw_job.workspace_id if raw_job is not None else None
        if resolved_workspace_id is None:
            resolved_workspace_id = ensure_workspace(session).id
        else:
            # Worker 必须先绑定租户上下文，再读取任务；PostgreSQL RLS 下未绑定会默认拒绝。
            ensure_workspace(session, workspace_id=resolved_workspace_id, create_default=False)
        job = self.job_repository.get(
            session,
            job_id,
            workspace_id=resolved_workspace_id,
        )
        if job is None:
            raise ResourceNotFoundError(details={"resource": "ingestion_job"})
        if job.state not in {"queued", "failed"}:
            raise ProcessingError(message="当前入库任务不能执行。", details={"state": job.state})

        job.state = "running"
        job.attempts += 1
        job.available_at = utc_now()
        job.locked_at = utc_now()
        job.locked_by = worker_id or "background"
        job.last_error_at = None
        job.error_code = None
        job.error_message = None
        job.document.status = "processing"
        session.commit()

        try:
            if job.document.source_type == "webpage":
                if not job.document.source_url:
                    raise ProcessingError(message="网页文档缺少来源 URL")
                fetched = fetch_web_page(job.document.source_url)
                sanitized_content = sanitize_knowledge_content(fetched.text).content
                content_hash = hashlib.sha256(sanitized_content.encode("utf-8")).hexdigest()
                duplicate = self.document_repository.get_by_content_hash(
                    session,
                    knowledge_base_id=job.document.knowledge_base_id,
                    workspace_id=job.document.workspace_id,
                    content_hash=content_hash,
                )
                if duplicate is not None and duplicate.id != job.document.id:
                    raise DuplicateResourceError(
                        message="网页正文与现有文档重复",
                        details={"documentId": duplicate.id, "status": duplicate.status},
                    )
                job.document.title = fetched.title[:240]
                job.document.raw_content = sanitized_content
                job.document.content_hash = content_hash
                # 首次入库后的正文就是当前基线，不将其误报为外部变更。
                job.document.web_content_state = "unchanged"
                job.document.web_content_checked_at = utc_now()
            else:
                # 兼容旧文档和历史失败任务：Worker 执行前再次清洗，确保重试不会绕过边界。
                job.document.raw_content = sanitize_knowledge_content(
                    job.document.raw_content
                ).content
            parser = self.registry.get_parser(job.config_snapshot["parser"])
            chunker = self.registry.get_chunker(job.config_snapshot["chunker"])
            parsed = parser.parse(title=job.document.title, content=job.document.raw_content)
            drafts = chunker.chunk(parsed)
            if not drafts:
                raise ProcessingError(message="文档没有可索引的有效文本。")
            chunks = [
                DocumentChunk(
                    workspace_id=resolved_workspace_id,
                    document_id=job.document_id,
                    ordinal=draft.ordinal,
                    content=draft.content,
                    metadata_json=draft.metadata,
                )
                for draft in drafts
            ]
            GraphIndexService().delete_document(
                session,
                document_id=job.document_id,
                workspace_id=resolved_workspace_id,
            )
            self.document_repository.replace_chunks(
                session,
                document_id=job.document_id,
                workspace_id=resolved_workspace_id,
                chunks=chunks,
            )
            session.flush()
            resolved_settings = ConfigurationService().resolve_settings(
                session, workspace_id=resolved_workspace_id
            )
            graph_service = GraphIndexService(extractor=build_graph_extractor(resolved_settings))
            graph_service.index_document(
                session,
                document_id=job.document_id,
                workspace_id=resolved_workspace_id,
                knowledge_base_id=job.document.knowledge_base_id,
            )
            # Embedding 索引与切块写入处于同一事务，避免文档显示已入库却无法语义检索。
            embedding_provider = build_embedding_provider(resolved_settings)
            EmbeddingService(
                provider=embedding_provider,
                expected_dimensions=resolved_settings.embedding_dimensions,
            ).index_document(
                session,
                document=job.document,
                embedding_revision=ConfigurationService().embedding_revision(
                    session, workspace_id=job.document.workspace_id
                ),
            )
            # 标签提议属于治理增强层：自动命中只进入 pending 队列，
            # 任何异常均不能影响正文和向量入库。
            try:
                with session.begin_nested():
                    TagGovernanceService().auto_propose_for_document(
                        session,
                        document=job.document,
                        workspace_id=resolved_workspace_id,
                    )
            except Exception:
                logger.exception(
                    "knowledge_tag_auto_proposal_failed",
                    document_id=job.document_id,
                )
            job.state = "succeeded"
            job.document.status = "indexed"
            job.available_at = utc_now()
            job.locked_at = None
            job.locked_by = None
            job.last_error_at = None
            summary_generator = None
            if (
                resolved_settings.llm_provider == "openai_compatible"
                and resolved_settings.llm_api_key
            ):
                try:
                    summary_generator = LLMCommunitySummaryGenerator(
                        build_llm_provider(resolved_settings)
                    )
                except Exception:
                    # 社区摘要模型是增强项，连接失败时保留确定性摘要。
                    summary_generator = None
            try:
                # 使用保存点隔离社区增强层；社区表异常不能回滚已完成的文档向量入库。
                with session.begin_nested():
                    CommunitySummaryService().rebuild(
                        session,
                        knowledge_base_id=job.document.knowledge_base_id,
                        workspace_id=resolved_workspace_id,
                        summary_generator=summary_generator,
                        extractor_provider=getattr(graph_service.extractor, "name", "rule"),
                        extractor_version=getattr(graph_service.extractor, "version", "v1"),
                    )
            except Exception:
                # 社区层是增强索引，不能让其单点故障把已经完成的向量入库标记为失败。
                knowledge_base = KnowledgeService().get_knowledge_base(
                    session,
                    job.document.knowledge_base_id,
                    workspace_id=resolved_workspace_id,
                )
                knowledge_base.graph_status = "stale"
                logger.exception(
                    "knowledge_community_rebuild_failed",
                    knowledge_base_id=job.document.knowledge_base_id,
                )
            session.commit()
        except Exception as exc:
            # Worker 必须把失败转化为可观察的任务状态，不能只依赖日志或静默丢弃。
            session.rollback()
            # rollback 会结束 PostgreSQL 的 RLS 租户上下文，必须重新绑定后再写回失败状态。
            ensure_workspace(session, workspace_id=resolved_workspace_id, create_default=False)
            job = self.job_repository.get(
                session,
                job_id,
                workspace_id=resolved_workspace_id,
            )
            assert job is not None
            settings = get_settings()
            now = utc_now()
            job.state = (
                "dead_letter" if job.attempts >= settings.ingestion_max_attempts else "failed"
            )
            job.error_code = (
                "INGESTION_DEAD_LETTER" if job.state == "dead_letter" else "INGESTION_FAILED"
            )
            job.error_message = str(exc)[:500]
            job.document.status = "failed"
            job.last_error_at = now
            job.locked_at = None
            job.locked_by = None
            retry_seconds = min(
                settings.ingestion_retry_max_seconds,
                settings.ingestion_retry_base_seconds * (2 ** max(job.attempts - 1, 0)),
            )
            job.available_at = now + timedelta(seconds=retry_seconds)
            session.commit()
            if isinstance(exc, ProcessingError):
                raise
            raise ProcessingError(message="文档入库失败。") from exc
        session.refresh(job)
        if job.document.source_type == "webpage":
            # 来源复核属于入库后的增强元数据：网络校验失败只能写回来源状态，
            # 不能回滚已完成的正文、向量和图谱索引。
            SourceValidationService().validate_document(
                session,
                document_id=job.document_id,
                workspace_id=resolved_workspace_id,
            )
            session.refresh(job)
        return job
