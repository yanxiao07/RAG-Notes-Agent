"""问答查询路由：在召回前决定是否需要访问知识库。

路由是 RAG 系统的第一道质量闸门：闲聊和系统操作问题不应浪费 Embedding
与召回成本，知识库事实问题必须进入检索，意图不完整的问题则先请求澄清。
该规则实现保持确定性，后续可以替换为轻量分类模型，但必须继续返回相同契约。
"""

import json
import re
from dataclasses import dataclass
from typing import Protocol

import httpx

from app.core.config import Settings
from app.core.logging import get_logger
from app.rag.cache import CacheBackend, stable_cache_key

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class QueryRoute:
    mode: str
    reason: str
    response_text: str | None = None
    router: str = "rule"
    confidence: float = 1.0
    cache_hit: bool = False

    @property
    def requires_rag(self) -> bool:
        return self.mode == "rag"


class RuleQueryRouter:
    """可替换的本地路由基线。

    分类顺序很重要：先识别明确的系统/社交问题，再识别会话记忆和不完整指代，
    最后将其余问题交给 RAG。只有明确命中规则才跳过检索，避免误把知识问题
    当作普通对话。
    """

    direct_phrases = {
        "你好",
        "您好",
        "嗨",
        "在吗",
        "谢谢",
        "感谢",
        "再见",
        "好的",
        "ok",
        "hello",
        "hi",
        "thanks",
    }

    # 这些问题不依赖知识库内容，直接由 Agent 或界面帮助回答即可。
    direct_patterns = (
        re.compile(r"^(你是谁|你是什么|你的身份|你能做什么|你有什么功能)[吗么呢?？!！。]*$"),
        re.compile(r"^(怎么使用|如何使用|使用帮助|操作帮助|帮助|help)[吗么呢?？!！。]*$"),
        re.compile(r"^(你)?(开心|高兴|快乐|累|饿|生气|有情绪|有感情|会做梦)[吗么呢?？!！。]*$"),
    )

    # 当前系统没有天气、新闻、股价等实时工具；这类问题必须明确说明能力边界，
    # 不能拿知识库中的示例代码或旧资料伪装成实时答案。
    realtime_patterns = (
        re.compile(r".*(天气|气温|温度|下雨|降雪|空气质量).*$"),
        re.compile(r".*(实时新闻|股价|股票价格|汇率|路况).*$"),
    )

    # 工作台的操作说明属于产品能力，不应因为知识库中恰好有“导入”一词而召回文档。
    # 如果用户明确说“文档中/资料中”，则交给 RAG 查询资料事实。
    operation_patterns = (
        re.compile(r"^(怎么|如何)(导入文档|上传文档|新建知识库|删除知识库|修改知识库名称)$"),
        re.compile(r"^(模型设置|设置|检索过程)(在哪里|在哪|怎么打开|如何打开)$"),
    )

    # 只处理用户明确指向当前会话/个人信息的表达，不把“文档中提到的我”误判为记忆。
    memory_patterns = (
        re.compile(
            r"(我刚才说|上一条消息|刚刚我提到|本次对话|记得我|我叫|我的名字|我喜欢|我之前说)"
        ),
    )

    # 无法确定指代对象时先澄清，避免无意义地扫描整个知识库。
    clarify_patterns = (
        re.compile(r"^我是谁$"),
        re.compile(r"^(这个|那个|它|这部分|那部分)(呢|是什么|怎么回事|什么意思)?[吗么?？。]*$"),
        re.compile(r"^(然后呢|接下来呢|还有吗|具体点|详细点)[吗么?？。]*$"),
    )

    def route(self, query: str) -> QueryRoute:
        normalized = re.sub(r"\s+", " ", query.strip().lower()).rstrip("。！!？?")
        if not normalized:
            return QueryRoute(mode="clarify", reason="empty_query")
        if normalized in self.direct_phrases:
            return QueryRoute(mode="direct", reason="social_or_acknowledgement")
        if not self._mentions_knowledge_source(normalized) and any(
            pattern.fullmatch(normalized) for pattern in self.realtime_patterns
        ):
            return QueryRoute(
                mode="direct",
                reason="unsupported_realtime_request",
                response_text=(
                    "当前工作台未接入天气、新闻等实时数据源，不能可靠回答这个实时问题。"
                ),
            )
        if any(pattern.fullmatch(normalized) for pattern in self.direct_patterns):
            return QueryRoute(
                mode="direct",
                reason=self._direct_reason_for(normalized),
                response_text=self._direct_response_for(normalized),
            )
        if not self._mentions_knowledge_source(normalized) and any(
            pattern.fullmatch(normalized) for pattern in self.operation_patterns
        ):
            return QueryRoute(mode="direct", reason="workspace_operation_instruction")
        if any(pattern.search(normalized) for pattern in self.memory_patterns):
            return QueryRoute(mode="memory", reason="conversation_or_user_memory")
        if any(pattern.fullmatch(normalized) for pattern in self.clarify_patterns):
            reason = "identity_ambiguous" if normalized == "我是谁" else "ambiguous_reference"
            response_text = (
                "你是指希望我记住的个人资料，还是当前会话中的身份？"
                if normalized == "我是谁"
                else None
            )
            return QueryRoute(mode="clarify", reason=reason, response_text=response_text)
        return QueryRoute(mode="rag", reason="knowledge_request")

    @staticmethod
    def _direct_response_for(query: str) -> str | None:
        """对高置信度系统问题返回策略文本，避免模型自由发挥造成错误身份描述。"""

        if query in {"你是谁", "你是什么", "你的身份"}:
            return "我是 RAG Notes Agent，负责基于当前知识库进行可追溯检索和回答。"
        if query in {"你能做什么", "你有什么功能"}:
            return "我可以导入和管理文档，执行混合检索，并基于可追溯证据生成回答。"
        if any(pattern.fullmatch(query) for pattern in RuleQueryRouter.direct_patterns[2:]):
            return "我是 AI，没有真实情绪或身体感受，但可以陪你交流和协助处理问题。"
        return None

    @staticmethod
    def _direct_reason_for(query: str) -> str:
        if any(pattern.fullmatch(query) for pattern in RuleQueryRouter.direct_patterns[2:]):
            return "assistant_emotion"
        return "system_capability_or_help"

    @staticmethod
    def _mentions_knowledge_source(query: str) -> bool:
        """识别显式资料范围，避免把“文档中如何……”误判为界面操作问题。"""

        return any(
            marker in query
            for marker in ("文档中", "文档里", "资料中", "资料里", "知识库中", "知识库里")
        )


