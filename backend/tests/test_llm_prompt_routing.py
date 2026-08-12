"""验证不同路由会生成不同的公开系统提示词。"""

from app.agent.llm import OpenAICompatibleLLM


def test_system_prompt_changes_with_route_reason() -> None:
    realtime = OpenAICompatibleLLM._system_prompt("direct", "unsupported_realtime_request")
    emotion = OpenAICompatibleLLM._system_prompt("direct", "assistant_emotion")
    rag = OpenAICompatibleLLM._system_prompt("rag", "knowledge_request")

    assert "实时数据源" in realtime
    assert "没有真实情绪" in emotion
    assert "仅可依据‘资料证据’作答" in rag
    assert realtime != emotion
    assert emotion != rag
