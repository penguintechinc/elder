"""HTTP route tests for the keys module (``/api/v1/keys``) with real JWTs and DB.

Authentication/scope enforcement, validation (400), not-found (404) mapping,
generic 500 handling (no internal text leaked) and data flow through the real
Postgres. Provider SDK clients are patched at ``KeysService._get_provider_client``
and the audit insert (``_log_access``, covered by the service tests) is
patched out. Tenant isolation (cross-tenant ids are 404 and never listed) is
asserted directly.
"""

# flake8: noqa: E501

import json
import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

import jwt
import pytest
import pytest_asyncio

from apps.api.services.keys import KeysService
from apps.api.utils.async_utils import run_in_threadpool

BASE = "/api/v1/keys"
ALL_SCOPES = ["secrets:read", "secrets:write", "secrets:admin"]


def _vid():
    """Return a unique 25-char lowercase hex village id."""
    return uuid.uuid4().hex[:25]


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


async def _call(app, method, url, token=None, **kw):
    """Issue a request through the app test client; return (status, json)."""
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
    """Two tenants (each with org, provider, key), one identity in tenant A."""
    db = app.db
    sfx = uuid.uuid4().hex[:8]

    def _setup():
        now = datetime.now(UTC)
        out = {}
        for side in ("a", "b"):
            tid = db.tenants.insert(
                name=f"kr-{side}-{sfx}", slug=f"kr-{side}-{sfx}", is_active=True
            )
            org = db.organizations.insert(
                name=f"kr-org-{side}-{sfx}",
                tenant_id=tid,
                created_at=now,
                updated_at=now,
            )
            prov = db.key_providers.insert(
                name=f"kr-prov-{side}-{sfx}",
                provider="aws_kms",
                config_json={"region": "r", "token": "t"},
                organization_id=org,
                enabled=True,
                village_id=_vid(),
                created_at=now,
                updated_at=now,
            )
            key = db.crypto_keys.insert(
                name=f"kr-key-{side}-{sfx}",
                key_provider_id=prov,
                provider_key_id=f"pk-{side}-{sfx}",
                key_hash="h",
                key_type="asymmetric" if side == "a" else "symmetric",
                key_state="Enabled",
                organization_id=org,
                village_id=_vid(),
                created_at=now,
                updated_at=now,
            )
            out.update(
                {f"tenant_{side}": tid, f"prov_{side}": prov, f"key_{side}": key}
            )
        out["key_sym_a"] = db.crypto_keys.insert(
            name=f"kr-sym-a-{sfx}",
            key_provider_id=out["prov_a"],
            provider_key_id=f"pk-sym-{sfx}",
            key_hash="h",
            key_type="symmetric",
            key_state="Enabled",
            organization_id=db(db.key_providers.id == out["prov_a"])
            .select(db.key_providers.organization_id)
            .first()
            .organization_id,
            village_id=_vid(),
            created_at=now,
            updated_at=now,
        )
        out["key_off_a"] = db.crypto_keys.insert(
            name=f"kr-off-a-{sfx}",
            key_provider_id=out["prov_a"],
            provider_key_id=f"pk-off-{sfx}",
            key_hash="h",
            key_type="symmetric",
            key_state="Disabled",
            organization_id=db(db.key_providers.id == out["prov_a"])
            .select(db.key_providers.organization_id)
            .first()
            .organization_id,
            village_id=_vid(),
            created_at=now,
            updated_at=now,
        )
        out["identity"] = db.identities.insert(
            identity_type="human",
            username=f"kr-user-{sfx}",
            email=f"kr-{sfx}@example.com",
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
    w["ro"] = _tok(app, w["tenant_a"], w["identity"], ["secrets:read"])
    w["rw"] = _tok(app, w["tenant_a"], w["identity"], ["secrets:read", "secrets:write"])
    w["none"] = _tok(app, w["tenant_a"], w["identity"], [])
    return w


@pytest.fixture
def provider_client():
    """Patch the provider client factory and audit logger; yield (client, log)."""
    client = MagicMock(name="provider_client")
    with (
        patch.object(KeysService, "_get_provider_client", return_value=client),
        patch.object(KeysService, "_log_access") as log,
    ):
        yield client, log


# (method, url template, required scope, body)
ENDPOINTS = [
    ("get", "", "secrets:read", None),
    ("get", "/{key}", "secrets:read", None),
    ("post", "", "secrets:write", {}),
    ("post", "/{key}/enable", "secrets:write", None),
    ("post", "/{key}/disable", "secrets:write", None),
    ("post", "/{key}/rotate", "secrets:admin", None),
    ("delete", "/{key}", "secrets:write", None),
    ("post", "/{key}/encrypt", "secrets:write", {}),
    ("post", "/{key}/decrypt", "secrets:write", {}),
    ("post", "/{key}/generate-data-key", "secrets:write", {}),
    ("post", "/{key}/sign", "secrets:write", {}),
    ("post", "/{key}/verify", "secrets:write", {}),
    ("get", "/{key}/access-log", "secrets:read", None),
    ("get", "/providers", "secrets:read", None),
    ("post", "/providers", "secrets:admin", {}),
    ("get", "/providers/{prov}", "secrets:read", None),
    ("put", "/providers/{prov}", "secrets:admin", {}),
    ("delete", "/providers/{prov}", "secrets:admin", None),
    ("post", "/providers/{prov}/test", "secrets:admin", None),
]
IDS = [f"{m}-{u or 'root'}" for m, u, _, _ in ENDPOINTS]


def _url(world, tmpl):
    """Expand {key}/{prov} placeholders to seeded ids."""
    return BASE + tmpl.format(key=world["key_a"], prov=world["prov_a"])


def _kw(body):
    """Return request kwargs for an optional JSON body."""
    return {"json": body} if body is not None else {}


class TestAuth:
    """401 without a token and 403 with the wrong scope on every route."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("ep", ENDPOINTS, ids=IDS)
    async def test_no_token_is_401(self, app, world, ep):
        """Unauthenticated calls are rejected with 401."""
        method, tmpl, _, body = ep
        status, _ = await _call(app, method, _url(world, tmpl), None, **_kw(body))
        assert status == 401

    @pytest.mark.asyncio
    @pytest.mark.parametrize("ep", ENDPOINTS, ids=IDS)
    async def test_no_scope_is_403(self, app, world, ep):
        """A valid token with no scopes is refused with 403 and names the scope."""
        method, tmpl, scope, body = ep
        status, data = await _call(
            app, method, _url(world, tmpl), world["none"], **_kw(body)
        )
        assert status == 403
        assert scope in data["error"]

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "ep",
        [e for e in ENDPOINTS if e[2] != "secrets:read"],
        ids=[i for i, e in zip(IDS, ENDPOINTS) if e[2] != "secrets:read"],
    )
    async def test_read_only_token_cannot_mutate(self, app, world, ep):
        """A secrets:read token cannot use write/admin routes."""
        method, tmpl, _, body = ep
        status, _ = await _call(
            app, method, _url(world, tmpl), world["ro"], **_kw(body)
        )
        assert status == 403

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("method", "tmpl"),
        [
            ("post", "/{key}/rotate"),
            ("post", "/providers"),
            ("put", "/providers/{prov}"),
            ("delete", "/providers/{prov}"),
            ("post", "/providers/{prov}/test"),
        ],
    )
    async def test_write_scope_cannot_use_admin_routes(self, app, world, method, tmpl):
        """secrets:write is not enough for admin-scoped routes."""
        status, _ = await _call(app, method, _url(world, tmpl), world["rw"], json={})
        assert status == 403


class TestReads:
    """GET routes backed by the real database."""

    @pytest.mark.asyncio
    async def test_list_keys_and_filters(self, app, world):
        """Listing supports provider, type and enabled filters."""
        status, data = await _call(app, "get", BASE, world["token"])
        assert status == 200
        ids = {k["id"] for k in data["keys"]}
        assert {world["key_a"], world["key_sym_a"], world["key_off_a"]} <= ids
        assert data["count"] == len(data["keys"])

        status, data = await _call(
            app,
            "get",
            f"{BASE}?provider_id={world['prov_a']}&key_type=symmetric&enabled_only=true",
            world["token"],
        )
        got = {k["id"] for k in data["keys"]}
        assert got == {world["key_sym_a"]}

        status, data = await _call(
            app,
            "get",
            f"{BASE}?provider_id={world['prov_a']}&enabled_only=TRUE",
            world["token"],
        )
        assert world["key_off_a"] not in {k["id"] for k in data["keys"]}

    @pytest.mark.asyncio
    async def test_get_key_and_404(self, app, world):
        """A key is returned with its provider name; unknown ids are 404."""
        status, data = await _call(
            app, "get", f"{BASE}/{world['key_a']}", world["token"]
        )
        assert status == 200
        assert data["provider_name"] == f"kr-prov-a-{world['sfx']}"
        status, data = await _call(app, "get", f"{BASE}/99999999", world["token"])
        assert status == 404
        assert data == {"error": "Failed to process request"}

    @pytest.mark.asyncio
    async def test_access_log_pagination_params(self, app, world):
        """The access-log route echoes limit/offset and returns the key's entries."""
        db = app.db

        def _seed():
            db.key_access_log.insert(
                key_id=world["key_a"],
                operation="encrypt",
                accessed_at=datetime.now(UTC),
            )
            db.commit()

        await run_in_threadpool(_seed)
        status, data = await _call(
            app,
            "get",
            f"{BASE}/{world['key_a']}/access-log?limit=5&offset=0",
            world["token"],
        )
        assert status == 200
        assert (data["key_id"], data["limit"], data["offset"]) == (world["key_a"], 5, 0)
        assert [log["operation"] for log in data["logs"]] == ["encrypt"]
        assert data["count"] == 1

    @pytest.mark.asyncio
    async def test_list_providers_redacts_secrets(self, app, world):
        """Provider listing masks sensitive config values."""
        status, data = await _call(app, "get", f"{BASE}/providers", world["token"])
        assert status == 200
        mine = [p for p in data["providers"] if p["id"] == world["prov_a"]][0]
        assert mine["config_json"]["token"] == "***REDACTED***"
        assert data["count"] == len(data["providers"])

    @pytest.mark.asyncio
    async def test_list_providers_enabled_only_param_accepted(self, app, world):
        """enabled_only=true is parsed and the call succeeds."""
        status, data = await _call(
            app, "get", f"{BASE}/providers?enabled_only=true", world["token"]
        )
        assert status == 200
        assert "providers" in data

    @pytest.mark.asyncio
    async def test_get_provider_and_404(self, app, world):
        """A provider is returned redacted; unknown ids are 404."""
        status, data = await _call(
            app, "get", f"{BASE}/providers/{world['prov_a']}", world["token"]
        )
        assert status == 200
        assert data["config_json"]["token"] == "***REDACTED***"
        status, _ = await _call(
            app, "get", f"{BASE}/providers/99999999", world["token"]
        )
        assert status == 404


class TestTenantIsolation:
    """Cross-tenant access must be denied (routes currently do not scope by tenant)."""

    @pytest.mark.asyncio
    async def test_get_other_tenants_key_is_404(self, app, world):
        """Tenant A's token must not read tenant B's key."""
        status, _ = await _call(app, "get", f"{BASE}/{world['key_b']}", world["token"])
        assert status == 404

    @pytest.mark.asyncio
    async def test_list_excludes_other_tenants_keys(self, app, world):
        """Tenant A's listing must not include tenant B's key."""
        _, data = await _call(app, "get", BASE, world["token"])
        assert world["key_b"] not in {k["id"] for k in data["keys"]}

    @pytest.mark.asyncio
    async def test_get_other_tenants_provider_is_404(self, app, world):
        """Tenant A's token must not read tenant B's provider."""
        status, _ = await _call(
            app, "get", f"{BASE}/providers/{world['prov_b']}", world["token"]
        )
        assert status == 404

    @pytest.mark.asyncio
    async def test_disable_other_tenants_key_is_404(self, app, world, provider_client):
        """Tenant A's token must not disable tenant B's key."""
        client, _ = provider_client
        client.disable_key.return_value = {"state": "Disabled"}
        status, _ = await _call(
            app, "post", f"{BASE}/{world['key_b']}/disable", world["token"]
        )
        assert status == 404


class TestStateChanges:
    """enable / disable / rotate / delete routes."""

    @pytest.mark.asyncio
    async def test_disable_then_enable(self, app, world, provider_client):
        """Disable and enable round-trip persists key state."""
        client, _ = provider_client
        client.disable_key.return_value = {"state": "Disabled"}
        client.enable_key.return_value = {"state": "Enabled"}
        status, data = await _call(
            app, "post", f"{BASE}/{world['key_sym_a']}/disable", world["token"]
        )
        assert (status, data["key_state"]) == (200, "Disabled")
        status, data = await _call(
            app, "post", f"{BASE}/{world['key_sym_a']}/enable", world["token"]
        )
        assert (status, data["key_state"]) == (200, "Enabled")

    @pytest.mark.asyncio
    async def test_rotate(self, app, world, provider_client):
        """Rotate returns the provider result."""
        client, log = provider_client
        client.rotate_key.return_value = {"rotation_enabled": True}
        status, data = await _call(
            app, "post", f"{BASE}/{world['key_a']}/rotate", world["token"]
        )
        assert (status, data) == (200, {"rotation_enabled": True})
        log.assert_called_once_with(world["key_a"], "rotate")

    @pytest.mark.asyncio
    @pytest.mark.parametrize(("query", "days"), [("", 30), ("?pending_days=9", 9)])
    async def test_delete_key_pending_days(
        self, app, world, provider_client, query, days
    ):
        """delete schedules deletion with the default or requested window."""
        client, _ = provider_client
        client.schedule_key_deletion.return_value = {"state": "PendingDeletion"}
        status, data = await _call(
            app, "delete", f"{BASE}/{world['key_off_a']}{query}", world["token"]
        )
        assert (status, data) == (200, {"state": "PendingDeletion"})
        client.schedule_key_deletion.assert_called_once_with(
            f"pk-off-{world['sfx']}", days
        )

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("method", "suffix"),
        [
            ("post", "/enable"),
            ("post", "/disable"),
            ("post", "/rotate"),
            ("delete", ""),
        ],
    )
    async def test_unknown_key_is_404(
        self, app, world, provider_client, method, suffix
    ):
        """State-changing routes map a missing key to 404."""
        status, _ = await _call(app, method, f"{BASE}/99999999{suffix}", world["token"])
        assert status == 404

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("method", "suffix", "client_op"),
        [
            ("post", "/enable", "enable_key"),
            ("post", "/disable", "disable_key"),
            ("post", "/rotate", "rotate_key"),
            ("delete", "", "schedule_key_deletion"),
        ],
    )
    async def test_provider_error_is_500_without_leak(
        self, app, world, provider_client, method, suffix, client_op
    ):
        """Provider failures give a generic 500 that hides internal text."""
        client, _ = provider_client
        getattr(client, client_op).side_effect = Exception("kms secret detail")
        status, data = await _call(
            app, method, f"{BASE}/{world['key_a']}{suffix}", world["token"]
        )
        assert status == 500
        assert data == {"error": "Failed to process request"}


