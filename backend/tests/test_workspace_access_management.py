"""数据库化工作区成员与访问令牌的安全回归测试。"""

from collections.abc import Generator
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings
from app.domain.workspace import Workspace


def create_workspace(session_factory: sessionmaker[Session]) -> str:
    workspace_id = str(uuid4())
    with session_factory() as session:
        session.add(Workspace(id=workspace_id, name="访问管理测试工作区"))
        session.commit()
    return workspace_id


@pytest.fixture()
def bootstrap_headers(
    client: TestClient,
    session_factory: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
) -> Generator[dict[str, str], None, None]:
    workspace_id = create_workspace(session_factory)
    monkeypatch.setenv("APP_AUTH_ENABLED", "true")
    monkeypatch.setenv("APP_WORKSPACE_API_KEYS", f"{workspace_id}=bootstrap-secret")
    get_settings.cache_clear()
    try:
        yield {"X-API-Key": "bootstrap-secret", "X-Workspace-ID": workspace_id}
    finally:
        get_settings.cache_clear()


def test_database_token_resolves_member_identity_and_can_be_revoked(
    client: TestClient, bootstrap_headers: dict[str, str]
) -> None:
    owner = client.post(
        "/api/v1/workspace/members",
        headers=bootstrap_headers,
        json={"email": "owner@example.com", "displayName": "Owner", "role": "owner"},
    )
    assert owner.status_code == 201
    owner_id = owner.json()["userId"]

    created = client.post(
        "/api/v1/workspace/access-tokens",
        headers=bootstrap_headers,
        json={"userId": owner_id, "label": "local development"},
    )
    assert created.status_code == 201
    body = created.json()
    assert body["accessToken"].startswith("rna_")
    assert "tokenHash" not in body

    identity = client.get(
        "/api/v1/workspace/identity",
        headers={"X-API-Key": body["accessToken"], "X-Actor-ID": "forged-user"},
    )
    assert identity.status_code == 200
    assert identity.json()["actorId"] == owner_id
    assert identity.json()["actorRole"] == "owner"

    revoked = client.delete(
        f"/api/v1/workspace/access-tokens/{body['id']}", headers=bootstrap_headers
    )
    assert revoked.status_code == 204
    denied = client.get(
        "/api/v1/workspace", headers={"X-API-Key": body["accessToken"]}
    )
    assert denied.status_code == 401


def test_cannot_disable_the_last_active_owner(
    client: TestClient, bootstrap_headers: dict[str, str]
) -> None:
    owner = client.post(
        "/api/v1/workspace/members",
        headers=bootstrap_headers,
        json={"email": "owner@example.com", "displayName": "Owner", "role": "owner"},
    )
    assert owner.status_code == 201

    disabled = client.patch(
        f"/api/v1/workspace/members/{owner.json()['userId']}",
        headers=bootstrap_headers,
        json={"state": "disabled"},
    )
    assert disabled.status_code == 422
    assert disabled.json()["error"]["code"] == "PROCESSING_ERROR"


def test_viewer_token_cannot_manage_workspace_members(
    client: TestClient, bootstrap_headers: dict[str, str]
) -> None:
    viewer = client.post(
        "/api/v1/workspace/members",
        headers=bootstrap_headers,
        json={"email": "viewer@example.com", "displayName": "Viewer", "role": "viewer"},
    )
    assert viewer.status_code == 201
    token = client.post(
        "/api/v1/workspace/access-tokens",
        headers=bootstrap_headers,
        json={"userId": viewer.json()["userId"], "label": "read-only client"},
    )
    assert token.status_code == 201

    denied = client.get(
        "/api/v1/workspace/members", headers={"X-API-Key": token.json()["accessToken"]}
    )
    assert denied.status_code == 403
    assert denied.json()["error"]["code"] == "WORKSPACE_ACCESS_DENIED"
