"""GraphRAG-lite 的实体抽取、入库和关系召回测试。"""

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.domain.knowledge.models import ChunkEntityMention, KnowledgeEntity, KnowledgeRelation
from app.rag.graph import GraphRetriever, RuleGraphExtractor, classify_graph_mode


def create_knowledge_base(client: TestClient) -> dict[str, object]:
    response = client.post("/api/v1/knowledge-bases", json={"name": "图谱检索测试库"})
    assert response.status_code == 201
    return response.json()


def test_rule_graph_extractor_keeps_explicit_relation_and_heading() -> None:
    extraction = RuleGraphExtractor().extract(
        "# 服务拓扑\n\nAPI \u4f9d\u8d56 Redis，Redis \u5f71\u54cd缓存稳定性。"
    )

    names = {item.normalized_name for item in extraction.entities}
    relations = {(item.source, item.target, item.relation_type) for item in extraction.relations}
    assert "服务拓扑" in names
    assert ("api", "redis", "依赖") in relations
    assert ("redis", "缓存稳定性", "影响") in relations


def test_relation_query_uses_graph_candidates_and_reports_diagnostics(
    client: TestClient,
) -> None:
    knowledge_base = create_knowledge_base(client)
    created = client.post(
        "/api/v1/documents",
        json={
            "knowledgeBaseId": knowledge_base["id"],
            "title": "topology.md",
            "sourceType": "markdown",
            "parser": "markdown",
            "content": ("# 服务拓扑\n\nAPI \u4f9d\u8d56 Redis。\n\nRedis \u5f71\u54cd缓存稳定性。"),
        },
    )
    assert created.status_code == 202

    response = client.post(
        "/api/v1/retrieval/search",
        json={
            "knowledgeBaseId": knowledge_base["id"],
            "query": "API 和 Redis 之间的关系是什么？",
            "limit": 4,
        },
    )
    assert response.status_code == 200
    payload = response.json()
    diagnostics = payload["diagnostics"]
    assert diagnostics["graphMode"] == "multi_hop"
    assert diagnostics["graphMatchedEntities"] >= 2
    assert diagnostics["graphCandidates"] >= 1
    assert payload["evidences"]
    assert all(item["locator"].startswith("document:") for item in payload["evidences"])


def test_archiving_document_removes_graph_edges(
    client: TestClient, session_factory: sessionmaker[Session]
) -> None:
    knowledge_base = create_knowledge_base(client)
    created = client.post(
        "/api/v1/documents",
        json={
            "knowledgeBaseId": knowledge_base["id"],
            "title": "archive-graph.md",
            "sourceType": "markdown",
            "parser": "markdown",
            "content": "API \u4f9d\u8d56 Redis。",
        },
    )
    assert created.status_code == 202
    document_id = created.json()["document"]["id"]

    with session_factory() as session:
        assert session.scalar(select(KnowledgeEntity.id)) is not None
        assert session.scalar(select(KnowledgeRelation.id)) is not None
        assert session.scalar(select(ChunkEntityMention.id)) is not None

    archived = client.delete(f"/api/v1/documents/{document_id}")
    assert archived.status_code == 200
    with session_factory() as session:
        assert session.scalar(select(KnowledgeRelation.id)) is None
        assert session.scalar(select(ChunkEntityMention.id)) is None
        assert session.scalar(select(KnowledgeEntity.id)) is None


def test_graph_mode_preserves_local_path_for_simple_facts() -> None:
    assert classify_graph_mode("Redis 的端口是多少") == "local"
    assert classify_graph_mode("API 和 Redis 之间有什么关系") == "multi_hop"
    assert classify_graph_mode("总结整个知识库的主题") == "global"
    assert GraphRetriever().last_stats.mode == "local"
