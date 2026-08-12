"""API 固定窗口限流：Redis 优先，进程内存兜底。

限流属于入口治理而不是业务规则。Redis 键和内存桶均只保存身份摘要，不能把 API Key、
工作区 ID、IP 或请求正文作为可读文本写入缓存、日志或审计事件。
"""

from __future__ import annotations

import hmac
import threading
from collections.abc import Callable
from dataclasses import dataclass
from hashlib import sha256
from time import time
from typing import Protocol

from fastapi import Request

from app.core.config import Settings
from app.core.logging import get_logger

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class RateLimitDecision:
    allowed: bool
    limit: int
    remaining: int
    retry_after_seconds: int
    backend: str


class RateLimitBackend(Protocol):
    name: str

    def increment(
        self, *, key: str, limit: int, window_seconds: int, now: float
    ) -> RateLimitDecision: ...


class InMemoryFixedWindowRateLimiter:
    """Redis 不可用时的单进程回退，不适用于多实例配额一致性。"""

    name = "memory"

    def __init__(self, *, clock: Callable[[], float] = time) -> None:
        self._clock = clock
        self._buckets: dict[tuple[str, int], int] = {}
        self._lock = threading.Lock()

    def increment(
        self, *, key: str, limit: int, window_seconds: int, now: float | None = None
    ) -> RateLimitDecision:
        effective_now = self._clock() if now is None else now
        window_start = int(effective_now // window_seconds) * window_seconds
        bucket_key = (key, window_start)
        retry_after = max(1, int(window_start + window_seconds - effective_now))
        with self._lock:
            # 仅保留当前及上一窗口，防止长时间运行的开发实例无界增长。
            earliest_window_start = window_start - window_seconds
            self._buckets = {
                existing_key: count
                for existing_key, count in self._buckets.items()
                if existing_key[1] >= earliest_window_start
            }
            count = self._buckets.get(bucket_key, 0) + 1
            self._buckets[bucket_key] = count
        return RateLimitDecision(
            allowed=count <= limit,
            limit=limit,
            remaining=max(limit - count, 0),
            retry_after_seconds=retry_after,
            backend=self.name,
        )


class RedisFixedWindowRateLimiter:
    """使用 Redis INCR + EXPIRE 实现跨实例固定窗口计数。"""

    name = "redis"

    def __init__(self, *, redis_url: str, key_prefix: str) -> None:
        try:
            import redis
        except ImportError as exc:  # pragma: no cover - 依赖由部署镜像决定
            raise RuntimeError("redis package is not installed") from exc
        self._client = redis.Redis.from_url(redis_url, decode_responses=True)
        self._key_prefix = key_prefix
        self._available = self._ping()

    def increment(
        self, *, key: str, limit: int, window_seconds: int, now: float
    ) -> RateLimitDecision:
        if not self._available:
            raise RuntimeError("redis rate limiter is unavailable")
        window_start = int(now // window_seconds) * window_seconds
        retry_after = max(1, int(window_start + window_seconds - now))
        redis_key = f"{self._key_prefix}:rate-limit:{key}:{window_start}"
        try:
            count = int(str(self._client.incr(redis_key)))
            if count == 1:
                # 取整后加一秒，避免窗口边界出现无 TTL 的计数键。
                self._client.expire(redis_key, retry_after + 1)
        except Exception as exc:  # pragma: no cover - 真实 Redis 故障依赖运行环境
            self._disable(exc)
            raise RuntimeError("redis rate limiter is unavailable") from exc
        return RateLimitDecision(
            allowed=count <= limit,
            limit=limit,
            remaining=max(limit - count, 0),
            retry_after_seconds=retry_after,
            backend=self.name,
        )

    def _ping(self) -> bool:
        try:
            self._client.ping()
            return True
        except Exception as exc:  # pragma: no cover - 真实 Redis 故障依赖运行环境
            logger.info("redis_rate_limit_fallback", error_type=type(exc).__name__)
            return False

    def _disable(self, exc: Exception) -> None:
        if self._available:
            logger.warning("redis_rate_limit_unavailable", error_type=type(exc).__name__)
        self._available = False


class FallbackRateLimiter:
    """Redis 故障时本请求立即退到内存桶，限流不会成为 API 故障源。"""

    def __init__(self, *, primary: RateLimitBackend | None, fallback: RateLimitBackend) -> None:
        self._primary = primary
        self._fallback = fallback

    def check(self, *, key: str, limit: int, window_seconds: int) -> RateLimitDecision:
        now = time()
        if self._primary is not None:
            try:
                return self._primary.increment(
                    key=key,
                    limit=limit,
                    window_seconds=window_seconds,
                    now=now,
                )
            except RuntimeError:
                pass
        return self._fallback.increment(
            key=key,
            limit=limit,
            window_seconds=window_seconds,
            now=now,
        )


_REGISTRY: dict[tuple[str, str], FallbackRateLimiter] = {}
_REGISTRY_LOCK = threading.Lock()


def build_rate_limiter(settings: Settings) -> FallbackRateLimiter:
    """按 Redis 连接配置复用限流器，避免每个请求重复连接 Redis。"""

    signature = (settings.redis_url, settings.redis_key_prefix)
    with _REGISTRY_LOCK:
        existing = _REGISTRY.get(signature)
        if existing is not None:
            return existing
        fallback = InMemoryFixedWindowRateLimiter()
        primary: RateLimitBackend | None = None
        if settings.redis_url:
            try:
                primary = RedisFixedWindowRateLimiter(
                    redis_url=settings.redis_url,
                    key_prefix=settings.redis_key_prefix,
                )
            except RuntimeError:
                logger.info("redis_rate_limit_fallback", reason="redis_unavailable")
        limiter = FallbackRateLimiter(primary=primary, fallback=fallback)
        _REGISTRY[signature] = limiter
        return limiter


def clear_rate_limiter_registry() -> None:
    """仅供测试与配置热更新清理进程内单例。"""

    with _REGISTRY_LOCK:
        _REGISTRY.clear()


def request_identity(request: Request, settings: Settings) -> str:
    """返回不可逆身份摘要；仅认证成功候选使用工作区维度。"""

    workspace_id = _workspace_for_valid_api_key(request, settings)
    if workspace_id is not None:
        source = f"workspace:{workspace_id}"
    else:
        source = f"ip:{request.client.host if request.client else 'unknown'}"
    return sha256(source.encode("utf-8")).hexdigest()


def _workspace_for_valid_api_key(request: Request, settings: Settings) -> str | None:
    """只用常量时间比较解析认证绑定，不把 Header 中的 Key 写入任何状态。"""

    if not settings.auth_enabled:
        return None
    supplied_key = request.headers.get("X-API-Key")
    if not supplied_key:
        return None
    for item in settings.workspace_api_keys.split(","):
        if "=" not in item:
            continue
        workspace_id, configured_key = (part.strip() for part in item.split("=", 1))
        if workspace_id and configured_key and hmac.compare_digest(supplied_key, configured_key):
            return workspace_id
    return None


def rate_limit_scope(request: Request) -> str | None:
    """按资源消耗而非业务名称分配配额，健康、文档和预检请求不参与计数。"""

    if request.method == "OPTIONS" or request.url.path in {
        "/health",
        "/metrics",
        "/docs",
        "/openapi.json",
    }:
        return None
    path = request.url.path
    if path in {"/api/v1/agent/runs/research", "/api/v1/agent/runs/research/stream"}:
        return "agent"
    if path.endswith("/messages") or path.endswith("/mind-maps/generate"):
        return "expensive"
    if request.method in {"POST", "PUT", "PATCH", "DELETE"} and path != "/api/v1/retrieval/search":
        return "write"
    return "api"


def scope_limit(settings: Settings, scope: str) -> int:
    return {
        "api": settings.rate_limit_api_requests,
        "write": settings.rate_limit_write_requests,
        "expensive": settings.rate_limit_expensive_requests,
        "agent": settings.rate_limit_agent_requests,
    }[scope]
