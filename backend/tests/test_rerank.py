"""重排的降级、噪声预过滤和缓存行为。"""

from app.rag.cache import InMemoryCache
from app.rag.rerank import CachedReranker, RuleReranker
from app.rag.retrieval import Evidence


def evidence(locator: str, title: str, content: str, score: float = 0.5) -> Evidence:
    return Evidence("document_chunk", locator, title, content, score, locator)


def test_rule_reranker_prioritizes_title_exact_match() -> None:
    candidates = [
        evidence("a", "普通说明", "检索增强生成的实现细节"),
        evidence("b", "RAG 检索增强", "概览"),
    ]
    reranked = RuleReranker().rerank(query="RAG 检索增强", candidates=candidates, limit=2)
    assert reranked[0].locator == "b"


def test_rerank_filters_noise_and_reuses_cached_order() -> None:
    cache = InMemoryCache(max_entries=8)
    reranker = CachedReranker(primary=RuleReranker(), cache=cache, ttl_seconds=60)
    candidates = [
        evidence("noise", "无", "摘要"),
        evidence("useful", "向量检索", "向量检索使用嵌入向量进行语义相似度计算。"),
    ]
    first = reranker.rerank(query="向量检索", candidates=candidates, limit=2)
    assert [item.locator for item in first] == ["useful"]
    second = reranker.rerank(query="向量检索", candidates=candidates, limit=2)
    assert reranker.cache_hit is True
    assert [item.locator for item in second] == ["useful"]
