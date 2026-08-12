"""Bounded Agentic RAG 的预算边界与运行时回归测试。"""

from fastapi.testclient import TestClient

from app.agent.bounded_research import (
    BoundedResearchPlan,
    EvidenceSufficiencyPolicy,
)


def create_knowledge_base(client: TestClient) -> dict[str, object]:
    response = client.post("/api/v1/knowledge-bases", json={"name": "有界 Agent 测试库"})
    assert response.status_code == 201
    return response.json()


def test_sufficiency_policy_stops_at_max_steps() -> None:
    plan = BoundedResearchPlan(
        enabled=True,
        mode="force",
        profile="multi_hop",
        planner="test",
        max_steps=2,
        min_evidence=10,
        token_budget=6_000,
        latency_budget_ms=12_000,
    )
    decision = EvidenceSufficiencyPolicy.decide(
        plan=plan,
        step=2,
        evidence_count=0,
        source_coverage=0,
        estimated_tokens=0,
        elapsed_ms=0,
        added_locators=0,
    )

    assert decision.continue_retrieval is False
    assert decision.reason == "max_steps_reached"


def test_sufficiency_policy_stops_when_budget_is_exhausted() -> None:
    plan = BoundedResearchPlan(
        enabled=True,
        mode="force",
        profile="global",
        planner="test",
        max_steps=3,
        min_evidence=10,
        token_budget=100,
        latency_budget_ms=12_000,
    )
    decision = EvidenceSufficiencyPolicy.decide(
        plan=plan,
        step=1,
        evidence_count=1,
        source_coverage=1,
        estimated_tokens=100,
        elapsed_ms=0,
        added_locators=1,
    )

    assert decision.continue_retrieval is False
    assert decision.reason == "token_budget_reached"


def test_forced_agentic_research_records_bounded_steps_and_public_decision(
    client: TestClient,
) -> None:
    knowledge_base = create_knowledge_base(client)
    response = client.post(
        "/api/v1/agent/runs/research",
        json={
            "knowledgeBaseId": knowledge_base["id"],
            "query": "请梳理实体之间的关系",
            "agenticMode": "force",
        },
    )

    assert response.status_code == 201
    payload = response.json()
    assert payload["state"] == "completed"
    assert payload["outputJson"]["agentic"]["steps"] <= 3
    assert payload["outputJson"]["agentic"]["decision"]["reason"] in {
        "evidence_sufficient",
        "max_steps_reached",
        "latency_budget_reached",
        "token_budget_reached",
        "no_new_evidence",
    }
    assert len(payload["toolCalls"]) == payload["outputJson"]["agentic"]["steps"]
    assert all(call["inputJson"]["retrievalStep"] >= 1 for call in payload["toolCalls"])

    checkpoints = client.get(f"/api/v1/agent/runs/{payload['id']}/checkpoints")
    assert checkpoints.status_code == 200
    checkpoint_nodes = [item["node"] for item in checkpoints.json()["items"]]
    assert checkpoint_nodes[0] == "route"
    assert checkpoints.json()["items"][0]["stateJson"]["agentic_plan"]["enabled"] is True
    assert "assess" in checkpoint_nodes


def test_auto_mode_keeps_local_question_on_single_retrieval_step(client: TestClient) -> None:
    knowledge_base = create_knowledge_base(client)
    response = client.post(
        "/api/v1/agent/runs/research",
        json={
            "knowledgeBaseId": knowledge_base["id"],
            "query": "当前知识库中有哪些缓存策略？",
        },
    )

    assert response.status_code == 201
    payload = response.json()
    assert "agentic" not in payload["outputJson"]
    assert len(payload["toolCalls"]) == 1
