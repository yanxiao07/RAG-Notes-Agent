"""模型网关调用治理的纯函数与重试策略测试。"""

import httpx

from app.core.config import Settings
from app.core.model_resilience import call_with_model_resilience, retry_delay


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
