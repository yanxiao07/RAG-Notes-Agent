"""查询路由、运行配置和 Embedding 重建 API。"""

from fastapi.testclient import TestClient

from app.rag.query_routing import RuleQueryRouter


def test_query_router_sends_social_messages_direct_and_knowledge_to_rag() -> None:
    router = RuleQueryRouter()
    assert router.route("你好").mode == "direct"
    assert router.route("比较 XGBoost 和 LightGBM 的结论").mode == "rag"


def test_query_router_distinguishes_non_rag_intents_before_retrieval() -> None:
    router = RuleQueryRouter()
    assert router.route("你是谁？").reason == "system_capability_or_help"
    assert router.route("你开心吗？").reason == "assistant_emotion"
    assert router.route("今天北京天气怎么样？").reason == "unsupported_realtime_request"
    assert router.route("我是谁？").reason == "identity_ambiguous"
    assert router.route("我刚才说的项目名称是什么？").mode == "memory"
    assert router.route("怎么导入文档").mode == "direct"
    assert router.route("文档中怎么导入数据").mode == "rag"
    assert router.route("文档中的天气示例是什么").mode == "rag"
    assert router.route("这个呢？").mode == "clarify"


def test_runtime_configuration_hides_secrets_and_marks_local_mode(client: TestClient) -> None:
    response = client.get("/api/v1/runtime/configuration")
    assert response.status_code == 200
    payload = response.json()
    assert payload["llm"]["provider"] == "evidence_synthesis"
    assert payload["embedding"]["provider"] == "hashing"
    assert payload["productionReady"] is False
    assert "apiKey" not in response.text
