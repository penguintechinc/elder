"""Unit tests for the Elder API Prometheus /metrics endpoint.

These tests use the synchronous Quart test client provided by the shared
conftest fixtures. No external network calls or real database required.
"""


class TestAPIMetrics:
    """Test the Prometheus metrics endpoint and request instrumentation."""

    def test_metrics_endpoint_returns_prometheus_exposition(self, client):
        """GET /metrics returns 200 with the Prometheus text content-type."""
        response = client.get("/metrics")

        assert response.status_code == 200
        assert response.headers["Content-Type"].startswith("text/plain; version=0.0.4")

    def test_metrics_include_http_requests_total_after_request(self, client):
        """After serving a request, /metrics reports http_requests_total."""
        # Drive a request so the instrumentation records at least one sample.
        client.get("/api/v1/status")

        response = client.get("/metrics")
        body = response.get_data(as_text=True)

        assert "http_requests_total" in body
        assert "http_request_duration_seconds" in body
        assert "elder_info" in body

    def test_metrics_endpoint_not_self_counted(self, client):
        """The /metrics endpoint itself is excluded from request counting."""
        response = client.get("/metrics")
        body = response.get_data(as_text=True)

        assert 'endpoint="metrics_endpoint"' not in body
