"""Coverage tests for the external-provider secrets HTTP routes (real client + real DB)."""

# flake8: noqa: E501

import json
import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import flask
import jwt
import pytest
import pytest_asyncio

from apps.api.services.secrets.base import (
    SecretAccessDeniedException,
    SecretMetadata,
    SecretNotFoundException,
    SecretProviderClient,
    SecretProviderException,
    SecretValue,
)
from apps.api.services.secrets.service import SecretsService
from apps.api.utils.async_utils import run_in_threadpool

_REAL_FLASK_G = flask.g
BASE = "/api/v1/secrets"
ALL_SCOPES = ["secrets:read", "secrets:write", "secrets:admin"]


class FakeClient(SecretProviderClient):
    """Provider client double driven by its config dict (the external boundary)."""

    def _validate_config(self):
        """Accept any config."""

    def test_connection(self):
        """Report the configured connection result."""
        return self.config.get("connect", True)

    def get_secret(self, path, version=None):
        """Return a value or raise per config."""
        mode = self.config.get("mode", "ok")
        if mode == "missing":
            raise SecretNotFoundException(path)
        if mode == "denied":
            raise SecretAccessDeniedException(path)
        if mode == "boom":
            raise SecretProviderException("down")
        return SecretValue(name=path, value="route-val", is_masked=False)

    def list_secrets(self, prefix=None):
        """Return the configured paths, or fail when mode is boom."""
        if self.config.get("mode") == "boom":
            raise SecretProviderException("down")
        return [
            SecretMetadata(name=p, path=p, is_kv=False)
            for p in self.config.get("paths", [])
        ]

    def create_secret(self, path, value, metadata=None):
        """Unused."""
        raise NotImplementedError

    def update_secret(self, path, value):
        """Unused."""
        raise NotImplementedError

    def delete_secret(self, path, force=False):
        """Unused."""
        raise NotImplementedError

    def get_secret_versions(self, path):
        """Unused."""
        raise NotImplementedError


@pytest.fixture(autouse=True)
def fake_provider():
    """Route every provider type to FakeClient for the duration of the test."""
    mapping = {k: FakeClient for k in SecretsService.PROVIDER_CLIENTS}
    with patch.dict(SecretsService.PROVIDER_CLIENTS, mapping):
        yield


class _StubG:
    """Stand-in for ``flask.g`` carrying the authenticated identity id."""

    def __init__(self, identity_id):
        """Hold a current_user exposing only the id the routes read."""
        self.current_user = type("U", (), {"id": identity_id})()


@pytest.fixture
def flask_g(world):
    """Patch ``flask.g`` (imported inside the routes) with a stub for the identity.

    The routes import Flask's ``g`` rather than Quart's, which has no app
    context here (source bug, see test_flask_g_import_breaks_routes); the stub
    lets the remaining route logic be exercised.
    """
    with patch("flask.g", _StubG(world["identity"])):
        yield


def _tok(app, tenant_id, identity_id, scopes):
    """Mint a signed JWT for an existing identity with the given scopes."""
    payload = {
        "sub": str(identity_id),
        "iat": datetime.now(UTC),
        "exp": datetime.now(UTC) + timedelta(hours=1),
        "scope": scopes,
        "tenant": str(tenant_id),
        "roles": ["admin"],
        "teams": [],
    }
    secret = app.config.get("JWT_SECRET_KEY") or app.config.get("SECRET_KEY")
    return jwt.encode(payload, secret, algorithm="HS256")


async def _call(app, method, url, token=None, **kw):
    """Issue a test-client request and return (status, parsed JSON or None)."""
    client = app.test_client()
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    resp = await getattr(client, method)(url, headers=headers, **kw)
    body = await resp.get_data()
    try:
        data = json.loads(body)
    except ValueError:
        data = None
    return resp.status_code, data


