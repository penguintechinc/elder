"""Unit tests for Streams action nodes (webhook_out) — new node contract.

Covers the SSRF guard (real, no network), the required-input contract, the
raw-in / wrapped-out output shape, and AuthConfig header building.

regression: streams-node-catalog-phase4b
"""

from __future__ import annotations

import pytest

from apps.worker.streams.nodes.actions.webhook_out import AuthConfig, WebhookOutAction

_CTX = {
    "execution_id": "exec-1",
    "playbook_id": "pb-1",
    "node_id": "node-1",
    "config": {},
}


def _node(config=None):
    ctx = dict(_CTX)
    ctx["config"] = config or {}
    return WebhookOutAction(ctx)


class TestWebhookOutSSRF:
    """The SSRF guard must fire before any request is issued."""

    @pytest.mark.asyncio
    async def test_blocks_cloud_metadata_ip(self):
        node = _node()
        with pytest.raises(ValueError, match="internal/reserved"):
            await node.execute(
                {"url": "http://169.254.169.254/latest/meta-data/", "payload": {}}
            )

    @pytest.mark.asyncio
    async def test_blocks_loopback(self):
        node = _node()
        with pytest.raises(ValueError, match="internal/reserved"):
            await node.execute({"url": "http://127.0.0.1:8080/", "payload": {}})

    @pytest.mark.asyncio
    async def test_blocks_non_http_scheme(self):
        node = _node()
        with pytest.raises(ValueError, match="scheme"):
            await node.execute({"url": "file:///etc/passwd", "payload": {}})

    @pytest.mark.asyncio
    async def test_missing_url_raises(self):
        node = _node()
        with pytest.raises(ValueError, match="URL is required"):
            await node.execute({"payload": {}})


class TestWebhookOutContract:
    """Raw-in / wrapped-out output shape."""

    @pytest.mark.asyncio
    async def test_success_output_is_wrapped(self, monkeypatch):
        node = _node()

        async def fake_send(url, payload, custom_headers, timeout, max_retries):
            return True, 200, "HTTP 200"

        monkeypatch.setattr(node, "_send_webhook", fake_send)

        # Public host that passes the guard; the sender is mocked so no request.
        out = await node.execute(
            {"url": "https://example.com/hook", "payload": {"a": 1}}
        )

        assert out["success"]["data"] is True
        assert out["status"]["data"] == 200
        assert out["message"]["data"] == "HTTP 200"
        assert out["success"]["source_node_id"] == "node-1"

    @pytest.mark.asyncio
    async def test_failure_status_reported_not_raised(self, monkeypatch):
        node = _node()

        async def fake_send(url, payload, custom_headers, timeout, max_retries):
            return False, 502, "HTTP 502"

        monkeypatch.setattr(node, "_send_webhook", fake_send)
        out = await node.execute({"url": "https://example.com/hook", "payload": {}})
        assert out["success"]["data"] is False
        assert out["status"]["data"] == 502


class TestAuthConfig:
    """Auth header construction per auth type."""

    def test_bearer(self):
        headers = AuthConfig(auth_type="bearer", bearer_token="abc").build_headers()
        assert headers["Authorization"] == "Bearer abc"

    def test_basic(self):
        headers = AuthConfig(
            auth_type="basic", basic_username="u", basic_password="p"
        ).build_headers()
        assert headers["Authorization"].startswith("Basic ")

    def test_apikey(self):
        headers = AuthConfig(
            auth_type="apikey", api_key_header="X-API-Key", api_key_value="k"
        ).build_headers()
        assert headers["X-API-Key"] == "k"

    def test_none(self):
        assert AuthConfig(auth_type="none").build_headers() == {}
