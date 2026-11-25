"""Unit tests for the application factory and request hooks in ``apps/api/main.py``.

Covers version resolution, startup failure handling, the before-request hooks
(claims population for every token type, CSRF double-submit, module
licensing/tenant enablement), fail-soft extension initialisers and the JSON
error handlers. Tokens are signed at runtime with the test app's own key.
"""

import os
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import jwt
import pytest
from penguin_aaa.middleware.asgi import AuditMiddleware
from quart import g
from werkzeug.exceptions import (
    BadRequest,
    Forbidden,
    InternalServerError,
    NotFound,
    TooManyRequests,
    Unauthorized,
)

from apps.api import main
from apps.api.auth import portal_cookies
from apps.api.auth.rbac import role_scopes
from apps.api.modules.runs.permissions import ALL_GRANTABLE_SCOPES

VERIFY = "apps.api.auth.jwt_handler.verify_token"
GRANTABLE = sorted(ALL_GRANTABLE_SCOPES)[0]


def _fake_token():
    """Opaque runtime placeholder for a bearer token."""
    return "-".join(["fake", "bearer", uuid4().hex[:8]])


def _sign(app, payload):
    """Sign a payload with the app's JWT key."""
    now = int(datetime.now(UTC).timestamp())
    secret = app.config.get("JWT_SECRET_KEY") or app.config.get("SECRET_KEY")
    body = {"iat": now, "exp": now + 120, **payload}
    return jwt.encode(body, secret, algorithm=app.config["JWT_ALGORITHM"])


async def _claims_for(app, payload=None, token=None):
    """Run the before-request chain with a bearer token; return g.claims (or None)."""
    headers = {"Authorization": f"Bearer {token or _fake_token()}"}
    async with app.test_request_context("/healthz", headers=headers) as ctx:
        if payload is None:
            await app.preprocess_request(ctx)
        else:
            with patch(VERIFY, return_value=payload):
                await app.preprocess_request(ctx)
        return getattr(g, "claims", None)


class TestResolveAppVersion:
    """_resolve_app_version prefers real config, then .version files."""

    def test_configured_version_wins(self):
        """A real configured version is returned without touching the filesystem."""
        with patch.object(Path, "read_text", side_effect=AssertionError("no IO")):
            assert main._resolve_app_version("4.2.1") == "4.2.1"

    @pytest.mark.parametrize("placeholder", [None, "", "0.0.0", "0.1.0"])
    def test_placeholder_reads_version_file(self, placeholder):
        """Placeholder versions are replaced by the first non-empty .version file."""
        with patch.object(Path, "read_text", side_effect=["  \n", " 9.8.7\n"]):
            assert main._resolve_app_version(placeholder) == "9.8.7"

    @pytest.mark.parametrize(
        "placeholder, expected", [(None, "0.0.0"), ("0.1.0", "0.1.0"), ("", "0.0.0")]
    )
    def test_no_version_file_falls_back(self, placeholder, expected):
        """Unreadable .version files fall back to the configured value or 0.0.0."""
        with patch.object(Path, "read_text", side_effect=OSError("missing")):
            assert main._resolve_app_version(placeholder) == expected


class TestAppFactory:
    """create_app / create_asgi_app wiring."""

    def test_database_unavailable_aborts_startup(self):
        """Startup fails loudly when the database cannot be reached."""
        with (
            patch.object(main, "init_telemetry", return_value=MagicMock()),
            patch.object(main, "setup_logging"),
            patch.object(main, "_init_extensions"),
            patch.object(
                main, "ensure_database_ready", return_value={"connected": False}
            ),
            patch.object(main, "log_startup_status") as log_status,
            pytest.raises(RuntimeError, match="database not available"),
        ):
            main.create_app("testing")
        log_status.assert_called_once_with({"connected": False})

    def test_asgi_app_wraps_quart_in_audit_and_tenant_middleware(self, app):
        """The ASGI entry point layers audit middleware around the Quart app."""
        with patch.object(main, "create_app", return_value=app) as factory:
            asgi = main.create_asgi_app("testing")
        factory.assert_called_once_with("testing")
        assert isinstance(asgi, AuditMiddleware)
        assert asgi is not app

    @pytest.mark.asyncio
    async def test_health_and_status_endpoints(self, app):
        """Health and status endpoints answer without authentication."""
        client = app.test_client()
        health = await client.get("/healthz")
        status = await client.get("/api/v1/status")
        assert health.status_code == 200
        assert await health.get_json() == {"status": "healthy", "service": "elder"}
        body = await status.get_json()
        assert status.status_code == 200
        assert body["status"] == "operational"
        assert body["version"] == app.config["APP_VERSION"]


