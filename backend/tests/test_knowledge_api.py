"""知识库 API 集成测试，覆盖正常链路和关键契约失败场景。"""

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from app.domain.knowledge.models import NoteEmbedding


def create_knowledge_base(client: TestClient) -> dict[str, object]:
    response = client.post(
        "/api/v1/knowledge-bases",
        json={"name": "研究资料", "description": "机器学习论文与实验记录"},
    )
    assert response.status_code == 201
    return response.json()


def test_create_and_list_knowledge_bases(client: TestClient) -> None:
    created = create_knowledge_base(client)
    assert created["name"] == "研究资料"
    assert "X-Request-ID" in client.get("/health").headers

    response = client.get("/api/v1/knowledge-bases")
    assert response.status_code == 200
    payload = response.json()
    assert payload["meta"] == {"offset": 0, "limit": 20, "total": 1}
    assert payload["items"][0]["id"] == created["id"]


def test_rename_and_archive_knowledge_base(client: TestClient) -> None:
    created = create_knowledge_base(client)
    knowledge_base_id = created["id"]

    renamed = client.patch(
        f"/api/v1/knowledge-bases/{knowledge_base_id}",
        json={"name": "已整理资料", "description": "已更新说明"},
    )
    assert renamed.status_code == 200
    assert renamed.json()["name"] == "已整理资料"
    assert renamed.json()["description"] == "已更新说明"

    archived = client.delete(f"/api/v1/knowledge-bases/{knowledge_base_id}")
    assert archived.status_code == 200
    assert archived.json()["status"] == "archived"

    listed = client.get("/api/v1/knowledge-bases")
    assert listed.status_code == 200
    assert listed.json()["items"] == []

    hidden = client.get(f"/api/v1/knowledge-bases/{knowledge_base_id}")
    assert hidden.status_code == 404


def test_note_update_requires_matching_version(client: TestClient) -> None:
    knowledge_base = create_knowledge_base(client)
    note_response = client.post(
        f"/api/v1/knowledge-bases/{knowledge_base['id']}/notes",
        json={"title": "注意力机制", "content": "初始记录"},
    )
    assert note_response.status_code == 201
    note = note_response.json()

    updated = client.patch(
        f"/api/v1/notes/{note['id']}",
        json={"content": "补充了实验观察", "version": 1},
    )
    assert updated.status_code == 200
    assert updated.json()["version"] == 2

    conflict = client.patch(
        f"/api/v1/notes/{note['id']}",
        json={"content": "过期写入", "version": 1},
    )
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "VERSION_CONFLICT"
    assert conflict.json()["error"]["details"]["currentVersion"] == 2
    assert conflict.json()["error"]["requestId"]


def test_missing_knowledge_base_uses_standard_error(client: TestClient) -> None:
    response = client.get("/api/v1/knowledge-bases/not-found")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "RESOURCE_NOT_FOUND"


def test_invalid_payload_uses_standard_error(client: TestClient) -> None:
    response = client.post("/api/v1/knowledge-bases", json={"name": ""})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_create_document_returns_queued_ingestion_job(client: TestClient) -> None:
    knowledge_base = create_knowledge_base(client)
    response = client.post(
        "/api/v1/documents",
        json={
            "knowledgeBaseId": knowledge_base["id"],
            "title": "实验报告",
            "content": "结论和实验过程。",
        },
    )
    assert response.status_code == 202
    payload = response.json()
    assert payload["document"]["status"] == "queued"
    assert payload["ingestionJob"]["state"] == "queued"
    assert payload["ingestionJob"]["configSnapshot"]["chunker"] == "structured"


def test_retrieval_returns_traceable_note_evidence(client: TestClient) -> None:
    knowledge_base = create_knowledge_base(client)
    client.post(
        f"/api/v1/knowledge-bases/{knowledge_base['id']}/notes",
        json={"title": "实验结论", "content": "注意力机制在长上下文任务中表现稳定。"},
    )

    response = client.post(
        "/api/v1/retrieval/search",
        json={"knowledgeBaseId": knowledge_base["id"], "query": "注意力机制"},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["retriever"] == "local_hybrid_rrf"
    assert payload["evidences"][0]["sourceType"] == "note"
    assert payload["evidences"][0]["locator"].startswith("note:")
    assert payload["diagnostics"]["finalCandidates"] == len(payload["evidences"])
    assert payload["diagnostics"]["dynamicTopKEnabled"] is True
    assert payload["diagnostics"]["dynamicTopKSelected"] == len(payload["evidences"])
    assert payload["diagnostics"]["dynamicTopKStopReason"] == "candidates_exhausted"
    assert payload["diagnostics"]["contextExpanded"] == 0


def test_note_create_and_update_replace_semantic_embedding(
    client: TestClient, session_factory: sessionmaker
) -> None:
    knowledge_base = create_knowledge_base(client)
    created = client.post(
        f"/api/v1/knowledge-bases/{knowledge_base['id']}/notes",
        json={"title": "缓存策略", "content": "Redis 缓存用于降低检索延迟。"},
    )
    assert created.status_code == 201
    note = created.json()

    with session_factory() as session:
        initial = session.scalar(select(NoteEmbedding).where(NoteEmbedding.note_id == note["id"]))
        assert initial is not None
        initial_vector = initial.embedding

    updated = client.patch(
        f"/api/v1/notes/{note['id']}",
        json={"content": "Redis 缓存和 TTL 用于降低检索延迟。", "version": 1},
    )
    assert updated.status_code == 200
    with session_factory() as session:
        replacement = session.scalar(
            select(NoteEmbedding).where(NoteEmbedding.note_id == note["id"])
        )
        assert replacement is not None
        assert replacement.embedding != initial_vector
