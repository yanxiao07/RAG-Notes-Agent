"""生成前的证据预算与确定性截断。

检索结果是审计事实，不能为了适配模型上下文而修改原始 Evidence。此模块只生成一份
发送给 LLM 的受限副本，并保留高相关证据的顺序、标题和来源定位。Token 估算采用不依赖
厂商 tokenizer 的保守规则，正式接入模型后仍可替换为对应 tokenizer 实现。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace

from app.rag.retrieval import Evidence

TRUNCATION_MARKER = "\n\n[证据正文已按上下文预算截断，原文仍可通过引用定位查看。]\n"


@dataclass(frozen=True, slots=True)
class ContextBudgetStats:
    """向诊断和审计公开的预算统计，不包含证据正文。"""

    max_tokens: int
    original_count: int
    selected_count: int
    original_characters: int
    selected_characters: int
    estimated_tokens: int
    truncated_count: int
    truncated_characters: int


class EvidenceBudgetBuilder:
    """按顺序选择并压缩证据，保证发送给模型的上下文不超过预算。"""

    def __init__(self, *, max_tokens: int = 4_096) -> None:
        if max_tokens < 128:
            raise ValueError("证据上下文预算不能小于 128 tokens。")
        self.max_tokens = max_tokens

    def build(self, evidences: list[Evidence]) -> tuple[list[Evidence], ContextBudgetStats]:
        original_characters = sum(len(item.content) for item in evidences)
        original_count = len(evidences)
        selected: list[Evidence] = []
        used_tokens = 0
        truncated_count = 0
        truncated_characters = 0

        for evidence in evidences:
            remaining_tokens = self.max_tokens - used_tokens
            if remaining_tokens < 32:
                break
            full_cost = estimate_evidence_tokens(evidence)
            candidate = evidence
            if full_cost > remaining_tokens:
                content_budget = max(64, _tokens_to_characters(remaining_tokens - 24))
                shortened = truncate_content(evidence.content, content_budget)
                candidate = replace(evidence, content=shortened)
                # 极短预算下，定位信息比正文更重要；再次压缩正文而不丢掉证据对象。
                while (
                    estimate_evidence_tokens(candidate) > remaining_tokens
                    and len(candidate.content) > 64
                ):
                    candidate = replace(
                        candidate,
                        content=truncate_content(
                            candidate.content,
                            max(64, len(candidate.content) - 32),
                        ),
                    )
                if candidate.content != evidence.content:
                    truncated_count += 1
                    truncated_characters += max(0, len(evidence.content) - len(candidate.content))
            cost = estimate_evidence_tokens(candidate)
            if cost > remaining_tokens:
                break
            selected.append(candidate)
            used_tokens += cost

        stats = ContextBudgetStats(
            max_tokens=self.max_tokens,
            original_count=original_count,
            selected_count=len(selected),
            original_characters=original_characters,
            selected_characters=sum(len(item.content) for item in selected),
            estimated_tokens=used_tokens,
            truncated_count=truncated_count,
            truncated_characters=truncated_characters,
        )
        return selected, stats


def estimate_tokens(text: str) -> int:
    """使用中英文混合文本的保守估算，避免引入 tokenizer 运行时依赖。"""

    ascii_characters = sum(character.isascii() for character in text)
    non_ascii_characters = len(text) - ascii_characters
    return max(1, math.ceil(ascii_characters / 4 + non_ascii_characters * 1.5))


def estimate_evidence_tokens(evidence: Evidence) -> int:
    """估算 Provider 证据块的标题、定位、来源和正文总 Token 数。"""

    source = evidence.source_url or ""
    envelope = f"[{evidence.locator}] {evidence.title} {source}\n"
    return estimate_tokens(envelope) + estimate_tokens(evidence.content) + 12


def truncate_content(content: str, max_characters: int) -> str:
    """头尾保留的确定性截断，并修复未闭合的 Markdown 代码围栏。"""

    if len(content) <= max_characters:
        return content
    usable = max(32, max_characters - len(TRUNCATION_MARKER))
    head_size = max(16, int(usable * 0.72))
    tail_size = max(16, usable - head_size)
    head = content[:head_size].rstrip()
    tail = content[-tail_size:].lstrip()
    shortened = head + TRUNCATION_MARKER + tail
    # 极短正文可能小于截断标记本身；此时宁可返回有限前缀，也不让预算副本膨胀。
    if len(shortened) >= len(content):
        return content[: max(1, max_characters)]
    if shortened.count("```") % 2:
        shortened = shortened.rstrip() + "\n```"
    return shortened


def _tokens_to_characters(tokens: int) -> int:
    """按中英文混合文本的保守比例换算正文字符预算。"""

    return max(64, int(tokens / 1.5))
