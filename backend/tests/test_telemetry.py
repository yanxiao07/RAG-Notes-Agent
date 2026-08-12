"""Trace 脱敏边界测试：任何调用点都不得把业务正文或凭证送入外部观测系统。"""

from app.core.telemetry import set_safe_attribute, traced_span


class RecordingSpan:
    """替代 SDK Span，直接验证允许写入的属性集合。"""

    def __init__(self) -> None:
        self.attributes: dict[str, object] = {}

    def set_attribute(self, key: str, value: object) -> None:
        self.attributes[key] = value


def test_safe_span_attribute_rejects_sensitive_names() -> None:
    span = RecordingSpan()

    set_safe_attribute(span, "rag.retrieval.final_candidates", 4)
    set_safe_attribute(span, "rag.query.text", "不应写入")
    set_safe_attribute(span, "http.url", "https://private.example")
    set_safe_attribute(span, "api_key", "secret")
    set_safe_attribute(span, "workspace.id", "workspace-1")

    assert span.attributes == {"rag.retrieval.final_candidates": 4}


def test_disabled_trace_does_not_create_span() -> None:
    with traced_span("rag.test", enabled=False) as span:
        assert span is None
