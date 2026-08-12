"""Query Rewrite 的缓存、模型调用与降级行为。"""

import httpx

from app.core.config import Settings
from app.rag.cache import InMemoryCache
from app.rag.query_rewrite import QueryRewriter


def test_disabled_rewrite_uses_normalized_original_query() -> None:
    result = QueryRewriter(Settings(), cache=None).rewrite(
        query="  RAG   检索  ", workspace_id="workspace"
    )
    assert result.query == "RAG 检索"
    assert result.provider == "rule"
    assert result.fallback is False


def test_enabled_rewrite_falls_back_when_llm_is_not_configured() -> None:
    result = QueryRewriter(Settings(query_rewrite_enabled=True), cache=None).rewrite(
        query="RAG 如何缓存", workspace_id="workspace"
    )
    assert result.query == "RAG 如何缓存"
    assert result.provider == "rule"
    assert result.fallback is True


def test_llm_rewrite_is_cached(monkeypatch) -> None:
    calls = [0]

    def fake_post(*_: object, **__: object) -> httpx.Response:
        calls[0] += 1
        return httpx.Response(
            200,
            request=httpx.Request("POST", "https://models.example.com/v1/chat/completions"),
            json={"choices": [{"message": {"content": "RAG 缓存 Redis TTL"}}]},
        )

    monkeypatch.setattr("app.rag.query_rewrite.httpx.post", fake_post)
    settings = Settings(
        query_rewrite_enabled=True,
        llm_model="rewrite-model",
        llm_api_key="test-key",
        llm_base_url="https://models.example.com/v1",
    )
    rewriter = QueryRewriter(settings, cache=InMemoryCache(max_entries=8))
    first = rewriter.rewrite(query="RAG 怎么做缓存", workspace_id="workspace")
    second = rewriter.rewrite(query="RAG 怎么做缓存", workspace_id="workspace")
    assert first.query == "RAG 缓存 Redis TTL"
    assert second.cache_hit is True
    assert calls[0] == 1


def test_multi_query_plan_keeps_original_query_and_deduplicates(monkeypatch) -> None:
    def fake_post(*_: object, **__: object) -> httpx.Response:
        return httpx.Response(
            200,
            request=httpx.Request("POST", "https://models.example.com/v1/chat/completions"),
            json={
                "choices": [
                    {
                        "message": {
                            "content": (
                                '{"main_query":"项目算力消耗",'
                                '"sub_queries":["每日处理成本"],'
                                '"synonyms":["推理成本","项目算力消耗"]}'
                            )
                        }
                    }
                ]
            },
        )

    monkeypatch.setattr("app.rag.query_rewrite.httpx.post", fake_post)
    settings = Settings(
        query_rewrite_enabled=True,
        query_rewrite_multi_query_enabled=True,
        llm_model="rewrite-model",
        llm_api_key="test-key",
        llm_base_url="https://models.example.com/v1",
    )
    plan = QueryRewriter(settings, cache=None).plan(
        query="这个项目每天消耗多少算力？", workspace_id="workspace"
    )
    assert plan.main_query == "项目算力消耗"
    assert plan.sub_queries == ("每日处理成本",)
    assert plan.synonyms == ("推理成本", "项目算力消耗")
    assert plan.original_query in plan.queries
    assert plan.variant_count == 4
