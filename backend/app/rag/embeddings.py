"""Embedding Provider 与本地开发向量实现。"""

import hashlib
import math
import re

import httpx

from app.core.config import Settings
from app.core.errors import ModelUnavailableError
from app.core.model_resilience import call_with_model_resilience
from app.extensions.contracts import EmbeddingProvider

TOKEN_PATTERN = re.compile(r"[\u4e00-\u9fff]|[a-zA-Z0-9_]{2,}")


class HashingEmbeddingProvider:
    """仅用于本地流程验证的确定性稀疏向量。

    它不是语义模型，因此设置 API 会明确标记为 development-only；生产环境需配置
    `openai_compatible` 或同契约的企业 Embedding Provider。
    """

    name = "hashing"

    def __init__(self, dimensions: int) -> None:
        self.model_name = f"hashing-{dimensions}"
        self.dimensions = dimensions

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self.embed_query(text) for text in texts]

    def embed_query(self, text: str) -> list[float]:
        vector = [0.0] * self.dimensions
        for token in TOKEN_PATTERN.findall(text.lower()):
            index = int.from_bytes(hashlib.blake2b(token.encode(), digest_size=4).digest(), "big")
            vector[index % self.dimensions] += 1.0
        return _normalize(vector)


class OpenAICompatibleEmbeddingProvider:
    """OpenAI Embeddings 兼容 Provider，支持企业模型网关。"""

    name = "openai_compatible"

    def __init__(self, settings: Settings) -> None:
        if not settings.embedding_api_key or not settings.embedding_model:
            raise ModelUnavailableError(message="Embedding 模型尚未完成配置。")
        self.model_name = settings.embedding_model
        self.dimensions = settings.embedding_dimensions
        self._api_key = settings.embedding_api_key
        self._url = f"{settings.embedding_base_url.rstrip('/')}/embeddings"
        self._timeout = settings.llm_timeout_seconds
        self._batch_size = settings.embedding_batch_size
        self._settings = settings

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        # 文档入库常包含数百个切块；部分兼容网关限制单次 input 数量，必须保序分批。
        return [
            vector
            for start in range(0, len(texts), self._batch_size)
            for vector in self._request(texts[start : start + self._batch_size])
        ]

    def embed_query(self, text: str) -> list[float]:
        return self._request([text])[0]

    def _request(self, inputs: list[str]) -> list[list[float]]:
        try:
            response = call_with_model_resilience(
                lambda: _post_and_raise(
                    self._url,
                    headers={"Authorization": f"Bearer {self._api_key}"},
                    json={"model": self.model_name, "input": inputs},
                    timeout=self._timeout,
                ),
                settings=self._settings,
                operation="embedding",
            )
            rows = response.json()["data"]
            vectors = [list(map(float, row["embedding"])) for row in rows]
        except (httpx.HTTPError, KeyError, TypeError, ValueError, IndexError) as exc:
            raise ModelUnavailableError(message="Embedding 模型暂时不可用。") from exc
        if len(vectors) != len(inputs) or not vectors:
            raise ModelUnavailableError(message="Embedding 模型返回的数据不完整。")
        self.dimensions = len(vectors[0])
        if any(len(vector) != self.dimensions for vector in vectors):
            raise ModelUnavailableError(message="Embedding 模型返回了不一致的向量维度。")
        return [_normalize(vector) for vector in vectors]


def build_embedding_provider(settings: Settings) -> EmbeddingProvider:
    if settings.embedding_provider == HashingEmbeddingProvider.name:
        if not settings.allow_local_development_providers:
            raise ModelUnavailableError(message="生产环境禁止使用本地 Hashing Embedding。")
        return HashingEmbeddingProvider(settings.embedding_dimensions)
    if settings.embedding_provider == OpenAICompatibleEmbeddingProvider.name:
        return OpenAICompatibleEmbeddingProvider(settings)
    raise ModelUnavailableError(message="指定的 Embedding Provider 未启用。")


def _post_and_raise(
    url: str,
    *,
    headers: dict[str, str],
    json: dict[str, object],
    timeout: float,
) -> httpx.Response:
    response = httpx.post(url, headers=headers, json=json, timeout=timeout)
    response.raise_for_status()
    return response


def cosine_similarity(left: list[float], right: list[float]) -> float:
    if len(left) != len(right) or not left or not right:
        return 0.0
    return sum(a * b for a, b in zip(left, right, strict=True))


def _normalize(vector: list[float]) -> list[float]:
    magnitude = math.sqrt(sum(value * value for value in vector))
    return [value / magnitude for value in vector] if magnitude else vector