class TestPopulateClaims:
    """populate_claims maps each token type to scoped g.claims."""

    @pytest.mark.asyncio
    async def test_no_or_invalid_token_sets_no_claims(self, app):
        """Anonymous and unverifiable requests carry no claims."""
        async with app.test_request_context("/healthz") as ctx:
            await app.preprocess_request(ctx)
            assert getattr(g, "claims", None) is None
        assert await _claims_for(app, token="garbage." + uuid4().hex) is None

    @pytest.mark.asyncio
    async def test_identity_token_claims(self, app):
        """A signed access token yields its tenant, scopes and identity id."""
        token = _sign(
            app,
            {
                "sub": "41",
                "tenant": "7",
                "scope": ["a:read", " b:write ", 5, ""],
                "roles": ["x"],
            },
        )
        claims = await _claims_for(app, token=token)
        assert claims["identity_id"] == 41
        assert claims["tenant"] == "7"
        assert claims["scope"] == ["a:read", "b:write"]
        assert claims["roles"] == ["x"]

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "payload, scopes, roles, identity_id",
        [
            (
                {"sub": "5", "scope": "a:read  b:read", "roles": "r1 r2"},
                ["a:read", "b:read"],
                ["r1", "r2"],
                5,
            ),
            ({"sub": "svc", "scope": 9, "roles": None}, [], [], None),
            (
                {"sub": None, "scope": ("a:read",), "roles": {"r"}},
                ["a:read"],
                ["r"],
                None,
            ),
        ],
    )
    async def test_scope_and_role_normalisation(
        self, app, payload, scopes, roles, identity_id
    ):
        """String/sequence/garbage scope and role claims are normalised safely."""
        claims = await _claims_for(app, {"tenant": "3", **payload})
        assert claims["scope"] == scopes
        assert claims["roles"] == roles
        assert claims["identity_id"] == identity_id

    @pytest.mark.asyncio
    async def test_tenant_id_fallback_claim(self, app):
        """A legacy ``tenant_id`` claim is used when ``tenant`` is absent."""
        claims = await _claims_for(app, {"sub": "1", "tenant_id": 12})
        assert claims["tenant"] == 12

    @pytest.mark.asyncio
    async def test_portal_user_derives_scopes_from_role(self, app):
        """Portal tokens without embedded scopes get their effective role's scopes."""
        derived = await _claims_for(
            app,
            {"sub": "8", "type": "portal_user", "tenant": "2", "global_role": "admin"},
        )
        explicit = await _claims_for(
            app,
            {
                "sub": "8",
                "type": "portal_user",
                "tenant": "2",
                "global_role": "admin",
                "scope": ["only:this"],
                "roles": ["custom"],
            },
        )
        assert derived["roles"] == ["admin"]
        assert derived["scope"] == role_scopes("admin")
        assert explicit["scope"] == ["only:this"]
        assert explicit["roles"] == ["custom"]

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "raw, expected",
        [
            ("entities:read entities:write", ["entities:read"]),
            (["entities:read", "*:read", 3], ["entities:read"]),
            ({"entities:read": 1}, []),
        ],
    )
    async def test_researcher_claims_are_read_only(self, app, raw, expected):
        """Researcher claims keep only concrete read scopes and the viewer role."""
        claims = await _claims_for(
            app,
            {
                "sub": "service-researcher:2:9",
                "type": "service_researcher",
                "tenant": "2",
                "scope": raw,
                "delegated_by": "9",
            },
        )
        assert claims["scope"] == expected
        assert claims["roles"] == ["viewer"]
        assert claims["delegated_by"] == "9"
        assert claims["identity_id"] is None

    @pytest.mark.asyncio
    async def test_worker_claims_filter_scopes_and_carry_worker_ids(self, app):
        """Worker claims drop non-allowlisted scopes and expose remote worker ids."""
        base = {
            "sub": "svc-scanner",
            "type": "service_worker",
            "tenant": "system",
            "scope": ["discovery:read", "secrets:admin"],
        }
        plain = await _claims_for(app, dict(base))
        remote = await _claims_for(
            app, {**base, "remote_worker_id": "w-1", "remote_worker_db_id": 6}
        )
        unknown = await _claims_for(app, {**base, "sub": "svc-nobody"})
        assert plain["scope"] == ["discovery:read"]
        assert "remote_worker_id" not in plain
        assert (remote["remote_worker_id"], remote["remote_worker_db_id"]) == ("w-1", 6)
        assert unknown["scope"] == []

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "raw, expected",
        [
            (f"{GRANTABLE} bogus:scope", [GRANTABLE]),
            ([GRANTABLE, "bogus:scope"], [GRANTABLE]),
            (None, []),
        ],
    )
    async def test_coderun_claims(self, app, raw, expected):
        """Coderun claims keep only grantable scopes plus function/execution ids."""
        claims = await _claims_for(
            app,
            {
                "sub": "coderun:1:2:e",
                "type": "coderun",
                "tenant": "1",
                "scope": raw,
                "function_id": 2,
                "execution_id": "e",
            },
        )
        assert claims["scope"] == expected
        assert claims["roles"] == ["coderun"]
        assert (claims["function_id"], claims["execution_id"]) == (2, "e")


