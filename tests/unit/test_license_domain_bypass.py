"""Unit tests for license domain bypass, decorator wrapper, and status endpoint."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from apps.api import licensing_fallback
from apps.api.common.licensing.limits import TIER_DEFAULTS, LimitSet, resolve_limits
from apps.api.common.licensing.tier_gate import get_tier
from apps.api.common.modules.licensing import (
    get_matched_bypass_domain,
    is_bypass_domain,
)


class FakeLicenseClient:
    """Minimal stand-in for penguin_licensing's LicenseClient."""

    def __init__(
        self, tier: str = "community", valid: bool = True, limits: dict | None = None
    ):
        self._tier = tier
        self._valid = valid
        self._limits = limits or {}
        self.validate_calls = 0

    def validate(self):
        self.validate_calls += 1
        return SimpleNamespace(
            tier=self._tier,
            valid=self._valid,
            limits=self._limits,
            message="invalid" if not self._valid else "ok",
            customer="test",
        )


@pytest.fixture(autouse=True)
def _clean_bypass_env(monkeypatch):
    """Ensure SITE_URL and APP_DOMAIN are unset by default in every test."""
    monkeypatch.delenv("SITE_URL", raising=False)
    monkeypatch.delenv("APP_DOMAIN", raising=False)


class TestBypassDomainMatching:
    """Verify domain bypass allowlist, header parsing, and spoofing defenses."""

    @pytest.mark.parametrize(
        ("host", "expected"),
        [
            # Exact apex domains
            ("penguintech.cloud", "penguintech.cloud"),
            ("penguincloud.io", "penguincloud.io"),
            ("elderrms.app", "elderrms.app"),
            ("elder.app", "elder.app"),
            # Subdomains (single & nested, mixed case, ports, trailing dot)
            ("elder.penguintech.cloud", "elder.penguintech.cloud"),
            ("alpha.beta.penguintech.cloud:443", "alpha.beta.penguintech.cloud"),
            ("app.penguincloud.io", "app.penguincloud.io"),
            ("alpha.elderrms.app", "alpha.elderrms.app"),
            ("ALPHA.ELDERRMS.APP:8443", "alpha.elderrms.app"),
            ("portal.elder.app.", "portal.elder.app"),
        ],
    )
    @pytest.mark.asyncio
    async def test_allowed_host_headers_bypass(self, app, host, expected):
        async with app.test_request_context("/", headers={"Host": host}):
            assert is_bypass_domain() is True
            assert get_matched_bypass_domain() == expected

    @pytest.mark.parametrize(
        "spoofed_host",
        [
            "evilpenguintech.cloud",
            "penguintech.cloud.attacker.com",
            "notelderrms.app",
            "elderrms.app.evil.com",
            "mypenguincloud.io",
            "penguincloud.io.evil.org",
            "notelder.app",
            ".penguintech.cloud",
            "..elderrms.app",
            "foo..elderrms.app",
            "evil.com/.penguintech.cloud",
            "evil.com@sub.penguintech.cloud",
            "sub.penguintech.cloud:notaport",
            "sub.penguintech.cloud:99999",
            "customer.example.com",
            "localhost:8000",
        ],
    )
    @pytest.mark.asyncio
    async def test_spoofed_and_non_bypass_domains_rejected(self, app, spoofed_host):
        async with app.test_request_context("/", headers={"Host": spoofed_host}):
            assert is_bypass_domain() is False
            assert get_matched_bypass_domain() is None

    @pytest.mark.asyncio
    async def test_x_forwarded_host_with_port_and_comma_list(self, app):
        headers = {
            "Host": "elder-api.elder.svc.cluster.local:8000",
            "X-Forwarded-Host": "internal.proxy.local, alpha.elderrms.app:443",
        }
        async with app.test_request_context("/", headers=headers):
            assert is_bypass_domain() is True
            assert get_matched_bypass_domain() == "alpha.elderrms.app"

    @pytest.mark.asyncio
    async def test_x_forwarded_host_rejects_spoofed_entries(self, app):
        headers = {
            "Host": "elder-api:8000",
            "X-Forwarded-Host": "evilpenguintech.cloud, penguintech.cloud.attacker.com:443",
        }
        async with app.test_request_context("/", headers=headers):
            assert is_bypass_domain() is False
            assert get_matched_bypass_domain() is None

    @pytest.mark.asyncio
    async def test_forwarded_header_host_directive(self, app):
        headers = {
            "Host": "elder-api:8000",
            "Forwarded": 'for=10.0.0.1;proto=https;host="stage.penguincloud.io:443", for=10.0.0.2;host=internal.local',
        }
        async with app.test_request_context("/", headers=headers):
            assert is_bypass_domain() is True
            assert get_matched_bypass_domain() == "stage.penguincloud.io"

    def test_site_url_and_app_domain_env_vars_outside_request_context(
        self, monkeypatch
    ):
        monkeypatch.setenv("SITE_URL", "https://alpha.elderrms.app:443/dashboard")
        assert is_bypass_domain() is True
        assert get_matched_bypass_domain() == "alpha.elderrms.app"

        monkeypatch.delenv("SITE_URL")
        monkeypatch.setenv("APP_DOMAIN", "elder.penguintech.cloud")
        assert is_bypass_domain() is True
        assert get_matched_bypass_domain() == "elder.penguintech.cloud"

    def test_site_url_rejects_spoofed_and_userinfo_urls(self, monkeypatch):
        monkeypatch.setenv("SITE_URL", "https://evilpenguintech.cloud")
        assert is_bypass_domain() is False
        assert get_matched_bypass_domain() is None

        monkeypatch.setenv("SITE_URL", "https://evil.com:80@alpha.elderrms.app")
        assert is_bypass_domain() is False
        assert get_matched_bypass_domain() is None


