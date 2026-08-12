from app.rag.context_budget import (
    EvidenceBudgetBuilder,
    estimate_evidence_tokens,
    truncate_content,
)
from app.rag.retrieval import Evidence


def evidence(content: str, *, locator: str = "document:doc-1:chunk:1") -> Evidence:
    return Evidence(
        source_type="document_chunk",
        source_id="chunk-1",
        title="检索策略",
        content=content,
        score=1.0,
        locator=locator,
        source_url="https://example.com/docs/rag",
    )


def test_budget_builder_keeps_original_evidence_unchanged() -> None:
    original = evidence("短证据，包含可追溯引用。")

    selected, stats = EvidenceBudgetBuilder(max_tokens=512).build([original])

    assert selected == [original]
    assert stats.truncated_count == 0
    assert stats.original_characters == stats.selected_characters
    assert stats.estimated_tokens == estimate_evidence_tokens(original)


def test_budget_builder_truncates_generation_copy_and_keeps_tail_source() -> None:
    content = "开头说明。\n" + ("中间步骤与实现细节。\n" * 80) + "注意：仅允许 HTTPS。"
    original = evidence(content)

    selected, stats = EvidenceBudgetBuilder(max_tokens=256).build([original])

    assert selected
    assert selected[0].content != original.content
    assert "注意：仅允许 HTTPS" in selected[0].content
    assert "证据正文已按上下文预算截断" in selected[0].content
    assert stats.truncated_count == 1
    assert stats.estimated_tokens <= stats.max_tokens


def test_truncate_content_closes_markdown_code_fence() -> None:
    content = "```python\nprint('hello')\n" + ("print('more')\n" * 20) + "```"

    truncated = truncate_content(content, 80)

    assert len(truncated) > 80
    assert truncated.count("```") % 2 == 0
