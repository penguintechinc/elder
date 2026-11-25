"""Tests for hosted ``hub-mcp`` admission control (global, per-caller, per-IP, XFF trust)."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx
import pytest

from apps.mcp.http_app import create_app
from apps.mcp.limits import BodyReadLimiter, ConcurrencyLimiter, client_ip

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


def _body_app() -> Any:
    """Build an app whose upstream always succeeds, for body-phase tests."""
    return create_app(
        elder_api_url="http://hub-api:5000",
        elder_transport=httpx.MockTransport(lambda r: httpx.Response(200, json={})),
    )


def _slow_body(monkeypatch: pytest.MonkeyPatch, gate: asyncio.Event) -> dict[str, int]:
    """Patch Request.get_data to block on ``gate``; return a call/peak counter."""
    from quart import Request

    stats = {"calls": 0}

    async def _slow(self: Request, *args: Any, **kwargs: Any) -> bytes:
        """Simulate a client trickling its body until the gate opens."""
        stats["calls"] += 1
        await gate.wait()
        return b'{"jsonrpc":"2.0","id":1,"method":"ping"}'

    monkeypatch.setattr(Request, "get_data", _slow)
    return stats


def _assert_clean(app: Any) -> None:
    """Assert both limiters returned to zero with empty tables."""
    bl = app.extensions["mcp_body_limiter"]
    assert bl.total == 0 and bl.ips == {}
    ml = app.extensions["mcp_limiter"]
    assert ml.total == 0 and ml.callers == {} and ml.ips == {}


def test_body_limiter_caps_total_per_ip_and_drops_zero_entries() -> None:
    """BodyReadLimiter enforces total and per-IP caps, groups by key, and fails closed."""
    lim = BodyReadLimiter()
    assert lim.try_acquire("a", max_total=3, max_per_ip=2) is None
    assert lim.try_acquire("a", max_total=3, max_per_ip=2) is None
    assert lim.try_acquire("a", max_total=3, max_per_ip=2) == "ip"
    assert lim.try_acquire("b", max_total=3, max_per_ip=2) is None
    assert lim.try_acquire("c", max_total=3, max_per_ip=2) == "total"
    for key in ("a", "a", "b"):
        lim.release(key)
    assert lim.total == 0 and lim.ips == {}
    lim.release("ghost")
    assert lim.total == 0
    # IPv6 /64 rotation shares one key, so the per-IP cap holds.
    admitted = sum(
        lim.try_acquire(
            client_ip(f"2001:db8:5:5::{i + 1:x}", {}), max_total=99, max_per_ip=4
        )
        is None
        for i in range(100)
    )
    assert admitted == 4 and len(lim.ips) == 1


@pytest.mark.asyncio
async def test_slow_body_connections_beyond_global_cap_get_429_unbuffered(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Slow-body requests beyond MCP_MAX_CONCURRENT_BODY_READS get 429 without get_data being called."""
    monkeypatch.setenv("MCP_MAX_CONCURRENT_BODY_READS", "4")
    monkeypatch.setenv("MCP_MAX_BODY_READS_PER_IP", "1000")
    gate = asyncio.Event()
    stats = _slow_body(monkeypatch, gate)
    app = _body_app()
    async with app.test_client() as client:
        tasks = [
            asyncio.create_task(
                client.post(
                    "/mcp", headers={"Authorization": f"Bearer t{i}"}, data=b"{}"
                )
            )
            for i in range(30)
        ]
        await asyncio.sleep(0.5)
        assert app.extensions["mcp_body_limiter"].total == 4
        assert stats["calls"] == 4
        gate.set()
        resps = await asyncio.gather(*tasks)
    codes = [r.status_code for r in resps]
    assert codes.count(429) == 26 and set(codes) <= {200, 429}
    assert all(r.headers.get("Retry-After") for r in resps if r.status_code == 429)
    _assert_clean(app)


