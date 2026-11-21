"""Rate limiter tests -- regression: security finding #5.

`apps/api/main.py` previously had only a 429 *error handler* with no
limiter ever instantiated -- nothing could ever raise a 429, so every
public endpoint was effectively unlimited. `apps.api.utils.rate_limiter`
wraps `penguin-limiter`'s sliding-window algorithm behind
`asyncio.to_thread()` (never block the event loop) with a per-tenant +
per-IP key, and must degrade OPEN (never crash a request) if the storage
backend errors.
"""

import functools
import json
from unittest.mock import MagicMock

import pytest
from penguin_limiter.config import RateLimitConfig
from penguin_limiter.storage.memory import MemoryStorage
from quart import Quart, g

from apps.api.config import Config
from apps.api.utils.rate_limiter import (
    RequestRateLimiter,
    init_rate_limiter,
    is_platform_request,
    rate_limit,
    rate_limit_exempt,
)

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


class TestCFConnectingIPExtraction:
    @pytest.mark.asyncio
    async def test_cf_connecting_ip_takes_precedence_over_xff_xri_remote(self):
        app = _build_app_ctx()
        limiter = RequestRateLimiter(
            RateLimitConfig.from_string("5/minute", key_prefix="test"),
            MemoryStorage(),
        )
        cf_ip = "198.51.100.42"
        async with app.test_request_context(
            "/",
            headers={
                "CF-Connecting-IP": cf_ip,
                "X-Forwarded-For": PUBLIC_IP,
                "X-Real-IP": "192.0.2.10",
            },
        ):
            assert limiter._key() == f"test:anon:{cf_ip}"

    @pytest.mark.asyncio
    async def test_cf_connecting_ip_ipv6_supported(self):
        app = _build_app_ctx()
        limiter = RequestRateLimiter(
            RateLimitConfig.from_string("5/minute", key_prefix="test"),
            MemoryStorage(),
        )
        async with app.test_request_context(
            "/",
            headers={
                "CF-Connecting-IP": " 2001:4860:4860::8888 ",
                "X-Forwarded-For": PUBLIC_IP,
            },
        ):
            assert limiter._key() == "test:anon:2001:4860:4860::8888"

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "bad_cf_ip",
        [
            "not-an-ip",
            "198.51.100.1, 203.0.113.9",
            "   ",
            "999.999.999.999",
            "203.0.113.5:8080",
        ],
    )
    async def test_invalid_cf_connecting_ip_falls_back_to_xff(self, bad_cf_ip):
        app = _build_app_ctx()
        limiter = RequestRateLimiter(
            RateLimitConfig.from_string("5/minute", key_prefix="test"),
            MemoryStorage(),
        )
        async with app.test_request_context(
            "/",
            headers={
                "CF-Connecting-IP": bad_cf_ip,
                "X-Forwarded-For": PUBLIC_IP,
            },
        ):
            assert limiter._key() == f"test:anon:{PUBLIC_IP}"

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "private_cf_ip",
        ["10.0.0.5", "192.168.1.10", "172.16.0.1", "127.0.0.1", "169.254.1.1", "::1"],
    )
    async def test_private_cf_connecting_ip_skipped_when_skip_private_ips_true(
        self, private_cf_ip
    ):
        app = _build_app_ctx()
        limiter = RequestRateLimiter(
            RateLimitConfig.from_string(
                "5/minute", key_prefix="test", skip_private_ips=True
            ),
            MemoryStorage(),
        )
        async with app.test_request_context(
            "/",
            headers={
                "CF-Connecting-IP": private_cf_ip,
                "X-Forwarded-For": PUBLIC_IP,
            },
        ):
            assert limiter._key() is None

    @pytest.mark.asyncio
    async def test_private_cf_connecting_ip_enforced_when_skip_private_ips_false(self):
        app = _build_app_ctx()
        limiter = RequestRateLimiter(
            RateLimitConfig.from_string(
                "5/minute", key_prefix="test", skip_private_ips=False
            ),
            MemoryStorage(),
        )
        async with app.test_request_context(
            "/",
            headers={"CF-Connecting-IP": "10.0.0.5"},
        ):
            assert limiter._key() == "test:anon:10.0.0.5"


