"""Rate limiter tests -- regression: security finding #5.

`apps/api/main.py` previously had only a 429 *error handler* with no
limiter ever instantiated -- nothing could ever raise a 429, so every
public endpoint was effectively unlimited. `apps.api.utils.rate_limiter`
wraps `penguin-limiter`'s sliding-window algorithm behind
`asyncio.to_thread()` (never block the event loop) with a per-tenant +
per-IP key, and must degrade OPEN (never crash a request) if the storage
backend errors.
"""

from unittest.mock import MagicMock

import pytest
from penguin_limiter.config import RateLimitConfig
from penguin_limiter.storage.memory import MemoryStorage
from quart import Quart, g

from apps.api.utils.rate_limiter import RequestRateLimiter, init_rate_limiter

# A routable TEST-NET-3 address (RFC 5737) -- guaranteed non-private, so the
# limiter's skip_private_ips bypass never swallows these test requests.
PUBLIC_IP = "203.0.113.5"


def _build_app_ctx():
    app = Quart(__name__)
    return app


class TestRequestRateLimiterKey:
    @pytest.mark.asyncio
    async def test_private_ip_skipped_returns_none_key(self):
        app = _build_app_ctx()
        limiter = RequestRateLimiter(
            RateLimitConfig.from_string("5/minute", key_prefix="test"),
            MemoryStorage(),
        )
        async with app.test_request_context(
            "/", headers={"X-Forwarded-For": "10.0.0.5"}
        ):
            assert limiter._key() is None

    @pytest.mark.asyncio
    async def test_public_ip_without_tenant_uses_anon_bucket(self):
        app = _build_app_ctx()
        limiter = RequestRateLimiter(
            RateLimitConfig.from_string("5/minute", key_prefix="test"),
            MemoryStorage(),
        )
        async with app.test_request_context(
            "/", headers={"X-Forwarded-For": PUBLIC_IP}
        ):
            key = limiter._key()
            assert key == f"test:anon:{PUBLIC_IP}"

    @pytest.mark.asyncio
    async def test_public_ip_with_tenant_claim_scopes_by_tenant(self):
        app = _build_app_ctx()
        limiter = RequestRateLimiter(
            RateLimitConfig.from_string("5/minute", key_prefix="test"),
            MemoryStorage(),
        )
        async with app.test_request_context(
            "/", headers={"X-Forwarded-For": PUBLIC_IP}
        ):
            g.claims = {"tenant": "42"}
            key = limiter._key()
            assert key == f"test:42:{PUBLIC_IP}"


class TestRequestRateLimiterCheck:
    @pytest.mark.asyncio
    async def test_allows_under_limit(self):
        app = _build_app_ctx()
        limiter = RequestRateLimiter(
            RateLimitConfig.from_string("5/minute", key_prefix="test"),
            MemoryStorage(),
        )
        async with app.test_request_context(
            "/", headers={"X-Forwarded-For": PUBLIC_IP}
        ):
            allowed, headers = await limiter.check()
            assert allowed is True
            assert headers["X-RateLimit-Limit"] == "5"

    @pytest.mark.asyncio
    async def test_rejects_over_limit_with_retry_after(self):
        app = _build_app_ctx()
        limiter = RequestRateLimiter(
            RateLimitConfig.from_string("1/minute", key_prefix="test"),
            MemoryStorage(),
        )
        async with app.test_request_context(
            "/", headers={"X-Forwarded-For": PUBLIC_IP}
        ):
            first_allowed, _ = await limiter.check()
            second_allowed, second_headers = await limiter.check()
        assert first_allowed is True
        assert second_allowed is False
        assert "Retry-After" in second_headers

    @pytest.mark.asyncio
    async def test_degrades_open_on_storage_error(self):
        """A rate limiter must never turn a backend outage into a request
        failure -- storage errors must fail OPEN, not closed.

        `SlidingWindow.is_allowed()` (penguin_limiter) already catches the
        storage exception itself and returns an allowed=True result -- this
        confirms that fail-open behavior reaches the caller unchanged, not
        just that our own outer try/except would also catch it.
        """
        app = _build_app_ctx()
        broken_storage = MagicMock()
        broken_storage.add_timestamp.side_effect = ConnectionError("redis down")
        limiter = RequestRateLimiter(
            RateLimitConfig.from_string("1/minute", key_prefix="test"), broken_storage
        )
        async with app.test_request_context(
            "/", headers={"X-Forwarded-For": PUBLIC_IP}
        ):
            allowed, headers = await limiter.check()
        assert allowed is True
        assert "Retry-After" not in headers

    @pytest.mark.asyncio
    async def test_degrades_open_when_algorithm_itself_raises(self):
        """Exercises check()'s OWN outer try/except directly (as opposed to
        the algorithm's internal one above) -- e.g. a failure in the
        threadpool dispatch itself, not just the storage call."""
        app = _build_app_ctx()
        limiter = RequestRateLimiter(
            RateLimitConfig.from_string("1/minute", key_prefix="test"), MemoryStorage()
        )
        # SlidingWindow is `__slots__`-based -- replace the whole algorithm
        # object rather than patching a single read-only attribute.
        limiter._algo = MagicMock(
            is_allowed=MagicMock(side_effect=RuntimeError("boom"))
        )
        async with app.test_request_context(
            "/", headers={"X-Forwarded-For": PUBLIC_IP}
        ):
            allowed, headers = await limiter.check()
        assert allowed is True
        assert headers == {}


