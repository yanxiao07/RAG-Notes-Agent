"""可解释的检索元数据加权策略。

元数据加权只负责调整候选排序，不负责扩大知识库范围，也不替代关键词或向量召回。
将这段逻辑独立成策略对象，便于离线评测时关闭、调整权重，或在 PostgreSQL 检索器
中复用同一套规则，避免不同存储后端产生不可比较的排序行为。
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass

from app.core.config import Settings

TOKEN_PATTERN = re.compile(r"[\u4e00-\u9fff]|[a-zA-Z0-9_]{2,}")


@dataclass(frozen=True, slots=True)
class MetadataBoostPolicy:
    """根据标题、章节和来源类型对基础分数做小幅、可审计的加权。"""

    enabled: bool = True
    title_weight: float = 0.12
    section_weight: float = 0.08
    source_type_weight: float = 0.03
    max_boost: float = 0.20

    @classmethod
    def from_settings(cls, settings: Settings) -> MetadataBoostPolicy:
        """从运行配置创建策略，确保评测和线上检索使用同一组权重。"""

        return cls(
            enabled=settings.metadata_boost_enabled,
            title_weight=settings.metadata_title_boost,
            section_weight=settings.metadata_section_boost,
            source_type_weight=settings.metadata_source_type_boost,
            max_boost=settings.metadata_max_boost,
        )

    def adjust(
        self,
        base_score: float,
        *,
        query_tokens: set[str],
        title: str,
        metadata: Mapping[str, str] | None = None,
        source_type: str = "",
    ) -> tuple[float, bool]:
        """返回调整后的分数和是否实际产生加权。

        所有加权都以查询 token 的覆盖率为基础，并设置总增益上限，避免一条标题巧合
        命中就压过真正的正文语义匹配。返回布尔值供诊断统计使用，不将内部正文写入日志。
        """

        if not self.enabled or not query_tokens:
            return base_score, False
        metadata = metadata or {}
        boost = self.title_weight * _coverage(query_tokens, title)
        boost += self.section_weight * _coverage(query_tokens, metadata.get("section", ""))
        boost += self.source_type_weight * _coverage(query_tokens, source_type)
        boost = min(max(boost, 0.0), self.max_boost)
        if boost <= 0:
            return base_score, False
        return min(max(base_score + boost, 0.0), 1.0), True


def _coverage(query_tokens: set[str], value: str) -> float:
    """计算查询 token 在一个元数据字段中的覆盖率。"""

    if not value:
        return 0.0
    value_tokens = _tokenize(value)
    return len(query_tokens & value_tokens) / len(query_tokens) if value_tokens else 0.0


def _tokenize(value: str) -> set[str]:
    """保持策略自包含，避免依赖具体 Retriever 的分词实现形成反向耦合。"""

    return {token.lower() for token in TOKEN_PATTERN.findall(value)}
