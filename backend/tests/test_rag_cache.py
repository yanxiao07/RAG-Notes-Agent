"""Redis 不可用时的缓存降级与查询向量缓存回归测试。"""

from app.core.config import Settings
from app.rag.cache import InMemoryCache, build_cache, clear_cache_registry
from app.rag.embeddings import HashingEmbeddingProvider
from app.rag.retrieval import LocalHybridRetriever


def test_memory_cache_expires_entries() -> None:
    current_time = [0.0]
    cache = InMemoryCache(max_entries=2, clock=lambda: current_time[0])
    cache.set_json("embedding", [0.1, 0.2], ttl_seconds=10)
    assert cache.get_json("embedding") == [0.1, 0.2]
    current_time[0] = 10.0
    assert cache.get_json("embedding") is None


def test_redis_is_optional_and_falls_back_to_memory(monkeypatch) -> None:
    def unavailable_redis(**_: object) -> None:
        raise RuntimeError()

    monkeypatch.setattr("app.rag.cache.RedisCache", unavailable_redis)
    cache = build_cache(Settings(redis_url="redis://unavailable:6379/0"))
    assert cache is not None
    assert cache.name == "memory"


def test_build_cache_reuses_process_fallback() -> None:
    clear_cache_registry()
    try:
        settings = Settings(redis_url="")
        first = build_cache(settings)
        second = build_cache(settings)
        assert first is second
    finally:
        clear_cache_registry()


def test_retriever_reuses_cached_query_embedding() -> None:
    cache = InMemoryCache(max_entries=8)
    retriever = LocalHybridRetriever(HashingEmbeddingProvider(dimensions=8), cache=cache)
    first = retriever._embed_query(query="检索缓存", workspace_id="workspace", embedding_revision=1)
    assert retriever.embedding_cache_hit is False
    second = retriever._embed_query(
        query="检索缓存", workspace_id="workspace", embedding_revision=1
    )
    assert retriever.embedding_cache_hit is True
    assert second == first