class TestLicenseRequiredDecoratorBypass:
    """Verify @license_required wraps both real and fallback decorators with domain bypass."""

    @pytest.mark.asyncio
    async def test_async_handler_bypassed_even_when_real_decorator_denies(
        self, app, monkeypatch
    ):
        real_calls = 0

        def denying_real_decorator(*_args, **_kwargs):
            def decorator(fn):
                async def wrapper(*a, **kw):
                    nonlocal real_calls
                    real_calls += 1
                    return {"error": "License Required"}, 403

                return wrapper

            return decorator

        monkeypatch.setattr(
            licensing_fallback, "_real_license_required", denying_real_decorator
        )

        @licensing_fallback.license_required("enterprise")
        async def enterprise_async_view():
            """Async handler docstring."""
            return {"ok": True}, 200

        assert enterprise_async_view.__name__ == "enterprise_async_view"
        assert enterprise_async_view.__doc__ == "Async handler docstring."

        # Non-bypass domain -> real decorator denies
        async with app.test_request_context(
            "/", headers={"Host": "customer.example.com"}
        ):
            body, status = await enterprise_async_view()
            assert status == 403
            assert body == {"error": "License Required"}
            assert real_calls == 1

        # Bypass domain -> wrapped async handler runs directly without calling real decorator
        async with app.test_request_context(
            "/", headers={"Host": "alpha.elderrms.app"}
        ):
            body, status = await enterprise_async_view()
            assert status == 200
            assert body == {"ok": True}
            assert real_calls == 1

    def test_sync_handler_bypassed_even_when_real_sync_decorator_denies(
        self, monkeypatch
    ):
        real_calls = 0

        def denying_sync_decorator(*_args, **_kwargs):
            def decorator(fn):
                def wrapper(*a, **kw):
                    nonlocal real_calls
                    real_calls += 1
                    return {"error": "License Required"}, 403

                return wrapper

            return decorator

        monkeypatch.setattr(
            licensing_fallback, "_real_license_required", denying_sync_decorator
        )

        @licensing_fallback.license_required("enterprise")
        def enterprise_sync_view():
            """Sync handler docstring."""
            return {"ok": "sync"}, 200

        assert enterprise_sync_view.__name__ == "enterprise_sync_view"
        assert enterprise_sync_view.__doc__ == "Sync handler docstring."

        # Non-bypass -> denied
        body, status = enterprise_sync_view()
        assert status == 403
        assert real_calls == 1

        # Bypass via APP_DOMAIN -> allowed synchronously
        monkeypatch.setenv("APP_DOMAIN", "elder.penguintech.cloud")
        body, status = enterprise_sync_view()
        assert status == 200
        assert body == {"ok": "sync"}
        assert real_calls == 1

    @pytest.mark.asyncio
    async def test_supports_bare_and_feature_kwarg_and_fallback_mode(
        self, app, monkeypatch
    ):
        # Test with real decorator that rejects unexpected kwargs (like penguin_licensing)
        def strict_real_decorator(required_tier: str = "enterprise"):
            def decorator(fn):
                async def wrapper(*a, **kw):
                    return {"error": f"requires {required_tier}"}, 403

                return wrapper

            return decorator

        monkeypatch.setattr(
            licensing_fallback, "_real_license_required", strict_real_decorator
        )

        @licensing_fallback.license_required
        async def bare_handler():
            return {"mode": "bare"}, 200

        @licensing_fallback.license_required(feature="custom_module")
        def feature_sync_handler():
            return {"mode": "feature"}, 200

        assert bare_handler.__name__ == "bare_handler"
        assert feature_sync_handler.__name__ == "feature_sync_handler"

        async with app.test_request_context(
            "/", headers={"Host": "customer.example.com"}
        ):
            _, s1 = await bare_handler()
            _, s2 = await feature_sync_handler()
            assert s1 == 403
            assert s2 == 403

        async with app.test_request_context(
            "/", headers={"X-Forwarded-Host": "app.penguincloud.io:443"}
        ):
            b1, s1 = await bare_handler()
            b2, s2 = await feature_sync_handler()
            assert (b1, s1) == ({"mode": "bare"}, 200)
            assert (b2, s2) == ({"mode": "feature"}, 200)

        # Fallback mode (penguin_licensing unavailable)
        monkeypatch.setattr(licensing_fallback, "_real_license_required", None)

        @licensing_fallback.license_required("enterprise")
        def fallback_sync():
            return "ok"

        @licensing_fallback.license_required("enterprise")
        async def fallback_async():
            return "ok-async"

        assert fallback_sync() == "ok"
        assert await fallback_async() == "ok-async"


