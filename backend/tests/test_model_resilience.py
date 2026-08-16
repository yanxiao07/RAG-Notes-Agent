"""模型网关调用治理的纯函数与重试策略测试。"""

import httpx
import pytest

from app.core.config import Settings
from app.core.errors import ModelUnavailableError
from app.core.model_resilience import (
    _distributed_lease_seconds,
    call_with_model_resilience,
    distributed_model_call_slot,
    retry_delay,
)


def _status_error(status_code: int) -> httpx.HTTPStatusError:
    request = httpx.Request("POST", "https://model.example.test/v1/chat/completions")
    response = httpx.Response(status_code, request=request)
    return httpx.HTTPStatusError("gateway error", request=request, response=response)


def test_model_call_retries_transient_status_with_exponential_backoff(monkeypatch) -> None:
    settings = Settings(
        model_retry_attempts=2,
        model_retry_base_seconds=0.25,
        model_retry_max_seconds=1.0,
    )
    sleeps: list[float] = []
    monkeypatch.setattr("app.core.model_resilience.time.sleep", sleeps.append)
    attempts = 0

    def flaky_request() -> str:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise _status_error(503)
        return "ok"

    assert (
        call_with_model_resilience(flaky_request, settings=settings, operation="test_gateway")
        == "ok"
    )
    assert attempts == 3
    assert sleeps == [0.25, 0.5]


def test_model_call_does_not_retry_client_configuration_error(monkeypatch) -> None:
    settings = Settings(model_retry_attempts=3)
    attempts = 0

    def invalid_request() -> str:
        nonlocal attempts
        attempts += 1
        raise _status_error(400)

    monkeypatch.setattr("app.core.model_resilience.time.sleep", lambda _: None)
    try:
        call_with_model_resilience(invalid_request, settings=settings, operation="test_gateway")
    except httpx.HTTPStatusError:
        pass
    else:
        raise AssertionError("400 should not be retried")
    assert attempts == 1


def test_retry_delay_is_capped() -> None:
    settings = Settings(model_retry_base_seconds=2.0, model_retry_max_seconds=5.0)
    assert retry_delay(settings, 0) == 2.0
    assert retry_delay(settings, 1) == 4.0
    assert retry_delay(settings, 2) == 5.0


def test_distributed_lease_covers_model_timeout_and_retries() -> None:
    settings = Settings(
        llm_timeout_seconds=120,
        model_retry_attempts=2,
        model_retry_base_seconds=1,
        model_retry_max_seconds=2,
        model_distributed_lease_seconds=30,
    )
    # 120 * 3 + (1 + 2) + 30 秒安全余量。
    assert _distributed_lease_seconds(settings) == 394


def test_distributed_model_slot_rejects_when_global_quota_is_full(monkeypatch) -> None:
    """Redis 正常可用但没有全局槽位时，不能退化为无限制调用。"""

    class FullGate:
        def acquire(self, **_: object) -> None:
            return None

    settings = Settings(redis_url="redis://redis.example.test:6379/0")
    monkeypatch.setattr("app.core.model_resilience._distributed_gate_for", lambda _: FullGate())

    with (
        pytest.raises(ModelUnavailableError, match="全局并发"),
        distributed_model_call_slot(settings, operation="embedding"),
    ):
        pass