class TestCsrfProtection:
    """Double-submit CSRF enforcement for cookie-authenticated writes."""

    async def _post(
        self, app, path="/healthz", cookies=None, headers=None, method="POST"
    ):
        """Run the before-request chain for a request; return its short-circuit result."""
        hdrs = dict(headers or {})
        if cookies:
            hdrs["Cookie"] = "; ".join(f"{k}={v}" for k, v in cookies.items())
        async with app.test_request_context(path, method=method, headers=hdrs) as ctx:
            return await app.preprocess_request(ctx)

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "cookie",
        [portal_cookies.ACCESS_COOKIE_NAME, portal_cookies.REFRESH_COOKIE_NAME],
    )
    async def test_cookie_session_without_csrf_header_rejected(self, app, cookie):
        """A cookie-authenticated write lacking the CSRF header gets 403."""
        result = await self._post(app, cookies={cookie: _fake_token()})
        resp, status = result
        assert status == 403
        assert (await resp.get_json())["error"] == "CSRF token missing or invalid"

    @pytest.mark.asyncio
    async def test_mismatched_csrf_rejected_and_matching_allowed(self, app):
        """The header must equal the CSRF cookie for the write to proceed."""
        csrf = uuid4().hex
        cookies = {
            portal_cookies.ACCESS_COOKIE_NAME: _fake_token(),
            portal_cookies.CSRF_COOKIE_NAME: csrf,
        }
        bad = await self._post(
            app, cookies=cookies, headers={portal_cookies.CSRF_HEADER_NAME: csrf + "x"}
        )
        good = await self._post(
            app, cookies=cookies, headers={portal_cookies.CSRF_HEADER_NAME: csrf}
        )
        assert bad[1] == 403
        assert good is None

    @pytest.mark.asyncio
    async def test_exemptions(self, app):
        """Safe methods, login paths, bearer callers and cookie-less requests pass."""
        cookies = {portal_cookies.ACCESS_COOKIE_NAME: _fake_token()}
        assert await self._post(app, cookies=cookies, method="GET") is None
        assert await self._post(app, path="/api/v1/auth/login", cookies=cookies) is None
        assert (
            await self._post(
                app,
                cookies=cookies,
                headers={"Authorization": f"Bearer {_fake_token()}"},
            )
            is None
        )
        assert await self._post(app) is None