class TestCreateKey:
    """POST /keys validation and mapping."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("body", "fragment"),
        [
            (None, "Request body required"),
            ({}, "Request body required"),
            ({"provider_id": 1}, "key_name"),
            ({"key_name": "k"}, "provider_id"),
        ],
    )
    async def test_validation_400(self, app, world, body, fragment):
        """Missing body or required fields yield 400."""
        kw = {"json": body} if body is not None else {"data": ""}
        status, data = await _call(app, "post", BASE, world["token"], **kw)
        assert status == 400
        assert fragment in data["error"]

    @pytest.mark.asyncio
    async def test_success_forwards_fields(self, app, world):
        """A valid body calls the service with parsed fields and returns 201."""
        with patch.object(KeysService, "create_key", return_value={"id": 5}) as ck:
            status, data = await _call(
                app,
                "post",
                BASE,
                world["token"],
                json={
                    "provider_id": world["prov_a"],
                    "key_name": "k1",
                    "key_type": "asymmetric",
                    "key_spec": "RSA_2048",
                    "description": "d",
                    "tags": {"e": "p"},
                },
            )
        assert (status, data) == (201, {"id": 5})
        ck.assert_called_once_with(
            provider_id=world["prov_a"],
            key_name="k1",
            key_type="asymmetric",
            key_spec="RSA_2048",
            description="d",
            tags={"e": "p"},
        )

    @pytest.mark.asyncio
    async def test_defaults_key_type(self, app, world):
        """key_type defaults to symmetric when omitted."""
        with patch.object(KeysService, "create_key", return_value={}) as ck:
            await _call(
                app,
                "post",
                BASE,
                world["token"],
                json={"provider_id": 1, "key_name": "k"},
            )
        assert ck.call_args.kwargs["key_type"] == "symmetric"

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("exc", "expected"),
        [(Exception("Key provider not found: 9"), 404), (Exception("boom"), 400)],
    )
    async def test_error_mapping(self, app, world, exc, expected):
        """Not-found maps to 404; other failures to 400."""
        with patch.object(KeysService, "create_key", side_effect=exc):
            status, data = await _call(
                app,
                "post",
                BASE,
                world["token"],
                json={"provider_id": 1, "key_name": "k"},
            )
        assert status == expected
        assert data == {"error": "Failed to process request"}


class TestCryptoRoutes:
    """encrypt / decrypt / generate-data-key / sign / verify routes."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("suffix", "body", "fragment"),
        [
            ("/encrypt", None, "plaintext"),
            ("/encrypt", {"x": 1}, "plaintext"),
            ("/decrypt", None, "ciphertext"),
            ("/decrypt", {"x": 1}, "ciphertext"),
            ("/sign", None, "message"),
            ("/sign", {"x": 1}, "message"),
            ("/verify", None, "Request body required"),
            ("/verify", {"message": "m"}, "signature, signing_algorithm"),
        ],
    )
    async def test_validation_400(self, app, world, suffix, body, fragment):
        """Missing or incomplete bodies are rejected with 400."""
        kw = {"json": body} if body is not None else {"data": ""}
        status, data = await _call(
            app, "post", f"{BASE}/{world['key_a']}{suffix}", world["token"], **kw
        )
        assert status == 400
        assert fragment in data["error"]

    @pytest.mark.asyncio
    async def test_encrypt_decrypt(self, app, world, provider_client):
        """Encrypt/decrypt pass body fields and context to the provider."""
        client, _ = provider_client
        client.encrypt.return_value = {"ciphertext": "ct", "key_id": "k"}
        client.decrypt.return_value = {"plaintext": "pt", "key_id": "k"}
        status, data = await _call(
            app,
            "post",
            f"{BASE}/{world['key_sym_a']}/encrypt",
            world["token"],
            json={"plaintext": "hello", "context": {"c": "1"}},
        )
        assert (status, data["ciphertext"]) == (200, "ct")
        client.encrypt.assert_called_once_with(
            f"pk-sym-{world['sfx']}", "hello", {"c": "1"}
        )
        status, data = await _call(
            app,
            "post",
            f"{BASE}/{world['key_sym_a']}/decrypt",
            world["token"],
            json={"ciphertext": "ct"},
        )
        assert (status, data["plaintext"]) == (200, "pt")
        client.decrypt.assert_called_once_with("ct", None)

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("body", "spec"),
        [(None, "AES_256"), ({}, "AES_256"), ({"key_spec": "AES_128"}, "AES_128")],
    )
    async def test_generate_data_key(self, app, world, provider_client, body, spec):
        """generate-data-key tolerates an empty body and forwards key_spec."""
        client, _ = provider_client
        client.generate_data_key.return_value = {"plaintext_key": "p"}
        kw = {"json": body} if body is not None else {"data": ""}
        status, _ = await _call(
            app,
            "post",
            f"{BASE}/{world['key_sym_a']}/generate-data-key",
            world["token"],
            **kw,
        )
        assert status == 200
        assert client.generate_data_key.call_args.args[1] == spec

    @pytest.mark.asyncio
    async def test_sign_and_verify(self, app, world, provider_client):
        """Sign (default algorithm) and verify work on an asymmetric key."""
        client, _ = provider_client
        client.sign.return_value = {"signature": "s"}
        client.verify.return_value = {"valid": True}
        status, data = await _call(
            app,
            "post",
            f"{BASE}/{world['key_a']}/sign",
            world["token"],
            json={"message": "m"},
        )
        assert (status, data) == (200, {"signature": "s"})
        client.sign.assert_called_once_with(
            f"pk-a-{world['sfx']}", "m", "RSASSA_PSS_SHA_256"
        )
        status, data = await _call(
            app,
            "post",
            f"{BASE}/{world['key_a']}/verify",
            world["token"],
            json={"message": "m", "signature": "s", "signing_algorithm": "ALG"},
        )
        assert (status, data) == (200, {"valid": True})

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("suffix", "body"),
        [
            ("/encrypt", {"plaintext": "p"}),
            ("/decrypt", {"ciphertext": "c"}),
            ("/generate-data-key", {}),
            ("/sign", {"message": "m"}),
            ("/verify", {"message": "m", "signature": "s", "signing_algorithm": "A"}),
        ],
    )
    async def test_unknown_key_is_404(self, app, world, provider_client, suffix, body):
        """Unknown key ids map to 404 for every crypto route."""
        status, _ = await _call(
            app, "post", f"{BASE}/99999999{suffix}", world["token"], json=body
        )
        assert status == 404

    @pytest.mark.asyncio
    @pytest.mark.parametrize("suffix", ["/sign", "/verify"])
    async def test_symmetric_key_sign_verify_is_400(
        self, app, world, provider_client, suffix
    ):
        """Signing/verifying with a symmetric key maps to 400."""
        body = {"message": "m", "signature": "s", "signing_algorithm": "A"}
        status, _ = await _call(
            app,
            "post",
            f"{BASE}/{world['key_sym_a']}{suffix}",
            world["token"],
            json=body,
        )
        assert status == 400

    @pytest.mark.asyncio
    async def test_disabled_key_encrypt_is_500(self, app, world, provider_client):
        """Using a disabled key surfaces as a generic server error."""
        status, data = await _call(
            app,
            "post",
            f"{BASE}/{world['key_off_a']}/encrypt",
            world["token"],
            json={"plaintext": "p"},
        )
        assert status == 500
        assert data == {"error": "Failed to process request"}

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("suffix", "body", "client_op"),
        [
            ("/encrypt", {"plaintext": "p"}, "encrypt"),
            ("/decrypt", {"ciphertext": "c"}, "decrypt"),
            ("/generate-data-key", {}, "generate_data_key"),
            ("/sign", {"message": "m"}, "sign"),
            (
                "/verify",
                {"message": "m", "signature": "s", "signing_algorithm": "A"},
                "verify",
            ),
        ],
    )
    async def test_provider_failure_is_500(
        self, app, world, provider_client, suffix, body, client_op
    ):
        """A provider exception (e.g. timeout) yields a generic 500."""
        client, _ = provider_client
        getattr(client, client_op).side_effect = TimeoutError("kms timed out")
        status, data = await _call(
            app, "post", f"{BASE}/{world['key_a']}{suffix}", world["token"], json=body
        )
        assert status == 500
        assert "timed out" not in json.dumps(data)