class TestResolveLimitsAndGetTierBypass:
    """Verify resolve_limits() and get_tier() return enterprise on bypass domains."""

    @pytest.mark.asyncio
    async def test_resolve_limits_and_get_tier_return_enterprise_on_bypass(self, app):
        community_client = FakeLicenseClient(tier="community")
        app.extensions["license_client"] = community_client

        async with app.test_request_context(
            "/", headers={"X-Forwarded-Host": "alpha.elderrms.app:443"}
        ):
            tier, limits = resolve_limits(community_client)
            assert tier == "enterprise"
            assert limits == TIER_DEFAULTS["enterprise"]
            assert get_tier() == "enterprise"
            assert community_client.validate_calls == 0

    @pytest.mark.asyncio
    async def test_resolve_limits_and_get_tier_do_not_bypass_on_spoofed_domain(
        self, app
    ):
        community_client = FakeLicenseClient(tier="community")
        app.extensions["license_client"] = community_client

        async with app.test_request_context(
            "/", headers={"Host": "evilpenguintech.cloud"}
        ):
            tier, limits = resolve_limits(community_client)
            assert tier == "community"
            assert limits == TIER_DEFAULTS["community"]
            assert get_tier() == "free"
            assert community_client.validate_calls == 1


class TestSecondaryLicenseCallSites:
    """Verify web/routes.py, collab.py, and grpc/server.py honor domain bypass."""

    @pytest.mark.asyncio
    async def test_web_template_context_returns_enterprise_on_bypass(self, app):
        from apps.api.web import routes as web_routes

        fake_client = FakeLicenseClient(tier="community")
        async with app.test_request_context(
            "/", headers={"Host": "elder.penguintech.cloud"}
        ):
            with (
                patch.object(
                    web_routes, "get_web_user", return_value=SimpleNamespace(id=1)
                ),
                patch.object(
                    web_routes, "get_license_client", return_value=fake_client
                ),
            ):
                ctx = web_routes.get_template_context()
                assert ctx["license_tier"] == "enterprise"
                assert fake_client.validate_calls == 0

    @pytest.mark.asyncio
    async def test_collab_bypasses_license_layer_on_bypass_domain(self, app):
        from apps.api.modules.diagrams.routes import collab

        fake_client = MagicMock()
        fake_client.validate.return_value = False
        app.extensions["license_client"] = fake_client

        async with app.test_request_context(
            "/", headers={"Host": "alpha.elderrms.app"}
        ):
            with patch.object(collab, "flag_enabled", return_value=True):
                enabled, reason = collab._is_collaboration_enabled(app, "user-1")
                assert enabled is True
                assert reason is None
                fake_client.validate.assert_not_called()

    def test_grpc_server_skips_license_validation_on_bypass_domain(self, monkeypatch):
        from apps.api.grpc import server as grpc_server

        monkeypatch.setenv("APP_DOMAIN", "alpha.elderrms.app")
        fake_app = SimpleNamespace(db=SimpleNamespace(engine=MagicMock()))
        fake_grpc_srv = MagicMock()
        fake_client = FakeLicenseClient(tier="community", valid=False)

        with (
            patch.object(grpc_server, "get_license_client", return_value=fake_client),
            patch.object(grpc_server.grpc, "server", return_value=fake_grpc_srv),
            patch.object(
                grpc_server.elder_pb2_grpc, "add_ElderServiceServicer_to_server"
            ),
            patch.object(grpc_server, "ElderServicer"),
            patch.object(grpc_server.reflection, "enable_server_reflection"),
            patch.object(grpc_server.signal, "signal"),
            patch.object(grpc_server.time, "sleep", side_effect=KeyboardInterrupt),
        ):
            grpc_server.serve(fake_app, require_license=True)

        assert fake_client.validate_calls == 0
        fake_grpc_srv.start.assert_called_once()


