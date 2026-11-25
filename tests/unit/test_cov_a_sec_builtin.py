"""Coverage tests for built-in secrets: HTTP routes and BuiltinSecretsClient (real DB)."""

# flake8: noqa: E501

import json
import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

import jwt
import pytest
import pytest_asyncio

from apps.api.services.secrets.base import (
    InvalidSecretConfigException,
    SecretAlreadyExistsException,
    SecretNotFoundException,
    SecretProviderException,
)
from apps.api.services.secrets.builtin_client import BuiltinSecretsClient
from apps.api.utils.async_utils import run_in_threadpool

BASE = "/api/v1/builtin-secrets"
ALL_SCOPES = ["secrets:read", "secrets:write", "secrets:admin"]


def _tok(app, tenant_id, identity_id, scopes):
    """Mint a signed JWT for an existing identity with the given scopes."""
    payload = {
        "sub": str(identity_id),
        "type": "access",
        "iat": datetime.now(UTC),
        "exp": datetime.now(UTC) + timedelta(hours=1),
        "scope": scopes,
        "tenant": str(tenant_id),
        "roles": ["admin"],
        "teams": [],
    }
    secret = app.config.get("JWT_SECRET_KEY") or app.config.get("SECRET_KEY")
    return jwt.encode(payload, secret, algorithm="HS256")


def _hdr(token):
    return {"Authorization": f"Bearer {token}"}


