"""检索后证据支持门，防止语义近似候选被伪装成可回答事实。

该门禁不是以单一向量分数阈值判断真伪：不同 Embedding、RRF 和 Reranker 的分数没有稳定量纲。
它只对局部问题做保守的字面支持检查，关系/全局问题仍交由 GraphRAG 与生成层的引用约束处理。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.rag.retrieval import Evidence

_LATIN_TOKEN = re.compile(r"[a-zA-Z0-9_]{2,}")
_CJK_SEQUENCE = re.compile(r"[\u4e00-\u9fff]+")
_GENERIC_SIGNALS = frozenset(
    {
        "什么",
        "怎么",
        "如何",
        "多少",
        "是否",
        "可以",
        "需要",
        "应该",
        "公司",
        "知识",
        "文档",
        "资料",
        "问题",
        "当前",
        "这个",
        "那个",
        "标准",
        "流程",
        "规定",
        "采购",
        "编号",
    }
)


@dataclass(frozen=True, slots=True)
class AnswerabilityDecision:
    is_answerable: bool
    reason: str
    matched_signals: int


class RetrievalAnswerabilityGate:
    """根据问题中的有效实体/短语是否被候选证据显式支撑，决定保留还是拒答。"""

    @classmethod
    def decide(
        cls,
        *,
        query: str,
        evidences: list[Evidence],
        enabled: bool,
        query_profile: str,
    ) -> AnswerabilityDecision:
        if not enabled:
            return AnswerabilityDecision(True, "disabled", 0)
        if not evidences:
            return AnswerabilityDecision(False, "empty_candidates", 0)
        if query_profile != "local":
            # 关系/全局问题的支撑词可能只出现在中间实体，不能用局部短语门禁误杀。
            return AnswerabilityDecision(True, "graph_profile_exempt", 0)
        signals = cls._signals(query)
        if not signals:
            # 过短或仅由泛化词构成的问题没有足够依据进行拒答，保留给上层澄清与证据约束。
            return AnswerabilityDecision(True, "insufficient_query_signals", 0)
        corpus = "\n".join(f"{item.title}\n{item.content}".lower() for item in evidences)
        matched = sum(signal in corpus for signal in signals)
        if matched:
            return AnswerabilityDecision(True, "lexical_support", matched)
        return AnswerabilityDecision(False, "no_lexical_support", 0)

    @staticmethod
    def _signals(query: str) -> set[str]:
        normalized = query.lower()
        signals = set(_LATIN_TOKEN.findall(normalized))
        for sequence in _CJK_SEQUENCE.findall(normalized):
            signals.update(sequence[index : index + 2] for index in range(len(sequence) - 1))
        return {signal for signal in signals if signal not in _GENERIC_SIGNALS}
