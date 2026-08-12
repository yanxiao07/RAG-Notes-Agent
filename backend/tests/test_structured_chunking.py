"""结构化切分与文本清洗的回归测试。"""

from app.extensions.builtin import MarkdownFileParser, PlainTextParser, StructuredChunker


def test_structured_chunker_removes_control_characters_and_preserves_heading() -> None:
    parsed = PlainTextParser().parse(
        title="测试资料",
        content=(
            "# 检索策略\n\nRRF 用于融合关键词与向量结果。\x01\n\n"
            "## 重排\n\nCross-encoder 对候选集重排。"
        ),
    )

    chunks = StructuredChunker(max_characters=70, overlap_characters=12).chunk(parsed)

    assert chunks
    assert all("\x01" not in chunk.content for chunk in chunks)
    assert any(chunk.metadata.get("section") == "检索策略" for chunk in chunks)
    assert any("重排" in chunk.content for chunk in chunks)


def test_structured_chunker_keeps_overlap_for_long_sections() -> None:
    content = "# 长文\n\n" + "。 ".join(f"第 {index} 个结论" for index in range(30))
    chunks = StructuredChunker(max_characters=80, overlap_characters=16).chunk(
        PlainTextParser().parse(title="长文", content=content)
    )

    assert len(chunks) > 1
    assert chunks[0].content[-16:].strip() in chunks[1].content


def test_markdown_parser_and_chunker_preserve_typora_fenced_code() -> None:
    source = """---
title: Typora 示例
---

# 动态模型

```python
from langchain_openai import ChatOpenAI

model = ChatOpenAI(model=\"gpt-4o-mini\")
print(model)
```

内容审核中间件 <img src=\"C:/Users/demo/image.png\" />
"""

    parsed = MarkdownFileParser().parse_bytes(title="typora.md", content=source.encode())
    chunks = StructuredChunker(max_characters=120, overlap_characters=16).chunk(parsed)

    assert parsed.metadata["format"] == "markdown"
    assert "from langchain_openai import ChatOpenAI" in parsed.text
    assert any("```python" in chunk.content for chunk in chunks)
    assert any('ChatOpenAI(model="gpt-4o-mini")' in chunk.content for chunk in chunks)
    assert all(
        chunk.metadata.get("containsCode") == "true" for chunk in chunks if "```" in chunk.content
    )


def test_long_fenced_code_is_split_with_balanced_markers_without_loss() -> None:
    code_lines = "\n".join(f'print("line-{index}")' for index in range(40))
    source = f"# 代码\n\n```python\n{code_lines}\n```"
    chunks = StructuredChunker(max_characters=100, overlap_characters=16).chunk(
        PlainTextParser().parse(title="long.md", content=source)
    )

    merged = "\n".join(chunk.content for chunk in chunks)
    assert len(chunks) > 1
    assert all(chunk.content.count("```python") == 1 for chunk in chunks)
    assert all(chunk.content.rstrip().endswith("```") for chunk in chunks)
    assert all(f'print("line-{index}")' in merged for index in range(40))
