"""Tests for hosted ``hub-mcp`` admission control (global, per-caller, per-IP, XFF trust)."""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
import pytest

from apps.mcp.http_app import create_app
from apps.mcp.limits import ConcurrencyLimiter, client_ip

BODY = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "tools/call",
    "params": {
        "name": "invoke_api_operation",
        "arguments": {"service": "licensing", "operation": "get_licensing_status"},
    },
}
KW: dict[str, int] = {
    "max_total": 64,
    "max_per_caller": 4,
    "max_per_ip": 16,
    "max_tracked_callers": 10000,
}


def _gated_app(release: asyncio.Event, peak: dict[str, int]) -> Any:
    """Build an app whose upstream blocks until ``release`` and records peak concurrency."""
    live = {"n": 0}

    async def _handler(_request: httpx.Request) -> httpx.Response:
        live["n"] += 1
        peak["max"] = max(peak["max"], live["n"])
        try:
            await release.wait()
        finally:
            live["n"] -= 1
        return httpx.Response(200, json={"ok": True})

    return create_app(
        elder_api_url="http://hub-api:5000",
        elder_transport=httpx.MockTransport(_handler),
    )


@pytest.mark.asyncio
async def test_random_token_flood_cannot_exceed_global_cap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """200 distinct random bearer tokens never run more than the global cap; extras get 429 + Retry-After."""
    monkeypatch.setenv("MCP_MAX_CONCURRENT_TOTAL", "5")
    monkeypatch.setenv("MCP_MAX_CONCURRENT_PER_IP", "1000")
    release = asyncio.Event()
    peak = {"max": 0}
    app = _gated_app(release, peak)
    async with app.test_client() as client:
        tasks = [
            asyncio.create_task(
                client.post(
                    "/mcp", headers={"Authorization": f"Bearer rnd-{i}"}, json=BODY
                )
            )
            for i in range(200)
        ]
        await asyncio.sleep(0.8)
        limiter = app.extensions["mcp_limiter"]
        assert limiter.total <= 5
        assert len(limiter.callers) <= 5
        release.set()
        resps = await asyncio.gather(*tasks)
    codes = [r.status_code for r in resps]
    assert peak["max"] <= 5
    assert codes.count(200) >= 5 and codes.count(429) > 0
    assert set(codes) <= {200, 429}
    assert all(r.headers.get("Retry-After") for r in resps if r.status_code == 429)
    assert app.extensions["mcp_limiter"].total == 0
    assert app.extensions["mcp_limiter"].callers == {}


def test_caller_table_never_exceeds_cap_and_busy_entries_not_evicted() -> None:
    """A full table of busy callers rejects new keys, keeps existing ones, and shrinks on release."""
    lim = ConcurrencyLimiter()
    kw = {**KW, "max_tracked_callers": 3}
    for i in range(3):
        assert lim.try_acquire(f"c{i}", f"ip{i}", **kw) is None
    assert lim.try_acquire("c-new", "ipx", **kw) == "tracked"
    assert set(lim.callers) == {"c0", "c1", "c2"}
    assert lim.try_acquire("c0", "ip0", **kw) is None
    lim.release("c1", "ip1")
    assert "c1" not in lim.callers
    assert lim.try_acquire("c-new", "ipx", **kw) is None
    for _ in range(1000):
        lim.try_acquire("flood", "ipf", **kw)
    assert len(lim.callers) <= 3


def test_per_ip_cap_and_total_cap() -> None:
    """Per-IP and global limits refuse with their own reason; other IPs are unaffected."""
    lim = ConcurrencyLimiter()
    kw = {**KW, "max_per_ip": 2, "max_total": 3}
    assert lim.try_acquire("a", "1.1.1.1", **kw) is None
    assert lim.try_acquire("b", "1.1.1.1", **kw) is None
    assert lim.try_acquire("c", "1.1.1.1", **kw) == "ip"
    assert lim.try_acquire("d", "2.2.2.2", **kw) is None
    assert lim.try_acquire("e", "3.3.3.3", **kw) == "total"


@pytest.mark.asyncio
async def test_per_ip_cap_over_http(monkeypatch: pytest.MonkeyPatch) -> None:
    """Distinct tokens from one address are refused beyond MCP_MAX_CONCURRENT_PER_IP."""
    monkeypatch.setenv("MCP_MAX_CONCURRENT_PER_IP", "2")
    release = asyncio.Event()
    app = _gated_app(release, {"max": 0})
    async with app.test_client() as client:
        tasks = [
            asyncio.create_task(
                client.post(
                    "/mcp", headers={"Authorization": f"Bearer ip-{i}"}, json=BODY
                )
            )
            for i in range(2)
        ]
        await asyncio.sleep(0.3)
        refused = await client.post(
            "/mcp", headers={"Authorization": "Bearer ip-x"}, json=BODY
        )
        assert refused.status_code == 429
        release.set()
        assert [r.status_code for r in await asyncio.gather(*tasks)] == [200, 200]


def test_xff_ignored_unless_peer_is_trusted_proxy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """X-Forwarded-For is honoured only from a trusted peer, using the right-most untrusted hop."""
    hdr = {"X-Forwarded-For": "9.9.9.9, 8.8.8.8, 10.0.0.2"}
    monkeypatch.delenv("MCP_TRUSTED_PROXIES", raising=False)
    assert client_ip("10.0.0.1", hdr) == "10.0.0.1"
    monkeypatch.setenv("MCP_TRUSTED_PROXIES", "10.0.0.0/24")
    assert client_ip("10.0.0.1", hdr) == "8.8.8.8"
    assert client_ip("203.0.113.5", hdr) == "203.0.113.5"
    assert client_ip("10.0.0.1", {"X-Forwarded-For": "not-an-ip"}) == "10.0.0.1"
    assert client_ip("10.0.0.1", {}) == "10.0.0.1"
    monkeypatch.setenv("MCP_TRUSTED_PROXIES", "garbage,")
    assert client_ip("10.0.0.1", hdr) == "10.0.0.1"


