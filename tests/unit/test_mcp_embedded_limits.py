"""Embedded (in-process) hub-mcp skips per-IP caps; a remote/non-embedded app never does."""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
import pytest
from test_mcp_limits import BODY, _slow_body

from apps.mcp.http_app import create_app


def _gated(release: asyncio.Event, embedded: bool) -> Any:
    """Build an app whose upstream blocks until ``release`` is set."""

    async def _handler(_request: httpx.Request) -> httpx.Response:
        await release.wait()
        return httpx.Response(200, json={"ok": True})

    return create_app(
        elder_api_url="http://hub-api:5000",
        elder_transport=httpx.MockTransport(_handler),
        embedded=embedded,
    )


@pytest.mark.asyncio
async def test_embedded_app_not_throttled_by_per_ip_caps(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """40 concurrent embedded calls from different users are all admitted (per-IP caps skipped)."""
    monkeypatch.setenv("MCP_MAX_CONCURRENT_PER_IP", "2")
    monkeypatch.setenv("MCP_MAX_BODY_READS_PER_IP", "2")
    monkeypatch.setenv("MCP_MAX_CONCURRENT_TOTAL", "64")
    release = asyncio.Event()
    app = _gated(release, embedded=True)
    assert app.config["MCP_EMBEDDED"] is True
    async with app.test_client() as client:
        tasks = [
            asyncio.create_task(
                client.post(
                    "/mcp", headers={"Authorization": f"Bearer user-{i}"}, json=BODY
                )
            )
            for i in range(40)
        ]
        await asyncio.sleep(0.8)
        assert app.extensions["mcp_limiter"].total == 40
        release.set()
        codes = [r.status_code for r in await asyncio.gather(*tasks)]
    assert codes == [200] * 40


@pytest.mark.asyncio
async def test_embedded_app_keeps_per_caller_and_global_caps(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Embedded mode still enforces the per-caller and global caps."""
    monkeypatch.setenv("MCP_MAX_CONCURRENT_PER_CALLER", "2")
    monkeypatch.setenv("MCP_MAX_CONCURRENT_TOTAL", "5")
    release = asyncio.Event()
    app = _gated(release, embedded=True)
    async with app.test_client() as client:
        same = [
            asyncio.create_task(
                client.post("/mcp", headers={"Authorization": "Bearer same"}, json=BODY)
            )
            for _ in range(2)
        ]
        await asyncio.sleep(0.3)
        refused = await client.post(
            "/mcp", headers={"Authorization": "Bearer same"}, json=BODY
        )
        assert refused.status_code == 429
        others = [
            asyncio.create_task(
                client.post(
                    "/mcp", headers={"Authorization": f"Bearer o{i}"}, json=BODY
                )
            )
            for i in range(3)
        ]
        await asyncio.sleep(0.3)
        over = await client.post(
            "/mcp", headers={"Authorization": "Bearer o-extra"}, json=BODY
        )
        assert over.status_code == 429
        release.set()
        await asyncio.gather(*same, *others)


@pytest.mark.asyncio
async def test_embedded_app_body_read_per_ip_skipped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Embedded mode does not apply the per-IP body-read cap (global cap still applies)."""
    monkeypatch.setenv("MCP_MAX_BODY_READS_PER_IP", "2")
    gate = asyncio.Event()
    stats = _slow_body(monkeypatch, gate)
    app = create_app(
        elder_api_url="http://hub-api:5000",
        elder_transport=httpx.MockTransport(lambda r: httpx.Response(200, json={})),
        embedded=True,
    )
    async with app.test_client() as client:
        tasks = [
            asyncio.create_task(
                client.post(
                    "/mcp", headers={"Authorization": f"Bearer t{i}"}, data=b"{}"
                )
            )
            for i in range(10)
        ]
        await asyncio.sleep(0.5)
        assert stats["calls"] == 10
        gate.set()
        codes = [r.status_code for r in await asyncio.gather(*tasks)]
    assert 429 not in codes


@pytest.mark.asyncio
async def test_forged_headers_cannot_disable_per_ip_limiting_on_remote_app(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A remote request claiming 'embedded' via headers/XFF is still throttled per IP."""
    monkeypatch.setenv("MCP_MAX_CONCURRENT_PER_IP", "2")
    monkeypatch.setenv("MCP_TRUSTED_PROXIES", "127.0.0.0/8")
    release = asyncio.Event()
    app = _gated(release, embedded=False)
    assert app.config["MCP_EMBEDDED"] is False
    forged = {
        "X-Embedded": "1",
        "X-MCP-Embedded": "true",
        "X-Forwarded-For": "10.1.2.3",
    }
    async with app.test_client() as client:
        tasks = [
            asyncio.create_task(
                client.post(
                    "/mcp",
                    headers={"Authorization": f"Bearer f{i}", **forged},
                    json=BODY,
                )
            )
            for i in range(2)
        ]
        await asyncio.sleep(0.3)
        refused = await client.post(
            "/mcp", headers={"Authorization": "Bearer f-x", **forged}, json=BODY
        )
        assert refused.status_code == 429
        release.set()
        await asyncio.gather(*tasks)
