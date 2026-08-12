"""文档时效、来源可信度与替代关系的治理测试。"""

from datetime import UTC, datetime, timedelta
from typing import cast

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from app.domain.knowledge.models import Document
from app.rag.document_governance import apply_document_governance
from app.rag.retrieval import Evidence


def create_knowledge_base(client: TestClient, name: str = "资料治理测试库") -> dict[str, object]:
    response = client.post("/api/v1/knowledge-bases", json={"name": name})
    assert response.status_code == 201
    return response.json()


def create_document(client: TestClient, knowledge_base_id: str, title: str) -> dict[str, object]:
    response = client.post(
        "/api/v1/documents",
        json={
            "knowledgeBaseId": knowledge_base_id,
            "title": title,
            "sourceType": "markdown",
            "content": f"# {title}\n\n用于验证资料治理的可检索内容。",
            "parser": "markdown",
        },
    )
    assert response.status_code == 202
    return response.json()["document"]


def test_document_governance_requires_current_version_and_same_knowledge_base(
    client: TestClient,
) -> None:
    first_base = create_knowledge_base(client, "资料治理 A")
    second_base = create_knowledge_base(client, "资料治理 B")
    predecessor = create_document(client, str(first_base["id"]), "旧版制度")
    successor = create_document(client, str(first_base["id"]), "新版制度")
    foreign = create_document(client, str(second_base["id"]), "其他资料")

    initial_version = cast(int, successor["governanceVersion"])
    updated = client.patch(
        f"/api/v1/documents/{successor['id']}/governance",
        json={
            "sourceTrustLevel": "verified",
            "effectiveAt": "2026-01-01T00:00:00Z",
            "expiresAt": None,
            "conflictState": "none",
            "supersedesDocumentId": predecessor["id"],
            "governanceVersion": initial_version,
        },
    )

    assert updated.status_code == 200
    assert updated.json()["sourceTrustLevel"] == "verified"
    assert updated.json()["supersedesDocumentId"] == predecessor["id"]
    assert updated.json()["governanceVersion"] == initial_version + 1

    stale = client.patch(
        f"/api/v1/documents/{successor['id']}/governance",
        json={
            "sourceTrustLevel": "standard",
            "conflictState": "none",
            "governanceVersion": initial_version,
        },
    )
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "VERSION_CONFLICT"

    cross_base = client.patch(
        f"/api/v1/documents/{successor['id']}/governance",
        json={
            "sourceTrustLevel": "standard",
            "conflictState": "none",
            "supersedesDocumentId": foreign["id"],
            "governanceVersion": updated.json()["governanceVersion"],
        },
    )
    assert cross_base.status_code == 422
    assert cross_base.json()["error"]["code"] == "PROCESSING_ERROR"


def test_governance_filter_excludes_superseded_and_future_documents(
    session_factory: sessionmaker[Session],
) -> None:
    workspace_id = "00000000-0000-0000-0000-000000000001"
    knowledge_base_id = "knowledge-base"
    now = datetime.now(UTC)
    with session_factory() as session:
        old = _document("old", workspace_id, knowledge_base_id)
        current = _document(
            "current",
            workspace_id,
            knowledge_base_id,
            supersedes_document_id="old",
        )
        future = _document(
            "future",
            workspace_id,
            knowledge_base_id,
            effective_at=now + timedelta(days=1),
        )
        expired = _document(
            "expired",
            workspace_id,
            knowledge_base_id,
            expires_at=now - timedelta(days=1),
        )
        conflicted = _document(
            "conflicted",
            workspace_id,
            knowledge_base_id,
            conflict_state="conflicted",
        )
        session.add_all([old, current, future, expired, conflicted])
        session.commit()

        evidences, stats = apply_document_governance(
            session,
            evidences=[_evidence(item.id) for item in [old, current, future, expired, conflicted]],
            workspace_id=workspace_id,
        )

    assert [item.locator.split(":")[1] for item in evidences] == [
        "current",
        "conflicted",
        "expired",
    ]
    assert stats.excluded_superseded == 1
    assert stats.excluded_future_effective == 1
    assert stats.expired_candidates == 1
    assert stats.conflicted_candidates == 1
    assert evidences[-1].governance_availability == "expired"
    assert evidences[1].conflict_state == "conflicted"


def _document(
    document_id: str,
    workspace_id: str,
    knowledge_base_id: str,
    **kwargs: object,
) -> Document:
    return Document(
        id=document_id,
        workspace_id=workspace_id,
        knowledge_base_id=knowledge_base_id,
        title=document_id,
        raw_content=document_id,
        status="indexed",
        **kwargs,
    )


def _evidence(document_id: str) -> Evidence:
    return Evidence(
        source_type="document_chunk",
        source_id=f"{document_id}-chunk",
        title=document_id,
        content=document_id,
        score=0.75,
        locator=f"document:{document_id}:chunk:1",
    )