@pytest_asyncio.fixture
async def world(app):
    """A tenant+org, identity and tokens for each scope level."""
    db = app.db
    sfx = uuid.uuid4().hex[:8]

    def _setup():
        now = datetime.now(UTC)
        tid = db.tenants.insert(name=f"rt-{sfx}", slug=f"rt-{sfx}", is_active=True)
        oid = db.organizations.insert(
            name=f"rorg-{sfx}", tenant_id=tid, created_at=now, updated_at=now
        )
        ident = db.identities.insert(
            identity_type="human",
            username=f"rt-user-{sfx}",
            email=f"rt-{sfx}@example.com",
            tenant_id=tid,
            auth_provider="local",
            is_active=True,
            is_superuser=False,
            mfa_enabled=False,
            must_change_password=False,
            portal_role="observer",
            created_at=now,
            updated_at=now,
        )
        db.commit()
        return {"tenant": tid, "org": oid, "identity": ident}

    w = await run_in_threadpool(_setup)
    w["sfx"] = sfx
    w["token"] = _tok(app, w["tenant"], w["identity"], ALL_SCOPES)
    w["ro"] = _tok(app, w["tenant"], w["identity"], ["secrets:read"])
    w["rw"] = _tok(app, w["tenant"], w["identity"], ["secrets:read", "secrets:write"])
    w["none"] = _tok(app, w["tenant"], w["identity"], [])
    return w


async def _mk_provider(app, world, **cfg):
    """Create a provider via the API and return its JSON."""
    status, data = await _call(
        app,
        "post",
        f"{BASE}/providers",
        world["token"],
        json={
            "name": f"rp-{uuid.uuid4().hex[:6]}",
            "provider": "aws_secrets_manager",
            "organization_id": world["org"],
            "config_json": cfg,
        },
    )
    assert status == 201, data
    return data["provider"]


async def _mk_secret(app, world, prov, path=None):
    """Register a secret via the API and return its JSON."""
    status, data = await _call(
        app,
        "post",
        BASE,
        world["token"],
        json={
            "name": f"rs-{uuid.uuid4().hex[:6]}",
            "provider_id": prov["id"],
            "provider_path": path or f"/r/{uuid.uuid4().hex[:8]}",
            "secret_type": "password",
            "organization_id": world["org"],
        },
    )
    assert status == 201, data
    return data["secret"]


def _set_mode(app, prov_id, mode):
    """Return a callable flipping a provider's fake-client mode in the DB."""

    def _do():
        app.db(app.db.secret_providers.id == prov_id).update(config_json={"mode": mode})
        app.db.commit()

    return _do


ENDPOINTS = [
    ("get", BASE, "secrets:read"),
    ("get", BASE + "/1", "secrets:read"),
    ("post", BASE + "/1/unmask", "secrets:write"),
    ("post", BASE, "secrets:write"),
    ("put", BASE + "/1", "secrets:write"),
    ("delete", BASE + "/1", "secrets:write"),
    ("post", BASE + "/1/sync", "secrets:write"),
    ("get", BASE + "/1/access-log", "secrets:read"),
    ("get", BASE + "/providers", "secrets:read"),
    ("post", BASE + "/providers", "secrets:admin"),
    ("get", BASE + "/providers/1", "secrets:read"),
    ("put", BASE + "/providers/1", "secrets:admin"),
    ("delete", BASE + "/providers/1", "secrets:admin"),
    ("post", BASE + "/providers/1/sync", "secrets:admin"),
    ("post", BASE + "/providers/1/test", "secrets:admin"),
]


