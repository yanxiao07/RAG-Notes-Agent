"""Agent 提议风险、最小证据快照和审批角色测试。"""

from datetime import UTC, datetime, timedelta
from typing import cast

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.domain.agent.models import ChangeProposal


def create_knowledge_base(client: TestClient) -> dict[str, object]:
    response = client.post("/api/v1/knowledge-bases", json={"name": "审批治理测试库"})
    assert response.status_code == 201
    return response.json()


def create_proposal(client: TestClient, knowledge_base_id: str) -> dict[str, object]:
    response = client.post(
        "/api/v1/agent/note-proposals",
        json={
            "knowledgeBaseId": knowledge_base_id,
            "title": "待审批笔记",
            "content": "只在批准后写入。",
            "rationale": "验证风险治理。",
            "evidenceSnapshot": [
                {
                    "sourceType": "document_chunk",
                    "sourceId": "chunk-1",
                    "title": "研究资料",
                    "locator": "document:doc-1:chunk:0",
                    "score": 0.92,
                    "content": "不应写入快照的正文",
                }
            ],
        },
    )
    assert response.status_code == 201
    return cast(dict[str, object], response.json()["proposal"])


def test_proposal_contains_risk_and_minimal_evidence_snapshot(client: TestClient) -> None:
    knowledge_base = create_knowledge_base(client)
    proposal = create_proposal(client, str(knowledge_base["id"]))

    assert proposal["riskLevel"] == "medium"
    assert proposal["requiredRole"] == "approver"
    evidence = proposal["evidenceSnapshot"]
    assert isinstance(evidence, list)
    assert len(evidence) == 1
    assert isinstance(evidence[0], dict)
    assert "content" not in evidence[0]
    assert proposal["expiresAt"] is not None


def test_insufficient_claimed_role_cannot_approve(client: TestClient) -> None:
    knowledge_base = create_knowledge_base(client)
    proposal = create_proposal(client, str(knowledge_base["id"]))
    response = client.post(
        f"/api/v1/change-proposals/{proposal['id']}/approve",
        headers={"X-Actor-ID": "editor", "X-Actor-Role": "viewer"},
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "PROCESSING_ERROR"


def test_expired_proposal_is_not_executable(
    client: TestClient, session_factory: sessionmaker[Session]
) -> None:
    knowledge_base = create_knowledge_base(client)
    proposal = create_proposal(client, str(knowledge_base["id"]))
    with session_factory() as session:
        item = session.scalar(select(ChangeProposal).where(ChangeProposal.id == proposal["id"]))
        assert item is not None
        item.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        session.commit()

    response = client.post(f"/api/v1/change-proposals/{proposal['id']}/approve")
    assert response.status_code == 410
    assert response.json()["error"]["code"] == "PROPOSAL_EXPIRED"
