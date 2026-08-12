"""检索查询改写：优先使用受控 LLM，任何异常都回退到确定性规则结果。"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

import httpx

from app.core.config import Settings
from app.core.logging import get_logger
from app.core.model_resilience import call_with_model_resilience
from app.rag.cache import CacheBackend, stable_cache_key

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class QueryRewriteResult:
    query: str
    provider: str
    cache_hit: bool
    fallback: bool


@dataclass(frozen=True, slots=True)
class QueryRewritePlan:
    """一次检索使用的多路 Query 计划。

    ``queries`` 始终包含原始问题，保证改写模型即使遗漏专有名词也不会降低原始召回；
    后续查询只作为补充候选，最终仍由 RRF 和重排决定排序。
    """

    original_query: str
    main_query: str
    sub_queries: tuple[str, ...]
    synonyms: tuple[str, ...]
    queries: tuple[str, ...]
    provider: str
    cache_hit: bool
    fallback: bool

    @property
    def variant_count(self) -> int:
        return len(self.queries)


class RuleQueryRewriter:
    """最小、可预测的规则基线，仅规范空白和重复分隔符。"""

    name = "rule"

    def rewrite(self, query: str) -> str:
        return re.sub(r"\s+", " ", query).strip()


class OpenAICompatibleQueryRewriter:
    """使用现有问答模型生成短检索短语，不允许它回答问题或加入知识库外事实。"""

    name = "openai_compatible"

    def __init__(self, settings: Settings) -> None:
        if not settings.llm_api_key or not settings.llm_model:
            raise ValueError("query rewrite llm is not configured")
        self._url = f"{settings.llm_base_url.rstrip('/')}/chat/completions"
        self._model = settings.llm_model
        self._api_key = settings.llm_api_key
        self._timeout = settings.query_rewrite_timeout_seconds
        self._max_length = settings.query_rewrite_max_length
        self._settings = settings

    def _post(self, url: str, *, headers: dict[str, str], json: dict[str, Any], timeout: float):
        return call_with_model_resilience(
            lambda: _post_and_raise(url, headers=headers, json=json, timeout=timeout),
            settings=self._settings,
            operation="query_rewrite",
        )

    def rewrite(self, query: str) -> str:
        response = self._post(
            self._url,
            headers={"Authorization": f"Bearer {self._api_key}"},
            json={
                "model": self._model,
                "messages": [
                    {
                        "role": "system",
                        "content": (
                            "将用户问题改写成用于知识库检索的简短关键词短语。"
                            "保留实体、时间、约束和专业术语；不回答问题、不编造事实、"
                            "不输出解释、编号或 Markdown。"
                        ),
                    },
                    {"role": "user", "content": query},
                ],
                "temperature": 0,
                "max_tokens": 128,
                "stream": False,
            },
            timeout=self._timeout,
        )
        response.raise_for_status()
        payload = response.json()
        rewritten = str(payload["choices"][0]["message"]["content"]).strip()
        rewritten = re.sub(r"\s+", " ", rewritten)
        if not rewritten or len(rewritten) > self._max_length:
            raise ValueError("invalid rewritten query")
        return rewritten


class OpenAICompatibleQueryPlanner(OpenAICompatibleQueryRewriter):
    """结构化多路改写器，输出主查询、子查询和同义检索词。"""

    name = "openai_compatible_multi_query"

    def __init__(self, settings: Settings) -> None:
        super().__init__(settings)
        self._max_subqueries = settings.query_rewrite_max_subqueries
        self._max_synonyms = settings.query_rewrite_max_synonyms

    def rewrite_plan(self, query: str) -> dict[str, Any]:
        response = self._post(
            self._url,
            headers={"Authorization": f"Bearer {self._api_key}"},
            json={
                "model": self._model,
                "messages": [
                    {
                        "role": "system",
                        "content": (
                            "你是知识库检索 Query Planner。只返回合法 JSON，不要 Markdown。"
                            "把自然语言问题改写成检索计划，不回答问题、不编造知识库外事实。"
                            '格式：{"main_query":"主检索短语",'
                            '"sub_queries":["必要的子问题"],'
                            '"synonyms":["同义检索词"]}。'
                            f"最多生成 {self._max_subqueries} 个子问题和 "
                            f"{self._max_synonyms} 个同义词；"
                            "简单问题可以为空。保留实体、版本、时间和约束。"
                        ),
                    },
                    {"role": "user", "content": query},
                ],
                "temperature": 0,
                "max_tokens": 256,
                "stream": False,
                "response_format": {"type": "json_object"},
            },
            timeout=self._timeout,
        )
        response.raise_for_status()
        payload = response.json()
        content = str(payload["choices"][0]["message"]["content"]).strip()
        try:
            parsed = json.loads(_strip_json_fence(content))
        except json.JSONDecodeError:
            # 兼容不支持 response_format 的旧网关：把纯文本当作主查询，仍保留原问题。
            return {"main_query": content, "sub_queries": [], "synonyms": []}
        if not isinstance(parsed, dict):
            raise ValueError("query planner response is not an object")
        return parsed

    def plan(self, query: str) -> tuple[str, list[str], list[str]]:
        payload = self.rewrite_plan(query)
        main_query = self._clean_query(payload.get("main_query"), query)
        sub_queries = self._clean_list(payload.get("sub_queries"), self._max_subqueries)
        synonyms = self._clean_list(payload.get("synonyms"), self._max_synonyms)
        return main_query, sub_queries, synonyms

    def _clean_query(self, value: object, fallback: str) -> str:
        text = re.sub(r"\s+", " ", str(value or "")).strip()
        if not text:
            text = re.sub(r"\s+", " ", fallback).strip()
        if len(text) > self._max_length:
            raise ValueError("invalid planner main query")
        return text

    def _clean_list(self, value: object, limit: int) -> list[str]:
        if value is None:
            return []
        if not isinstance(value, list):
            raise ValueError("invalid planner query list")
        cleaned: list[str] = []
        for item in value[:limit]:
            text = re.sub(r"\s+", " ", str(item or "")).strip()
            if text and len(text) <= self._max_length and text not in cleaned:
                cleaned.append(text)
        return cleaned


class QueryRewriter:
    """统一 Rewrite、缓存和回退策略，调用方始终获得可用于检索的文本。"""

    def __init__(self, settings: Settings, *, cache: CacheBackend | None) -> None:
        self._settings = settings
        self._cache = cache
        self._rule = RuleQueryRewriter()

    def rewrite(self, *, query: str, workspace_id: str) -> QueryRewriteResult:
        plan = self.plan(query=query, workspace_id=workspace_id)
        return QueryRewriteResult(
            query=plan.main_query,
            provider=plan.provider,
            cache_hit=plan.cache_hit,
            fallback=plan.fallback,
        )

    def plan(self, *, query: str, workspace_id: str) -> QueryRewritePlan:
        original = self._rule.rewrite(query)
        if not self._settings.query_rewrite_enabled:
            return self._build_plan(
                original,
                main_query=original,
                sub_queries=(),
                synonyms=(),
                provider="rule",
                cache_hit=False,
                fallback=False,
            )
        try:
            provider: OpenAICompatibleQueryRewriter
            if self._settings.query_rewrite_multi_query_enabled:
                provider = OpenAICompatibleQueryPlanner(self._settings)
            else:
                provider = OpenAICompatibleQueryRewriter(self._settings)
        except ValueError:
            return self._build_plan(
                original,
                main_query=original,
                sub_queries=(),
                synonyms=(),
                provider="rule",
                cache_hit=False,
                fallback=True,
            )
        cache_namespace = (
            "query_rewrite_plan"
            if self._settings.query_rewrite_multi_query_enabled
            else "query_rewrite"
        )
        cache_key = stable_cache_key(
            cache_namespace,
            workspace_id,
            provider.name,
            self._settings.llm_model,
            original,
        )
        if self._cache is not None:
            cached = self._cache.get_json(cache_key)
            restored = self._restore_plan(cached, original)
            if restored is not None:
                return self._build_plan(
                    original,
                    main_query=restored[0],
                    sub_queries=restored[1],
                    synonyms=restored[2],
                    provider=provider.name,
                    cache_hit=True,
                    fallback=False,
                )
        try:
            if isinstance(provider, OpenAICompatibleQueryPlanner):
                main_query, sub_queries, synonyms = provider.plan(original)
            else:
                main_query = provider.rewrite(original)
                sub_queries, synonyms = [], []
        except (httpx.HTTPError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            logger.warning("query_rewrite_fallback", error_type=type(exc).__name__)
            return self._build_plan(
                original,
                main_query=original,
                sub_queries=(),
                synonyms=(),
                provider="rule",
                cache_hit=False,
                fallback=True,
            )
        if self._cache is not None:
            self._cache.set_json(
                cache_key,
                {
                    "main_query": main_query,
                    "sub_queries": sub_queries,
                    "synonyms": synonyms,
                },
                ttl_seconds=self._settings.cache_default_ttl_seconds,
            )
        return self._build_plan(
            original,
            main_query=main_query,
            sub_queries=sub_queries,
            synonyms=synonyms,
            provider=provider.name,
            cache_hit=False,
            fallback=False,
        )

    def _build_plan(
        self,
        original: str,
        *,
        main_query: str,
        sub_queries: tuple[str, ...] | list[str],
        synonyms: tuple[str, ...] | list[str],
        provider: str,
        cache_hit: bool,
        fallback: bool,
    ) -> QueryRewritePlan:
        variants: list[str] = []
        for candidate in (main_query, original, *sub_queries, *synonyms):
            normalized = self._rule.rewrite(candidate)
            if normalized and normalized not in variants:
                variants.append(normalized)
            if len(variants) >= self._settings.query_rewrite_max_variants:
                break
        return QueryRewritePlan(
            original_query=original,
            main_query=self._rule.rewrite(main_query),
            sub_queries=tuple(
                self._rule.rewrite(item)
                for item in sub_queries[: self._settings.query_rewrite_max_subqueries]
            ),
            synonyms=tuple(
                self._rule.rewrite(item)
                for item in synonyms[: self._settings.query_rewrite_max_synonyms]
            ),
            queries=tuple(variants),
            provider=provider,
            cache_hit=cache_hit,
            fallback=fallback,
        )

    @staticmethod
    def _restore_plan(value: object, original: str) -> tuple[str, list[str], list[str]] | None:
        # 兼容旧版缓存中的字符串改写结果，升级期间不会直接丢弃缓存。
        if isinstance(value, str) and value.strip():
            return value.strip(), [], []
        if not isinstance(value, dict):
            return None
        main_query = str(value.get("main_query") or "").strip()
        sub_queries = value.get("sub_queries", [])
        synonyms = value.get("synonyms", [])
        if not main_query or not isinstance(sub_queries, list) or not isinstance(synonyms, list):
            return None
        return (
            main_query,
            [str(item).strip() for item in sub_queries if str(item).strip()],
            [str(item).strip() for item in synonyms if str(item).strip()],
        )


def _post_and_raise(
    url: str,
    *,
    headers: dict[str, str],
    json: dict[str, Any],
    timeout: float,
) -> httpx.Response:
    response = httpx.post(url, headers=headers, json=json, timeout=timeout)
    response.raise_for_status()
    return response


def _strip_json_fence(value: str) -> str:
    return re.sub(r"^```(?:json)?\s*|\s*```$", "", value.strip(), flags=re.IGNORECASE)
