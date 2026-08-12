"""工作区模型配置的加密存储与有效配置解析。"""

from urllib.parse import urlparse

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.errors import ConfigurationError, ResourceNotFoundError
from app.domain.knowledge.repositories import KnowledgeBaseRepository
from app.domain.workspace import WorkspaceModelConfiguration


class ConfigurationService:
    """配置值可由环境变量提供，也可由工作区加密覆盖。"""

    def get(self, session: Session, *, workspace_id: str) -> WorkspaceModelConfiguration | None:
        return (
            session.query(WorkspaceModelConfiguration)
            .filter(WorkspaceModelConfiguration.workspace_id == workspace_id)
            .one_or_none()
        )

    def resolve_settings(self, session: Session, *, workspace_id: str) -> Settings:
        settings = get_settings()
        config = self.get(session, workspace_id=workspace_id)
        if config is None:
            return settings
        return settings.model_copy(
            update={
                "llm_provider": config.llm_provider,
                "llm_model": config.llm_model,
                "llm_base_url": config.llm_base_url,
                "llm_api_key": self._decrypt(config.llm_api_key_encrypted) or settings.llm_api_key,
                "embedding_provider": config.embedding_provider,
                "embedding_model": config.embedding_model,
                "embedding_base_url": config.embedding_base_url,
                "embedding_api_key": self._decrypt(config.embedding_api_key_encrypted)
                or settings.embedding_api_key,
                "embedding_dimensions": config.embedding_dimensions,
                "query_rewrite_enabled": config.use_query_rewrite,
                "query_router_enabled": config.use_query_router,
                "reranker_enabled": config.use_reranker,
                "reranker_provider": config.reranker_provider,
                "reranker_model": config.reranker_model,
                "reranker_base_url": config.reranker_base_url,
                "reranker_api_key": self._decrypt(config.reranker_api_key_encrypted)
                or settings.reranker_api_key,
            }
        )

    def embedding_revision(self, session: Session, *, workspace_id: str) -> int:
        """解析当前工作区的嵌入修订号；未覆写配置时沿用默认的第 1 版。"""

        config = self.get(session, workspace_id=workspace_id)
        return config.embedding_revision if config is not None else 1

    def update(
        self,
        session: Session,
        *,
        workspace_id: str,
        llm_provider: str,
        llm_model: str,
        llm_base_url: str,
        llm_api_key: str | None,
        clear_llm_api_key: bool,
        embedding_provider: str,
        embedding_model: str,
        embedding_base_url: str,
        embedding_api_key: str | None,
        clear_embedding_api_key: bool,
        embedding_dimensions: int,
        use_query_rewrite: bool,
        use_query_router: bool,
        use_reranker: bool,
        reranker_provider: str,
        reranker_model: str,
        reranker_base_url: str,
        reranker_api_key: str | None,
        clear_reranker_api_key: bool,
    ) -> WorkspaceModelConfiguration:
        settings = get_settings()
        if not settings.allow_user_model_configuration:
            raise ConfigurationError(message="当前部署禁止在工作区中修改模型配置。")
        self.validate_provider(
            llm_provider,
            llm_model,
            llm_base_url,
            allowed_providers={"evidence_synthesis", "openai_compatible"},
        )
        self.validate_provider(
            embedding_provider,
            embedding_model,
            embedding_base_url,
            allowed_providers={"hashing", "openai_compatible"},
        )
        self.validate_provider(
            reranker_provider,
            reranker_model,
            reranker_base_url,
            allowed_providers={"rule", "dashscope_compatible"},
        )
        config = self.get(session, workspace_id=workspace_id)
        # 比较的是实际向量空间身份而非 API Key。密钥轮换不应造成不必要的全库重建。
        old_embedding_identity = (
            (
                config.embedding_provider,
                config.embedding_model,
                config.embedding_base_url,
                config.embedding_dimensions,
            )
            if config is not None
            else (
                settings.embedding_provider,
                settings.embedding_model,
                settings.embedding_base_url,
                settings.embedding_dimensions,
            )
        )
        new_embedding_identity = (
            embedding_provider,
            embedding_model,
            embedding_base_url,
            embedding_dimensions,
        )
        if config is None:
            config = WorkspaceModelConfiguration(workspace_id=workspace_id)
            session.add(config)
        config.llm_provider = llm_provider
        config.llm_model = llm_model
        config.llm_base_url = llm_base_url
        config.embedding_provider = embedding_provider
        config.embedding_model = embedding_model
        config.embedding_base_url = embedding_base_url
        config.embedding_dimensions = embedding_dimensions
        config.use_query_rewrite = use_query_rewrite
        config.use_query_router = use_query_router
        config.use_reranker = use_reranker
        config.reranker_provider = reranker_provider
        config.reranker_model = reranker_model
        config.reranker_base_url = reranker_base_url
        if old_embedding_identity != new_embedding_identity:
            # ORM 的 Python 端 default 会在 flush 时才落值；新建配置对象这里需要显式兜底。
            config.embedding_revision = (config.embedding_revision or 1) + 1
            KnowledgeBaseRepository().mark_indexes_stale(session, workspace_id=workspace_id)
        if llm_api_key:
            config.llm_api_key_encrypted = self._encrypt(llm_api_key)
        elif clear_llm_api_key:
            config.llm_api_key_encrypted = None
        if embedding_api_key:
            config.embedding_api_key_encrypted = self._encrypt(embedding_api_key)
        elif clear_embedding_api_key:
            config.embedding_api_key_encrypted = None
        if reranker_api_key:
            config.reranker_api_key_encrypted = self._encrypt(reranker_api_key)
        elif clear_reranker_api_key:
            config.reranker_api_key_encrypted = None
        session.commit()
        session.refresh(config)
        return config

    @staticmethod
    def validate_provider(
        provider: str,
        model: str,
        base_url: str,
        *,
        allowed_providers: set[str],
    ) -> None:
        if provider not in allowed_providers:
            raise ConfigurationError(message="指定的 Provider 不受支持。")
        if provider in {"evidence_synthesis", "hashing", "rule"}:
            return
        if not model.strip():
            raise ConfigurationError(message="真实模型 Provider 必须填写模型名称。")
        parsed = urlparse(base_url)
        if parsed.scheme != "https" or not parsed.netloc:
            raise ConfigurationError(message="模型网关地址必须是合法的 HTTPS URL。")

    def _encrypt(self, value: str) -> str:
        return self._cipher().encrypt(value.encode()).decode()

    def _decrypt(self, value: str | None) -> str | None:
        if not value:
            return None
        try:
            return self._cipher().decrypt(value.encode()).decode()
        except InvalidToken as exc:
            raise ResourceNotFoundError(details={"resource": "model_configuration_secret"}) from exc

    @staticmethod
    def _cipher() -> Fernet:
        key = get_settings().configuration_encryption_key
        if not key:
            raise ConfigurationError(
                message="服务端未配置 APP_CONFIGURATION_ENCRYPTION_KEY，不能安全保存模型密钥。"
            )
        try:
            return Fernet(key.encode())
        except ValueError as exc:
            raise ConfigurationError(message="APP_CONFIGURATION_ENCRYPTION_KEY 格式无效。") from exc
