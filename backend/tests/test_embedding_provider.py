"""Embedding Provider 的分批请求回归测试。"""

from typing import cast

import httpx
import pytest

from app.application.embedding_service import EmbeddingService
from app.core.config import Settings
from app.core.errors import ConfigurationError
from app.rag.embeddings import OpenAICompatibleEmbeddingProvider


def test_openai_compatible_embedding_batches_large_document_requests(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[list[str]] = []

    def fake_post(_: str, *, json: dict[str, object], **__: object) -> httpx.Response:
        inputs = cast(list[str], json["input"])
        calls.append(inputs)
        return httpx.Response(
            200,
            request=httpx.Request("POST", "https://models.example.com/v1/embeddings"),
            json={"data": [{"embedding": [1.0, 0.0]} for _ in inputs]},
        )

    monkeypatch.setattr("app.rag.embeddings.httpx.post", fake_post)
    provider = OpenAICompatibleEmbeddingProvider(
        Settings(
            embedding_api_key="test-key",
            embedding_model="test-embedding",
            embedding_base_url="https://models.example.com/v1",
            embedding_batch_size=25,
        )
    )

    vectors = provider.embed_documents([f"chunk-{index}" for index in range(26)])

    assert [len(batch) for batch in calls] == [25, 1]
    assert len(vectors) == 26
    assert vectors[0] == [1.0, 0.0]


def test_embedding_service_rejects_dimension_mismatch_before_database_write() -> None:
    service = EmbeddingService(expected_dimensions=1536)

    with pytest.raises(ConfigurationError) as error:
        service._validate_vector_dimensions([[0.0] * 256])

    assert error.value.details == {"expectedDimensions": 1536, "actualDimensions": 256}
