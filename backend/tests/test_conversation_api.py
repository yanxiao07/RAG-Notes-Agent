"""证据约束问答 API：会话、SSE 流和引用持久化。"""

from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from app.domain.agent.models import AgentRun
from app.domain.workspace import Workspace


def create_knowledge_base(client: TestClient) -> dict[str, object]:
    response = client.post("/api/v1/knowledge-bases", json={"name": "问答测试库"})
    assert response.status_code == 201
    return response.json()


def create_conversation(client: TestClient, knowledge_base_id: str) -> dict[str, object]:
    response = client.post(
        "/api/v1/conversations",
        json={"knowledgeBaseId": knowledge_base_id, "title": "研究问答"},
    )
    assert response.status_code == 201
    return response.json()


def test_conversation_streams_grounded_answer_and_persists_citations(client: TestClient) -> None:
    knowledge_base = create_knowledge_base(client)
    client.post(
        f"/api/v1/knowledge-bases/{knowledge_base['id']}/notes",
        json={"title": "实验结论", "content": "检索系统必须返回可追溯的原始证据。"},
    )
    conversation = create_conversation(client, str(knowledge_base["id"]))

    response = client.post(
        f"/api/v1/conversations/{conversation['id']}/messages",
        json={"content": "检索系统应该具备什么能力？"},
    )
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert "event: started" in response.text
    assert "event: citation" in response.text
    assert "event: delta" in response.text
    assert "event: completed" in response.text

    history = client.get(f"/api/v1/conversations/{conversation['id']}/messages")
    assert history.status_code == 200
    messages = history.json()["items"]
    assert [message["role"] for message in messages] == ["user", "assistant"]
    assert messages[1]["state"] == "completed"
    assert messages[1]["citations"][0]["locator"].startswith("note:")
    assert messages[1]["citations"][0]["sourceTrustLevel"] == "standard"
    assert messages[1]["citations"][0]["governanceAvailability"] == "available"
    assert messages[1]["citations"][0]["conflictState"] == "none"
    assert "可追溯的原始证据" in messages[1]["content"]


def test_conversation_refuses_to_invent_answer_without_evidence(client: TestClient) -> None:
    knowledge_base = create_knowledge_base(client)
    conversation = create_conversation(client, str(knowledge_base["id"]))

    response = client.post(
        f"/api/v1/conversations/{conversation['id']}/messages",
        json={"content": "不存在的火星项目结论是什么？"},
    )
    assert response.status_code == 200
    assert "没有检索到足以支撑" in response.text

    history = client.get(f"/api/v1/conversations/{conversation['id']}/messages")
    assistant_message = history.json()["items"][1]
    assert assistant_message["citations"] == []
    assert assistant_message["state"] == "completed"


def test_direct_question_skips_rag_and_has_no_citations(client: TestClient) -> None:
    knowledge_base = create_knowledge_base(client)
    conversation = create_conversation(client, str(knowledge_base["id"]))

    response = client.post(
        f"/api/v1/conversations/{conversation['id']}/messages",
        json={"content": "你是谁？", "explainRetrieval": True},
    )

    assert response.status_code == 200
    assert '"route": "direct"' in response.text
    assert '"routeReason": "system_capability_or_help"' in response.text
    assert "event: citation" not in response.text
    assert "我是 RAG Notes Agent" in response.text


def test_realtime_weather_question_does_not_use_stale_rag_evidence(client: TestClient) -> None:
    knowledge_base = create_knowledge_base(client)
    client.post(
        f"/api/v1/knowledge-bases/{knowledge_base['id']}/notes",
        json={"title": "天气示例", "content": "北京天气示例代码，不是实时数据。"},
    )
    conversation = create_conversation(client, str(knowledge_base["id"]))

    response = client.post(
        f"/api/v1/conversations/{conversation['id']}/messages",
        json={"content": "今天北京天气怎么样？", "explainRetrieval": True},
    )

    assert response.status_code == 200
    assert '"routeReason": "unsupported_realtime_request"' in response.text
    assert "未接入天气、新闻等实时数据源" in response.text
    assert "event: citation" not in response.text


def test_emotional_smalltalk_uses_non_rag_policy_response(client: TestClient) -> None:
    knowledge_base = create_knowledge_base(client)
    conversation = create_conversation(client, str(knowledge_base["id"]))

    response = client.post(
        f"/api/v1/conversations/{conversation['id']}/messages",
        json={"content": "你开心吗？", "explainRetrieval": True},
    )

    assert response.status_code == 200
    assert '"routeReason": "assistant_emotion"' in response.text
    assert "没有真实情绪或身体感受" in response.text
    assert "event: citation" not in response.text