class TestModuleEnforcement:
    """enforce_module_access: license (layer 2) and tenant toggle (layer 3)."""

    def _module_route(self, app):
        """Pick a parameter-free GET route that belongs to a feature module."""
        by_bp = app.extensions["elder_module_by_blueprint"]
        for rule in app.url_map.iter_rules():
            blueprint = (
                rule.endpoint.rsplit(".", 1)[0] if "." in rule.endpoint else None
            )
            if blueprint in by_bp and not rule.arguments and "GET" in rule.methods:
                return rule.rule, by_bp[blueprint]
        pytest.fail("no module route registered")

    async def _run(self, app, claims, path=None):
        """Run the hooks for a module route with ``claims``; return the result."""
        path = path or self._module_route(app)[0]
        headers = {"Authorization": f"Bearer {_fake_token()}"}
        async with app.test_request_context(path, headers=headers) as ctx:
            with patch(VERIFY, return_value=claims):
                return await app.preprocess_request(ctx)

    @pytest.mark.asyncio
    async def test_unlicensed_module_denied(self, app):
        """A module that is not licensed answers 403 MODULE_UNLICENSED."""
        _, module = self._module_route(app)
        with patch(
            "apps.api.common.modules.licensing.module_licensed", return_value=False
        ):
            resp, status = await self._run(app, {"sub": "1", "tenant": "1"})
        body = await resp.get_json()
        assert status == 403
        assert (body["error"], body["module"]) == ("MODULE_UNLICENSED", module)

    @pytest.mark.asyncio
    async def test_tenant_disabled_module_denied_per_tenant(self, app):
        """A module disabled for tenant A is denied for A but allowed for tenant B."""
        _, module = self._module_route(app)

        def enabled(db, redis_client, tenant_id, module_name, default):
            return tenant_id != 101

        with (
            patch(
                "apps.api.common.modules.licensing.module_licensed", return_value=True
            ),
            patch(
                "apps.api.common.modules.tenant_toggle.is_module_enabled",
                side_effect=enabled,
            ),
        ):
            denied = await self._run(app, {"sub": "1", "tenant": "101"})
            allowed = await self._run(app, {"sub": "1", "tenant": "102"})
        body = await denied[0].get_json()
        assert denied[1] == 403
        assert (body["error"], body["module"]) == ("MODULE_DISABLED", module)
        assert allowed is None

    @pytest.mark.asyncio
    async def test_toggle_skipped_for_system_worker_bad_tenant_and_anonymous(self, app):
        """System workers, unparsable tenants and tenant-less callers skip the toggle."""
        toggle = "apps.api.common.modules.tenant_toggle.is_module_enabled"
        worker = {
            "sub": "svc-scanner",
            "type": "service_worker",
            "tenant": "system",
            "scope": ["discovery:read"],
        }
        with (
            patch(
                "apps.api.common.modules.licensing.module_licensed", return_value=True
            ),
            patch(toggle, return_value=False) as is_enabled,
        ):
            assert await self._run(app, worker) is None
            assert await self._run(app, {"sub": "1", "tenant": "not-a-number"}) is None
            assert await self._run(app, {"sub": "1"}) is None
        is_enabled.assert_not_called()

    @pytest.mark.asyncio
    async def test_infra_failures_fail_open(self, app):
        """Toggle or licensing infrastructure errors never hard-deny a request."""
        licensed = "apps.api.common.modules.licensing.module_licensed"
        toggle = "apps.api.common.modules.tenant_toggle.is_module_enabled"
        with (
            patch(licensed, return_value=True),
            patch(toggle, side_effect=RuntimeError("redis down")),
        ):
            assert await self._run(app, {"sub": "1", "tenant": "5"}) is None
        with patch(licensed, side_effect=RuntimeError("license server down")):
            assert await self._run(app, {"sub": "1", "tenant": "5"}) is None

    @pytest.mark.asyncio
    async def test_missing_manifest_and_core_routes_allowed(self, app):
        """A blueprint with no manifest and core routes are not module-gated."""
        licensed = "apps.api.common.modules.licensing.module_licensed"
        with patch(licensed, return_value=False) as check:
            with patch.dict(app.extensions, {"elder_modules": {}}):
                assert await self._run(app, {"sub": "1", "tenant": "5"}) is None
            assert (
                await self._run(app, {"sub": "1", "tenant": "5"}, path="/healthz")
                is None
            )
        check.assert_not_called()


