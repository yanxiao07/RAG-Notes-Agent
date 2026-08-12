"""模型网关的一次性连通测试，不持久化调用时提供的密钥。"""

from time import perf_counter

import httpx
from sqlalchemy.orm import Session

from app.application.configuration_service import ConfigurationService
from app.core.errors import ConfigurationError, ModelUnavailableError


class ModelConnectivityService:
    """用最小请求验证模型网关、凭据和目标模型均可访问。"""

    def __init__(self, configuration_service: ConfigurationService | None = None) -> None:
        self.configuration_service = configuration_service or ConfigurationService()

    def test_llm(
        self,
        session: Session,
        *,
        workspace_id: str,
        provider: str,
        model: str,
        base_url: str,
        api_key: str | None,
    ) -> tuple[str, str, int, str]:
        if provider == "evidence_synthesis":
            return provider, "deterministic-evidence-synthesis", 0, "本地问答降级能力可用。"
        self.configuration_service.validate_provider(
            provider,
            model,
            base_url,
            allowed_providers={"evidence_synthesis", "openai_compatible"},
        )
        resolved_key = self._resolve_key(
            session, workspace_id=workspace_id, supplied_key=api_key, kind="llm"
        )
        started = perf_counter()
        try:
            response = httpx.post(
                f"{base_url.rstrip('/')}/chat/completions",
                headers={"Authorization": f"Bearer {resolved_key}"},
                json={
                    "model": model,
                    "messages": [{"role": "user", "content": "ping"}],
                    "max_tokens": 1,
                    "temperature": 0,
                    "stream": False,
                },
                timeout=15.0,
            )
            response.raise_for_status()
            if not isinstance(response.json().get("choices"), list):
                raise ValueError("missing choices")
        except (httpx.HTTPError, TypeError, ValueError) as exc:
            raise ModelUnavailableError(message="LLM 网关、凭据或模型不可用。") from exc
        return provider, model, self._latency_ms(started), "LLM 连通测试成功。"

    def test_embedding(
        self,
        session: Session,
        *,
        workspace_id: str,
        provider: str,
        model: str,
        base_url: str,
        api_key: str | None,
    ) -> tuple[str, str, int, str]:
        if provider == "hashing":
            return provider, model or "hashing", 0, "本地 Hashing Embedding 可用。"
        self.configuration_service.validate_provider(
            provider,
            model,
            base_url,
            allowed_providers={"hashing", "openai_compatible"},
        )
        resolved_key = self._resolve_key(
            session, workspace_id=workspace_id, supplied_key=api_key, kind="embedding"
        )
        started = perf_counter()
        try:
            response = httpx.post(
                f"{base_url.rstrip('/')}/embeddings",
                headers={"Authorization": f"Bearer {resolved_key}"},
                json={"model": model, "input": "connectivity check"},
                timeout=15.0,
            )
            response.raise_for_status()
            data = response.json().get("data")
            if (
                not isinstance(data, list)
                or not data
                or not isinstance(data[0].get("embedding"), list)
            ):
                raise ValueError("missing embedding")
        except (httpx.HTTPError, AttributeError, TypeError, ValueError) as exc:
            raise ModelUnavailableError(message="Embedding 网关、凭据或模型不可用。") from exc
        return provider, model, self._latency_ms(started), "Embedding 连通测试成功。"

    def test_reranker(
        self,
        session: Session,
        *,
        workspace_id: str,
        provider: str,
        model: str,
        base_url: str,
        api_key: str | None,
    ) -> tuple[str, str, int, str]:
        if provider == "rule":
            return provider, "deterministic-rule-reranker", 0, "本地规则重排可用。"
        self.configuration_service.validate_provider(
            provider,
            model,
            base_url,
            allowed_providers={"rule", "dashscope_compatible"},
        )
        resolved_key = self._resolve_key(
            session, workspace_id=workspace_id, supplied_key=api_key, kind="reranker"
        )
        started = perf_counter()
        try:
            response = httpx.post(
                f"{base_url.rstrip('/')}/reranks",
                headers={"Authorization": f"Bearer {resolved_key}"},
                json={
                    "model": model,
                    "query": "connectivity check",
                    "documents": ["candidate one", "candidate two"],
                    "top_n": 1,
                },
                timeout=15.0,
            )
            response.raise_for_status()
            payload = response.json()
            results = payload.get("output", {}).get("results") or payload.get("results")
            if not isinstance(results, list):
                raise ValueError("missing results")
        except (httpx.HTTPError, AttributeError, TypeError, ValueError) as exc:
            raise ModelUnavailableError(message="Reranker 网关、凭据或模型不可用。") from exc
        return provider, model, self._latency_ms(started), "Reranker 连通测试成功。"

    def _resolve_key(
        self, session: Session, *, workspace_id: str, supplied_key: str | None, kind: str
    ) -> str:
        if supplied_key:
            return supplied_key
        settings = self.configuration_service.resolve_settings(session, workspace_id=workspace_id)
        key_by_kind = {
            "llm": settings.llm_api_key,
            "embedding": settings.embedding_api_key,
            "reranker": settings.reranker_api_key,
        }
        key = key_by_kind.get(kind, "")
        if not key:
            raise ConfigurationError(message="请先填写 API Key，或保存该工作区的模型密钥。")
        return key

    @staticmethod
    def _latency_ms(started: float) -> int:
        return max(1, round((perf_counter() - started) * 1000))
