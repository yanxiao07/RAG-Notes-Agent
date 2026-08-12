"""网页来源健康与受信任域名校验。

校验结果是文档元数据，不参与正文解析和向量生成。这样外部站点短暂不可用时，
系统仍可保留已审计的历史证据，同时在回答引用中如实标注来源状态。
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from datetime import datetime, timedelta
from urllib.parse import urlsplit

from sqlalchemy.orm import Session, sessionmaker

from app.application.web_import_service import (
    FetchedWebPage,
    WebSourceValidation,
    fetch_web_page,
    validate_web_source,
)
from app.core.config import Settings, get_settings
from app.core.database import SessionLocal
from app.core.logging import get_logger
from app.core.workspace import ensure_workspace
from app.domain.agent.models import AuditEvent
from app.domain.agent.repositories import AuditEventRepository
from app.domain.knowledge.models import Document, utc_now
from app.domain.knowledge.repositories import DocumentRepository
from app.security.content_sanitization import sanitize_knowledge_content

logger = get_logger(__name__)

SourceValidator = Callable[[str], WebSourceValidation]
WebPageFetcher = Callable[[str], FetchedWebPage]


class SourceValidationService:
    """将无状态网络校验结果安全写回当前工作区文档。"""

    def __init__(
        self,
        *,
        validator: SourceValidator = validate_web_source,
        page_fetcher: WebPageFetcher = fetch_web_page,
        settings: Settings | None = None,
        document_repository: DocumentRepository | None = None,
    ) -> None:
        self.validator = validator
        self.page_fetcher = page_fetcher
        self.settings = settings or get_settings()
        self.document_repository = document_repository or DocumentRepository()
        self.audit_repository = AuditEventRepository()

    def mark_pending(
        self,
        session: Session,
        *,
        document_id: str,
        workspace_id: str,
    ) -> Document:
        """请求人工复核时先写入 pending，避免 UI 展示过期的历史结论。"""

        document = self._get_web_document(
            session, document_id=document_id, workspace_id=workspace_id
        )
        document.source_validation_state = "pending"
        document.source_validated_at = None
        document.source_validation_status_code = None
        document.source_redirect_url = None
        document.source_content_type = None
        document.source_validation_error_code = None
        session.commit()
        session.refresh(document)
        return document

    def validate_document(
        self,
        session: Session,
        *,
        document_id: str,
        workspace_id: str,
    ) -> Document | None:
        """执行一次校验并持久化脱敏状态；校验失败不会影响文档的 indexed 状态。"""

        document = self.document_repository.get(session, document_id, workspace_id=workspace_id)
        if document is None or document.status == "archived":
            return None
        if document.source_type != "webpage" or not document.source_url:
            return document

        ensure_workspace(session, workspace_id=workspace_id, create_default=False)
        if not self.settings.source_validation_enabled:
            document.source_validation_state = "unchecked"
            document.source_is_approved = False
            document.source_validated_at = utc_now()
            document.source_validation_error_code = "validator_disabled"
            session.commit()
            session.refresh(document)
            return document

        try:
            result = self.validator(document.source_url)
        except Exception:
            # 第三方客户端或扩展校验器异常不能传播到 Worker，更不能改写入库任务状态。
            logger.exception("source_validation_unexpected_error", document_id=document_id)
            result = WebSourceValidation(
                state="unavailable",
                final_url=None,
                status_code=None,
                content_type=None,
                error_code="validator_error",
            )

        document.source_validation_state = result.state
        document.source_validated_at = utc_now()
        document.source_validation_status_code = result.status_code
        document.source_redirect_url = result.final_url
        document.source_content_type = result.content_type
        document.source_validation_error_code = result.error_code
        document.source_is_approved = _is_approved_domain(
            result.final_url or document.source_url,
            approved_domains=self.settings.source_validation_approved_domains,
        )
        session.commit()
        session.refresh(document)
        logger.info(
            "source_validation_finished",
            document_id=document.id,
            state=document.source_validation_state,
            status_code=document.source_validation_status_code,
            approved=document.source_is_approved,
        )
        return document

    def revalidate_due_documents(
        self,
        session: Session,
        *,
        workspace_id: str,
        now: datetime | None = None,
    ) -> int:
        """按受控周期批量复核网页来源，返回实际领取数量。

        自动任务只能判断链接是否仍可安全访问，不能据此修改可信度、冲突状态或替代关系。
        这些业务判断仍必须由人工或经过批准的领域规则维护。
        """

        if not (
            self.settings.source_validation_enabled
            and self.settings.source_validation_recheck_enabled
        ):
            return 0

        effective_now = now or utc_now()
        ensure_workspace(session, workspace_id=workspace_id, create_default=False)
        documents = self.document_repository.claim_due_web_source_validations(
            session,
            workspace_id=workspace_id,
            validated_before=effective_now
            - timedelta(hours=self.settings.source_validation_recheck_interval_hours),
            claimed_at=effective_now,
            limit=self.settings.source_validation_recheck_batch_size,
        )
        document_ids = [document.id for document in documents]
        for document_id in document_ids:
            document = self.validate_document(
                session,
                document_id=document_id,
                workspace_id=workspace_id,
            )
            if document is not None:
                self.detect_content_change(
                    session,
                    document_id=document.id,
                    workspace_id=workspace_id,
                )
        if document_ids:
            logger.info(
                "source_validation_batch_finished",
                document_count=len(document_ids),
            )
        return len(document_ids)

    def detect_content_change(
        self,
        session: Session,
        *,
        document_id: str,
        workspace_id: str,
    ) -> Document | None:
        """检测网页正文变化，但绝不替换已入库正文、切块或向量。

        内容更新通常需要领域人员判断是否适合覆盖现有知识。该检测只给出受控的 ``changed``
        信号，用户必须在工作台显式重新导入，才会创建新的索引版本。
        """

        document = self.document_repository.get(session, document_id, workspace_id=workspace_id)
        if (
            document is None
            or document.status == "archived"
            or document.source_type != "webpage"
            or not document.source_url
            or document.source_validation_state != "valid"
            or not self.settings.web_content_change_detection_enabled
        ):
            return document

        try:
            fetched = self.page_fetcher(document.source_url)
            candidate_content = sanitize_knowledge_content(fetched.text).content
            candidate_hash = hashlib.sha256(candidate_content.encode("utf-8")).hexdigest()
        except Exception:
            # 页面正文探测是来源健康的增强项，抓取失败不能污染连通性状态或阻断后续批次。
            logger.exception("web_content_change_detection_failed", document_id=document_id)
            return document

        previous_state = document.web_content_state
        document.web_content_checked_at = utc_now()
        document.web_content_state = (
            "unchanged" if candidate_hash == document.content_hash else "changed"
        )
        if document.web_content_state == "changed" and previous_state != "changed":
            self.audit_repository.create(
                session,
                AuditEvent(
                    workspace_id=workspace_id,
                    actor_type="system",
                    actor_id="source-revalidator",
                    action="web_content_change_detected",
                    target_type="document",
                    target_id=document.id,
                    payload={"reindexRequired": "true"},
                ),
            )
        session.commit()
        session.refresh(document)
        logger.info(
            "web_content_change_detection_finished",
            document_id=document.id,
            state=document.web_content_state,
        )
        return document

    def _get_web_document(
        self, session: Session, *, document_id: str, workspace_id: str
    ) -> Document:
        ensure_workspace(session, workspace_id=workspace_id, create_default=False)
        document = self.document_repository.get(session, document_id, workspace_id=workspace_id)
        if document is None or document.status == "archived":
            from app.core.errors import ResourceNotFoundError

            raise ResourceNotFoundError(details={"resource": "document"})
        if document.source_type != "webpage" or not document.source_url:
            from app.core.errors import ProcessingError

            raise ProcessingError(message="仅网页文档支持来源校验。")
        return document


def execute_source_validation(
    document_id: str,
    workspace_id: str,
    session_factory: sessionmaker[Session] = SessionLocal,
) -> None:
    """供 BackgroundTasks 或独立 Worker 调用的任务入口。"""

    with session_factory() as session:
        try:
            SourceValidationService().validate_document(
                session,
                document_id=document_id,
                workspace_id=workspace_id,
            )
        except Exception:
            session.rollback()
            logger.exception("source_validation_task_failed", document_id=document_id)


def _is_approved_domain(url: str, *, approved_domains: str) -> bool:
    """支持精确域名与子域名匹配；空白白名单表示“已验证但非受信任域名”。"""

    hostname = (urlsplit(url).hostname or "").rstrip(".").lower()
    domains = {
        item.strip().lower().lstrip(".") for item in approved_domains.split(",") if item.strip()
    }
    return bool(hostname and domains) and any(
        hostname == domain or hostname.endswith(f".{domain}") for domain in domains
    )
