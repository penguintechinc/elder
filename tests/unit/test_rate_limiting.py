"""Unit tests for the Redis-backed API rate limiter.

These tests never touch a live Redis: ``apps.api.rate_limit.get_redis_client``
is monkeypatched to return an in-memory fake (or to raise, for the fail-open
case). The limiter is exercised end-to-end through the shared synchronous
Quart ``client`` fixture against the unauthenticated ``/api/v1/status`` route.
"""

import pytest

from apps.api import rate_limit

# A non-exempt, no-auth, no-DB endpoint to drive requests through the limiter.
TEST_PATH = "/api/v1/status"


# ---------------------------------------------------------------------------
# In-memory fake Redis (async interface matching what the limiter uses).
# ---------------------------------------------------------------------------
class _FakePipeline:
    def __init__(self, store):
        self._store = store
        self._ops = []

    def incr(self, key):
        self._ops.append(("incr", key))
        return self

    def ttl(self, key):
        self._ops.append(("ttl", key))
        return self

    async def execute(self):
        results = []
        for op, key in self._ops:
            if op == "incr":
                self._store["counts"][key] = self._store["counts"].get(key, 0) + 1
                results.append(self._store["counts"][key])
            else:  # ttl
                results.append(self._store["ttls"].get(key, -1))
        self._ops = []
        return results


class _FakeRedis:
    """Minimal async Redis stand-in implementing pipeline/incr/ttl/expire."""

    def __init__(self):
        self.store = {"counts": {}, "ttls": {}}

    def pipeline(self):
        return _FakePipeline(self.store)

    async def expire(self, key, window):
        self.store["ttls"][key] = window


# ---------------------------------------------------------------------------
# Pure-function tests (no app required).
# ---------------------------------------------------------------------------
@pytest.mark.unit
@pytest.mark.parametrize(
    "spec,expected",
    [
        ("100/minute", (100, 60)),
        ("50/hour", (50, 3600)),
        ("10/second", (10, 1)),
        ("5/day", (5, 86400)),
        ("20/15minutes", (20, 900)),
        ("30/m", (30, 60)),
    ],
)
def test_parse_rate_limit_valid(spec, expected):
    assert rate_limit.parse_rate_limit(spec) == expected


@pytest.mark.unit
@pytest.mark.parametrize("spec", ["", None, "garbage", "100", "abc/minute", "100/eon"])
def test_parse_rate_limit_falls_back(spec):
    # Any unparseable spec yields the documented fallback.
    assert rate_limit.parse_rate_limit(spec) == rate_limit._FALLBACK_LIMIT


@pytest.mark.unit
def test_build_key_prefers_identity():
    class Ident:
        id = 42

    key = rate_limit._build_key(Ident(), tenant_id=7)
    assert key == "ratelimit:tenant:7:identity:42"


# ---------------------------------------------------------------------------
# Middleware tests through the Quart client.
# ---------------------------------------------------------------------------
@pytest.fixture
def enable_limiter(app, monkeypatch):
    """Enable the limiter with a small limit for the duration of a test."""
    monkeypatch.setitem(app.config, "RATELIMIT_ENABLED", True)
    monkeypatch.setitem(app.config, "RATELIMIT_DEFAULT", "3/minute")
    # Ensure no cached real client lingers.
    rate_limit._redis_clients.clear()
    yield
    rate_limit._redis_clients.clear()


@pytest.mark.unit
def test_allows_requests_under_limit(client, app, monkeypatch, enable_limiter):
    fake = _FakeRedis()
    monkeypatch.setattr(rate_limit, "get_redis_client", lambda url: fake)

    # Limit is 3/minute: first three requests must be allowed.
    for i in range(3):
        resp = client.get(TEST_PATH)
        assert resp.status_code == 200, f"request {i + 1} should be allowed"
        assert resp.headers.get("X-RateLimit-Limit") == "3"

    # Remaining decrements: after 3 requests remaining is 0.
    assert resp.headers.get("X-RateLimit-Remaining") == "0"


@pytest.mark.unit
def test_returns_429_with_headers_over_limit(client, app, monkeypatch, enable_limiter):
    fake = _FakeRedis()
    monkeypatch.setattr(rate_limit, "get_redis_client", lambda url: fake)

    # Exhaust the 3/minute budget.
    for _ in range(3):
        assert client.get(TEST_PATH).status_code == 200

    # The 4th request is rejected with a full set of rate-limit headers.
    resp = client.get(TEST_PATH)
    assert resp.status_code == 429
    assert resp.headers.get("Retry-After") is not None
    assert resp.headers.get("X-RateLimit-Limit") == "3"
    assert resp.headers.get("X-RateLimit-Remaining") == "0"
    assert resp.headers.get("X-RateLimit-Reset") is not None
    body = resp.get_json()
    assert body["error"] == "Rate Limit Exceeded"


@pytest.mark.unit
def test_noop_when_disabled(client, app, monkeypatch):
    # Explicitly disabled: the limiter must never consult Redis.
    monkeypatch.setitem(app.config, "RATELIMIT_ENABLED", False)

    def _boom(url):
        raise AssertionError("Redis must not be touched when disabled")

    monkeypatch.setattr(rate_limit, "get_redis_client", _boom)

    resp = client.get(TEST_PATH)
    assert resp.status_code == 200
    # No rate-limit headers are attached when the limiter is off.
    assert resp.headers.get("X-RateLimit-Limit") is None


@pytest.mark.unit
def test_fails_open_when_redis_unavailable(client, app, monkeypatch, enable_limiter):
    # Redis connection raises -> the request must still succeed (fail open).
    def _unavailable(url):
        raise ConnectionError("redis down")

    monkeypatch.setattr(rate_limit, "get_redis_client", _unavailable)

    resp = client.get(TEST_PATH)
    assert resp.status_code == 200
    # Headers are not set because the backend never produced a count.
    assert resp.headers.get("X-RateLimit-Limit") is None


@pytest.mark.unit
def test_fails_open_when_pipeline_errors(client, app, monkeypatch, enable_limiter):
    # A client that connects but errors mid-operation must also fail open.
    class _BrokenPipeline:
        def incr(self, key):
            return self

        def ttl(self, key):
            return self

        async def execute(self):
            raise RuntimeError("backend exploded")

    class _BrokenRedis:
        def pipeline(self):
            return _BrokenPipeline()

    monkeypatch.setattr(rate_limit, "get_redis_client", lambda url: _BrokenRedis())

    resp = client.get(TEST_PATH)
    assert resp.status_code == 200
