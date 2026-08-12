"""模型设置 API 的密钥脱敏、加密持久化与工作区隔离测试。"""

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from app.core.config import get_settings
from app.domain.workspace import Workspace, WorkspaceModelConfiguration


def model_configuration_payload() -> dict[str, object]:
    return {
        "llmProvider": "openai_compatible",
        "llmModel": "gpt-4.1-mini",
        "llmBaseUrl": "https://models.example.com/v1",
        "llmApiKey": "llm-secret-for-test",
        "clearLlmApiKey": False,
        "embeddingProvider": "openai_compatible",
        "embeddingModel": "text-embedding-3-small",
        "embeddingBaseUrl": "https://models.example.com/v1",
        "embeddingApiKey": "embedding-secret-for-test",
        "clearEmbeddingApiKey": False,
        "embeddingDimensions": 1536,
        "useQueryRewrite": False,
        "useReranker": False,
    }


def test_model_configuration_encrypts_secrets_and_never_returns_them(
    client: TestClient,
    session_factory: sessionmaker,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    encryption_key = Fernet.generate_key().decode()
    monkeypatch.setenv("APP_CONFIGURATION_ENCRYPTION_KEY", encryption_key)
    get_settings.cache_clear()
    try:
        payload = model_configuration_payload()
        payload["useQueryRouter"] = True
        saved = client.put("/api/v1/runtime/model-configuration", json=payload)
        assert saved.status_code == 200
        assert saved.json()["hasLlmApiKey"] is True
        assert saved.json()["hasEmbeddingApiKey"] is True
        assert saved.json()["useQueryRouter"] is True
        assert "secret-for-test" not in saved.text

        fetched = client.get("/api/v1/runtime/model-configuration")
        assert fetched.status_code == 200
        assert "apiKey" not in fetched.text
        assert "secret-for-test" not in fetched.text
        assert fetched.json()["useQueryRouter"] is True

        with session_factory() as session:
            stored = session.query(WorkspaceModelConfiguration).one()
            assert stored.llm_api_key_encrypted != "llm-secret-for-test"
            assert (
                Fernet(encryption_key.encode())
                .decrypt(stored.llm_api_key_encrypted.encode())
                .decode()
                == "llm-secret-for-test"
            )
    finally:
        get_settings.cache_clear()


def test_saving_secret_requires_encryption_key(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    # 空环境变量优先于本地 .env，用于验证服务不会退化为明文保存。
    monkeypatch.setenv("APP_CONFIGURATION_ENCRYPTION_KEY", "")
    get_settings.cache_clear()
    try:
        response = client.put(
            "/api/v1/runtime/model-configuration", json=model_configuration_payload()
        )
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "CONFIGURATION_ERROR"
    finally:
        get_settings.cache_clear()


def test_model_configuration_is_isolated_by_workspace(
    client: TestClient,
    session_factory: sessionmaker,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    encryption_key = Fernet.generate_key().decode()
    monkeypatch.setenv("APP_CONFIGURATION_ENCRYPTION_KEY", encryption_key)
    get_settings.cache_clear()
    try:
        assert (
            client.put(
                "/api/v1/runtime/model-configuration", json=model_configuration_payload()
            ).status_code
            == 200
        )
        other_workspace_id = "00000000-0000-0000-0000-000000000099"
        with session_factory() as session:
            session.add(Workspace(id=other_workspace_id, name="隔离设置工作区"))
            session.commit()

        isolated = client.get(
            "/api/v1/runtime/model-configuration",
            headers={"X-Workspace-ID": other_workspace_id},
        )
        assert isolated.status_code == 200
        assert isolated.json()["hasLlmApiKey"] is False
        assert isolated.json()["llmProvider"] == "evidence_synthesis"
    finally:
        get_settings.cache_clear()


def test_local_model_connectivity_checks_do_not_persist_configuration(client: TestClient) -> None:
    llm = client.post(
        "/api/v1/runtime/model-configuration/test/llm",
        json={"provider": "evidence_synthesis", "model": "", "baseUrl": ""},
    )
    assert llm.status_code == 200
    assert llm.json()["latencyMs"] == 0
    assert "密钥" not in llm.text

    embedding = client.post(
        "/api/v1/runtime/model-configuration/test/embedding",
        json={"provider": "hashing", "model": "hashing-256", "baseUrl": ""},
    )
    assert embedding.status_code == 200
    assert embedding.json()["latencyMs"] == 0

    reranker = client.post(
        "/api/v1/runtime/model-configuration/test/reranker",
        json={"provider": "rule", "model": "", "baseUrl": ""},
    )
    assert reranker.status_code == 200
    assert reranker.json()["latencyMs"] == 0

    configuration = client.get("/api/v1/runtime/model-configuration")
    assert configuration.status_code == 200
    assert configuration.json()["llmProvider"] == "evidence_synthesis"
    assert configuration.json()["embeddingProvider"] == "hashing"
    assert configuration.json()["useQueryRouter"] is False


def test_embedding_model_change_requires_rebuild_before_retrieval(client: TestClient) -> None:
    """模型语义空间切换后，服务不得拿旧向量继续生成带引用的回答。"""

    knowledge_base = client.post(
        "/api/v1/knowledge-bases",
        json={"name": "索引版本验证", "description": None},
    ).json()
    client.post(
        f"/api/v1/knowledge-bases/{knowledge_base['id']}/notes",
        json={"title": "说明", "content": "索引版本变更必须重建。"},
    )

    response = client.put(
        "/api/v1/runtime/model-configuration",
        json={
            "llmProvider": "evidence_synthesis",
            "llmModel": "",
            "llmBaseUrl": "",
            "embeddingProvider": "hashing",
            "embeddingModel": "hashing-256-v2",
            "embeddingBaseUrl": "",
            "embeddingDimensions": 256,
            "useReranker": False,
        },
    )
    assert response.status_code == 200
    assert response.json()["embeddingRevision"] == 2

    blocked = client.post(
        "/api/v1/retrieval/search",
        json={"knowledgeBaseId": knowledge_base["id"], "query": "索引版本"},
    )
    assert blocked.status_code == 409
    assert blocked.json()["error"]["code"] == "INDEX_REBUILD_REQUIRED"

    rebuilt = client.post(f"/api/v1/knowledge-bases/{knowledge_base['id']}/embeddings/rebuild")
    assert rebuilt.status_code == 202
    assert rebuilt.json()["indexStatus"] == "ready"
    assert rebuilt.json()["embeddingRevision"] == 2

    restored = client.post(
        "/api/v1/retrieval/search",
        json={"knowledgeBaseId": knowledge_base["id"], "query": "索引版本"},
    )
    assert restored.status_code == 200
