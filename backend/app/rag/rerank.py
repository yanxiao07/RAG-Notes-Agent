"""候选重排：Cross-encoder 优先，确定性规则兜底。

重排只调整已经通过工作区和知识库过滤的候选项，不能扩大召回范围。缓存仅保存 locator 和
分数，正文始终来自当次数据库检索，避免在 Redis 中复制知识库内容。
"""

from __future__ import annotations

from dataclasses import replace
from typing import Protocol

import httpx

from app.core.config import Settings
from app.core.logging import get_logger
from app.core.model_resilience import call_with_model_resilience
from app.rag.cache import CacheBackend, stable_cache_key
from app.rag.retrieval import Evidence, tokenize

logger = get_logger(__name__)


class Reranker(Protocol):
    name: str

    def rerank(self, *, query: str, candidates: list[Evidence], limit: int) -> list[Evidence]: ...


class RuleReranker:
    """无模型依赖的回退排序，主要保证服务退化时输出仍可解释且稳定。"""

    name = "rule"

    def rerank(self, *, query: str, candidates: list[Evidence], limit: int) -> list[Evidence]:
        query_tokens = tokenize(query)
        ranked: list[Evidence] = []
        for item in candidates:
            title_tokens = tokenize(item.title)
            content_tokens = tokenize(item.content)
            overlap = len(query_tokens & content_tokens)
            title_overlap = len(query_tokens & title_tokens)
            # 保留混合召回原分作为弱信号，标题精确命中则提供更强的排序信号。
            score = item.score + title_overlap * 0.35 + overlap * 0.08
            ranked.append(replace(item, score=score))
        return sorted(ranked, key=lambda item: (-item.score, item.locator))[:limit]


class DashScopeCompatibleReranker:
    """兼容 DashScope `/reranks` 返回格式的 Cross-encoder 适配器。"""

    name = "dashscope_compatible"

    def __init__(self, settings: Settings) -> None:
        if not settings.reranker_model or not settings.reranker_api_key:
            raise ValueError("reranker is not configured")
        self._model = settings.reranker_model
        self._api_key = settings.reranker_api_key
        self._url = f"{settings.reranker_base_url.rstrip('/')}/reranks"
        self._timeout = settings.reranker_timeout_seconds
        self._settings = settings

    def rerank(self, *, query: str, candidates: list[Evidence], limit: int) -> list[Evidence]:
        response = call_with_model_resilience(
            lambda: _post_and_raise(
                self._url,
                headers={"Authorization": f"Bearer {self._api_key}"},
                json={
                    "model": self._model,
                    "query": query,
                    "documents": [f"{item.title}\n{item.content}" for item in candidates],
                    "top_n": min(limit, len(candidates)),
                },
                timeout=self._timeout,
            ),
            settings=self._settings,
            operation="rerank",
        )
        payload = response.json()
        results = payload.get("output", {}).get("results") or payload.get("results")
        if not isinstance(results, list):
            raise ValueError("reranker response is missing results")
        ranked: list[Evidence] = []
        for result in results:
            if not isinstance(result, dict):
                continue
            index = result.get("index")
            if not isinstance(index, int) or not 0 <= index < len(candidates):
                continue
            score = result.get("relevance_score", result.get("score", 0))
            if not isinstance(score, (int, float)):
                continue
            ranked.append(replace(candidates[index], score=float(score)))
        if not ranked:
            raise ValueError("reranker returned no valid candidate")
        return sorted(ranked, key=lambda item: (-item.score, item.locator))[:limit]


class CachedReranker:
    """对任意重排器增加候选排序缓存和安全回退。"""

    def __init__(self, *, primary: Reranker, cache: CacheBackend | None, ttl_seconds: int) -> None:
        self._primary = primary
        self._fallback = RuleReranker()
        self._cache = cache
        self._ttl_seconds = ttl_seconds
        self.name = primary.name
        self.cache_hit = False
        self.used_fallback = False

    def rerank(self, *, query: str, candidates: list[Evidence], limit: int) -> list[Evidence]:
        filtered = [item for item in candidates if not _is_noise(item)]
        # 候选全是低信息项时保留原集合，避免没有答案的错误语义。
        effective_candidates = filtered or candidates
        cache_key = stable_cache_key(
            "rerank",
            self.name,
            query.strip().lower(),
            *(f"{item.locator}:{item.content}" for item in effective_candidates),
        )
        if self._cache is not None:
            cached = self._cache.get_json(cache_key)
            restored = self._restore(cached, effective_candidates, limit)
            if restored is not None:
                self.cache_hit = True
                self.used_fallback = False
                return restored
        self.cache_hit = False
        try:
            ranked = self._primary.rerank(query=query, candidates=effective_candidates, limit=limit)
            self.used_fallback = False
        except (httpx.HTTPError, TypeError, ValueError) as exc:
            logger.warning(
                "reranker_fallback",
                provider=self.name,
                error_type=type(exc).__name__,
                candidate_count=len(effective_candidates),
            )
            ranked = self._fallback.rerank(
                query=query, candidates=effective_candidates, limit=limit
            )
            self.used_fallback = True
        if self._cache is not None:
            self._cache.set_json(
                cache_key,
                [{"locator": item.locator, "score": item.score} for item in ranked],
                ttl_seconds=self._ttl_seconds,
            )
        return ranked

    @staticmethod
    def _restore(cached: object, candidates: list[Evidence], limit: int) -> list[Evidence] | None:
        if not isinstance(cached, list):
            return None
        by_locator = {item.locator: item for item in candidates}
        ranked: list[Evidence] = []
        for item in cached:
            if not isinstance(item, dict):
                return None
            locator, score = item.get("locator"), item.get("score")
            if not isinstance(locator, str) or not isinstance(score, (int, float)):
                return None
            evidence = by_locator.get(locator)
            if evidence is not None:
                ranked.append(replace(evidence, score=float(score)))
        return ranked[:limit] if ranked else None


def build_reranker(settings: Settings, *, cache: CacheBackend | None) -> CachedReranker:
    if settings.reranker_provider == DashScopeCompatibleReranker.name:
        try:
            primary: Reranker = DashScopeCompatibleReranker(settings)
        except ValueError:
            primary = RuleReranker()
    else:
        primary = RuleReranker()
    return CachedReranker(
        primary=primary,
        cache=cache,
        ttl_seconds=settings.cache_default_ttl_seconds,
    )


def _post_and_raise(
    url: str,
    *,
    headers: dict[str, str],
    json: dict[str, object],
    timeout: float,
) -> httpx.Response:
    response = httpx.post(url, headers=headers, json=json, timeout=timeout)
    response.raise_for_status()
    return response


def _is_noise(item: Evidence) -> bool:
    """过滤明显不具备回答信息量的候选，避免占用昂贵的 Cross-encoder 名额。"""

    normalized = "".join(item.content.split())
    return not item.title.strip() or len(normalized) < 12
