"""Hybrid Query Router 的结构化输出、阈值和安全回退测试。"""

from app.core.config import Settings
from app.rag.cache import InMemoryCache
from app.rag.query_routing import HybridQueryRouter, IntentDecision


class FakeClassifier:
    name = "fake"

    def __init__(self, decision: IntentDecision) -> None:
        self.decision = decision
        self.calls = 0

    def classify(self, query: str) -> IntentDecision:
        del query
        self.calls += 1
        return self.decision


def router_settings() -> Settings:
    return Settings(
        query_router_enabled=True,
        llm_provider="openai_compatible",
        llm_model="router-model",
        llm_api_key="test-key",
    )


def test_hybrid_router_uses_llm_only_for_rule_gray_zone() -> None:
    classifier = FakeClassifier(IntentDecision(mode="direct", confidence=0.96))
    router = HybridQueryRouter(classifier=classifier)

    result = router.route(
        "请简单介绍一下这个工作台",
        settings=router_settings(),
        workspace_id="workspace",
    )

    assert result.mode == "direct"
    assert result.router == "fake"
    assert result.confidence == 0.96
    assert classifier.calls == 1


def test_hybrid_router_forces_explicit_document_request_to_rag() -> None:
    classifier = FakeClassifier(IntentDecision(mode="direct", confidence=0.99))
    router = HybridQueryRouter(classifier=classifier)

    result = router.route(
        "文档中如何配置 Redis？",
        settings=router_settings(),
        workspace_id="workspace",
    )

    assert result.mode == "rag"
    assert result.reason == "explicit_knowledge_request"
    assert classifier.calls == 0


def test_hybrid_router_low_confidence_falls_back_to_rag() -> None:
    classifier = FakeClassifier(IntentDecision(mode="direct", confidence=0.4))
    router = HybridQueryRouter(classifier=classifier)

    result = router.route(
        "这个功能应该怎么理解",
        settings=router_settings(),
        workspace_id="workspace",
    )

    assert result.mode == "rag"
    assert result.reason == "llm_router_low_confidence"
    assert result.router == "rule_fallback"


def test_hybrid_router_caches_valid_decision() -> None:
    classifier = FakeClassifier(IntentDecision(mode="clarify", confidence=0.91))
    router = HybridQueryRouter(classifier=classifier)
    cache = InMemoryCache(max_entries=16)
    settings = router_settings()

    first = router.route(
        "项目应该如何推进",
        settings=settings,
        workspace_id="workspace",
        cache=cache,
    )
    second = router.route(
        "项目应该如何推进",
        settings=settings,
        workspace_id="workspace",
        cache=cache,
    )

    assert first.mode == "clarify"
    assert second.mode == "clarify"
    assert first.cache_hit is False
    assert second.cache_hit is True
    assert classifier.calls == 1