@pytest.mark.asyncio
async def test_per_ip_body_read_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    """One source address cannot hold more than MCP_MAX_BODY_READS_PER_IP body reads."""
    monkeypatch.setenv("MCP_MAX_BODY_READS_PER_IP", "2")
    gate = asyncio.Event()
    stats = _slow_body(monkeypatch, gate)
    app = _body_app()
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
        assert app.extensions["mcp_body_limiter"].total == 2
        assert stats["calls"] == 2
        gate.set()
        resps = await asyncio.gather(*tasks)
    assert [r.status_code for r in resps].count(429) == 8
    _assert_clean(app)


@pytest.mark.asyncio
async def test_declared_content_length_over_cap_413_before_body_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A Content-Length above the cap gets 413 and the body is never read or slot taken."""
    monkeypatch.setenv("MCP_MAX_REQUEST_BYTES", "1000")
    stats = _slow_body(monkeypatch, asyncio.Event())
    app = _body_app()
    assert app.config["MAX_CONTENT_LENGTH"] == 1000
    async with app.test_client() as client:
        resp = await client.post(
            "/mcp",
            headers={"Authorization": "Bearer t", "Content-Length": "5000"},
            data=b"x" * 10,
        )
    assert resp.status_code == 413
    assert stats["calls"] == 0
    _assert_clean(app)


@pytest.mark.asyncio
async def test_chunked_body_over_cap_413(monkeypatch: pytest.MonkeyPatch) -> None:
    """A chunked body (no Content-Length) over the cap is cut off with 413 and counters reset."""
    monkeypatch.setenv("MCP_MAX_REQUEST_BYTES", "1000")
    app = _body_app()

    async with app.test_client() as client:
        resp = await client.post(
            "/mcp",
            headers={
                "Authorization": "Bearer t",
                "Transfer-Encoding": "chunked",
                "Content-Length": "",
            },
            data=b"x" * 5000,
        )
    assert resp.status_code == 413
    _assert_clean(app)


def test_default_request_cap_is_1_mib(monkeypatch: pytest.MonkeyPatch) -> None:
    """The default MAX_CONTENT_LENGTH is 1 MiB when MCP_MAX_REQUEST_BYTES is unset."""
    monkeypatch.delenv("MCP_MAX_REQUEST_BYTES", raising=False)
    assert _body_app().config["MAX_CONTENT_LENGTH"] == 1024 * 1024


@pytest.mark.asyncio
async def test_legitimate_five_call_batch_fits_default_cap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A 5-call tools/call batch with typical arguments is well under the default cap and passes."""
    monkeypatch.delenv("MCP_MAX_REQUEST_BYTES", raising=False)
    batch = [{**BODY, "id": i} for i in range(5)]
    assert len(json.dumps(batch)) < 4096
    app = _body_app()
    async with app.test_client() as client:
        resp = await client.post(
            "/mcp", headers={"Authorization": "Bearer t"}, json=batch
        )
    assert resp.status_code == 200
    assert len(await resp.get_json()) == 5
    _assert_clean(app)


@pytest.mark.asyncio
async def test_counters_zero_after_body_timeout_and_refusal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """408 timeouts and 429 refusals both leave the body limiter at zero."""
    monkeypatch.setenv("MCP_BODY_READ_TIMEOUT_S", "0.2")
    monkeypatch.setenv("MCP_MAX_CONCURRENT_BODY_READS", "2")
    monkeypatch.setenv("MCP_MAX_BODY_READS_PER_IP", "1000")
    _slow_body(monkeypatch, asyncio.Event())
    app = _body_app()
    async with app.test_client() as client:
        resps = await asyncio.gather(
            *[
                client.post(
                    "/mcp", headers={"Authorization": f"Bearer t{i}"}, data=b"{}"
                )
                for i in range(6)
            ]
        )
    codes = sorted(r.status_code for r in resps)
    assert codes == [408, 408, 429, 429, 429, 429]
    _assert_clean(app)