def test_ipv6_grouped_by_prefix_and_mapped_ipv4(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Addresses in one /64 share a key, other /64s differ, and ::ffff:a.b.c.d maps to IPv4."""
    monkeypatch.delenv("MCP_IPV6_PREFIX_LEN", raising=False)
    monkeypatch.delenv("MCP_TRUSTED_PROXIES", raising=False)
    a = client_ip("2001:db8:1:2::1", {})
    b = client_ip("2001:db8:1:2:ffff:ffff:ffff:ffff", {})
    c = client_ip("2001:db8:1:3::1", {})
    assert a == b and a != c
    assert client_ip("::ffff:192.0.2.7", {}) == "192.0.2.7"
    assert client_ip("192.0.2.7", {}) == "192.0.2.7"
    assert client_ip("fe80::1%eth0", {}) == client_ip("fe80::2", {})
    assert client_ip(None, {}) == "unknown"
    assert client_ip("garbage", {}) == "unknown"
    monkeypatch.setenv("MCP_IPV6_PREFIX_LEN", "128")
    assert client_ip("2001:db8:1:2::1", {}) != client_ip("2001:db8:1:2::2", {})


@pytest.mark.parametrize("raw", ["abc", "0", "31", "129", "-1", "63.5"])
def test_invalid_ipv6_prefix_env_falls_back_to_64(
    monkeypatch: pytest.MonkeyPatch, raw: str, caplog: pytest.LogCaptureFixture
) -> None:
    """Out-of-range or malformed MCP_IPV6_PREFIX_LEN behaves exactly like the default 64 and warns."""
    monkeypatch.delenv("MCP_TRUSTED_PROXIES", raising=False)
    monkeypatch.delenv("MCP_IPV6_PREFIX_LEN", raising=False)
    expected = client_ip("2001:db8:1:2::1", {})
    monkeypatch.setenv("MCP_IPV6_PREFIX_LEN", raw)
    with caplog.at_level("WARNING"):
        assert client_ip("2001:db8:1:2::1", {}) == expected
        assert client_ip("2001:db8:1:2::9", {}) == expected
        assert client_ip("2001:db8:1:3::9", {}) != expected
    assert any("MCP_IPV6_PREFIX_LEN" in r.message for r in caplog.records)


def test_xff_hop_is_normalised_like_peer(monkeypatch: pytest.MonkeyPatch) -> None:
    """A trusted-proxy X-Forwarded-For hop is grouped by IPv6 prefix and IPv4-mapped is unwrapped."""
    monkeypatch.delenv("MCP_IPV6_PREFIX_LEN", raising=False)
    monkeypatch.setenv("MCP_TRUSTED_PROXIES", "10.0.0.0/24")
    h1 = {"X-Forwarded-For": "2001:db8:5:6::aaaa"}
    h2 = {"X-Forwarded-For": "2001:db8:5:6::bbbb"}
    h3 = {"X-Forwarded-For": "2001:db8:5:7::bbbb"}
    assert client_ip("10.0.0.1", h1) == client_ip("10.0.0.1", h2)
    assert client_ip("10.0.0.1", h1) != client_ip("10.0.0.1", h3)
    assert client_ip("10.0.0.1", {"X-Forwarded-For": "::ffff:198.51.100.4"}) == (
        "198.51.100.4"
    )


def test_rotating_addresses_in_one_prefix_cannot_exceed_per_ip_cap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Flooding from many addresses inside one /64 never admits more than max_per_ip."""
    monkeypatch.delenv("MCP_IPV6_PREFIX_LEN", raising=False)
    monkeypatch.delenv("MCP_TRUSTED_PROXIES", raising=False)
    lim = ConcurrencyLimiter()
    kw = {**KW, "max_per_ip": 3}
    admitted = 0
    for i in range(500):
        key = client_ip(f"2001:db8:9:9::{i + 1:x}", {})
        if lim.try_acquire(f"tok{i}", key, **kw) is None:
            admitted += 1
    assert admitted == 3
    assert lim.total == 3 and len(lim.ips) == 1


@pytest.mark.asyncio
async def test_stalled_body_returns_408_and_holds_no_slot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A body that stalls past MCP_BODY_READ_TIMEOUT_S gets 408 and never occupies the limiter."""
    from quart import Request

    monkeypatch.setenv("MCP_BODY_READ_TIMEOUT_S", "0.2")
    seen: dict[str, int] = {}
    app = _gated_app(asyncio.Event(), {"max": 0})
    limiter = app.extensions["mcp_limiter"]

    async def _stall(self: Request, *args: Any, **kwargs: Any) -> bytes:
        """Simulate a slow-loris client that never finishes sending its body."""
        seen["total"] = limiter.total
        await asyncio.sleep(30)
        return b""

    monkeypatch.setattr(Request, "get_data", _stall)
    async with app.test_client() as client:
        resp = await client.post(
            "/mcp", headers={"Authorization": "Bearer slow"}, json=BODY
        )
    assert resp.status_code == 408
    assert seen["total"] == 0
    assert limiter.total == 0 and limiter.callers == {} and limiter.ips == {}