def test_ambiguous_identity_question_asks_for_clarification(client: TestClient) -> None:
    knowledge_base = create_knowledge_base(client)
    conversation = create_conversation(client, str(knowledge_base["id"]))

    response = client.post(
        f"/api/v1/conversations/{conversation['id']}/messages",
        json={"content": "我是谁？", "explainRetrieval": True},
    )

    assert response.status_code == 200
    assert '"route": "clarify"' in response.text
    assert '"routeReason": "identity_ambiguous"' in response.text
    assert "你是指希望我记住的个人资料，还是当前会话中的身份？" in response.text
    assert "event: citation" not in response.text


def test_memory_question_reads_session_without_document_citations(client: TestClient) -> None:
    knowledge_base = create_knowledge_base(client)
    conversation = create_conversation(client, str(knowledge_base["id"]))

    first = client.post(
        f"/api/v1/conversations/{conversation['id']}/messages",
        json={"content": "我叫小明。"},
    )
    assert first.status_code == 200

    response = client.post(
        f"/api/v1/conversations/{conversation['id']}/messages",
        json={"content": "我刚才说了什么？", "explainRetrieval": True},
    )

    assert response.status_code == 200
    assert '"route": "memory"' in response.text
    assert "我叫小明" in response.text
    assert "event: citation" not in response.text


def test_conversation_can_be_renamed_and_archived_without_physical_delete(
    client: TestClient,
) -> None:
    knowledge_base = create_knowledge_base(client)
    conversation = create_conversation(client, str(knowledge_base["id"]))

    renamed = client.patch(
        f"/api/v1/conversations/{conversation['id']}",
        json={"title": "重新整理的研究问答"},
    )
    assert renamed.status_code == 200
    assert renamed.json()["title"] == "重新整理的研究问答"

    archived = client.delete(f"/api/v1/conversations/{conversation['id']}")
    assert archived.status_code == 200
    assert archived.json()["state"] == "archived"

    conversations = client.get(f"/api/v1/knowledge-bases/{knowledge_base['id']}/conversations")
    assert conversations.status_code == 200
    assert conversations.json()["items"] == []
    assert client.get(f"/api/v1/conversations/{conversation['id']}/messages").status_code == 404


def test_conversation_can_stream_explainable_retrieval_trace(client: TestClient) -> None:
    knowledge_base = create_knowledge_base(client)
    client.post(
        f"/api/v1/knowledge-bases/{knowledge_base['id']}/notes",
        json={"title": "检索策略", "content": "RRF 用于融合关键词与向量召回结果。"},
    )
    conversation = create_conversation(client, str(knowledge_base["id"]))

    response = client.post(
        f"/api/v1/conversations/{conversation['id']}/messages",
        json={"content": "RRF 有什么作用？", "explainRetrieval": True},
    )

    assert response.status_code == 200
    assert "event: trace" in response.text
    assert '"step": "routing"' in response.text
    assert '"step": "grounding"' in response.text


def test_rag_stage_events_are_persisted_without_question_or_evidence_content(
    client: TestClient, session_factory: sessionmaker[Session]
) -> None:
    knowledge_base = create_knowledge_base(client)
    client.post(
        f"/api/v1/knowledge-bases/{knowledge_base['id']}/notes",
        json={"title": "阶段事件证据", "content": "仅用于验证阶段事件不会保存这段正文。"},
    )
    conversation = create_conversation(client, str(knowledge_base["id"]))
    question = "阶段事件是否会暴露输入问题？"
    response = client.post(
        f"/api/v1/conversations/{conversation['id']}/messages",
        json={"content": question},
    )
    assert response.status_code == 200

    with session_factory() as session:
        run = session.query(AgentRun).filter(AgentRun.conversation_id == conversation["id"]).one()
        run_id = run.id
    stage_events = client.get(f"/api/v1/agent/runs/{run_id}/stage-events")
    assert stage_events.status_code == 200
    payload = stage_events.json()
    assert [item["stage"] for item in payload["items"]] == [
        "route",
        "rewrite",
        "retrieve",
        "fuse",
        "rerank",
        "truncate",
        "answer",
        "judge",
    ]
    assert payload["items"][-2]["state"] == "completed"
    assert all(len(item["inputHash"] or "") in {0, 64} for item in payload["items"])
    assert question not in stage_events.text
    assert "不会保存这段正文" not in stage_events.text
    assert any(item["candidateLocators"] for item in payload["items"])


def test_no_evidence_is_attributed_as_retrieval_miss(
    client: TestClient, session_factory: sessionmaker[Session]
) -> None:
    knowledge_base = create_knowledge_base(client)
    conversation = create_conversation(client, str(knowledge_base["id"]))
    response = client.post(
        f"/api/v1/conversations/{conversation['id']}/messages",
        json={"content": "不存在的火星项目结论是什么？"},
    )
    assert response.status_code == 200
    with session_factory() as session:
        run = session.query(AgentRun).filter(AgentRun.conversation_id == conversation["id"]).one()
        run_id = run.id
    badcases = client.get(f"/api/v1/agent/runs/{run_id}/badcases")
    assert badcases.status_code == 200
    item = badcases.json()["items"]
    assert len(item) == 1
    assert item[0]["category"] == "retrieval_miss"
    assert item[0]["reasonCode"] == "NO_GROUNDED_EVIDENCE"
    assert "火星项目" not in badcases.text


