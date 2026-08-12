"""实体定向召回与通用 Hybrid 兜底的回归测试。"""

from fastapi.testclient import TestClient


def _create_knowledge_base(client: TestClient) -> dict[str, object]:
    response = client.post("/api/v1/knowledge-bases", json={"name": "实体召回测试库"})
    assert response.status_code == 201
    return response.json()


def _create_document(client: TestClient, knowledge_base_id: str) -> None:
    response = client.post(
        "/api/v1/documents",
        json={
            "knowledgeBaseId": knowledge_base_id,
            "title": "service-topology.md",
            "sourceType": "markdown",
            "parser": "markdown",
            "content": "# 服务拓扑\n\nAPI depends on Redis. Redis supports cache stability.",
        },
    )
    assert response.status_code == 202


def test_entity_route_merges_with_hybrid_retrieval_and_reports_diagnostics(
    client: TestClient,
) -> None:
    knowledge_base = _create_knowledge_base(client)
    _create_document(client, str(knowledge_base["id"]))

    response = client.post(
        "/api/v1/retrieval/search",
        json={
            "knowledgeBaseId": knowledge_base["id"],
            "query": "What does API depend on?",
            "limit": 4,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    diagnostics = payload["diagnostics"]
    assert diagnostics["entityRetrievalEnabled"] is True
    assert diagnostics["entityMatchedEntities"] >= 1
    assert diagnostics["entityCandidates"] >= 1
    assert diagnostics["entityCoveredDocuments"] == 1
    assert diagnostics["dualRouteFusedCandidates"] >= diagnostics["entityCandidates"]
    assert payload["evidences"]
    assert all(item["locator"].startswith("document:") for item in payload["evidences"])


def test_empty_entity_route_keeps_general_hybrid_candidates(client: TestClient) -> None:
    knowledge_base = _create_knowledge_base(client)
    _create_document(client, str(knowledge_base["id"]))

    response = client.post(
        "/api/v1/retrieval/search",
        json={
            "knowledgeBaseId": knowledge_base["id"],
            "query": "supports",
            "limit": 4,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    diagnostics = payload["diagnostics"]
    assert diagnostics["entityMatchedEntities"] == 0
    assert diagnostics["entityCandidates"] == 0
    assert diagnostics["dualRouteFusedCandidates"] == diagnostics["fusedCandidates"]
    assert payload["evidences"]