class TestPlatformRouteClassification:
    @pytest.mark.parametrize(
        "path",
        [
            "/api/v1/webhooks/receive/my-slug",
            "/api/v1/webhooks/inbound/receive/my-slug",
            "/api/v1/on-call/webhooks/alertmanager/webhook",
            "/api/v1/hooks/tok_abc123",
            "/api/v1/hooks/tok_abc123/test",
            "/api/v1/flows-hooks/wh_123",
            "/api/v1/sso/scim/1/Users",
            "/api/v1/sso/scim/1/Users/42",
            "/api/v1/scim/1/Users",
            "/scim/Users",
            "/api/v1/discovery/jobs/pending",
            "/api/v1/discovery/jobs/42/start",
            "/api/v1/discovery/jobs/42/complete",
            "/api/v1/sbom/scans/pending",
            "/api/v1/sbom/scans/42/start",
            "/api/v1/sbom/scans/42/results",
        ],
    )
    def test_platform_receiver_paths_classified_as_platform(self, path):
        assert is_platform_request(path) is True

    @pytest.mark.parametrize(
        "path",
        [
            "",
            "/api/v1/webhooks",
            "/api/v1/webhooks/123",
            "/api/v1/webhooks/inbound",
            "/api/v1/webhooks/inbound/123",
            "/api/v1/webhooks/inbound/123/events",
            "/api/v1/webhooks/inbound/123/rotate-credentials",
            "/api/v1/webhooks/notification-rules",
            "/api/v1/webhooks/broadcast",
            "/api/v1/streams/1/webhooks",
            "/api/v1/on-call/rotations",
            "/api/v1/sso/scim/config",
            "/api/v1/sso/scim/config/1",
            "/api/v1/sso/scim/config/1/regenerate-token",
            "/api/v1/scim/config",
            "/scim/config",
            "/api/v1/discovery/jobs",
            "/api/v1/discovery/jobs/42",
            "/api/v1/discovery/jobs/42/run",
            "/api/v1/sbom/scans",
            "/api/v1/sbom/scans/42",
            "/api/v1/sbom/scans/upload",
            "/api/v1/entities",
        ],
    )
    def test_admin_and_user_paths_not_classified_as_platform(self, path):
        assert is_platform_request(path) is False


class TestPlatformTierIsolation:
    def test_config_exposes_platform_default(self):
        assert Config.RATELIMIT_PLATFORM_DEFAULT == "2000/minute"
        assert hasattr(Config, "RATELIMIT_ENDPOINT_OVERRIDES")

    @pytest.mark.asyncio
    async def test_platform_and_user_tiers_use_separate_limits_and_buckets(self):
        app = Quart(__name__)
        app.config["RATELIMIT_ENABLED"] = True
        app.config["RATELIMIT_DEFAULT"] = "2/minute"
        app.config["RATELIMIT_PLATFORM_DEFAULT"] = "5/minute"

        @app.route("/api/v1/entities")
        async def list_entities():
            return "ok"

        @app.route("/api/v1/webhooks/receive/<slug>", methods=["POST"])
        async def receive_webhook(slug):
            return f"received:{slug}"

        limiter = init_rate_limiter(app)
        assert limiter is app.extensions["rate_limiter"]
        assert "platform_rate_limiter" in app.extensions

        headers = {"CF-Connecting-IP": PUBLIC_IP}
        async with app.test_client() as client:
            # Exhaust user-facing quota (2/minute)
            u1 = await client.get("/api/v1/entities", headers=headers)
            u2 = await client.get("/api/v1/entities", headers=headers)
            u3 = await client.get("/api/v1/entities", headers=headers)

            assert u1.status_code == 200
            assert u1.headers["X-RateLimit-Limit"] == "2"
            assert u2.status_code == 200
            assert u3.status_code == 429
            assert u3.headers["X-RateLimit-Limit"] == "2"

            # Platform tier must be completely unaffected and enforce 5/minute
            for idx in range(5):
                p = await client.post("/api/v1/webhooks/receive/hook1", headers=headers)
                assert p.status_code == 200, f"platform request {idx + 1} failed"
                assert p.headers["X-RateLimit-Limit"] == "5"

            p6 = await client.post("/api/v1/webhooks/receive/hook1", headers=headers)
            assert p6.status_code == 429
            assert p6.headers["X-RateLimit-Limit"] == "5"

    @pytest.mark.asyncio
    async def test_platform_traffic_cannot_exhaust_user_quota(self):
        app = Quart(__name__)
        app.config["RATELIMIT_ENABLED"] = True
        app.config["RATELIMIT_DEFAULT"] = "1/minute"
        app.config["RATELIMIT_PLATFORM_DEFAULT"] = "2/minute"

        @app.route("/api/v1/webhooks", methods=["GET"])
        async def admin_list_webhooks():
            return "admin"

        @app.route("/api/v1/on-call/webhooks/alertmanager/webhook", methods=["POST"])
        async def alertmanager_receive():
            return "alert"

        init_rate_limiter(app)

        headers = {"CF-Connecting-IP": PUBLIC_IP}
        async with app.test_client() as client:
            # Exhaust platform quota first
            assert (
                await client.post(
                    "/api/v1/on-call/webhooks/alertmanager/webhook", headers=headers
                )
            ).status_code == 200
            assert (
                await client.post(
                    "/api/v1/on-call/webhooks/alertmanager/webhook", headers=headers
                )
            ).status_code == 200
            assert (
                await client.post(
                    "/api/v1/on-call/webhooks/alertmanager/webhook", headers=headers
                )
            ).status_code == 429

            # User-facing admin webhook CRUD still has its full 1/minute quota
            admin_resp = await client.get("/api/v1/webhooks", headers=headers)
            assert admin_resp.status_code == 200
            assert admin_resp.headers["X-RateLimit-Limit"] == "1"