def test_rag_replay_creates_analysis_only_run_and_is_idempotent(
    client: TestClient, session_factory: sessionmaker[Session]
) -> None:
    knowledge_base = create_knowledge_base(client)
    client.post(
        f"/api/v1/knowledge-bases/{knowledge_base['id']}/notes",
        json={"title": "回放证据", "content": "RRF 会融合独立排序的候选列表。"},
    )
    conversation = create_conversation(client, str(knowledge_base["id"]))
    question = "RRF 如何融合候选？"
    assert (
        client.post(
            f"/api/v1/conversations/{conversation['id']}/messages",
            json={"content": question},
        ).status_code
        == 200
    )
    with session_factory() as session:
        source_run = (
            session.query(AgentRun).filter(AgentRun.conversation_id == conversation["id"]).one()
        )
        source_run_id = source_run.id

    replay = client.post(
        f"/api/v1/agent/runs/{source_run_id}/stage-events/replay",
        json={"startStage": "rewrite"},
        headers={"Idempotency-Key": "replay-rag-run-001"},
    )
    assert replay.status_code == 201
    payload = replay.json()
    replay_run_id = payload["replayRun"]["id"]
    assert payload["sourceRunId"] == source_run_id
    assert payload["startStage"] == "rewrite"
    assert payload["comparison"]["replayCandidateCount"] >= 1
    assert question not in replay.text

    events = client.get(f"/api/v1/agent/runs/{replay_run_id}/stage-events")
    assert events.status_code == 200
    answer_event = next(item for item in events.json()["items"] if item["stage"] == "answer")
    assert answer_event["state"] == "skipped"
    assert answer_event["metrics"]["reason"] == "analysis_only_replay"

    duplicate = client.post(
        f"/api/v1/agent/runs/{source_run_id}/stage-events/replay",
        json={"startStage": "rewrite"},
        headers={"Idempotency-Key": "replay-rag-run-001"},
    )
    assert duplicate.status_code == 201
    assert duplicate.headers["Idempotency-Replayed"] == "true"
    assert duplicate.json()["replayRun"]["id"] == replay_run_id


def test_unhelpful_answer_feedback_is_linked_to_stage_events_and_triage(
    client: TestClient,
) -> None:
    knowledge_base = create_knowledge_base(client)
    client.post(
        f"/api/v1/knowledge-bases/{knowledge_base['id']}/notes",
        json={"title": "反馈证据", "content": "检索结果需要具备可追溯引用。"},
    )
    conversation = create_conversation(client, str(knowledge_base["id"]))
    assert (
        client.post(
            f"/api/v1/conversations/{conversation['id']}/messages",
            json={"content": "检索结果应该具备什么能力？"},
        ).status_code
        == 200
    )
    assistant_message = client.get(f"/api/v1/conversations/{conversation['id']}/messages").json()[
        "items"
    ][1]
    feedback = client.put(
        f"/api/v1/conversation-messages/{assistant_message['id']}/feedback",
        json={"sentiment": "unhelpful", "reasonCode": "missing_evidence"},
    )
    assert feedback.status_code == 200
    payload = feedback.json()
    assert payload["feedback"]["sentiment"] == "unhelpful"
    assert len(payload["feedback"]["stageEventIds"]) == 8
    assert payload["triage"]["category"] == "retrieval_miss"
    assert "可追溯引用" not in feedback.text

    triage = client.get(
        f"/api/v1/knowledge-bases/{knowledge_base['id']}/feedback-triage?state=open"
    )
    assert triage.status_code == 200
    assert triage.json()["items"][0]["id"] == payload["triage"]["id"]
    reviewed = client.patch(
        f"/api/v1/feedback-triage/{payload['triage']['id']}",
        json={"state": "resolved", "resolutionTarget": "evaluation_case"},
    )
    assert reviewed.status_code == 200
    assert reviewed.json()["state"] == "resolved"
    assert reviewed.json()["resolutionTarget"] == "evaluation_case"


