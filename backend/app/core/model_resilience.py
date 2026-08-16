"""模型网关调用治理：并发闸门、可重试错误和指数退避。

模型调用通常比数据库操作慢很多，不能让多个文档任务或多路检索无限制地
同时占满网关。该模块只处理调用层可靠性，不改变上层 Provider 的业务回退策略。
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from threading import BoundedSemaphore, Lock
from typing import Any, TypeVar
from uuid import uuid4

import httpx

from app.core.config import Settings
from app.core.errors import ModelUnavailableError
from app.core.logging import get_logger
from app.core.telemetry import set_safe_attribute, traced_span

logger = get_logger(__name__)
T = TypeVar("T")

_semaphore_lock = Lock()
_semaphores: dict[int, BoundedSemaphore] = {}
_distributed_gate_lock = Lock()
_distributed_gates: dict[tuple[str, str], RedisModelConcurrencyGate] = {}


def _semaphore_for(limit: int) -> BoundedSemaphore:
    with _semaphore_lock:
        semaphore = _semaphores.get(limit)
        if semaphore is None:
            semaphore = BoundedSemaphore(limit)
            _semaphores[limit] = semaphore
        return semaphore


class RedisModelConcurrencyGate:
    """用 Redis ZSET 实现带租约的跨进程模型并发槽位。

    正常返回时调用方会主动释放槽位；Worker 崩溃时，过期分数会在下一次领取前被清理，
    防止某个异常实例永久占住模型配额。
    """

    def __init__(self, *, redis_url: str, key_prefix: str) -> None:
        try:
            import redis
        except ImportError as exc:  # pragma: no cover - 由部署镜像依赖保证
            raise RuntimeError("redis package is not installed") from exc
        # redis-py 的 from_url 类型同时覆盖同步/异步客户端；本适配器只使用同步 Redis。
        self._client: Any = redis.Redis.from_url(redis_url, decode_responses=True)
        self._key = f"{key_prefix}:model-concurrency"
        self._client.ping()

    def acquire(self, *, limit: int, timeout_seconds: float, lease_seconds: int) -> str | None:
        """原子清理过期租约并尝试取得一个槽位，超时返回 ``None``。"""

        token = uuid4().hex
        deadline = time.monotonic() + timeout_seconds
        script = (
            "local now = tonumber(ARGV[1]); "
            "local limit = tonumber(ARGV[2]); "
            "local lease = tonumber(ARGV[3]); "
            "redis.call('ZREMRANGEBYSCORE', KEYS[1], '-inf', now); "
            "if redis.call('ZCARD', KEYS[1]) >= limit then return 0 end; "
            "redis.call('ZADD', KEYS[1], now + lease, ARGV[4]); "
            "redis.call('PEXPIRE', KEYS[1], lease + 1000); "
            "return 1"
        )
        while True:
            now_ms = int(time.time() * 1000)
            acquired = int(
                self._client.eval(script, 1, self._key, now_ms, limit, lease_seconds * 1000, token)
            )
            if acquired == 1:
                return token
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            time.sleep(min(0.05, remaining))

    def release(self, token: str) -> None:
        self._client.zrem(self._key, token)


def _distributed_gate_for(settings: Settings) -> RedisModelConcurrencyGate:
    signature = (settings.redis_url, settings.redis_key_prefix)
    with _distributed_gate_lock:
        gate = _distributed_gates.get(signature)
        if gate is None:
            gate = RedisModelConcurrencyGate(
                redis_url=settings.redis_url,
                key_prefix=settings.redis_key_prefix,
            )
            _distributed_gates[signature] = gate
        return gate


def _distributed_lease_seconds(settings: Settings) -> int:
    """保证异常慢的可重试请求不会在完成前让 Redis 槽位过期。"""

    retry_budget = sum(
        retry_delay(settings, retry_index)
        for retry_index in range(settings.model_retry_attempts)
    )
    maximum_request_window = (
        settings.llm_timeout_seconds * (settings.model_retry_attempts + 1)
        + retry_budget
        + 30
    )
    return max(settings.model_distributed_lease_seconds, int(maximum_request_window) + 1)


@contextmanager
def distributed_model_call_slot(settings: Settings, *, operation: str) -> Iterator[None]:
    """在 Redis 可用时为所有 API/Worker 副本共享模型并发上限。

    Redis 连接异常不阻断模型调用，系统记录一次告警后退化为本进程闸门；只有 Redis
    正常响应但没有可用槽位时，才按统一的 ``MODEL_UNAVAILABLE`` 语义快速失败。
    """

    if not settings.model_distributed_concurrency_enabled or not settings.redis_url:
        yield
        return

    gate: RedisModelConcurrencyGate | None = None
    token: str | None = None
    try:
        gate = _distributed_gate_for(settings)
        token = gate.acquire(
            limit=settings.model_max_concurrency,
            timeout_seconds=settings.model_acquire_timeout_seconds,
            lease_seconds=_distributed_lease_seconds(settings),
        )
    except Exception as exc:  # pragma: no cover - 依赖真实 Redis 故障场景
        logger.warning("redis_model_concurrency_fallback", error_type=type(exc).__name__)
        yield
        return
    if token is None:
        logger.warning(
            "distributed_model_concurrency_limit",
            operation=operation,
            max_concurrency=settings.model_max_concurrency,
        )
        raise ModelUnavailableError(message="模型全局并发额度已用尽，请稍后重试。")
    try:
        yield
    finally:
        try:
            gate.release(token)
        except Exception as exc:  # pragma: no cover - 进程退出时依赖租约回收
            logger.warning("redis_model_concurrency_release_failed", error_type=type(exc).__name__)


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

    with distributed_model_call_slot(settings, operation=operation), traced_span(
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