@pytest.mark.asyncio
class TestLicensingStatusEndpoint:
    """Tests for GET /api/v1/licensing/status."""

    async def test_requires_authentication(self, async_client):
        resp = await async_client.get("/api/v1/licensing/status")
        assert resp.status_code == 401

    async def test_returns_enterprise_with_domain_bypass_active(
        self, app, async_client
    ):
        app.extensions["license_client"] = None
        fake_user = SimpleNamespace(id=1, username="admin", is_superuser=True)

        with patch("apps.api.auth.decorators.get_current_user", return_value=fake_user):
            resp = await async_client.get(
                "/api/v1/licensing/status",
                headers={"X-Forwarded-Host": "alpha.elderrms.app:443"},
            )

        assert resp.status_code == 200
        data = await resp.get_json()
        assert data == {
            "tier": "enterprise",
            "status": "active",
            "domain_bypass": True,
            "bypass_domain": "alpha.elderrms.app",
            "limits": {
                "max_global_admins": None,
                "max_tenant_admins": None,
                "max_teams": None,
                "max_tenants": None,
                "max_objects": None,
                "max_nodes_per_type": None,
            },
            "features": {
                "multi_tenancy": True,
                "portal_users": True,
                "saml_sso": True,
                "scim_provisioning": True,
                "audit_logging": True,
                "compliance_reports": True,
                "access_reviews": True,
                "enterprise_integrations": True,
                "google_oauth_sso": True,
            },
        }

    async def test_returns_free_on_non_bypass_and_spoofed_domains(
        self, app, async_client
    ):
        app.extensions["license_client"] = None
        fake_user = SimpleNamespace(id=1, username="viewer", is_superuser=False)

        with patch("apps.api.auth.decorators.get_current_user", return_value=fake_user):
            resp = await async_client.get(
                "/api/v1/licensing/status",
                headers={"Host": "evilpenguintech.cloud"},
            )

        assert resp.status_code == 200
        data = await resp.get_json()
        assert data == {
            "tier": "free",
            "status": "active",
            "domain_bypass": False,
            "bypass_domain": None,
            "limits": {
                "max_global_admins": 1,
                "max_tenant_admins": 0,
                "max_teams": 1,
                "max_tenants": 1,
                "max_objects": 1000,
                "max_nodes_per_type": 1,
            },
            "features": {
                "multi_tenancy": False,
                "portal_users": False,
                "saml_sso": False,
                "scim_provisioning": False,
                "audit_logging": False,
                "compliance_reports": False,
                "access_reviews": False,
                "enterprise_integrations": False,
                "google_oauth_sso": False,
            },
        }

    async def test_returns_professional_features_correctly(self, app, async_client):
        app.extensions["license_client"] = FakeLicenseClient(tier="professional")
        fake_user = SimpleNamespace(id=1, username="admin", is_superuser=True)

        with patch("apps.api.auth.decorators.get_current_user", return_value=fake_user):
            resp = await async_client.get(
                "/api/v1/licensing/status",
                headers={"Host": "customer.example.com"},
            )

        assert resp.status_code == 200
        data = await resp.get_json()
        assert data["tier"] == "professional"
        assert data["domain_bypass"] is False
        assert data["bypass_domain"] is None
        assert data["features"]["google_oauth_sso"] is True
        assert data["features"]["multi_tenancy"] is False
        assert data["features"]["saml_sso"] is False
        assert data["limits"]["max_tenant_admins"] == 10
        assert data["limits"]["max_teams"] is None
