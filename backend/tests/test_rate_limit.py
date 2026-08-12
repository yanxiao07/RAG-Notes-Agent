"""入口限流的固定窗口、降级与 HTTP 契约测试。"""

from __future__ import annotations

from fastapi.testclient import TestClient
from starlette.requests import Request

from app import main
from app.core.config import Settings
from app.core.rate_limit import (
    InMemoryFixedWindowRateLimiter,
    clear_rate_limiter_registry,
    rate_limit_scope,
    request_identity,
    scope_limit,
)


def make_request(
    *, path: str, method: str = "GET", headers: list[tuple[bytes, bytes]] | None = None
) -> Request:
    return Request(
        {
            "type": "http",
            "method": method,
            "path": path,
            "headers": headers or [],
            "client": ("127.0.0.1", 8080),
            "scheme": "http",
            "server": ("testserver", 80),
            "query_string": b"",
        }
    )


def test_memory_fixed_window_resets_and_returns_retry_after() -> None:
    limiter = InMemoryFixedWindowRateLimiter(clock=lambda: 10.5)

    first = limiter.increment(key="identity", limit=2, window_seconds=60, now=10.5)
    second = limiter.increment(key="identity", limit=2, window_seconds=60, now=10.5)
    blocked = limiter.increment(key="identity", limit=2, window_seconds=60, now=10.5)
    next_window = limiter.increment(key="identity", limit=2, window_seconds=60, now=60.1)

    assert first.allowed is True
    assert second.remaining == 0
    assert blocked.allowed is False
    assert blocked.retry_after_seconds == 49
    assert next_window.allowed is True
    assert next_window.remaining == 1


def test_scope_classifies_high_cost_endpoints_and_skips_health() -> None:
    assert rate_limit_scope(make_request(path="/health")) is None
    assert rate_limit_scope(
        make_request(path="/api/v1/agent/runs/research", method="POST")
    ) == "agent"
    assert rate_limit_scope(
        make_request(path="/api/v1/conversations/id/messages", method="POST")
    ) == "expensive"
    assert rate_limit_scope(make_request(path="/api/v1/knowledge-bases", method="POST")) == "write"
    assert rate_limit_scope(make_request(path="/api/v1/knowledge-bases")) == "api"


def test_identity_is_hashed_and_does_not_expose_ip_or_api_key() -> None:
    request = make_request(
        path="/api/v1/knowledge-bases",
        headers=[(b"x-api-key", b"user-secret")],
    )
    settings = Settings(auth_enabled=False, workspace_api_keys="")

    identity = request_identity(request, settings)

    assert len(identity) == 64
    assert "127.0.0.1" not in identity
    assert "user-secret" not in identity


def test_api_returns_standard_429_with_request_id_and_rate_headers(client: TestClient) -> None:
    original_enabled = main.settings.rate_limit_enabled
    original_api_limit = main.settings.rate_limit_api_requests
    try:
        main.settings.rate_limit_enabled = True
        main.settings.rate_limit_api_requests = 1
        clear_rate_limiter_registry()

        first = client.get("/api/v1/knowledge-bases")
        blocked = client.get("/api/v1/knowledge-bases")

        assert first.status_code == 200
        assert first.headers["X-RateLimit-Limit"] == "1"
        assert first.headers["X-RateLimit-Remaining"] == "0"
        assert blocked.status_code == 429
        assert blocked.headers["X-RateLimit-Remaining"] == "0"
        assert int(blocked.headers["Retry-After"]) >= 1
        assert blocked.json()["error"]["code"] == "RATE_LIMITED"
        assert blocked.json()["error"]["details"]["retryAfterSeconds"] >= 1
        assert blocked.json()["error"]["requestId"]
        assert blocked.headers["X-Request-ID"]
    finally:
        main.settings.rate_limit_enabled = original_enabled
        main.settings.rate_limit_api_requests = original_api_limit
        clear_rate_limiter_registry()


def test_health_check_is_exempt_from_rate_limit(client: TestClient) -> None:
    original_enabled = main.settings.rate_limit_enabled
    try:
        main.settings.rate_limit_enabled = True
        clear_rate_limiter_registry()

        assert client.get("/health").status_code == 200
        assert client.get("/health").status_code == 200
    finally:
        main.settings.rate_limit_enabled = original_enabled
        clear_rate_limiter_registry()


def test_scope_limit_reads_distinct_budget() -> None:
    settings = Settings(
        rate_limit_api_requests=100,
        rate_limit_write_requests=30,
        rate_limit_expensive_requests=8,
        rate_limit_agent_requests=4,
    )

    assert scope_limit(settings, "api") == 100
    assert scope_limit(settings, "write") == 30
    assert scope_limit(settings, "expensive") == 8
    assert scope_limit(settings, "agent") == 4