def _create_resolved_feedback_triage(
    client: TestClient, knowledge_base_id: str, resolution_target: str
) -> dict[str, object]:
    """通过公开 API 构造可回流的反馈，避免测试绕开真实的权限和运行链路。"""

    conversation = create_conversation(client, knowledge_base_id)
    answer = client.post(
        f"/api/v1/conversations/{conversation['id']}/messages",
        json={"content": "检索结果应该具备什么能力？"},
    )
    assert answer.status_code == 200
    messages = client.get(f"/api/v1/conversations/{conversation['id']}/messages").json()["items"]
    feedback = client.put(
        f"/api/v1/conversation-messages/{messages[1]['id']}/feedback",
        json={"sentiment": "unhelpful", "reasonCode": "missing_evidence"},
    )
    assert feedback.status_code == 200
    triage = feedback.json()["triage"]
    reviewed = client.patch(
        f"/api/v1/feedback-triage/{triage['id']}",
        json={"state": "resolved", "resolutionTarget": resolution_target},
    )
    assert reviewed.status_code == 200
    return reviewed.json()


def test_feedback_learning_requires_triage_then_approval_before_writing_note(
    client: TestClient,
) -> None:
    knowledge_base = create_knowledge_base(client)
    client.post(
        f"/api/v1/knowledge-bases/{knowledge_base['id']}/notes",
        json={"title": "反馈证据", "content": "检索结果需要具备可追溯引用。"},
    )
    triage = _create_resolved_feedback_triage(
        client, str(knowledge_base["id"]), "knowledge_draft"
    )

    created = client.post(
        "/api/v1/feedback-knowledge-drafts",
        json={
            "feedbackTriageId": triage["id"],
            "title": "证据引用规范",
            "content": "所有事实结论必须保留可定位的来源引用。",
        },
        headers={"Idempotency-Key": "feedback-draft-create-001"},
    )
    assert created.status_code == 201
    draft = created.json()
    assert draft["state"] == "pending"
    assert draft["createdNoteId"] is None
    note_list = client.get(f"/api/v1/knowledge-bases/{knowledge_base['id']}/notes").json()
    assert len(note_list["items"]) == 1

    replay = client.post(
        "/api/v1/feedback-knowledge-drafts",
        json={
            "feedbackTriageId": triage["id"],
            "title": "证据引用规范",
            "content": "所有事实结论必须保留可定位的来源引用。",
        },
        headers={"Idempotency-Key": "feedback-draft-create-001"},
    )
    assert replay.status_code == 201
    assert replay.headers["Idempotency-Replayed"] == "true"
    assert replay.json()["id"] == draft["id"]

    approved = client.post(
        f"/api/v1/feedback-knowledge-drafts/{draft['id']}/review",
        json={"decision": "approved"},
        headers={"Idempotency-Key": "feedback-draft-approve-001"},
    )
    assert approved.status_code == 200
    assert approved.json()["state"] == "approved"
    assert approved.json()["createdNoteId"]
    notes = client.get(f"/api/v1/knowledge-bases/{knowledge_base['id']}/notes").json()["items"]
    assert {note["title"] for note in notes} == {"反馈证据", "证据引用规范"}


def test_feedback_evaluation_case_is_approved_into_controlled_collection(
    client: TestClient,
) -> None:
    knowledge_base = create_knowledge_base(client)
    client.post(
        f"/api/v1/knowledge-bases/{knowledge_base['id']}/notes",
        json={"title": "反馈证据", "content": "检索结果需要具备可追溯引用。"},
    )
    triage = _create_resolved_feedback_triage(
        client, str(knowledge_base["id"]), "evaluation_case"
    )
    created = client.post(
        "/api/v1/feedback-evaluation-cases",
        json={
            "feedbackTriageId": triage["id"],
            "query": "检索结果应该具备什么能力？",
            "expectedSourceTitles": ["反馈证据"],
            "requiredKeywords": ["可追溯引用"],
            "limit": 5,
        },
        headers={"Idempotency-Key": "feedback-case-create-001"},
    )
    assert created.status_code == 201
    case = created.json()
    assert case["state"] == "pending"

    approved = client.post(
        f"/api/v1/feedback-evaluation-cases/{case['id']}/review",
        json={"decision": "approved"},
        headers={"Idempotency-Key": "feedback-case-approve-001"},
    )
    assert approved.status_code == 200
    assert approved.json()["state"] == "approved"
    listed = client.get(
        f"/api/v1/knowledge-bases/{knowledge_base['id']}/feedback-evaluation-cases?state=approved"
    )
    assert listed.status_code == 200
    assert listed.json()["items"][0]["id"] == case["id"]


def test_conversation_is_not_visible_across_workspaces(
    client: TestClient, session_factory: sessionmaker[Session]
) -> None:
    other_workspace_id = str(uuid4())
    with session_factory() as session:
        session.add(Workspace(id=other_workspace_id, name="问答隔离工作区"))
        session.commit()
    knowledge_base = create_knowledge_base(client)
    conversation = create_conversation(client, str(knowledge_base["id"]))
    response = client.get(
        f"/api/v1/conversations/{conversation['id']}/messages",
        headers={"X-Workspace-ID": other_workspace_id},
    )
    assert response.status_code == 404
