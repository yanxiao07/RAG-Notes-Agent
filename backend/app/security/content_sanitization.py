"""知识入库前的高置信凭证脱敏。

文档正文会进入向量库、缓存和回答上下文。用户把示例代码、导出的配置或日志导入知识库时，
其中的真实凭证不能继续沿检索链路扩散。本模块只处理格式高度明确的访问凭证，避免把普通
技术术语、变量名或环境变量表达式误删。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

REDACTED_SECRET = "[REDACTED_SECRET]"

# 常见厂商公开前缀具有较低误报率。仅匹配足够长的 token，避免误伤示例占位符。
_SECRET_PATTERNS = (
    re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"),
    re.compile(r"\btvly-[A-Za-z0-9_-]{20,}\b"),
)


@dataclass(frozen=True, slots=True)
class SanitizationResult:
    """脱敏结果只保留计数，调用方不得记录原始命中内容。"""

    content: str
    redacted_count: int


def sanitize_knowledge_content(content: str) -> SanitizationResult:
    """替换高置信访问凭证，保留文档其他结构以维持 Markdown/代码可读性。"""

    sanitized = content
    redacted_count = 0
    for pattern in _SECRET_PATTERNS:
        sanitized, count = pattern.subn(REDACTED_SECRET, sanitized)
        redacted_count += count
    return SanitizationResult(content=sanitized, redacted_count=redacted_count)
