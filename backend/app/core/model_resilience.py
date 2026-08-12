"""模型网关调用治理：并发闸门、可重试错误和指数退避。

模型调用通常比数据库操作慢很多，不能让多个文档任务或多路检索无限制地
同时占满网关。该模块只处理调用层可靠性，不改变上层 Provider 的业务回退策略。
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from threading import BoundedSemaphore, Lock
from typing import TypeVar

import httpx

from app.core.config import Settings
from app.core.errors import ModelUnavailableError
from app.core.logging import get_logger
from app.core.telemetry import set_safe_attribute, traced_span

logger = get_logger(__name__)
T = TypeVar("T")

_semaphore_lock = Lock()
_semaphores: dict[int, BoundedSemaphore] = {}


def _semaphore_for(limit: int) -> BoundedSemaphore:
    with _semaphore_lock:
        semaphore = _semaphores.get(limit)
        if semaphore is None:
            semaphore = BoundedSemaphore(limit)
            _semaphores[limit] = semaphore
        return semaphore


@contextmanager
def model_call_slot(settings: Settings, *, operation: str) -> Iterator[None]:
    """限制单进程内同时进行的模型调用数量，超时则快速失败并触发上层降级。"""

    semaphore = _semaphore_for(settings.model_max_concurrency)
    acquired = semaphore.acquire(timeout=settings.model_acquire_timeout_seconds)
    if not acquired:
        logger.warning(
            "model_concurrency_limit",
            operation=operation,
            max_concurrency=settings.model_max_concurrency,
        )
        raise ModelUnavailableError(message="模型并发额度已用尽，请稍后重试。")
    try:
        yield
    finally:
        semaphore.release()


def is_retryable_http_error(exc: httpx.HTTPError) -> bool:
    """只对网络抖动、超时、限流和服务端错误重试，4xx 参数错误直接失败。"""

    if isinstance(exc, (httpx.TimeoutException, httpx.TransportError)):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        status_code = exc.response.status_code
        return status_code == 408 or status_code == 409 or status_code == 429 or status_code >= 500
    return False


def retry_delay(settings: Settings, retry_index: int) -> float:
    """计算第 retry_index 次重试前的退避时间，并限制最大值。"""

    return min(
        settings.model_retry_max_seconds,
        settings.model_retry_base_seconds * (2 ** max(retry_index, 0)),
    )


def call_with_model_resilience(
    request: Callable[[], T],
    *,
    settings: Settings,
    operation: str,
) -> T:
    """在并发闸门内执行同步模型请求，并对可恢复 HTTP 错误指数退避。"""

    with traced_span(
        "rag.model.call",
        enabled=settings.telemetry_enabled,
        attributes={"rag.model.operation": operation},
    ) as span, model_call_slot(settings, operation=operation):
        for retry_index in range(settings.model_retry_attempts + 1):
            try:
                result = request()
                set_safe_attribute(span, "rag.model.retry_count", retry_index)
                return result
            except httpx.HTTPError as exc:
                if (
                    not is_retryable_http_error(exc)
                    or retry_index >= settings.model_retry_attempts
                ):
                    raise
                delay = retry_delay(settings, retry_index)
                logger.warning(
                    "model_call_retry",
                    operation=operation,
                    retry_index=retry_index + 1,
                    delay_seconds=delay,
                    error_type=type(exc).__name__,
                )
                time.sleep(delay)
    raise RuntimeError("model request did not return")
