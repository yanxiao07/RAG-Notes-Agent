"""受控业务标签的词表、自动提议、审批和隔离回归测试。"""

from fastapi.testclient import TestClient

from app.core.config import get_settings


def _knowledge_base(client: TestClient, name: str) -> dict[str, object]:
    response = client.post("/api/v1/knowledge-bases", json={"name": name})
    assert response.status_code == 201
    return response.json()


def test_document_auto_tag_proposal_requires_review_before_activation(client: TestClient) -> None:
    knowledge_base = _knowledge_base(client, "标签治理测试库")
    tag_response = client.post(
        f"/api/v1/knowledge-bases/{knowledge_base['id']}/tags",
        json={"name": "Redis", "description": "缓存组件"},
    )
    assert tag_response.status_code == 201
    tag = tag_response.json()

    document_response = client.post(
        "/api/v1/documents",
        json={
            "knowledgeBaseId": knowledge_base["id"],
            "title": "cache.md",
            "sourceType": "markdown",
            "parser": "markdown",
            "content": "# Cache\n\nRedis is used for query cache.",
        },
    )
    assert document_response.status_code == 202
    document_id = document_response.json()["document"]["id"]

    assignments_response = client.get(
        f"/api/v1/knowledge-bases/{knowledge_base['id']}/tag-assignments?state=pending"
    )
    assert assignments_response.status_code == 200
    assignments = assignments_response.json()["items"]
    assert len(assignments) == 1
    assignment = assignments[0]
    assert assignment["tagId"] == tag["id"]
    assert assignment["assetType"] == "document"
    assert assignment["assetId"] == document_id
    assert assignment["source"] == "rule_match"
    assert assignment["state"] == "pending"

    reviewed = client.post(
        f"/api/v1/tag-assignments/{assignment['id']}/review",
        json={"decision": "approved"},
    )
    assert reviewed.status_code == 200
    assert reviewed.json()["state"] == "approved"
    assert reviewed.json()["reviewedAt"] is not None


def test_note_auto_proposal_and_duplicate_manual_proposal_are_governed(client: TestClient) -> None:
    knowledge_base = _knowledge_base(client, "笔记标签测试库")
    tag = client.post(
        f"/api/v1/knowledge-bases/{knowledge_base['id']}/tags",
        json={"name": "RAG"},
    ).json()
    note = client.post(
        f"/api/v1/knowledge-bases/{knowledge_base['id']}/notes",
        json={"title": "RAG 结论", "content": "RAG 需要可追溯引用。"},
    ).json()

    pending = client.get(
        f"/api/v1/knowledge-bases/{knowledge_base['id']}/tag-assignments?state=pending"
    ).json()["items"]
    assert len(pending) == 1
    assert pending[0]["assetType"] == "note"
    assert pending[0]["assetId"] == note["id"]

    duplicate = client.post(
        f"/api/v1/knowledge-bases/{knowledge_base['id']}/tag-assignments",
        json={"tagId": tag["id"], "assetType": "note", "assetId": note["id"]},
    )
    assert duplicate.status_code == 409
    assert duplicate.json()["error"]["code"] == "DUPLICATE_RESOURCE"


def test_tag_cannot_be_applied_across_knowledge_bases(client: TestClient) -> None:
    first = _knowledge_base(client, "标签来源库")
    second = _knowledge_base(client, "标签目标库")
    tag = client.post(f"/api/v1/knowledge-bases/{first['id']}/tags", json={"name": "安全"}).json()
    note = client.post(
        f"/api/v1/knowledge-bases/{second['id']}/notes",
        json={"title": "隔离笔记", "content": "安全边界。"},
    ).json()

    response = client.post(
        f"/api/v1/knowledge-bases/{second['id']}/tag-assignments",
        json={"tagId": tag["id"], "assetType": "note", "assetId": note["id"]},
    )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "RESOURCE_NOT_FOUND"


def test_archive_tag_uses_optimistic_version(client: TestClient) -> None:
    knowledge_base = _knowledge_base(client, "标签版本库")
    tag = client.post(
        f"/api/v1/knowledge-bases/{knowledge_base['id']}/tags", json={"name": "文档"}
    ).json()

    archived = client.request(
        "DELETE",
        f"/api/v1/knowledge-tags/{tag['id']}",
        json={"version": tag["version"]},
    )
    assert archived.status_code == 200
    assert archived.json()["state"] == "archived"
    assert archived.json()["version"] == tag["version"] + 1

    stale = client.request(
        "DELETE",
        f"/api/v1/knowledge-tags/{tag['id']}",
        json={"version": tag["version"]},
    )
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "VERSION_CONFLICT"


def test_approved_tag_can_be_fused_as_a_controlled_retrieval_route(
    client: TestClient, monkeypatch
) -> None:
    """待审核标签不进入检索，批准后才作为 Hybrid 的补充候选。"""

    monkeypatch.setenv("APP_TAG_RETRIEVAL_ENABLED", "true")
    get_settings.cache_clear()
    try:
        knowledge_base = _knowledge_base(client, "标签定向召回库")
        tag_response = client.post(
            f"/api/v1/knowledge-bases/{knowledge_base['id']}/tags", json={"name": "Redis"}
        )
        assert tag_response.status_code == 201
        document = client.post(
            "/api/v1/documents",
            json={
                "knowledgeBaseId": knowledge_base["id"],
                "title": "cache.md",
                "sourceType": "markdown",
                "parser": "markdown",
                "content": "# Cache\n\nRedis is used as a query cache.",
            },
        ).json()["document"]
        pending = client.get(
            f"/api/v1/knowledge-bases/{knowledge_base['id']}/tag-assignments?state=pending"
        ).json()["items"]
        assert len(pending) == 1

        before_review = client.post(
            "/api/v1/retrieval/search",
            json={"knowledgeBaseId": knowledge_base["id"], "query": "Redis", "limit": 4},
        )
        assert before_review.status_code == 200
        assert before_review.json()["diagnostics"]["tagMatchedTags"] == 1
        assert before_review.json()["diagnostics"]["tagCandidates"] == 0

        reviewed = client.post(
            f"/api/v1/tag-assignments/{pending[0]['id']}/review",
            json={"decision": "approved"},
        )
        assert reviewed.status_code == 200
        response = client.post(
            "/api/v1/retrieval/search",
            json={"knowledgeBaseId": knowledge_base["id"], "query": "Redis", "limit": 4},
        )
        assert response.status_code == 200
        diagnostics = response.json()["diagnostics"]
        assert diagnostics["tagRetrievalEnabled"] is True
        assert diagnostics["tagMatchedTags"] == 1
        assert diagnostics["tagCandidates"] >= 1
        assert diagnostics["tagCoveredAssets"] == 1
        assert diagnostics["tagRouteFusedCandidates"] >= diagnostics["dualRouteFusedCandidates"]
        assert any(
            item["locator"].startswith(f"document:{document['id']}:chunk:")
            for item in response.json()["evidences"]
        )
    finally:
        get_settings.cache_clear()
