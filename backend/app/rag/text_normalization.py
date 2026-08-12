"""文档进入索引或模型上下文前的统一文本清洗。"""

import re
import unicodedata

# 保留换行与制表符，其余 C0/C1 控制字符通常来自 PDF/OCR 的版式占位符。
_UNSAFE_CONTROL_CHARACTERS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]")
_ZERO_WIDTH_CHARACTERS = re.compile(r"[\u200b-\u200f\ufeff]")
_MULTIPLE_BLANK_LINES = re.compile(r"\n[ \t]*\n(?:[ \t]*\n)+")


def normalize_document_text(value: str) -> str:
    """清理不可见字符，同时保留标题、段落与代码的基本阅读结构。"""

    normalized = unicodedata.normalize("NFKC", value).replace("\r\n", "\n").replace("\r", "\n")
    normalized = _UNSAFE_CONTROL_CHARACTERS.sub(" ", normalized)
    normalized = _ZERO_WIDTH_CHARACTERS.sub("", normalized)
    normalized = "\n".join(line.rstrip() for line in normalized.split("\n"))
    return _MULTIPLE_BLANK_LINES.sub("\n\n", normalized).strip()


def compact_for_llm(value: str, *, limit: int) -> str:
    """生成摘要/导图时压缩无效版式字符，避免把原始 Markdown 噪声交给模型。"""

    compact = normalize_document_text(value)
    compact = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", compact)
    compact = re.sub(r"\s+", " ", compact).strip()
    return compact[:limit]
