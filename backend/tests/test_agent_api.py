"""审批链路测试：未批准的 Agent 提议不得写入知识库。"""

from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from app.domain.workspace import Workspace


def create_knowledge_base(client: TestClient) -> dict[str, object]:
    response = client.post("/api/v1/knowledge-bases", json={"name": "Agent 测试库"})
    assert response.status_code == 201
    return response.json()


def test_approval_is_required_before_agent_note_write(client: TestClient) -> None:
    knowledge_base = create_knowledge_base(client)
    proposal_response = client.post(
        "/api/v1/agent/note-proposals",
        json={
            "knowledgeBaseId": knowledge_base["id"],
            "title": "Agent 整理结果",
            "content": "基于研究笔记提取的结论。",
            "rationale": "用户要求将可复用结论沉淀为笔记。",
        },
    )
    assert proposal_response.status_code == 201
    proposal = proposal_response.json()["proposal"]
    assert proposal["state"] == "pending"
    pending_run = client.get(f"/api/v1/agent/runs/{proposal['agentRunId']}")
    assert pending_run.status_code == 200
    assert pending_run.json()["state"] == "awaiting_approval"
    assert pending_run.json()["currentNode"] == "approval"
    assert pending_run.json()["toolCalls"][0]["toolName"] == "create_note_proposal"
    assert pending_run.json()["toolCalls"][0]["state"] == "awaiting_approval"

    before_approval = client.get(f"/api/v1/knowledge-bases/{knowledge_base['id']}/notes")
    assert before_approval.json()["meta"]["total"] == 0

    approval = client.post(
        f"/api/v1/change-proposals/{proposal['id']}/approve",
        headers={"X-Actor-ID": "test-user"},
    )
    assert approval.status_code == 200
    assert approval.json()["state"] == "approved"

    after_approval = client.get(f"/api/v1/knowledge-bases/{knowledge_base['id']}/notes")
    assert after_approval.json()["meta"]["total"] == 1
    assert after_approval.json()["items"][0]["title"] == "Agent 整理结果"
    approved_run = client.get(f"/api/v1/agent/runs/{proposal['agentRunId']}")
    assert approved_run.json()["state"] == "completed"
    assert approved_run.json()["currentNode"] == "finish"
    assert approved_run.json()["toolCalls"][0]["state"] == "completed"

    repeat = client.post(f"/api/v1/change-proposals/{proposal['id']}/approve")
    assert repeat.status_code == 422
    assert repeat.json()["error"]["code"] == "PROCESSING_ERROR"


def test_research_runtime_persists_nodes_and_tool_calls(client: TestClient) -> None:
    knowledge_base = create_knowledge_base(client)
    response = client.post(
        "/api/v1/agent/runs/research",
        json={
            "knowledgeBaseId": knowledge_base["id"],
            "query": "检索当前知识库中的缓存策略",
        },
    )

    assert response.status_code == 201
    payload = response.json()
    assert payload["state"] == "completed"
    assert payload["currentNode"] == "finish"
    assert payload["policyVersion"] == "agent-runtime-v1"
    assert len(payload["toolCalls"]) == 1
    assert payload["toolCalls"][0]["toolName"] == "knowledge_search"
    assert payload["toolCalls"][0]["state"] == "completed"
    assert payload["outputJson"]["data"]["evidenceCount"] == 0

    restored = client.get(f"/api/v1/agent/runs/{payload['id']}")
    assert restored.status_code == 200
    assert restored.json()["threadId"] == payload["threadId"]

    checkpoints = client.get(f"/api/v1/agent/runs/{payload['id']}/checkpoints")
    assert checkpoints.status_code == 200
    checkpoint_items = checkpoints.json()["items"]
    assert [item["node"] for item in checkpoint_items] == ["route", "retrieve", "finish"]
    assert all(len(item["stateChecksum"]) == 64 for item in checkpoint_items)

    resumed = client.post(f"/api/v1/agent/runs/{payload['id']}/resume")
    assert resumed.status_code == 200
    assert resumed.json()["id"] == payload["id"]
    assert resumed.json()["state"] == "completed"