@pytest_asyncio.fixture
async def world(app):
    """Two tenants, an org in each and one identity in the first tenant."""
    db = app.db
    sfx = uuid.uuid4().hex[:8]

    def _setup():
        now = datetime.now(UTC)
        out = {}
        for key in ("a", "b"):
            tid = db.tenants.insert(
                name=f"sec-{key}-{sfx}", slug=f"sec-{key}-{sfx}", is_active=True
            )
            oid = db.organizations.insert(
                name=f"org-{key}-{sfx}", tenant_id=tid, created_at=now, updated_at=now
            )
            out[f"tenant_{key}"] = tid
            out[f"org_{key}"] = oid
        out["identity"] = db.identities.insert(
            identity_type="human",
            username=f"sec-user-{sfx}",
            email=f"sec-{sfx}@example.com",
            tenant_id=out["tenant_a"],
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
        return out

    w = await run_in_threadpool(_setup)
    w["sfx"] = sfx
    w["token"] = _tok(app, w["tenant_a"], w["identity"], ALL_SCOPES)
    w["ro_token"] = _tok(app, w["tenant_a"], w["identity"], ["secrets:read"])
    w["noscope_token"] = _tok(app, w["tenant_a"], w["identity"], [])
    return w


async def _call(app, method, url, token=None, **kw):
    client = app.test_client()
    headers = _hdr(token) if token else {}
    resp = await getattr(client, method)(url, headers=headers, **kw)
    body = await resp.get_data()
    try:
        data = json.loads(body)
    except ValueError:
        data = None
    return resp.status_code, data


class TestBuiltinRoutesAuth:
    """Authentication and scope enforcement on every built-in secrets route."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "method,url",
        [
            ("get", BASE + "?organization_id=1"),
            ("get", BASE + "/x?organization_id=1"),
            ("post", BASE),
            ("put", BASE + "/x?organization_id=1"),
            ("delete", BASE + "/x?organization_id=1"),
            ("post", BASE + "/test-connection"),
        ],
    )
    async def test_no_token_is_401(self, app, method, url):
        """Unauthenticated calls are rejected with 401."""
        status, _ = await _call(app, method, url, json={} if method != "get" else None)
        assert status == 401

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "method,url",
        [
            ("get", BASE + "?organization_id=1"),
            ("get", BASE + "/x?organization_id=1"),
            ("post", BASE),
            ("put", BASE + "/x?organization_id=1"),
            ("delete", BASE + "/x?organization_id=1"),
            ("post", BASE + "/test-connection"),
        ],
    )
    async def test_missing_scope_is_403(self, app, world, method, url):
        """A token lacking the required secrets scope gets 403."""
        status, data = await _call(app, method, url, world["noscope_token"], json={})
        assert status == 403
        assert "scope" in data["error"]

    @pytest.mark.asyncio
    async def test_read_scope_cannot_write(self, app, world):
        """A read-only token cannot create secrets."""
        status, _ = await _call(
            app,
            "post",
            BASE,
            world["ro_token"],
            json={"name": "n", "value": "v", "organization_id": world["org_a"]},
        )
        assert status == 403


class TestBuiltinRoutesCrud:
    """CRUD behaviour of the built-in secrets routes against the real DB."""

    @pytest.mark.asyncio
    async def test_full_lifecycle(self, app, world):
        """Create, list, get (masked), update and delete a string secret."""
        org = world["org_a"]
        name = f"db/pw-{world['sfx']}"
        status, data = await _call(
            app,
            "post",
            BASE,
            world["token"],
            json={
                "name": name,
                "value": "plain-value",
                "organization_id": org,
                "description": "d",
                "tags": ["x"],
            },
        )
        assert status == 201
        assert data["name"] == name and data["is_kv"] is False

        status, data = await _call(
            app, "get", f"{BASE}?organization_id={org}&prefix=db/", world["token"]
        )
        assert status == 200
        assert name in [s["name"] for s in data["secrets"]]

        status, data = await _call(
            app, "get", f"{BASE}/{name}?organization_id={org}", world["token"]
        )
        assert status == 200
        assert data["is_masked"] is True
        assert "plain-value" not in json.dumps(data)

        status, data = await _call(
            app,
            "put",
            f"{BASE}/{name}?organization_id={org}",
            world["token"],
            json={"value": json.dumps({"k": "v"})},
        )
        assert status == 200 and data["is_kv"] is True

        status, data = await _call(
            app, "delete", f"{BASE}/{name}?organization_id={org}", world["token"]
        )
        assert status == 200 and data["success"] is True

        status, _ = await _call(
            app, "get", f"{BASE}/{name}?organization_id={org}", world["token"]
        )
        assert status == 404

    @pytest.mark.asyncio
    async def test_duplicate_create_is_409(self, app, world):
        """Creating a secret whose name already exists returns 409."""
        body = {
            "name": f"dup-{world['sfx']}",
            "value": "v",
            "organization_id": world["org_a"],
        }
        assert (await _call(app, "post", BASE, world["token"], json=body))[0] == 201
        status, _ = await _call(app, "post", BASE, world["token"], json=body)
        assert status == 409

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "method,suffix,body",
        [
            ("get", "", None),
            ("get", "/some-name", None),
            ("put", "/some-name", {"value": "v"}),
            ("delete", "/some-name", None),
        ],
    )
    async def test_missing_organization_id_is_400(
        self, app, world, method, suffix, body
    ):
        """Routes needing an organization_id reject requests without one."""
        kw = {"json": body} if body is not None else {}
        status, data = await _call(app, method, BASE + suffix, world["token"], **kw)
        assert status == 400
        assert "organization_id" in data["error"]

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "body,fragment",
        [
            ({}, "Request body required"),
            ({"name": "n"}, "Missing required fields"),
        ],
    )
    async def test_create_bad_payload_is_400(self, app, world, body, fragment):
        """Create rejects an empty body or missing required fields."""
        status, data = await _call(app, "post", BASE, world["token"], json=body)
        assert status == 400
        assert fragment in data["error"]

    @pytest.mark.asyncio
    async def test_update_bad_payloads(self, app, world):
        """Update rejects empty bodies and bodies lacking the value field."""
        org = world["org_a"]
        url = f"{BASE}/x?organization_id={org}"
        status, data = await _call(app, "put", url, world["token"], json={"a": 1})
        assert status == 400 and "value" in data["error"]
        status, data = await _call(app, "put", url, world["token"], json={})
        assert status == 400 and "Request body" in data["error"]

    @pytest.mark.asyncio
    async def test_update_and_delete_unknown_are_404(self, app, world):
        """Updating or deleting a nonexistent secret returns 404."""
        org = world["org_a"]
        url = f"{BASE}/nope-{world['sfx']}?organization_id={org}"
        assert (await _call(app, "put", url, world["token"], json={"value": "v"}))[
            0
        ] == 404
        assert (await _call(app, "delete", url, world["token"]))[0] == 404

    @pytest.mark.asyncio
    async def test_test_connection(self, app, world):
        """test-connection reports success for the live database."""
        status, data = await _call(
            app,
            "post",
            BASE + "/test-connection",
            world["token"],
            json={"organization_id": world["org_a"]},
        )
        assert status == 200
        assert data == {"success": True, "provider": "builtin"}

    @pytest.mark.asyncio
    async def test_test_connection_failure_is_500(self, app, world):
        """A client exception in test-connection becomes a 500 error body."""
        with patch(
            "apps.api.modules.secrets.routes.builtin_secrets.BuiltinSecretsClient",
            side_effect=RuntimeError("boom"),
        ):
            status, data = await _call(
                app, "post", BASE + "/test-connection", world["token"], json={}
            )
        assert status == 500 and data["success"] is False

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "method,url,kw",
        [
            ("get", BASE + "?organization_id={org}", {}),
            ("get", BASE + "/x?organization_id={org}", {}),
            ("post", BASE, {"json": {"name": "n", "value": "v"}}),
            ("put", BASE + "/x?organization_id={org}", {"json": {"value": "v"}}),
            ("delete", BASE + "/x?organization_id={org}", {}),
        ],
    )
    async def test_unexpected_client_error_is_500(self, app, world, method, url, kw):
        """Unexpected provider failures (own-tenant org) surface as a generic 500."""
        url = url.format(org=world["org_a"])
        if "json" in kw and method == "post":
            kw = {"json": {**kw["json"], "organization_id": world["org_a"]}}
        with patch(
            "apps.api.modules.secrets.routes.builtin_secrets.BuiltinSecretsClient",
            side_effect=RuntimeError("kaboom"),
        ):
            status, data = await _call(app, method, url, world["token"], **kw)
        assert status == 500
        assert "kaboom" not in json.dumps(data)


class TestBuiltinTenantIsolation:
    """Tenant isolation of the built-in secrets routes."""

    @pytest.mark.asyncio
    async def test_cannot_read_other_tenants_secret(self, app, world):
        """Tenant A must not be able to list or read tenant B's org secrets."""
        name = f"tb-{world['sfx']}"

        def _seed():
            with_ctx = app.db
            with_ctx.builtin_secrets.insert(
                name=name,
                organization_id=world["org_b"],
                secret_value="x",
                secret_type="password",
                is_active=True,
                village_id=f"vb-{world['sfx']}",
            )
            with_ctx.commit()

        await run_in_threadpool(_seed)
        status, data = await _call(
            app,
            "get",
            f"{BASE}?organization_id={world['org_b']}",
            world["token"],
        )
        leaked = [] if status != 200 else [s["name"] for s in data["secrets"]]
        assert name not in leaked


@pytest.fixture
def db_client(app):
    """Factory returning a BuiltinSecretsClient built inside an app context."""

    async def _make(org_id):
        async with app.app_context():
            return BuiltinSecretsClient({"organization_id": org_id})

    return _make


class TestBuiltinClient:
    """Direct tests of BuiltinSecretsClient against the real DB."""

    @pytest.mark.asyncio
    async def test_missing_org_config_rejected(self, app):
        """Constructing without organization_id raises InvalidSecretConfigException."""
        async with app.app_context():
            with pytest.raises(InvalidSecretConfigException):
                BuiltinSecretsClient({})

    @pytest.mark.asyncio
    async def test_roundtrip_and_isolation(self, app, world):
        """Secrets are scoped per organization; kv and string values mask correctly."""
        async with app.app_context():
            a = BuiltinSecretsClient({"organization_id": world["org_a"]})
            b = BuiltinSecretsClient({"organization_id": world["org_b"]})
            kv = a.create_secret(
                "kv-" + world["sfx"],
                json.dumps({"u": "1"}),
                {"expires_at": datetime.now(UTC) + timedelta(days=1)},
            )
            assert kv.is_kv is True and kv.metadata["expires_at"]
            plain = a.create_secret("s-" + world["sfx"], '"just-a-string"')
            assert plain.is_kv is False
            num = a.create_secret("n-" + world["sfx"], "123")
            assert num.is_kv is False

            got = a.get_secret("kv-" + world["sfx"])
            assert got.kv_pairs == {"u": "***MASKED***"}
            got2 = a.get_secret("s-" + world["sfx"])
            assert got2.value == "***ENCRYPTED***" and got2.is_masked

            assert a.get_secret_versions("kv-" + world["sfx"]) == ["1"]
            assert [s.name for s in a.list_secrets(prefix="kv-" + world["sfx"])] == [
                "kv-" + world["sfx"]
            ]
            # tenant/org isolation
            assert b.list_secrets(prefix="kv-" + world["sfx"]) == []
            with pytest.raises(SecretNotFoundException):
                b.get_secret("kv-" + world["sfx"])
            with pytest.raises(SecretNotFoundException):
                b.get_secret_versions("kv-" + world["sfx"])
            with pytest.raises(SecretNotFoundException):
                b.delete_secret("kv-" + world["sfx"])
            with pytest.raises(SecretNotFoundException):
                b.update_secret("kv-" + world["sfx"], "x")
            with pytest.raises(SecretAlreadyExistsException):
                a.create_secret("kv-" + world["sfx"], "x")

            assert a.delete_secret("kv-" + world["sfx"]) is True
            with pytest.raises(SecretNotFoundException):
                a.get_secret("kv-" + world["sfx"])

    @pytest.mark.asyncio
    async def test_get_secret_corrupt_json_yields_empty_kv(self, app, world):
        """Unparseable secret_json is tolerated and masked to no pairs."""
        async with app.app_context():
            c = BuiltinSecretsClient({"organization_id": world["org_a"]})
            row = MagicMock()
            row.name = "bad"
            row.secret_json = "{not json"
            row.secret_type = "kv"
            row.description = None
            row.tags = None
            row.expires_at = datetime.now(UTC)
            row.created_at = None
            row.updated_at = None
            c._get_secret_row = MagicMock(return_value=row)
            sv = c.get_secret("bad")
            assert sv.is_kv is True and sv.kv_pairs is None
            assert sv.metadata["expires_at"]

    @pytest.mark.asyncio
    async def test_get_secret_dict_json_column(self, app, world):
        """A secret_json already decoded to a dict is accepted and masked."""
        async with app.app_context():
            c = BuiltinSecretsClient({"organization_id": world["org_a"]})
            row = MagicMock()
            row.name = "d"
            row.secret_json = {"a": "b"}
            c._get_secret_row = MagicMock(return_value=row)
            assert c.get_secret("d").kv_pairs == {"a": "***MASKED***"}

    @pytest.mark.asyncio
    async def test_get_error_is_wrapped(self, app, world):
        """An unexpected DB failure in get_secret becomes SecretProviderException."""
        async with app.app_context():
            c = BuiltinSecretsClient({"organization_id": world["org_a"]})
            c._get_secret_row = MagicMock(side_effect=RuntimeError("db down"))
            with pytest.raises(SecretProviderException):
                c.get_secret("x")

    @pytest.mark.asyncio
    @pytest.mark.parametrize("op", ["create", "update", "delete"])
    async def test_write_errors_are_wrapped(self, app, world, op):
        """DB failures in write paths should surface as SecretProviderException."""
        async with app.app_context():
            c = BuiltinSecretsClient({"organization_id": world["org_a"]})
            c._get_secret_row = MagicMock(side_effect=RuntimeError("db down"))
            call = {
                "create": lambda: c.create_secret("x", "v"),
                "update": lambda: c.update_secret("x", "v"),
                "delete": lambda: c.delete_secret("x"),
            }[op]
            with pytest.raises(SecretProviderException):
                call()

    @pytest.mark.asyncio
    async def test_list_error_and_connection_failure(self, app, world):
        """list_secrets wraps DB errors; test_connection returns False on failure."""
        async with app.app_context():
            c = BuiltinSecretsClient({"organization_id": world["org_a"]})
            c.db = MagicMock(side_effect=RuntimeError("db down"))
            with pytest.raises(SecretProviderException):
                c.list_secrets()
            assert c.test_connection() is False
