"""检索链路的可降级缓存。

缓存不是正确性依赖：Redis 不可达、序列化异常或达到容量上限时，检索仍应返回正确结果。
键中只存查询哈希，不把用户问题直接写入 Redis key；Embedding 缓存值仅包含向量，不包含原始问题。
"""

from __future__ import annotations

import json
import threading
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass
from hashlib import sha256
from time import monotonic
from typing import Any, Protocol, cast

from app.core.config import Settings
from app.core.logging import get_logger

logger = get_logger(__name__)

_CACHE_REGISTRY: dict[tuple[object, ...], CacheBackend] = {}
_CACHE_REGISTRY_LOCK = threading.Lock()


class CacheBackend(Protocol):
    """JSON 兼容缓存契约，便于在 Redis 与本地实现之间无感切换。"""

    name: str

    def get_json(self, key: str) -> Any | None: ...

    def set_json(self, key: str, value: Any, *, ttl_seconds: int) -> None: ...


@dataclass(slots=True)
class _MemoryEntry:
    expires_at: float
    payload: str


class InMemoryCache:
    """开发和 Redis 故障回退使用的线程安全 TTL LRU 缓存，不能替代多实例共享缓存。"""

    name = "memory"

    def __init__(self, *, max_entries: int, clock: Callable[[], float] = monotonic) -> None:
        self._max_entries = max_entries
        self._clock = clock
        self._entries: OrderedDict[str, _MemoryEntry] = OrderedDict()
        self._lock = threading.Lock()

    def get_json(self, key: str) -> Any | None:
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                return None
            if entry.expires_at <= self._clock():
                del self._entries[key]
                return None
            self._entries.move_to_end(key)
            payload = entry.payload
        try:
            return json.loads(payload)
        except json.JSONDecodeError:
            # 内存内容损坏时清理该键，不能让缓存污染请求结果。
            with self._lock:
                self._entries.pop(key, None)
            return None

    def set_json(self, key: str, value: Any, *, ttl_seconds: int) -> None:
        payload = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        with self._lock:
            self._entries[key] = _MemoryEntry(self._clock() + ttl_seconds, payload)
            self._entries.move_to_end(key)
            while len(self._entries) > self._max_entries:
                self._entries.popitem(last=False)


class RedisCache:
    """Redis JSON 缓存适配器；一次连接失败后本实例熔断，交由内存回退处理。"""

    name = "redis"

    def __init__(self, *, redis_url: str, key_prefix: str) -> None:
        try:
            import redis
        except ImportError as exc:  # pragma: no cover - 依赖由部署环境决定
            raise RuntimeError("redis package is not installed") from exc
        self._client = redis.Redis.from_url(redis_url, decode_responses=True)
        self._key_prefix = key_prefix
        self._available = False
        self._available = self._ping()

    def get_json(self, key: str) -> Any | None:
        if not self._available:
            return None
        try:
            # redis-py 的泛型类型同时覆盖同步/异步客户端；此处构造的是同步客户端。
            payload = cast(str | None, self._client.get(self._key(key)))
            return json.loads(payload) if payload else None
        except Exception as exc:  # pragma: no cover - 真实 Redis 故障依赖运行环境
            self._disable(exc)
            return None

    def set_json(self, key: str, value: Any, *, ttl_seconds: int) -> None:
        if not self._available:
            return
        try:
            self._client.set(self._key(key), json.dumps(value, ensure_ascii=False), ex=ttl_seconds)
        except Exception as exc:  # pragma: no cover - 真实 Redis 故障依赖运行环境
            self._disable(exc)

    def _ping(self) -> bool:
        try:
            self._client.ping()
            return True
        except Exception as exc:  # pragma: no cover - 真实 Redis 故障依赖运行环境
            self._disable(exc)
            return False

    def _key(self, key: str) -> str:
        return f"{self._key_prefix}:{key}"

    def _disable(self, exc: Exception) -> None:
        if self._available:
            logger.warning("redis_cache_unavailable", error_type=type(exc).__name__)
        self._available = False


class FallbackCache:
    """优先 Redis，未命中或 Redis 熔断时使用本地缓存保障可用性。"""

    def __init__(self, primary: CacheBackend | None, fallback: InMemoryCache) -> None:
        self._primary = primary
        self._fallback = fallback
        self.name = primary.name if primary is not None else fallback.name

    def get_json(self, key: str) -> Any | None:
        if self._primary is not None:
            value = self._primary.get_json(key)
            if value is not None:
                return value
        return self._fallback.get_json(key)

    def set_json(self, key: str, value: Any, *, ttl_seconds: int) -> None:
        self._fallback.set_json(key, value, ttl_seconds=ttl_seconds)
        if self._primary is not None:
            self._primary.set_json(key, value, ttl_seconds=ttl_seconds)


def build_cache(settings: Settings) -> CacheBackend | None:
    """按运行配置创建并复用进程级缓存；关闭缓存时返回 ``None``。

    检索服务会在每个 HTTP 请求中实例化用例对象，如果这里每次都创建新的内存
    LRU，Redis 故障回退实际上无法跨请求命中。配置签名作为注册表键，模型/租户
    配置变化不会错误复用另一套缓存。
    """

    if not settings.cache_enabled:
        return None
    signature = (
        settings.redis_url,
        settings.redis_key_prefix,
        settings.cache_local_max_entries,
        settings.cache_default_ttl_seconds,
    )
    with _CACHE_REGISTRY_LOCK:
        cached = _CACHE_REGISTRY.get(signature)
        if cached is not None:
            return cached
        fallback = InMemoryCache(max_entries=settings.cache_local_max_entries)
        if not settings.redis_url:
            _CACHE_REGISTRY[signature] = fallback
            return fallback
        try:
            cache: CacheBackend = FallbackCache(
                RedisCache(redis_url=settings.redis_url, key_prefix=settings.redis_key_prefix),
                fallback,
            )
        except RuntimeError:
            logger.info("redis_cache_fallback", reason="redis_dependency_or_connection_unavailable")
            cache = fallback
        _CACHE_REGISTRY[signature] = cache
        return cache


def clear_cache_registry() -> None:
    """测试或配置热更新时清理进程级缓存；生产请求不应调用。"""

    with _CACHE_REGISTRY_LOCK:
        _CACHE_REGISTRY.clear()


def stable_cache_key(*parts: str) -> str:
    """将可能包含用户输入的键片段统一散列，避免在缓存键中暴露原文。"""

    digest = sha256("\x1f".join(parts).encode("utf-8")).hexdigest()
    return f"v1:{digest}"