class TestInitRateLimiterWiring:
    @pytest.mark.asyncio
    async def test_disabled_registers_nothing(self):
        app = Quart(__name__)
        app.config["RATELIMIT_ENABLED"] = False

        @app.route("/ping")
        async def ping():
            return "pong"

        result = init_rate_limiter(app)
        assert result is None
        async with app.test_client() as client:
            resp = await client.get("/ping")
            assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_enabled_enforces_limit_end_to_end(self):
        app = Quart(__name__)
        app.config["RATELIMIT_ENABLED"] = True
        app.config["RATELIMIT_DEFAULT"] = "2/minute"
        # No app.redis_client set -> falls back to in-process MemoryStorage.

        @app.route("/ping")
        async def ping():
            return "pong"

        limiter = init_rate_limiter(app)
        assert limiter is not None

        async with app.test_client() as client:
            headers = {"X-Forwarded-For": PUBLIC_IP}
            first = await client.get("/ping", headers=headers)
            second = await client.get("/ping", headers=headers)
            third = await client.get("/ping", headers=headers)

        assert first.status_code == 200
        assert second.status_code == 200
        assert third.status_code == 429
        body = await third.get_json()
        assert body["error"] == "Rate Limit Exceeded"

    @pytest.mark.asyncio
    async def test_different_tenants_get_independent_quotas(self):
        """One tenant exhausting its quota must not affect another tenant
        sharing the same IP (e.g. behind a shared corporate NAT)."""
        app = Quart(__name__)
        app.config["RATELIMIT_ENABLED"] = True
        app.config["RATELIMIT_DEFAULT"] = "1/minute"

        @app.before_request
        async def _fake_claims():
            from quart import request

            tenant = request.headers.get("X-Test-Tenant")
            if tenant:
                g.claims = {"tenant": tenant}

        @app.route("/ping")
        async def ping():
            return "pong"

        init_rate_limiter(app)

        async with app.test_client() as client:
            headers_a = {"X-Forwarded-For": PUBLIC_IP, "X-Test-Tenant": "1"}
            headers_b = {"X-Forwarded-For": PUBLIC_IP, "X-Test-Tenant": "2"}
            resp_a1 = await client.get("/ping", headers=headers_a)
            resp_b1 = await client.get("/ping", headers=headers_b)
            resp_a2 = await client.get("/ping", headers=headers_a)

        assert resp_a1.status_code == 200
        assert resp_b1.status_code == 200  # different tenant, own quota
        assert resp_a2.status_code == 429  # tenant 1's second request, same minute
