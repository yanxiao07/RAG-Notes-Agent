"""Prometheus 指标端点的权限、脱敏标签与聚合测试。"""

from fastapi.testclient import TestClient

from app import main
from app.core.metrics import clear_metrics_registry
from app.core.rate_limit import clear_rate_limiter_registry


def test_metrics_endpoint_is_hidden_by_default(client: TestClient) -> None:
    response = client.get("/metrics")

    assert response.status_code == 404


def test_metrics_requires_configured_token_and_exposes_route_templates(client: TestClient) -> None:
    original_enabled = main.settings.metrics_enabled
    original_token = main.settings.metrics_token
    try:
        main.settings.metrics_enabled = True
        main.settings.metrics_token = "metrics-test-token"
        clear_metrics_registry()
        clear_rate_limiter_registry()

        response = client.get("/api/v1/knowledge-bases")
        forbidden = client.get("/metrics")
        allowed = client.get("/metrics", headers={"X-Metrics-Token": "metrics-test-token"})

        assert response.status_code == 200
        assert forbidden.status_code == 404
        assert allowed.status_code == 200
        assert allowed.headers["content-type"].startswith("text/plain")
        assert 'route="/api/v1/knowledge-bases"' in allowed.text
        assert "workspace" not in allowed.text
        assert "metrics-test-token" not in allowed.text
    finally:
        main.settings.metrics_enabled = original_enabled
        main.settings.metrics_token = original_token
        clear_metrics_registry()
        clear_rate_limiter_registry()


def test_metrics_counts_rate_limit_rejection_without_identity_labels(client: TestClient) -> None:
    original_metrics_enabled = main.settings.metrics_enabled
    original_rate_limit_enabled = main.settings.rate_limit_enabled
    original_api_limit = main.settings.rate_limit_api_requests
    try:
        main.settings.metrics_enabled = True
        main.settings.metrics_token = ""
        main.settings.rate_limit_enabled = True
        main.settings.rate_limit_api_requests = 1
        clear_metrics_registry()
        clear_rate_limiter_registry()

        assert client.get("/api/v1/knowledge-bases").status_code == 200
        assert client.get("/api/v1/knowledge-bases").status_code == 429
        metrics = client.get("/metrics")

        assert metrics.status_code == 200
        assert 'rag_notes_rate_limit_rejections_total{scope="api"' in metrics.text
        assert "127.0.0.1" not in metrics.text
    finally:
        main.settings.metrics_enabled = original_metrics_enabled
        main.settings.rate_limit_enabled = original_rate_limit_enabled
        main.settings.rate_limit_api_requests = original_api_limit
        clear_metrics_registry()
        clear_rate_limiter_registry()
