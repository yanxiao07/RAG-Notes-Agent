"""通用写请求幂等行为测试。"""

from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from app.application.idempotency_service import IdempotencyService
from app.domain.idempotency import IdempotencyRecord
from app.domain.workspace import Workspace


def create_knowledge_base(client: TestClient) -> dict[str, object]:
    response = client.post("/api/v1/knowledge-bases", json={"name": "幂等测试库"})
    assert response.status_code == 201
    return response.json()


def document_payload(knowledge_base_id: str, *, title: str = "幂等文档") -> dict[str, object]:
    return {
        "knowledgeBaseId": knowledge_base_id,
        "title": title,
        "sourceType": "plain_text",
        "content": "这是一段用于验证幂等写入的内容。",
        "parser": "plain_text",
        "chunker": "structured",
    }


def test_document_idempotency_replays_same_response(client: TestClient) -> None:
    knowledge_base = create_knowledge_base(client)
    payload = document_payload(str(knowledge_base["id"]))
    headers = {"Idempotency-Key": "document-create-001"}

    first = client.post("/api/v1/documents", json=payload, headers=headers)
    second = client.post("/api/v1/documents", json=payload, headers=headers)

    assert first.status_code == 202
    assert second.status_code == 202
    assert second.headers["Idempotency-Replayed"] == "true"
    assert second.json() == first.json()
    documents = client.get(f"/api/v1/knowledge-bases/{knowledge_base['id']}/documents")
    assert documents.json()["meta"]["total"] == 1


def test_idempotency_key_cannot_be_reused_for_different_body(client: TestClient) -> None:
    knowledge_base = create_knowledge_base(client)
    headers = {"Idempotency-Key": "document-create-conflict"}
    first = client.post(
        "/api/v1/documents",
        json=document_payload(str(knowledge_base["id"])),
        headers=headers,
    )
    assert first.status_code == 202

    conflict = client.post(
        "/api/v1/documents",
        json=document_payload(str(knowledge_base["id"]), title="另一个标题"),
        headers=headers,
    )
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "IDEMPOTENCY_CONFLICT"


def test_failed_request_releases_key_for_retry(client: TestClient) -> None:
    knowledge_base = create_knowledge_base(client)
    headers = {"Idempotency-Key": "document-create-retry"}
    invalid = client.post(
        "/api/v1/documents",
        json=document_payload("missing-knowledge-base"),
        headers=headers,
    )
    assert invalid.status_code == 404

    retried = client.post(
        "/api/v1/documents",
        json=document_payload(str(knowledge_base["id"])),
        headers=headers,
    )
    assert retried.status_code == 202
    assert "Idempotency-Replayed" not in retried.headers


def test_agent_proposal_and_approval_are_idempotent(client: TestClient) -> None:
    knowledge_base = create_knowledge_base(client)
    proposal_payload = {
        "knowledgeBaseId": knowledge_base["id"],
        "title": "审批后的笔记",
        "content": "需要人工确认后才能写入知识库。",
        "rationale": "验证 Agent 写操作幂等。",
    }
    proposal_headers = {"Idempotency-Key": "agent-proposal-001"}
    first = client.post(
        "/api/v1/agent/note-proposals",
        json=proposal_payload,
        headers=proposal_headers,
    )
    second = client.post(
        "/api/v1/agent/note-proposals",
        json=proposal_payload,
        headers=proposal_headers,
    )
    assert first.status_code == 201
    assert second.status_code == 201
    assert second.headers["Idempotency-Replayed"] == "true"
    assert second.json() == first.json()

    proposal_id = first.json()["proposal"]["id"]
    approve_headers = {"Idempotency-Key": "agent-approve-001"}
    approved = client.post(
        f"/api/v1/change-proposals/{proposal_id}/approve",
        headers=approve_headers,
    )
    repeated = client.post(
        f"/api/v1/change-proposals/{proposal_id}/approve",
        headers=approve_headers,
    )
    assert approved.status_code == 200
    assert repeated.status_code == 200
    assert repeated.headers["Idempotency-Replayed"] == "true"
    assert repeated.json() == approved.json()


def test_idempotency_isolated_by_workspace_and_expired_keys_can_retry(
    session_factory: sessionmaker[Session],
) -> None:
    session = session_factory()
    try:
        first_workspace = Workspace(id="workspace-idempotency-1", name="工作区 1")
        second_workspace = Workspace(id="workspace-idempotency-2", name="工作区 2")
        session.add_all([first_workspace, second_workspace])
        session.commit()
        service = IdempotencyService()

        first = service.start(
            session,
            workspace_id=first_workspace.id,
            operation_scope="notes:create",
            idempotency_key="shared-key",
            request_payload={"title": "同一内容"},
        )
        assert first is not None and not first.replay
        service.complete(session, first, status_code=201, response_json={"id": "one"})
        session.commit()

        second = service.start(
            session,
            workspace_id=second_workspace.id,
            operation_scope="notes:create",
            idempotency_key="shared-key",
            request_payload={"title": "同一内容"},
        )
        assert second is not None and not second.replay
        service.release(session, second)

        record = session.get(IdempotencyRecord, first.record_id)
        assert record is not None
        record.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        session.commit()
        retried = service.start(
            session,
            workspace_id=first_workspace.id,
            operation_scope="notes:create",
            idempotency_key="shared-key",
            request_payload={"title": "同一内容"},
        )
        assert retried is not None and not retried.replay
        service.release(session, retried)
    finally:
        session.close()
