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
        llm_provider: str | None,
        llm_model: str | None,
        llm_base_url: str | None,
        llm_api_key: str | None,
        clear_llm_api_key: bool | None,
        embedding_provider: str | None,
        embedding_model: str | None,
        embedding_base_url: str | None,
        embedding_api_key: str | None,
        clear_embedding_api_key: bool | None,
        embedding_dimensions: int | None,
        use_query_rewrite: bool | None,
        use_query_router: bool | None,
        use_reranker: bool | None,
        reranker_provider: str | None,
        reranker_model: str | None,
        reranker_base_url: str | None,
        reranker_api_key: str | None,
        clear_reranker_api_key: bool | None,
    ) -> WorkspaceModelConfiguration:
        settings = get_settings()
        if not settings.allow_user_model_configuration:
            raise ConfigurationError(message="当前部署禁止在工作区中修改模型配置。")
        config = self.get(session, workspace_id=workspace_id)
        effective = self.resolve_settings(session, workspace_id=workspace_id)

        # 先用当前有效配置合并请求，再按模型组验证。这样一次保存一个模型组时，
        # 未填写的其他模型不会因为空字段而触发校验失败。
        next_llm_provider = llm_provider if llm_provider is not None else effective.llm_provider
        next_llm_model = llm_model if llm_model is not None else effective.llm_model
        next_llm_base_url = llm_base_url if llm_base_url is not None else effective.llm_base_url
        next_embedding_provider = (
            embedding_provider if embedding_provider is not None else effective.embedding_provider
        )
        next_embedding_model = (
            embedding_model if embedding_model is not None else effective.embedding_model
        )
        next_embedding_base_url = (
            embedding_base_url if embedding_base_url is not None else effective.embedding_base_url
        )
        next_embedding_dimensions = (
            embedding_dimensions
            if embedding_dimensions is not None
            else effective.embedding_dimensions
        )
        next_reranker_provider = (
            reranker_provider if reranker_provider is not None else effective.reranker_provider
        )
        next_reranker_model = (
            reranker_model if reranker_model is not None else effective.reranker_model
        )
        next_reranker_base_url = (
            reranker_base_url if reranker_base_url is not None else effective.reranker_base_url
        )
        next_use_query_rewrite = (
            use_query_rewrite
            if use_query_rewrite is not None
            else effective.query_rewrite_enabled
        )
        next_use_query_router = (
            use_query_router if use_query_router is not None else effective.query_router_enabled
        )
        next_use_reranker = (
            use_reranker if use_reranker is not None else effective.reranker_enabled
        )
        llm_touched = any(
            value is not None
            for value in (
                llm_provider,
                llm_model,
                llm_base_url,
                llm_api_key,
                clear_llm_api_key,
                use_query_rewrite,
                use_query_router,
            )
        )
        embedding_touched = any(
            value is not None
            for value in (
                embedding_provider,
                embedding_model,
                embedding_base_url,
                embedding_api_key,
                clear_embedding_api_key,
                embedding_dimensions,
            )
        )
        reranker_fields_touched = any(
            value is not None
            for value in (
                reranker_provider,
                reranker_model,
                reranker_base_url,
                reranker_api_key,
                clear_reranker_api_key,
            )
        )
        if llm_touched:
            self.validate_provider(
                next_llm_provider,
                next_llm_model,
                next_llm_base_url,
                allowed_providers={"evidence_synthesis", "openai_compatible"},
            )
        if embedding_touched:
            self.validate_provider(
                next_embedding_provider,
                next_embedding_model,
                next_embedding_base_url,
                allowed_providers={"hashing", "openai_compatible"},
            )
        if reranker_fields_touched or use_reranker is True:
            self.validate_provider(
                next_reranker_provider,
                next_reranker_model,
                next_reranker_base_url,
                allowed_providers={"rule", "dashscope_compatible"},
            )

        # 比较的是实际向量空间身份而非 API Key。密钥轮换不应造成不必要的全库重建。
        old_embedding_identity = (
            effective.embedding_provider,
            effective.embedding_model,
            effective.embedding_base_url,
            effective.embedding_dimensions,
        )
        new_embedding_identity = (
            next_embedding_provider,
            next_embedding_model,
            next_embedding_base_url,
            next_embedding_dimensions,
        )
        if config is None:
            # 首次创建时把完整的有效值写入工作区，避免 ORM 默认值覆盖部署配置。
            config = WorkspaceModelConfiguration(workspace_id=workspace_id)
            config.embedding_revision = 1
            session.add(config)
        config.llm_provider = next_llm_provider
        config.llm_model = next_llm_model
        config.llm_base_url = next_llm_base_url
        config.embedding_provider = next_embedding_provider
        config.embedding_model = next_embedding_model
        config.embedding_base_url = next_embedding_base_url
        config.embedding_dimensions = next_embedding_dimensions
        config.use_query_rewrite = next_use_query_rewrite
        config.use_query_router = next_use_query_router
        config.use_reranker = next_use_reranker
        config.reranker_provider = next_reranker_provider
        config.reranker_model = next_reranker_model
        config.reranker_base_url = next_reranker_base_url
        if old_embedding_identity != new_embedding_identity:
            # ORM 的 Python 端 default 会在 flush 时才落值；新建配置对象这里需要显式兜底。
            config.embedding_revision = (config.embedding_revision or 1) + 1
            KnowledgeBaseRepository().mark_indexes_stale(session, workspace_id=workspace_id)
        if llm_api_key:
            config.llm_api_key_encrypted = self._encrypt(llm_api_key)
        elif clear_llm_api_key is True:
            config.llm_api_key_encrypted = None
        if embedding_api_key:
            config.embedding_api_key_encrypted = self._encrypt(embedding_api_key)
        elif clear_embedding_api_key is True:
            config.embedding_api_key_encrypted = None
        if reranker_api_key:
            config.reranker_api_key_encrypted = self._encrypt(reranker_api_key)
        elif clear_reranker_api_key is True:
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
