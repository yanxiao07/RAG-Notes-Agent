"""GraphRAG 社区摘要与多层全局召回测试。"""

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.domain.knowledge.models import KnowledgeBase, KnowledgeCommunitySummary
from app.rag.communities import CommunitySummaryGenerator, CommunitySummaryService


def create_knowledge_base(client: TestClient) -> dict[str, object]:
    response = client.post("/api/v1/knowledge-bases", json={"name": "社区测试库"})
    assert response.status_code == 201
    return response.json()


def create_document(client: TestClient, knowledge_base_id: str, title: str, content: str) -> str:
    response = client.post(
        "/api/v1/documents",
        json={
            "knowledgeBaseId": knowledge_base_id,
            "title": title,
            "sourceType": "markdown",
            "parser": "markdown",
            "content": content,
        },
    )
    assert response.status_code == 202
    return str(response.json()["document"]["id"])


def test_ingestion_builds_multi_level_communities_and_global_retrieval(
    client: TestClient, session_factory: sessionmaker[Session]
) -> None:
    knowledge_base = create_knowledge_base(client)
    knowledge_base_id = str(knowledge_base["id"])
    create_document(client, knowledge_base_id, "服务拓扑", "API 依赖 Redis，Redis 影响缓存稳定性。")
    create_document(client, knowledge_base_id, "数据管道", "Kafka 连接 Flink，Flink 支持实时分析。")

    with session_factory() as session:
        summaries = list(
            session.scalars(
                select(KnowledgeCommunitySummary).where(
                    KnowledgeCommunitySummary.knowledge_base_id == knowledge_base["id"]
                )
            )
        )
    assert summaries
    assert {summary.level for summary in summaries} == {0, 1}
    assert all(summary.source_chunk_ids for summary in summaries)
    assert all(summary.graph_revision >= 1 for summary in summaries)

    response = client.post(
        "/api/v1/retrieval/search",
        json={
            "knowledgeBaseId": knowledge_base["id"],
            "query": "总结整个知识库的主题",
            "limit": 6,
        },
    )
    assert response.status_code == 200
    payload = response.json()
    diagnostics = payload["diagnostics"]
    assert diagnostics["matchedCommunities"] >= 1
    assert diagnostics["communityExpandedChunks"] >= 2
    assert diagnostics["communityCoveredDocuments"] == 2
    assert payload["evidences"]
    assert all(item["sourceType"] == "document_chunk" for item in payload["evidences"])
    assert all(item["locator"].startswith("document:") for item in payload["evidences"])


def test_archiving_document_invalidates_community_summaries(
    client: TestClient, session_factory: sessionmaker[Session]
) -> None:
    knowledge_base = create_knowledge_base(client)
    document_id = create_document(client, str(knowledge_base["id"]), "待归档", "API 依赖 Redis。")
    with session_factory() as session:
        assert session.scalar(select(KnowledgeCommunitySummary.id)) is not None

    archived = client.delete(f"/api/v1/documents/{document_id}")
    assert archived.status_code == 200
    with session_factory() as session:
        assert session.scalar(select(KnowledgeCommunitySummary.id)) is None


def test_graph_rebuild_endpoint_returns_building_state(client: TestClient) -> None:
    knowledge_base = create_knowledge_base(client)
    knowledge_base_id = str(knowledge_base["id"])
    create_document(client, knowledge_base_id, "重建测试", "API 依赖 Redis。")
    response = client.post(f"/api/v1/knowledge-bases/{knowledge_base['id']}/graph/rebuild")
    assert response.status_code == 202
    assert response.json()["state"] == "building"

    status = client.get(f"/api/v1/knowledge-bases/{knowledge_base['id']}/graph/status")
    assert status.status_code == 200
    assert status.json()["state"] == "ready"
    assert status.json()["communityCount"] >= 1
    assert status.json()["communityAlgorithm"] == "connected_components"
    assert status.json()["communityAlgorithmFallback"] == 0


def test_llm_summary_failure_falls_back_to_deterministic_summary(
    client: TestClient, session_factory: sessionmaker[Session]
) -> None:
    knowledge_base = create_knowledge_base(client)
    create_document(client, str(knowledge_base["id"]), "回退测试", "API 依赖 Redis。")

    class BrokenGenerator(CommunitySummaryGenerator):
        name = "broken-llm"

        def generate(self, *, title: str, chunks: list[str]) -> str:
            del title, chunks
            raise RuntimeError("模拟模型超时")

    with session_factory() as session:
        workspace_id = session.scalar(
            select(KnowledgeBase.workspace_id).where(KnowledgeBase.id == knowledge_base["id"])
        )
        assert workspace_id
        stats = CommunitySummaryService().rebuild(
            session,
            knowledge_base_id=str(knowledge_base["id"]),
            workspace_id=workspace_id,
            summary_generator=BrokenGenerator(),
        )
        session.commit()
        summaries = list(session.scalars(select(KnowledgeCommunitySummary)))
    assert stats.summary_fallback >= 1
    assert summaries
    assert all(item.summary_provider == "deterministic-community-summary" for item in summaries)