def test_research_runtime_stream_exposes_only_public_trace_events(client: TestClient) -> None:
    knowledge_base = create_knowledge_base(client)
    response = client.post(
        "/api/v1/agent/runs/research/stream",
        json={
            "knowledgeBaseId": knowledge_base["id"],
            "query": "查询 RRF 融合策略",
        },
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    body = response.text
    assert "event: started" in body
    assert "event: checkpoint" in body
    assert "event: tool_completed" in body
    assert "event: completed" in body
    assert "查询 RRF" not in body


def test_runtime_registry_supports_catalog_tool_without_returning_document_body(
    client: TestClient,
) -> None:
    knowledge_base = create_knowledge_base(client)
    response = client.post(
        "/api/v1/agent/runs/research",
        json={
            "knowledgeBaseId": knowledge_base["id"],
            "query": "当前知识库有哪些资料",
            "toolName": "knowledge_catalog",
        },
    )

    assert response.status_code == 201
    payload = response.json()
    assert payload["toolCalls"][0]["toolName"] == "knowledge_catalog"
    assert payload["outputJson"]["data"]["documentCount"] == 0
    assert payload["outputJson"]["data"]["documents"] == []


def test_runtime_rejects_unregistered_tool_name(client: TestClient) -> None:
    knowledge_base = create_knowledge_base(client)
    response = client.post(
        "/api/v1/agent/runs/research",
        json={
            "knowledgeBaseId": knowledge_base["id"],
            "query": "test",
            "toolName": "unknown_tool",
        },
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_runtime_write_tool_pauses_until_approval(client: TestClient) -> None:
    knowledge_base = create_knowledge_base(client)
    response = client.post(
        "/api/v1/agent/runs/research",
        json={
            "knowledgeBaseId": knowledge_base["id"],
            "query": "将检索结论沉淀为笔记",
            "toolName": "create_note_proposal",
            "toolArguments": {
                "title": "运行时提议",
                "content": "这是一条必须经过审批的笔记。",
                "rationale": "便于后续复用研究结论。",
            },
        },
    )

    assert response.status_code == 201
    payload = response.json()
    assert payload["state"] == "awaiting_approval"
    assert payload["currentNode"] == "approval"
    assert payload["toolCalls"][0]["toolName"] == "create_note_proposal"
    assert payload["toolCalls"][0]["state"] == "awaiting_approval"
    assert payload["toolCalls"][0]["requiresApproval"] is True
    assert payload["outputJson"]["awaitingApproval"] is True
    assert "proposalId" in payload["outputJson"]

    notes = client.get(f"/api/v1/knowledge-bases/{knowledge_base['id']}/notes")
    assert notes.json()["meta"]["total"] == 0

    resume = client.post(f"/api/v1/agent/runs/{payload['id']}/resume")
    assert resume.status_code == 422
    assert resume.json()["error"]["code"] == "PROCESSING_ERROR"

    checkpoints = client.get(f"/api/v1/agent/runs/{payload['id']}/checkpoints")
    assert [item["node"] for item in checkpoints.json()["items"]] == [
        "route",
        "retrieve",
        "approval",
    ]

    approved = client.post(
        f"/api/v1/change-proposals/{payload['outputJson']['proposalId']}/approve",
        headers={"X-Actor-ID": "runtime-reviewer"},
    )
    assert approved.status_code == 200
    assert approved.json()["state"] == "approved"

    notes = client.get(f"/api/v1/knowledge-bases/{knowledge_base['id']}/notes")
    assert notes.json()["meta"]["total"] == 1
    completed = client.get(f"/api/v1/agent/runs/{payload['id']}")
    assert completed.json()["state"] == "completed"
    assert completed.json()["toolCalls"][0]["state"] == "completed"

    checkpoints = client.get(f"/api/v1/agent/runs/{payload['id']}/checkpoints")
    assert [item["node"] for item in checkpoints.json()["items"]][-1] == "finish"

    repeated = client.post(
        f"/api/v1/change-proposals/{payload['outputJson']['proposalId']}/approve"
    )
    assert repeated.status_code == 422
    assert repeated.json()["error"]["code"] == "PROCESSING_ERROR"


def test_runtime_write_tool_rejection_does_not_write_note(client: TestClient) -> None:
    knowledge_base = create_knowledge_base(client)
    response = client.post(
        "/api/v1/agent/runs/research",
        json={
            "knowledgeBaseId": knowledge_base["id"],
            "query": "拒绝写入",
            "toolName": "create_note_proposal",
            "toolArguments": {
                "title": "不会落库",
                "content": "审批被拒绝时不应写入。",
                "rationale": "验证拒绝分支。",
            },
        },
    )
    assert response.status_code == 201
    payload = response.json()
    proposal_id = payload["outputJson"]["proposalId"]

    rejected = client.post(f"/api/v1/change-proposals/{proposal_id}/reject")
    assert rejected.status_code == 200
    assert rejected.json()["state"] == "rejected"

    notes = client.get(f"/api/v1/knowledge-bases/{knowledge_base['id']}/notes")
    assert notes.json()["meta"]["total"] == 0
    run = client.get(f"/api/v1/agent/runs/{payload['id']}")
    assert run.json()["state"] == "cancelled"
    assert run.json()["toolCalls"][0]["state"] == "rejected"

    repeated = client.post(f"/api/v1/change-proposals/{proposal_id}/reject")
    assert repeated.status_code == 422
    assert repeated.json()["error"]["code"] == "PROCESSING_ERROR"


def test_runtime_write_stream_exposes_approval_event_without_payload_body(
    client: TestClient,
) -> None:
    knowledge_base = create_knowledge_base(client)
    response = client.post(
        "/api/v1/agent/runs/research/stream",
        json={
            "knowledgeBaseId": knowledge_base["id"],
            "query": "流式写工具",
            "toolName": "create_note_proposal",
            "toolArguments": {
                "title": "流式提议标题",
                "content": "不应在公开轨迹中回传正文。",
                "rationale": "验证脱敏。",
            },
        },
    )

    assert response.status_code == 200
    assert "event: approval_required" in response.text
    assert "流式提议标题" not in response.text
    assert "不应在公开轨迹中回传正文" not in response.text


def test_runtime_update_note_proposal_uses_optimistic_version(client: TestClient) -> None:
    knowledge_base = create_knowledge_base(client)
    created = client.post(
        f"/api/v1/knowledge-bases/{knowledge_base['id']}/notes",
        json={"title": "原始笔记", "content": "旧内容"},
    )
    assert created.status_code == 201
    note = created.json()

    response = client.post(
        "/api/v1/agent/runs/research",
        json={
            "knowledgeBaseId": knowledge_base["id"],
            "query": "更新笔记",
            "toolName": "update_note_proposal",
            "toolArguments": {
                "noteId": note["id"],
                "expectedVersion": note["version"],
                "content": "审批后新内容",
                "rationale": "修正研究结论。",
            },
        },
    )
    assert response.status_code == 201
    payload = response.json()
    assert payload["state"] == "awaiting_approval"

    before = client.get(f"/api/v1/knowledge-bases/{knowledge_base['id']}/notes").json()
    assert before["items"][0]["content"] == "旧内容"
    proposal_id = payload["outputJson"]["proposalId"]
    approved = client.post(f"/api/v1/change-proposals/{proposal_id}/approve")
    assert approved.status_code == 200

    after = client.get(f"/api/v1/knowledge-bases/{knowledge_base['id']}/notes").json()
    assert after["items"][0]["content"] == "审批后新内容"
    assert after["items"][0]["version"] == note["version"] + 1


def test_runtime_archive_document_proposal_cleans_index_after_approval(
    client: TestClient,
) -> None:
    knowledge_base = create_knowledge_base(client)
    created = client.post(
        "/api/v1/documents",
        json={
            "knowledgeBaseId": knowledge_base["id"],
            "title": "待归档文档",
            "sourceType": "plain_text",
            "content": "文档内容",
        },
    )
    assert created.status_code == 202
    document_id = created.json()["document"]["id"]

    response = client.post(
        "/api/v1/agent/runs/research",
        json={
            "knowledgeBaseId": knowledge_base["id"],
            "query": "归档文档",
            "toolName": "archive_document_proposal",
            "toolArguments": {
                "documentId": document_id,
                "rationale": "该资料已过期。",
            },
        },
    )
    assert response.status_code == 201
    payload = response.json()
    documents_before = client.get(
        f"/api/v1/knowledge-bases/{knowledge_base['id']}/documents"
    ).json()
    assert documents_before["meta"]["total"] == 1

    approved = client.post(
        f"/api/v1/change-proposals/{payload['outputJson']['proposalId']}/approve"
    )
    assert approved.status_code == 200
    documents_after = client.get(f"/api/v1/knowledge-bases/{knowledge_base['id']}/documents").json()
    assert documents_after["meta"]["total"] == 0


def test_runtime_archive_proposal_rejects_cross_knowledge_base_target(
    client: TestClient,
) -> None:
    first = create_knowledge_base(client)
    second = create_knowledge_base(client)
    document = client.post(
        "/api/v1/documents",
        json={
            "knowledgeBaseId": second["id"],
            "title": "另一知识库文档",
            "sourceType": "plain_text",
            "content": "不能被第一知识库归档。",
        },
    )
    assert document.status_code == 202
    document_id = document.json()["document"]["id"]

    response = client.post(
        "/api/v1/agent/runs/research",
        json={
            "knowledgeBaseId": first["id"],
            "query": "越权归档",
            "toolName": "archive_document_proposal",
            "toolArguments": {
                "documentId": document_id,
                "rationale": "不应通过审批。",
            },
        },
    )
    assert response.status_code == 201
    proposal_id = response.json()["outputJson"]["proposalId"]
    approve = client.post(f"/api/v1/change-proposals/{proposal_id}/approve")
    assert approve.status_code == 404
    assert approve.json()["error"]["code"] == "RESOURCE_NOT_FOUND"

    documents = client.get(f"/api/v1/knowledge-bases/{second['id']}/documents").json()
    assert documents["meta"]["total"] == 1


def test_change_proposal_is_isolated_by_workspace(
    client: TestClient, session_factory: sessionmaker[Session]
) -> None:
    other_workspace_id = str(uuid4())
    with session_factory() as session:
        session.add(Workspace(id=other_workspace_id, name="审批隔离工作区"))
        session.commit()

    knowledge_base = create_knowledge_base(client)
    response = client.post(
        "/api/v1/agent/runs/research",
        json={
            "knowledgeBaseId": knowledge_base["id"],
            "query": "跨工作区审批测试",
            "toolName": "create_note_proposal",
            "toolArguments": {
                "title": "隔离提议",
                "content": "不能被其他工作区批准。",
                "rationale": "验证租户边界。",
            },
        },
    )
    assert response.status_code == 201
    payload = response.json()
    proposal_id = payload["outputJson"]["proposalId"]

    cross_workspace = client.post(
        f"/api/v1/change-proposals/{proposal_id}/approve",
        headers={"X-Workspace-ID": other_workspace_id},
    )
    assert cross_workspace.status_code == 404
    assert cross_workspace.json()["error"]["code"] == "RESOURCE_NOT_FOUND"

    still_pending = client.get(f"/api/v1/agent/runs/{payload['id']}")
    assert still_pending.json()["state"] == "awaiting_approval"
    approved = client.post(f"/api/v1/change-proposals/{proposal_id}/approve")
    assert approved.status_code == 200