class TestPerEndpointRateLimitOverrides:
    @pytest.mark.asyncio
    async def test_decorator_overrides_global_limit_upward(self):
        """A route with @rate_limit('500/minute') must NOT be capped by a 2/minute global limit."""
        app = Quart(__name__)
        app.config["RATELIMIT_ENABLED"] = True
        app.config["RATELIMIT_DEFAULT"] = "2/minute"

        @app.route("/high-volume")
        @rate_limit("500/minute", key_prefix="elder_high_volume")
        async def high_volume():
            return "ok"

        @app.route("/normal")
        async def normal():
            return "ok"

        init_rate_limiter(app)

        headers = {"CF-Connecting-IP": PUBLIC_IP}
        async with app.test_client() as client:
            for _ in range(5):
                resp = await client.get("/high-volume", headers=headers)
                assert resp.status_code == 200
                assert resp.headers["X-RateLimit-Limit"] == "500"

            # Global bucket was NOT consumed by /high-volume
            assert (await client.get("/normal", headers=headers)).status_code == 200
            assert (await client.get("/normal", headers=headers)).status_code == 200
            assert (await client.get("/normal", headers=headers)).status_code == 429

    @pytest.mark.asyncio
    async def test_decorator_overrides_global_limit_downward(self):
        """A route with @rate_limit('2/minute') must 429 on the 3rd request despite 100/minute global."""
        app = Quart(__name__)
        app.config["RATELIMIT_ENABLED"] = True
        app.config["RATELIMIT_DEFAULT"] = "100/minute"

        @app.route("/sensitive")
        @rate_limit("2/minute", key_prefix="elder_sensitive")
        async def sensitive():
            return "ok"

        init_rate_limiter(app)

        headers = {"CF-Connecting-IP": PUBLIC_IP}
        async with app.test_client() as client:
            r1 = await client.get("/sensitive", headers=headers)
            r2 = await client.get("/sensitive", headers=headers)
            r3 = await client.get("/sensitive", headers=headers)

        assert r1.status_code == 200
        assert r1.headers["X-RateLimit-Limit"] == "2"
        assert r2.status_code == 200
        assert r3.status_code == 429
        assert r3.headers["X-RateLimit-Limit"] == "2"
        assert "Retry-After" in r3.headers

    @pytest.mark.asyncio
    async def test_decorator_detected_through_wrapped_outer_decorator(self):
        """Outer decorators like @login_required must not hide @rate_limit's override_global flag."""
        app = Quart(__name__)
        app.config["RATELIMIT_ENABLED"] = True
        app.config["RATELIMIT_DEFAULT"] = "1/minute"

        def outer_auth(fn):
            @functools.wraps(fn)
            async def _inner(*args, **kwargs):
                return await fn(*args, **kwargs)

            return _inner

        @app.route("/wrapped")
        @outer_auth
        @rate_limit("10/minute", key_prefix="elder_wrapped")
        async def wrapped_route():
            return "ok"

        init_rate_limiter(app)

        headers = {"CF-Connecting-IP": PUBLIC_IP}
        async with app.test_client() as client:
            for _ in range(3):
                resp = await client.get("/wrapped", headers=headers)
                assert resp.status_code == 200
                assert resp.headers["X-RateLimit-Limit"] == "10"

    @pytest.mark.asyncio
    async def test_decorator_works_without_init_rate_limiter_and_supports_config_key(
        self,
    ):
        """@rate_limit must work standalone in minimal test apps without init_rate_limiter()."""
        app = Quart(__name__)
        app.config["CUSTOM_ROUTE_LIMIT"] = "2/minute"

        @app.route("/standalone")
        @rate_limit(
            "50/minute", key_prefix="elder_standalone", config_key="CUSTOM_ROUTE_LIMIT"
        )
        async def standalone():
            return "ok"

        headers = {"CF-Connecting-IP": PUBLIC_IP}
        async with app.test_client() as client:
            r1 = await client.get("/standalone", headers=headers)
            r2 = await client.get("/standalone", headers=headers)
            r3 = await client.get("/standalone", headers=headers)
            # Private CF-Connecting-IP is still skipped on @rate_limit routes
            priv = await client.get(
                "/standalone", headers={"CF-Connecting-IP": "10.0.0.5"}
            )

        assert r1.status_code == 200
        assert r1.headers["X-RateLimit-Limit"] == "2"
        assert r2.status_code == 200
        assert r3.status_code == 429
        assert priv.status_code == 200

    @pytest.mark.asyncio
    async def test_config_endpoint_overrides_by_path_prefix_and_endpoint_name(self):
        app = Quart(__name__)
        app.config["RATELIMIT_ENABLED"] = True
        app.config["RATELIMIT_DEFAULT"] = "1/minute"
        app.config["RATELIMIT_PLATFORM_DEFAULT"] = "2/minute"
        app.config["RATELIMIT_ENDPOINT_OVERRIDES"] = json.dumps(
            {
                "/api/v1/webhooks/inbound/receive": "4/minute",
                "custom_ep": "3/minute",
            }
        )

        @app.route("/api/v1/webhooks/inbound/receive/<slug>", methods=["POST"])
        @rate_limit("1/minute", key_prefix="elder_should_not_double_count")
        async def receive_inbound(slug):
            return slug

        @app.route("/custom", endpoint="custom_ep")
        async def custom_view():
            return "custom"

        init_rate_limiter(app)

        headers = {"CF-Connecting-IP": PUBLIC_IP}
        async with app.test_client() as client:
            # Path prefix override (4/minute) wins over both platform (2/min) and @rate_limit (1/min)
            for _ in range(4):
                r = await client.post(
                    "/api/v1/webhooks/inbound/receive/s1", headers=headers
                )
                assert r.status_code == 200
                assert r.headers["X-RateLimit-Limit"] == "4"
            r5 = await client.post(
                "/api/v1/webhooks/inbound/receive/s1", headers=headers
            )
            assert r5.status_code == 429

            # Endpoint name override (3/minute) wins over global (1/min)
            for _ in range(3):
                c = await client.get("/custom", headers=headers)
                assert c.status_code == 200
                assert c.headers["X-RateLimit-Limit"] == "3"
            c4 = await client.get("/custom", headers=headers)
            assert c4.status_code == 429