class TestProviderRoutes:
    """Provider create / update / delete / test routes."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("body", "fragment"),
        [
            (None, "Request body required"),
            ({}, "Request body required"),
            ({"name": "n"}, "provider_type, config"),
        ],
    )
    async def test_create_validation_400(self, app, world, body, fragment):
        """Missing body/fields on create gives 400."""
        kw = {"json": body} if body is not None else {"data": ""}
        status, data = await _call(
            app, "post", f"{BASE}/providers", world["token"], **kw
        )
        assert status == 400
        assert fragment in data["error"]

    @pytest.mark.asyncio
    async def test_create_success_and_invalid_type(self, app, world):
        """Create forwards fields (201); service rejection maps to 400."""
        body = {
            "name": "n",
            "provider_type": "aws_kms",
            "config": {"r": 1},
            "description": "d",
        }
        with patch.object(KeysService, "create_provider", return_value={"id": 9}) as cp:
            status, data = await _call(
                app, "post", f"{BASE}/providers", world["token"], json=body
            )
        assert (status, data) == (201, {"id": 9})
        cp.assert_called_once_with(
            name="n",
            provider_type="aws_kms",
            config={"r": 1},
            description="d",
            organization_id=None,
        )

        status, data = await _call(
            app,
            "post",
            f"{BASE}/providers",
            world["token"],
            json={"name": "n", "provider_type": "vault", "config": {}},
        )
        assert status == 400
        assert data == {"error": "Failed to process request"}

    @pytest.mark.asyncio
    async def test_update_validation_and_success(self, app, world):
        """Update requires a body and persists provided fields."""
        url = f"{BASE}/providers/{world['prov_a']}"
        status, data = await _call(app, "put", url, world["token"], data="")
        assert (status, data["error"]) == (400, "Request body required")
        status, data = await _call(
            app,
            "put",
            url,
            world["token"],
            json={
                "name": f"renamed-{world['sfx']}",
                "enabled": False,
                "config": {"region": "z"},
            },
        )
        assert status == 200
        assert data["name"] == f"renamed-{world['sfx']}"
        assert data["enabled"] is False
        assert data["config_json"] == {"region": "z"}

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("exc", "expected"),
        [(Exception("Provider not found: 1"), 404), (Exception("db error"), 400)],
    )
    async def test_update_error_mapping(self, app, world, exc, expected):
        """Not-found maps to 404, other failures to 400."""
        with patch.object(KeysService, "update_provider", side_effect=exc):
            status, _ = await _call(
                app,
                "put",
                f"{BASE}/providers/{world['prov_a']}",
                world["token"],
                json={"name": "x"},
            )
        assert status == expected

    @pytest.mark.asyncio
    async def test_update_unknown_provider_404(self, app, world):
        """Updating an unknown provider id is 404 on the real DB."""
        status, _ = await _call(
            app, "put", f"{BASE}/providers/99999999", world["token"], json={"name": "x"}
        )
        assert status == 404

    @pytest.mark.asyncio
    async def test_delete_provider_with_keys_is_400(self, app, world):
        """Deleting a provider that still has keys is refused with 400."""
        status, _ = await _call(
            app, "delete", f"{BASE}/providers/{world['prov_a']}", world["token"]
        )
        assert status == 400

    @pytest.mark.asyncio
    async def test_delete_provider_unknown_and_success(self, app, world):
        """Unknown provider is 404; an empty provider is deleted (200)."""
        status, _ = await _call(
            app, "delete", f"{BASE}/providers/99999999", world["token"]
        )
        assert status == 404
        db = app.db

        def _mk():
            now = datetime.now(UTC)
            org = (
                db(db.key_providers.id == world["prov_a"])
                .select(db.key_providers.organization_id)
                .first()
                .organization_id
            )
            pid = db.key_providers.insert(
                name=f"empty-{world['sfx']}",
                provider="infisical",
                config_json={},
                organization_id=org,
                enabled=True,
                village_id=_vid(),
                created_at=now,
                updated_at=now,
            )
            db.commit()
            return pid

        pid = await run_in_threadpool(_mk)
        status, data = await _call(
            app, "delete", f"{BASE}/providers/{pid}", world["token"]
        )
        assert (status, data) == (200, {"message": "Provider deleted successfully"})
        status, _ = await _call(app, "get", f"{BASE}/providers/{pid}", world["token"])
        assert status == 404

    @pytest.mark.asyncio
    async def test_delete_provider_unexpected_error_is_500(self, app, world):
        """An unexpected service error is a generic 500."""
        with patch.object(
            KeysService, "delete_provider", side_effect=Exception("weird")
        ):
            status, _ = await _call(
                app, "delete", f"{BASE}/providers/{world['prov_a']}", world["token"]
            )
        assert status == 500

    @pytest.mark.asyncio
    @pytest.mark.parametrize("connected", [True, False])
    async def test_test_provider(self, app, world, provider_client, connected):
        """The connectivity test route reports the client's result."""
        client, _ = provider_client
        client.test_connection.return_value = connected
        status, data = await _call(
            app, "post", f"{BASE}/providers/{world['prov_a']}/test", world["token"]
        )
        assert status == 200
        assert data["success"] is connected

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("exc", "expected"),
        [(Exception("Provider not found: 1"), 404), (Exception("x"), 500)],
    )
    async def test_test_provider_error_mapping(self, app, world, exc, expected):
        """Exceptions escaping the service map to 404/500."""
        with patch.object(KeysService, "test_provider", side_effect=exc):
            status, _ = await _call(
                app, "post", f"{BASE}/providers/{world['prov_a']}/test", world["token"]
            )
        assert status == expected


class TestUnexpectedErrors:
    """Service exceptions on read routes yield generic responses."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("method", "url", "patch_name", "expected"),
        [
            ("get", BASE, "list_keys", 500),
            ("get", f"{BASE}/1", "get_key", 500),
            ("get", f"{BASE}/1/access-log", "get_access_log", 500),
            ("get", f"{BASE}/1/access-log", "get_access_log_nf", 404),
            ("get", f"{BASE}/providers", "list_providers", 500),
            ("get", f"{BASE}/providers/1", "get_provider", 500),
        ],
    )
    async def test_service_errors(self, app, world, method, url, patch_name, expected):
        """Unexpected errors give 500; 'not found' errors give 404; no leak either way."""
        name = patch_name.removesuffix("_nf")
        msg = (
            "Key not found: 1" if patch_name.endswith("_nf") else "internal detail xyz"
        )
        with patch.object(KeysService, name, side_effect=Exception(msg)):
            status, data = await _call(app, method, url, world["token"])
        assert status == expected
        assert data == {"error": "Failed to process request"}
