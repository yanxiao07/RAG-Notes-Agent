"""工作区租户隔离和 API Key 认证回归测试。"""

from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings
from app.domain.workspace import Workspace


def create_workspace(session_factory: sessionmaker[Session]) -> str:
    workspace_id = str(uuid4())
    with session_factory() as session:
        session.add(Workspace(id=workspace_id, name="隔离测试工作区"))
        session.commit()
    return workspace_id


def test_resources_are_isolated_by_workspace(
    client: TestClient, session_factory: sessionmaker[Session]
) -> None:
    other_workspace_id = create_workspace(session_factory)
    created = client.post(
        "/api/v1/knowledge-bases",
        json={"name": "默认工作区资料"},
    )
    assert created.status_code == 201
    knowledge_base_id = created.json()["id"]

    hidden = client.get(
        f"/api/v1/knowledge-bases/{knowledge_base_id}",
        headers={"X-Workspace-ID": other_workspace_id},
    )
    assert hidden.status_code == 404
    assert hidden.json()["error"]["code"] == "RESOURCE_NOT_FOUND"

    isolated_list = client.get(
        "/api/v1/knowledge-bases",
        headers={"X-Workspace-ID": other_workspace_id},
    )
    assert isolated_list.status_code == 200
    assert isolated_list.json()["meta"]["total"] == 0


def test_api_key_auth_requires_matching_workspace(
    client: TestClient,
    session_factory: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace_id = create_workspace(session_factory)
    monkeypatch.setenv("APP_AUTH_ENABLED", "true")
    monkeypatch.setenv("APP_WORKSPACE_API_KEYS", f"{workspace_id}=test-secret")
    get_settings.cache_clear()
    try:
        missing = client.get("/api/v1/workspace")
        assert missing.status_code == 401
        assert missing.json()["error"]["code"] == "AUTHENTICATION_REQUIRED"

        allowed = client.get(
            "/api/v1/workspace",
            headers={"X-API-Key": "test-secret", "X-Workspace-ID": workspace_id},
        )
        assert allowed.status_code == 200
        assert allowed.json()["id"] == workspace_id

        mismatched = client.get(
            "/api/v1/workspace",
            headers={"X-API-Key": "test-secret", "X-Workspace-ID": str(uuid4())},
        )
        assert mismatched.status_code == 403
        assert mismatched.json()["error"]["code"] == "WORKSPACE_ACCESS_DENIED"
    finally:
        get_settings.cache_clear()