class TestRateLimitDisablingAndOptOut:
    @pytest.mark.asyncio
    async def test_ratelimit_enabled_false_disables_all_tiers_and_overrides(self):
        """When rate limiting is offloaded to the edge (Envoy/Cloudflare),
        RATELIMIT_ENABLED=False must disable global, platform, @rate_limit,
        and RATELIMIT_ENDPOINT_OVERRIDES with zero 429s and no X-RateLimit headers."""
        app = Quart(__name__)
        app.config["RATELIMIT_ENABLED"] = False
        app.config["RATELIMIT_DEFAULT"] = "1/minute"
        app.config["RATELIMIT_PLATFORM_DEFAULT"] = "1/minute"
        app.config["RATELIMIT_ENDPOINT_OVERRIDES"] = json.dumps(
            {"/override": "1/minute"}
        )

        @app.route("/global")
        async def global_route():
            return "ok"

        @app.route("/api/v1/webhooks/receive/<slug>", methods=["POST"])
        async def platform_route(slug):
            return slug

        @app.route("/decorated")
        @rate_limit("1/minute", key_prefix="elder_dec_disabled")
        async def decorated_route():
            return "ok"

        @app.route("/override")
        async def override_route():
            return "ok"

        assert init_rate_limiter(app) is None

        headers = {"CF-Connecting-IP": PUBLIC_IP}
        async with app.test_client() as client:
            for _ in range(5):
                for method, path in (
                    ("GET", "/global"),
                    ("POST", "/api/v1/webhooks/receive/s1"),
                    ("GET", "/decorated"),
                    ("GET", "/override"),
                ):
                    resp = await client.open(path, method=method, headers=headers)
                    assert resp.status_code == 200
                    assert "X-RateLimit-Limit" not in resp.headers
                    assert "X-RateLimit-Remaining" not in resp.headers

    @pytest.mark.asyncio
    async def test_per_endpoint_off_in_config_overrides_and_decorators(self):
        """Individual endpoints can opt out via RATELIMIT_ENDPOINT_OVERRIDES
        ('off' / null / false), @rate_limit('off'), or @rate_limit_exempt
        while other endpoints remain rate-limited."""
        app = Quart(__name__)
        app.config["RATELIMIT_ENABLED"] = True
        app.config["RATELIMIT_DEFAULT"] = "1/minute"
        app.config["RATELIMIT_ENDPOINT_OVERRIDES"] = json.dumps(
            {
                "/api/v1/webhooks/inbound/receive": "off",
                "/null-off": None,
                "/bool-off": False,
            }
        )

        @app.route("/api/v1/webhooks/inbound/receive/<slug>", methods=["POST"])
        async def inbound_off(slug):
            return slug

        @app.route("/null-off")
        async def null_off():
            return "ok"

        @app.route("/bool-off")
        async def bool_off():
            return "ok"

        @app.route("/dec-off")
        @rate_limit("off")
        async def dec_off():
            return "ok"

        @app.route("/exempt")
        @rate_limit_exempt
        async def exempt_route():
            return "ok"

        @app.route("/still-limited")
        async def still_limited():
            return "ok"

        init_rate_limiter(app)

        headers = {"CF-Connecting-IP": PUBLIC_IP}
        async with app.test_client() as client:
            for _ in range(4):
                for method, path in (
                    ("POST", "/api/v1/webhooks/inbound/receive/s1"),
                    ("GET", "/null-off"),
                    ("GET", "/bool-off"),
                    ("GET", "/dec-off"),
                    ("GET", "/exempt"),
                ):
                    resp = await client.open(path, method=method, headers=headers)
                    assert resp.status_code == 200
                    assert "X-RateLimit-Limit" not in resp.headers

            # Other endpoints remain enforced by global 1/minute
            assert (
                await client.get("/still-limited", headers=headers)
            ).status_code == 200
            assert (
                await client.get("/still-limited", headers=headers)
            ).status_code == 429

    @pytest.mark.asyncio
    async def test_tier_level_off_disables_only_that_tier(self):
        """Setting RATELIMIT_DEFAULT='off' disables global tier while
        platform tier still enforces its own limit."""
        app = Quart(__name__)
        app.config["RATELIMIT_ENABLED"] = True
        app.config["RATELIMIT_DEFAULT"] = "off"
        app.config["RATELIMIT_PLATFORM_DEFAULT"] = "1/minute"

        @app.route("/api/v1/entities")
        async def entities():
            return "ok"

        @app.route("/api/v1/webhooks/receive/<slug>", methods=["POST"])
        async def receive(slug):
            return slug

        init_rate_limiter(app)

        headers = {"CF-Connecting-IP": PUBLIC_IP}
        async with app.test_client() as client:
            for _ in range(4):
                r = await client.get("/api/v1/entities", headers=headers)
                assert r.status_code == 200
                assert "X-RateLimit-Limit" not in r.headers

            assert (
                await client.post("/api/v1/webhooks/receive/s1", headers=headers)
            ).status_code == 200
            assert (
                await client.post("/api/v1/webhooks/receive/s1", headers=headers)
            ).status_code == 429