@dataclass(frozen=True, slots=True)
class IntentDecision:
    """LLM Router 的最小结构化结果，禁止把自然语言直接当作路由。"""

    mode: str
    confidence: float


class QueryIntentClassifier(Protocol):
    name: str

    def classify(self, query: str) -> IntentDecision: ...


class OpenAICompatibleIntentClassifier:
    """OpenAI Chat Completions 兼容的意图分类器。

    这里只发送用户问题，不发送知识库正文；模型只能在固定枚举中选择路由，
    无法通过证据内容影响系统策略。
    """

    name = "openai_compatible"
    allowed_modes = frozenset({"direct", "memory", "clarify", "rag"})

    def __init__(self, settings: Settings) -> None:
        if settings.llm_provider != "openai_compatible":
            raise ValueError("query router requires an OpenAI-compatible LLM")
        if not settings.llm_api_key or not settings.llm_model:
            raise ValueError("query router llm is not configured")
        self._url = f"{settings.llm_base_url.rstrip('/')}/chat/completions"
        self._model = settings.llm_model
        self._api_key = settings.llm_api_key
        self._timeout = settings.query_router_timeout_seconds

    def classify(self, query: str) -> IntentDecision:
        response = httpx.post(
            self._url,
            headers={"Authorization": f"Bearer {self._api_key}"},
            json={
                "model": self._model,
                "messages": [
                    {
                        "role": "system",
                        "content": (
                            "你是企业知识库问答的意图分类器。只输出 JSON，不要 Markdown。"
                            "JSON 必须是 {'route':'direct|memory|clarify|rag',"
                            "'confidence':0到1之间的数字}。"
                            "direct=身份、能力、寒暄、工作台操作；"
                            "memory=当前会话或用户明确保存的个人信息；"
                            "clarify=指代或意图不完整；"
                            "rag=知识库事实、文档内容、需要引用或时效性信息。"
                            "不确定时选择 rag。"
                        ),
                    },
                    {"role": "user", "content": query},
                ],
                "temperature": 0,
                "max_tokens": 64,
                "stream": False,
                "response_format": {"type": "json_object"},
            },
            timeout=self._timeout,
        )
        response.raise_for_status()
        payload = response.json()
        content = str(payload["choices"][0]["message"]["content"]).strip()
        parsed = json.loads(_strip_json_fence(content))
        mode = parsed.get("route")
        confidence = parsed.get("confidence")
        if mode not in self.allowed_modes or not isinstance(confidence, (int, float)):
            raise ValueError("invalid query route response")
        normalized_confidence = float(confidence)
        if not 0.0 <= normalized_confidence <= 1.0:
            raise ValueError("invalid query route confidence")
        return IntentDecision(mode=mode, confidence=normalized_confidence)


