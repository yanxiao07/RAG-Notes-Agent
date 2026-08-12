"""可选 OpenTelemetry Trace 基础设施。

Trace 只服务于部署侧诊断：仅采集固定操作名、路由模板、耗时、计数和错误类型。严禁把
问题、Prompt、证据正文、URL、密钥、用户或工作区标识写入 span 属性或事件。
"""

from __future__ import annotations

import threading
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from typing import Protocol

from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import SERVICE_NAME, Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.sdk.trace.sampling import ParentBasedTraceIdRatio
from opentelemetry.trace import Span, Status, StatusCode

from app.core.config import Settings

_SENSITIVE_ATTRIBUTE_PARTS = (
    "api_key",
    "authorization",
    "content",
    "document",
    "evidence",
    "key",
    "prompt",
    "query",
    "secret",
    "token",
    "url",
    "user",
    "workspace",
)
_provider_lock = threading.Lock()
_configured = False


class AttributeSpan(Protocol):
    """仅暴露属性写入能力的最小 Span 协议，便于验证脱敏策略。"""

    def set_attribute(self, key: str, value: str | int | float | bool) -> None: ...


def configure_telemetry(settings: Settings) -> None:
    """仅在显式启用并提供 OTLP 地址时配置导出器；默认完全不创建外部连接。"""

    global _configured
    if not settings.telemetry_enabled or not settings.telemetry_otlp_endpoint:
        return
    with _provider_lock:
        if _configured:
            return
        provider = TracerProvider(
            resource=Resource.create({SERVICE_NAME: settings.telemetry_service_name}),
            sampler=ParentBasedTraceIdRatio(settings.telemetry_sample_ratio),
        )
        provider.add_span_processor(
            BatchSpanProcessor(OTLPSpanExporter(endpoint=settings.telemetry_otlp_endpoint))
        )
        trace.set_tracer_provider(provider)
        _configured = True


@contextmanager
def traced_span(
    name: str,
    *,
    enabled: bool,
    attributes: Mapping[str, str | int | float | bool] | None = None,
) -> Iterator[Span | None]:
    """创建脱敏 span；禁用时零导出且不影响业务调用。"""

    if not enabled:
        yield None
        return
    tracer = trace.get_tracer("rag_notes_agent")
    with tracer.start_as_current_span(name) as span:
        for key, value in (attributes or {}).items():
            set_safe_attribute(span, key, value)
        try:
            yield span
        except Exception as exc:
            span.set_status(Status(StatusCode.ERROR, type(exc).__name__))
            # 异常对象可能携带请求或模型响应正文，只记录稳定错误类型。
            span.set_attribute("error.type", type(exc).__name__)
            raise


def set_safe_attribute(
    span: AttributeSpan | None, key: str, value: str | int | float | bool
) -> None:
    """拒绝疑似敏感属性；防止新增调用点意外将业务正文送往 Trace 系统。"""

    normalized = key.lower()
    if span is None or any(part in normalized for part in _SENSITIVE_ATTRIBUTE_PARTS):
        return
    span.set_attribute(key, value)