class TestAuth:
    """Authentication and scope enforcement across all routes."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("method,url,scope", ENDPOINTS)
    async def test_no_token_is_401(self, app, method, url, scope):
        """Unauthenticated calls get 401."""
        kw = {"json": {}} if method in ("post", "put") else {}
        status, _ = await _call(app, method, url, None, **kw)
        assert status == 401

    @pytest.mark.asyncio
    @pytest.mark.parametrize("method,url,scope", ENDPOINTS)
    async def test_missing_scope_is_403(self, app, world, method, url, scope):
        """A token without any secrets scope gets 403."""
        kw = {"json": {}} if method in ("post", "put") else {}
        status, _ = await _call(app, method, url, world["none"], **kw)
        assert status == 403

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "method,url",
        [
            ("post", BASE + "/providers"),
            ("put", BASE + "/providers/1"),
            ("delete", BASE + "/providers/1"),
            ("post", BASE + "/providers/1/sync"),
            ("post", BASE + "/providers/1/test"),
        ],
    )
    async def test_write_scope_cannot_admin_providers(self, app, world, method, url):
        """secrets:write is insufficient for provider administration."""
        kw = {"json": {}} if method in ("post", "put") else {}
        status, _ = await _call(app, method, url, world["rw"], **kw)
        assert status == 403

    @pytest.mark.asyncio
    async def test_read_scope_cannot_create_secret(self, app, world):
        """A read-only token cannot register secrets."""
        status, _ = await _call(app, "post", BASE, world["ro"], json={"name": "x"})
        assert status == 403


@pytest.fixture(autouse=True)
def _use_flask_g(request):
    """Apply the flask.g stub to every test except the explicit bug test."""
    if request.node.name == "test_flask_g_import_breaks_routes":
        yield
        return
    request.getfixturevalue("flask_g")
    yield


class TestProviderRoutes:
    """Provider endpoints."""

    @pytest.mark.asyncio
    async def test_provider_lifecycle(self, app, world):
        """Create, list (filtered), get, update and test a provider."""
        prov = await _mk_provider(app, world)
        status, data = await _call(
            app,
            "get",
            f"{BASE}/providers?organization_id={world['org']}&provider_type=aws_secrets_manager",
            world["token"],
        )
        assert status == 200
        assert prov["id"] in [p["id"] for p in data["providers"]]
        assert data["total"] == len(data["providers"])

        status, data = await _call(
            app, "get", f"{BASE}/providers/{prov['id']}", world["token"]
        )
        assert status == 200 and data["provider"]["name"] == prov["name"]

        status, data = await _call(
            app,
            "put",
            f"{BASE}/providers/{prov['id']}",
            world["token"],
            json={"name": f"upd-{world['sfx']}", "enabled": True},
        )
        assert status == 200 and data["provider"]["name"] == f"upd-{world['sfx']}"

        status, data = await _call(
            app, "post", f"{BASE}/providers/{prov['id']}/test", world["token"]
        )
        assert status == 200 and data["success"] is True

    @pytest.mark.asyncio
    async def test_create_validation(self, app, world):
        """Missing body, missing fields and bad provider type are 400."""
        url = f"{BASE}/providers"
        status, _ = await _call(app, "post", url, world["token"], json={})
        assert status == 400
        status, data = await _call(app, "post", url, world["token"], json={"name": "x"})
        assert status == 400 and "provider" in data["missing_fields"]
        status, _ = await _call(
            app,
            "post",
            url,
            world["token"],
            json={
                "name": "x",
                "provider": "nope",
                "organization_id": world["org"],
                "config_json": {},
            },
        )
        assert status == 400

    @pytest.mark.asyncio
    async def test_get_update_missing(self, app, world):
        """Unknown provider ids give 404 (get) and 400 (update, ValueError mapping)."""
        status, _ = await _call(
            app, "get", f"{BASE}/providers/2000000000", world["token"]
        )
        assert status == 404
        status, _ = await _call(
            app,
            "put",
            f"{BASE}/providers/2000000000",
            world["token"],
            json={"name": "x"},
        )
        assert status == 400

    @pytest.mark.asyncio
    async def test_update_requires_body(self, app, world):
        """An empty update body is 400."""
        prov = await _mk_provider(app, world)
        status, _ = await _call(
            app, "put", f"{BASE}/providers/{prov['id']}", world["token"], json={}
        )
        assert status == 400

    @pytest.mark.asyncio
    async def test_delete_in_use_and_missing(self, app, world):
        """Deleting a provider in use is 400; an unknown provider is 404."""
        prov = await _mk_provider(app, world)
        await _mk_secret(app, world, prov)
        status, data = await _call(
            app, "delete", f"{BASE}/providers/{prov['id']}", world["token"]
        )
        assert status == 400 and data["error"] == "Provider in use"
        status, data = await _call(
            app, "delete", f"{BASE}/providers/2000000000", world["token"]
        )
        assert status == 404 and data["error"] == "Provider not found"

    @pytest.mark.asyncio
    @pytest.mark.xfail(
        strict=True,
        reason="bug: service.py:579 `del db.secret_providers[id]` unsupported by penguin-dal -> delete route returns 500",
    )
    async def test_delete_unused_provider_204(self, app, world):
        """Deleting an unused provider returns 204."""
        prov = await _mk_provider(app, world)
        status, _ = await _call(
            app, "delete", f"{BASE}/providers/{prov['id']}", world["token"]
        )
        assert status == 204

    @pytest.mark.asyncio
    async def test_sync_provider(self, app, world):
        """Provider sync reports counts; unknown -> 404; provider error -> 500."""
        known = f"/k/{world['sfx']}"
        prov = await _mk_provider(app, world, paths=[known])
        await _mk_secret(app, world, prov, path=known)
        status, data = await _call(
            app, "post", f"{BASE}/providers/{prov['id']}/sync", world["token"]
        )
        assert status == 200 and data["secrets_synced"] == 1
        status, _ = await _call(
            app, "post", f"{BASE}/providers/2000000000/sync", world["token"]
        )
        assert status == 404
        await run_in_threadpool(_set_mode(app, prov["id"], "boom"))
        status, _ = await _call(
            app, "post", f"{BASE}/providers/{prov['id']}/sync", world["token"]
        )
        assert status == 500

    @pytest.mark.asyncio
    async def test_test_connection_failure_is_400(self, app, world):
        """A provider whose connection test fails yields 400 success=false."""
        prov = await _mk_provider(app, world)

        def _flip():
            app.db(app.db.secret_providers.id == prov["id"]).update(
                config_json={"connect": False}
            )
            app.db.commit()

        await run_in_threadpool(_flip)
        status, data = await _call(
            app, "post", f"{BASE}/providers/{prov['id']}/test", world["token"]
        )
        assert status == 400 and data["success"] is False


class TestSecretRoutes:
    """Secret endpoints."""

    @pytest.mark.asyncio
    @pytest.mark.xfail(
        strict=True,
        reason="bug: routes/secrets.py:77,120,198,244,282,309 import flask.g (not quart.g) -> RuntimeError, secret routes 500",
    )
    async def test_flask_g_import_breaks_routes(self, app, world):
        """Fetching a secret should not 500 because of a Flask/Quart g mix-up."""
        prov = await _mk_provider(app, world)
        with patch("flask.g", _REAL_FLASK_G):
            status, _ = await _call(
                app,
                "post",
                BASE,
                world["token"],
                json={
                    "name": "x",
                    "provider_id": prov["id"],
                    "provider_path": "/p",
                    "secret_type": "password",
                    "organization_id": world["org"],
                },
            )
        assert status == 201

    @pytest.mark.asyncio
    async def test_create_get_list_update_log(self, app, world):
        """Full happy path: create, list, get masked/unmasked, update, audit log."""
        prov = await _mk_provider(app, world)
        sec = await _mk_secret(app, world, prov)

        status, data = await _call(
            app,
            "get",
            f"{BASE}?organization_id={world['org']}&provider_id={prov['id']}&secret_type=password",
            world["token"],
        )
        assert status == 200 and [s["id"] for s in data["secrets"]] == [sec["id"]]

        status, data = await _call(app, "get", f"{BASE}/{sec['id']}", world["token"])
        assert status == 200
        assert data["secret"]["value"] == "***MASKED***"

        status, data = await _call(
            app, "get", f"{BASE}/{sec['id']}?unmask=true", world["token"]
        )
        assert status == 200 and data["secret"]["value"] == "route-val"

        status, data = await _call(
            app, "post", f"{BASE}/{sec['id']}/unmask", world["token"]
        )
        assert status == 200 and data["secret"]["value"] == "route-val"

        status, data = await _call(
            app,
            "put",
            f"{BASE}/{sec['id']}",
            world["token"],
            json={"name": f"ren-{world['sfx']}", "metadata": {"a": 1}},
        )
        assert status == 200 and data["secret"]["name"] == f"ren-{world['sfx']}"

        status, data = await _call(
            app, "get", f"{BASE}/{sec['id']}/access-log?limit=50", world["token"]
        )
        assert status == 200 and data["total"] == len(data["access_log"])
        actions = {e["action"] for e in data["access_log"]}
        assert {"create", "view_masked", "view_unmasked", "update"} <= actions

    @pytest.mark.asyncio
    async def test_create_validation(self, app, world):
        """Empty body, missing fields and an unknown provider are all 400."""
        status, _ = await _call(app, "post", BASE, world["token"], json={})
        assert status == 400
        status, data = await _call(
            app, "post", BASE, world["token"], json={"name": "x"}
        )
        assert status == 400 and "provider_id" in data["missing_fields"]
        status, _ = await _call(
            app,
            "post",
            BASE,
            world["token"],
            json={
                "name": "x",
                "provider_id": 2_000_000_000,
                "provider_path": "/p",
                "secret_type": "password",
                "organization_id": world["org"],
            },
        )
        assert status == 400

    @pytest.mark.asyncio
    async def test_create_path_missing_in_provider_is_400(self, app, world):
        """Registering a path the provider does not have is rejected (400)."""
        prov = await _mk_provider(app, world)
        await run_in_threadpool(_set_mode(app, prov["id"], "missing"))
        status, _ = await _call(
            app,
            "post",
            BASE,
            world["token"],
            json={
                "name": "x",
                "provider_id": prov["id"],
                "provider_path": "/nope",
                "secret_type": "password",
                "organization_id": world["org"],
            },
        )
        assert status == 400

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "method,suffix",
        [
            ("get", ""),
            ("post", "/unmask"),
            ("put", ""),
            ("post", "/sync"),
        ],
    )
    async def test_unknown_secret_is_404(self, app, world, method, suffix):
        """Operations on an unknown secret id map ValueError to 404."""
        kw = {"json": {"name": "x"}} if method in ("post", "put") else {}
        status, _ = await _call(
            app, method, f"{BASE}/2000000000{suffix}", world["token"], **kw
        )
        assert status == 404

    @pytest.mark.asyncio
    async def test_delete_unknown_secret_is_404(self, app, world):
        """Deleting an unknown secret returns 404."""
        status, _ = await _call(app, "delete", f"{BASE}/2000000000", world["token"])
        assert status == 404

    @pytest.mark.asyncio
    @pytest.mark.xfail(
        strict=True,
        reason="bug: service.py:352 `del db.secrets[id]` unsupported by penguin-dal -> delete route returns 500",
    )
    async def test_delete_secret_204(self, app, world):
        """Deleting a registered secret returns 204."""
        prov = await _mk_provider(app, world)
        sec = await _mk_secret(app, world, prov)
        status, _ = await _call(app, "delete", f"{BASE}/{sec['id']}", world["token"])
        assert status == 204

    @pytest.mark.asyncio
    async def test_update_requires_body(self, app, world):
        """An empty update body is 400."""
        prov = await _mk_provider(app, world)
        sec = await _mk_secret(app, world, prov)
        status, _ = await _call(
            app, "put", f"{BASE}/{sec['id']}", world["token"], json={}
        )
        assert status == 400

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "method,suffix",
        [("get", ""), ("post", "/unmask"), ("post", "/sync")],
    )
    @pytest.mark.parametrize(
        "mode,expected",
        [("missing", 404), ("denied", 403), ("boom", 500)],
    )
    async def test_provider_errors_map_to_status(
        self, app, world, method, suffix, mode, expected
    ):
        """Provider not-found/denied/other failures map to 404/403/500."""
        prov = await _mk_provider(app, world)
        sec = await _mk_secret(app, world, prov)
        await run_in_threadpool(_set_mode(app, prov["id"], mode))
        status, data = await _call(
            app, method, f"{BASE}/{sec['id']}{suffix}", world["token"]
        )
        if mode == "denied" and suffix == "" and method == "get":
            assert status == 403
        else:
            assert status == expected
        assert "Traceback" not in json.dumps(data)

    @pytest.mark.asyncio
    async def test_sync_secret_ok(self, app, world):
        """Syncing a secret returns the refreshed record."""
        prov = await _mk_provider(app, world)
        sec = await _mk_secret(app, world, prov)
        status, data = await _call(
            app, "post", f"{BASE}/{sec['id']}/sync", world["token"]
        )
        assert status == 200 and data["secret"]["id"] == sec["id"]

    @pytest.mark.asyncio
    async def test_access_log_service_failure_is_500(self, app, world):
        """An unexpected service error on the access log yields a 500 JSON error."""
        with patch.object(
            SecretsService, "get_secret_access_log", side_effect=RuntimeError("db")
        ):
            status, data = await _call(
                app, "get", f"{BASE}/1/access-log", world["token"]
            )
        assert status == 500 and "error" in data

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "method,url",
        [
            ("get", BASE),
            ("get", BASE + "/providers"),
            ("get", BASE + "/providers/1"),
            ("post", BASE + "/providers/1/test"),
        ],
    )
    async def test_unexpected_service_error_is_500_generic(
        self, app, world, method, url
    ):
        """Unexpected exceptions return a generic 500 without leaking details."""
        names = {
            BASE: "list_secrets",
            BASE + "/providers": "list_providers",
            BASE + "/providers/1": "get_provider",
            BASE + "/providers/1/test": "get_provider_client",
        }
        target = names[url]
        with patch.object(
            SecretsService, target, side_effect=RuntimeError("leaky-detail")
        ):
            status, data = await _call(app, method, url, world["token"])
        if url.endswith("/test"):
            # test_provider_connection swallows client errors -> reported as failed test
            assert status == 400
        else:
            assert status == 500
            assert "leaky-detail" not in json.dumps(data)


class TestGenericErrorHandling:
    """Unexpected service errors become generic 500s (or mapped 404/400) without leaks."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "method,url,target,body",
        [
            ("put", BASE + "/1", "update_secret_metadata", {"name": "x"}),
            ("post", BASE, "create_secret", None),
            ("delete", BASE + "/1", "delete_secret", None),
            ("post", BASE + "/1/sync", "sync_secret", None),
            ("post", BASE + "/1/unmask", "get_secret", None),
            ("post", BASE + "/providers", "create_provider", None),
            ("put", BASE + "/providers/1", "update_provider", {"name": "x"}),
            ("delete", BASE + "/providers/1", "delete_provider", None),
            ("post", BASE + "/providers/1/sync", "sync_provider", None),
        ],
    )
    async def test_unexpected_error_is_generic_500(
        self, app, world, method, url, target, body
    ):
        """An unexpected RuntimeError never leaks its message and returns 500."""
        if body is None and method == "post" and url == BASE:
            body = {
                "name": "x",
                "provider_id": 1,
                "provider_path": "/p",
                "secret_type": "password",
                "organization_id": world["org"],
            }
        if url == BASE + "/providers" and method == "post":
            body = {
                "name": "x",
                "provider": "infisical",
                "organization_id": world["org"],
                "config_json": {},
            }
        kw = {"json": body} if body is not None else {}
        with patch.object(
            SecretsService, target, side_effect=RuntimeError("leaky-detail")
        ):
            status, data = await _call(app, method, url, world["token"], **kw)
        assert status == 500
        assert "leaky-detail" not in json.dumps(data)

    @pytest.mark.asyncio
    async def test_sync_provider_provider_exception_is_500(self, app, world):
        """A SecretProviderException from sync_provider maps to 500."""
        with patch.object(
            SecretsService, "sync_provider", side_effect=SecretProviderException("x")
        ):
            status, _ = await _call(
                app, "post", f"{BASE}/providers/1/sync", world["token"]
            )
        assert status == 500

    @pytest.mark.asyncio
    async def test_test_endpoint_value_error_is_404(self, app, world):
        """A ValueError raised by the service on the test endpoint maps to 404."""
        with patch.object(
            SecretsService, "test_provider_connection", side_effect=ValueError("nf")
        ):
            status, _ = await _call(
                app, "post", f"{BASE}/providers/1/test", world["token"]
            )
        assert status == 404

    @pytest.mark.asyncio
    async def test_test_endpoint_unexpected_error_is_500(self, app, world):
        """An unexpected error on the test endpoint maps to a generic 500."""
        with patch.object(
            SecretsService, "test_provider_connection", side_effect=RuntimeError("zz")
        ):
            status, data = await _call(
                app, "post", f"{BASE}/providers/1/test", world["token"]
            )
        assert status == 500 and "zz" not in json.dumps(data)

    @pytest.mark.asyncio
    async def test_create_secret_not_found_exception_is_404(self, app, world):
        """A SecretNotFoundException escaping the service on create maps to 404."""
        with patch.object(
            SecretsService, "create_secret", side_effect=SecretNotFoundException("p")
        ):
            status, _ = await _call(
                app,
                "post",
                BASE,
                world["token"],
                json={
                    "name": "x",
                    "provider_id": 1,
                    "provider_path": "/p",
                    "secret_type": "password",
                    "organization_id": world["org"],
                },
            )
        assert status == 404