class TestExtensionInitialisers:
    """Fail-soft startup initialisers."""

    def _app(self, **config):
        """Minimal app stand-in with config/extensions/db."""
        return SimpleNamespace(config=config, extensions={}, db=MagicMock())

    def test_license_client_success_and_failure(self):
        """The license client is stored on success and None on failure."""
        fake = self._app()
        client = MagicMock()
        client.validate.return_value = SimpleNamespace(tier="enterprise")
        with patch("penguin_licensing.get_license_client", return_value=client):
            main._init_license_client(fake)
        assert fake.extensions["license_client"] is client
        with patch(
            "penguin_licensing.get_license_client", side_effect=RuntimeError("down")
        ):
            main._init_license_client(fake)
        assert fake.extensions["license_client"] is None

    def test_redis_client_skipped_without_url(self):
        """No REDIS_URL means module Redis is explicitly disabled."""
        fake = self._app(REDIS_URL="")
        main._init_redis_client(fake)
        assert fake.extensions["module_redis"] is None
        assert fake.redis_client is None

    def test_redis_client_connects_to_real_redis(self):
        """A reachable Redis is pinged and cached on the app."""
        fake = self._app(REDIS_URL=os.environ["REDIS_URL"])
        main._init_redis_client(fake)
        assert fake.redis_client is fake.extensions["module_redis"]
        assert fake.redis_client.ping() is True

    def test_redis_client_unreachable_fails_soft(self):
        """An unreachable Redis leaves the client unset instead of crashing."""
        fake = self._app(REDIS_URL="redis://127.0.0.1:1/0")
        main._init_redis_client(fake)
        assert fake.extensions["module_redis"] is None
        assert fake.redis_client is None

    @pytest.mark.parametrize(
        "func, target",
        [
            (
                main._init_access_review_scheduler,
                "apps.api.services.access_review.scheduler.init_scheduler",
            ),
            (
                main._init_audit_retention_scheduler,
                "apps.api.services.audit.scheduler.init_scheduler",
            ),
        ],
    )
    def test_scheduler_init_success_and_failure(self, func, target):
        """Schedulers start with the app DB; a failing scheduler does not abort startup."""
        fake = self._app()
        with patch(target) as init:
            func(fake)
        init.assert_called_once_with(fake.db)
        with patch(target, side_effect=RuntimeError("scheduler broken")) as init:
            func(fake)
        init.assert_called_once_with(fake.db)

    def test_service_node_registration(self, app, monkeypatch):
        """The pod registers itself after the node-limit check passes."""
        monkeypatch.setenv("ELDER_SERVICE_TYPE", "cov-main")
        monkeypatch.setenv("HOSTNAME", "cov-pod-1")
        with (
            patch(
                "apps.api.common.licensing.enforce.check_limit", new=AsyncMock()
            ) as check,
            patch("apps.api.models.service_node.register_node") as register,
        ):
            main._init_service_node_registration(app)
        check.assert_awaited_once_with("node", None)
        register.assert_called_once_with(app.db, "cov-main", "cov-pod-1")

    def test_service_node_registration_failure_is_non_fatal(self, app):
        """A failing node-limit check skips registration without raising."""
        with (
            patch(
                "apps.api.common.licensing.enforce.check_limit",
                new=AsyncMock(side_effect=RuntimeError("limit backend down")),
            ),
            patch("apps.api.models.service_node.register_node") as register,
        ):
            main._init_service_node_registration(app)
        register.assert_not_called()

    def test_load_modules_failure_propagates(self):
        """A module-resolution failure aborts startup (fail loud)."""
        with patch(
            "apps.api.modules.registry.resolve_enabled",
            side_effect=ValueError("bad env"),
        ):
            with pytest.raises(ValueError, match="bad env"):
                main._load_modules(MagicMock())


class TestErrorHandlers:
    """HTTP errors are rendered as the generic JSON envelope."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "exc, status, error, message",
        [
            (BadRequest("leaky detail"), 400, "Bad Request", "Invalid request"),
            (Unauthorized(), 401, "Unauthorized", "Authentication required"),
            (Forbidden(), 403, "Forbidden", "Insufficient permissions"),
            (NotFound(), 404, "Not Found", "Resource not found"),
            (TooManyRequests(), 429, "Rate Limit Exceeded", "Too many requests"),
            (
                InternalServerError("stack"),
                500,
                "Internal Server Error",
                "An error occurred",
            ),
        ],
    )
    async def test_json_error_envelope(self, app, exc, status, error, message):
        """Each handled status returns its fixed, detail-free JSON body."""
        async with app.test_request_context("/healthz") as ctx:
            resp, code = await app.handle_http_exception(ctx, exc)
            body = await resp.get_json()
        assert code == status
        assert body == {"error": error, "message": message}

    @pytest.mark.asyncio
    async def test_unknown_api_route_returns_json_404(self, app):
        """An unrouted API path produces a JSON 404 through the real client."""
        resp = await app.test_client().get(f"/api/v1/no-such-route-{uuid4().hex[:6]}")
        assert resp.status_code == 404
        assert (await resp.get_json())["error"].lower() == "not found"