class HybridQueryRouter:
    """规则策略 + 可选 LLM 灰区分类的级联路由。

    规则层拥有 direct/memory/clarify 的高置信度决策权；LLM 只处理默认会进入
    RAG 的灰区。任何异常、低置信度或显式知识库请求都安全回退到 RAG。
    """

    force_rag_markers = (
        "文档中",
        "文档里",
        "资料中",
        "资料里",
        "知识库中",
        "知识库里",
        "根据文档",
        "原文",
        "引用",
        "最新版本",
    )

    def __init__(
        self,
        *,
        rule_router: RuleQueryRouter | None = None,
        classifier: QueryIntentClassifier | None = None,
    ) -> None:
        self.rule_router = rule_router or RuleQueryRouter()
        self.classifier = classifier

    def route(
        self,
        query: str,
        *,
        settings: Settings,
        workspace_id: str,
        cache: CacheBackend | None = None,
    ) -> QueryRoute:
        rule_route = self.rule_router.route(query)
        if rule_route.mode != "rag" or not settings.query_router_enabled:
            return rule_route
        normalized = re.sub(r"\s+", " ", query.strip().lower())
        if any(marker in normalized for marker in self.force_rag_markers):
            return QueryRoute(
                mode="rag",
                reason="explicit_knowledge_request",
                router="rule",
                confidence=1.0,
            )
        try:
            classifier = self.classifier or OpenAICompatibleIntentClassifier(settings)
        except ValueError:
            return self._fallback(rule_route, reason="llm_router_not_configured")
        cache_key = stable_cache_key(
            "query_route",
            workspace_id,
            classifier.name,
            settings.llm_model,
            normalized,
        )
        if cache is not None:
            cached = cache.get_json(cache_key)
            restored = self._restore_cached(cached, settings.query_router_confidence_threshold)
            if restored is not None:
                return QueryRoute(
                    mode=restored.mode,
                    reason=f"llm_{restored.mode}",
                    router=classifier.name,
                    confidence=restored.confidence,
                    cache_hit=True,
                )
        try:
            decision = classifier.classify(query)
        except (httpx.HTTPError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            logger.warning("query_router_fallback", error_type=type(exc).__name__)
            return self._fallback(rule_route, reason="llm_router_error")
        if cache is not None:
            cache.set_json(
                cache_key,
                {"mode": decision.mode, "confidence": decision.confidence},
                ttl_seconds=settings.cache_default_ttl_seconds,
            )
        if decision.confidence < settings.query_router_confidence_threshold:
            return self._fallback(rule_route, reason="llm_router_low_confidence")
        return QueryRoute(
            mode=decision.mode,
            reason=f"llm_{decision.mode}",
            router=classifier.name,
            confidence=decision.confidence,
        )

    @staticmethod
    def _fallback(rule_route: QueryRoute, *, reason: str) -> QueryRoute:
        return QueryRoute(
            mode="rag",
            reason=reason,
            router="rule_fallback",
            confidence=0.0,
            response_text=rule_route.response_text,
        )

    @staticmethod
    def _restore_cached(value: object, threshold: float) -> IntentDecision | None:
        if not isinstance(value, dict):
            return None
        mode = value.get("mode")
        confidence = value.get("confidence")
        if mode not in OpenAICompatibleIntentClassifier.allowed_modes:
            return None
        if not isinstance(confidence, (int, float)) or float(confidence) < threshold:
            return None
        return IntentDecision(mode=mode, confidence=float(confidence))


def _strip_json_fence(content: str) -> str:
    """兼容少数网关无视 JSON 模式而返回 ```json 包裹的情况。"""

    return re.sub(r"^```(?:json)?\s*|\s*```$", "", content, flags=re.IGNORECASE).strip()
