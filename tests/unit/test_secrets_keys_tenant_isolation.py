"""Two-tenant isolation tests for the secrets, keys, certificates and built-in routes.

Tenant A owns one of everything; a caller from tenant B (and a token with no
tenant) must never read, list, mutate or attach to it. Foreign ids are 404,
a token without a tenant is 403. Provider SDK clients are faked at the
boundary; everything else runs against the real Postgres.
"""

# flake8: noqa: E501

import json
import uuid
from datetime import UTC, date, datetime, timedelta
from unittest.mock import MagicMock, patch

import jwt
import pytest
import pytest_asyncio

from apps.api.services.keys import KeysService
from apps.api.services.secrets.base import SecretProviderClient, SecretValue
from apps.api.services.secrets.service import SecretsService
from apps.api.services.secrets.tenancy import (
    TenantScopeError,
    org_in_tenant,
    require_tenant,
    tenant_org_ids,
)
from apps.api.utils.async_utils import run_in_threadpool

SCOPES = ["secrets:read", "secrets:write", "secrets:admin"]


class FakeClient(SecretProviderClient):
    """Secret provider double: always connects and returns a fixed value."""

    def _validate_config(self):
        """Accept any config."""

    def test_connection(self):
        """Always reachable."""
        return True

    def get_secret(self, path, version=None):
        """Return a fixed value for any path."""
        return SecretValue(name=path, value="iso-val", is_masked=False)

    def list_secrets(self, prefix=None):
        """No provider-side secrets."""
        return []

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
def fake_secret_provider():
    """Route every secret provider type to FakeClient."""
    mapping = {k: FakeClient for k in SecretsService.PROVIDER_CLIENTS}
    with patch.dict(SecretsService.PROVIDER_CLIENTS, mapping):
        yield


@pytest.fixture
def kms():
    """Fake key-provider client and silence the key audit insert."""
    client = MagicMock(name="kms")
    client.disable_key.return_value = {"state": "Disabled"}
    client.encrypt.return_value = {"ciphertext": "c"}
    client.create_key.return_value = {"key_id": f"pk-{uuid.uuid4().hex[:6]}"}
    with patch.object(KeysService, "_get_provider_client", return_value=client):
        yield client


def _tok(app, tenant, identity_id, scopes=SCOPES):
    """Mint a signed access JWT; ``tenant`` is put in the claim verbatim."""
    now = datetime.now(UTC)
    payload = {
        "sub": str(identity_id),
        "type": "access",
        "iat": now,
        "exp": now + timedelta(hours=1),
        "scope": scopes,
        "tenant": str(tenant),
        "roles": ["admin"],
        "teams": [],
    }
    secret = app.config.get("JWT_SECRET_KEY") or app.config.get("SECRET_KEY")
    return jwt.encode(payload, secret, algorithm="HS256")


async def _call(app, method, url, token, **kw):
    """Issue a request; return (status, parsed JSON or None)."""
    client = app.test_client()
    resp = await getattr(client, method)(
        url, headers={"Authorization": f"Bearer {token}"}, **kw
    )
    body = await resp.get_data()
    try:
        return resp.status_code, json.loads(body)
    except ValueError:
        return resp.status_code, None


def _vid():
    """Unique 25-char village id for seeded rows."""
    return uuid.uuid4().hex[:25]


@pytest_asyncio.fixture
async def world(app):
    """Tenants A and B, each with an org and identity; A owns one of every resource."""
    db = app.db
    sfx = uuid.uuid4().hex[:8]

    def _setup():
        now = datetime.now(UTC)
        out = {"sfx": sfx}
        for side in ("a", "b"):
            tid = db.tenants.insert(
                name=f"iso-{side}-{sfx}", slug=f"iso-{side}-{sfx}", is_active=True
            )
            org = db.organizations.insert(
                name=f"iso-org-{side}-{sfx}",
                tenant_id=tid,
                created_at=now,
                updated_at=now,
            )
            ident = db.identities.insert(
                identity_type="human",
                username=f"iso-{side}-{sfx}",
                email=f"iso-{side}-{sfx}@example.com",
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
            out.update(
                {f"tenant_{side}": tid, f"org_{side}": org, f"user_{side}": ident}
            )
        out["kprov_a"] = db.key_providers.insert(
            name=f"iso-kp-{sfx}",
            provider="aws_kms",
            config_json={"region": "r"},
            organization_id=out["org_a"],
            enabled=True,
            village_id=_vid(),
            created_at=now,
            updated_at=now,
        )
        out["key_a"] = db.crypto_keys.insert(
            name=f"iso-key-{sfx}",
            key_provider_id=out["kprov_a"],
            provider_key_id=f"pk-{sfx}",
            key_hash="h",
            key_type="symmetric",
            key_state="Enabled",
            organization_id=out["org_a"],
            village_id=_vid(),
            created_at=now,
            updated_at=now,
        )
        out["sprov_a"] = db.secret_providers.insert(
            name=f"iso-sp-{sfx}",
            provider="aws_secrets_manager",
            config_json={},
            organization_id=out["org_a"],
            enabled=True,
            village_id=_vid(),
        )
        out["secret_a"] = db.secrets.insert(
            name=f"iso-sec-{sfx}",
            provider_id=out["sprov_a"],
            provider_path="/iso/a",
            secret_type="password",
            is_kv=False,
            organization_id=out["org_a"],
            village_id=_vid(),
        )
        out["builtin_name"] = f"iso-b-{sfx}"
        db.builtin_secrets.insert(
            name=out["builtin_name"],
            organization_id=out["org_a"],
            secret_value="tenant-a-value",
            secret_type="password",
            is_active=True,
            village_id=_vid(),
        )
        today = date.today()
        out["cert_a"] = db.certificates.insert(
            tenant_id=out["tenant_a"],
            name=f"iso-cert-{sfx}",
            organization_id=out["org_a"],
            creator="letsencrypt",
            cert_type="server_cert",
            issue_date=today,
            expiration_date=today + timedelta(days=365),
            auto_renew=False,
            is_revoked=False,
            status="active",
            is_active=True,
            created_at=now,
            updated_at=now,
        )
        db.commit()
        return out

    w = await run_in_threadpool(_setup)
    w["a"] = _tok(app, w["tenant_a"], w["user_a"])
    w["b"] = _tok(app, w["tenant_b"], w["user_b"])
    w["no_tenant"] = _tok(app, "", w["user_a"])
    w["zero_tenant"] = _tok(app, "0", w["user_a"])
    return w


async def _row_exists(app, table, row_id):
    """True when ``table`` still holds ``row_id``."""
    return await run_in_threadpool(
        lambda: app.db(getattr(app.db, table).id == row_id).count() == 1
    )


class TestMissingTenantIs403:
    """A verified token without a tenant claim is refused before any query."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("which", ["no_tenant", "zero_tenant"])
    @pytest.mark.parametrize(
        "url",
        [
            "/api/v1/secrets",
            "/api/v1/secrets/providers",
            "/api/v1/keys",
            "/api/v1/keys/providers",
            "/api/v1/certificates",
        ],
    )
    async def test_list_routes(self, app, world, which, url):
        """Listing without a tenant is 403."""
        status, _ = await _call(app, "get", url, world[which])
        assert status == 403

    @pytest.mark.asyncio
    async def test_builtin_requires_tenant(self, app, world):
        """Built-in secrets refuse a tenant-less token even for a valid org."""
        status, _ = await _call(
            app,
            "get",
            f"/api/v1/builtin-secrets?organization_id={world['org_a']}",
            world["no_tenant"],
        )
        assert status == 403


class TestSecretsRoutes:
    """/api/v1/secrets with tenant B's token against tenant A's rows."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "method,suffix,kw",
        [
            ("get", "", {}),
            ("post", "/unmask", {}),
            ("put", "", {"json": {"name": "stolen"}}),
            ("post", "/sync", {}),
            ("get", "/access-log", {}),
            ("delete", "", {}),
        ],
    )
    async def test_foreign_secret_is_404(self, app, world, method, suffix, kw):
        """Every per-secret route answers 404 for another tenant's secret."""
        url = f"/api/v1/secrets/{world['secret_a']}{suffix}"
        status, data = await _call(app, method, url, world["b"], **kw)
        assert status == 404
        assert "iso-val" not in json.dumps(data)
        assert await _row_exists(app, "secrets", world["secret_a"])

    @pytest.mark.asyncio
    async def test_owner_can_read_and_delete(self, app, world):
        """Tenant A reads (via Quart ``g``) and then deletes its own secret."""
        url = f"/api/v1/secrets/{world['secret_a']}"
        status, data = await _call(app, "get", url, world["a"])
        assert status == 200 and data["secret"]["id"] == world["secret_a"]
        status, _ = await _call(app, "delete", url, world["a"])
        assert status == 204
        assert not await _row_exists(app, "secrets", world["secret_a"])

    @pytest.mark.asyncio
    async def test_list_excludes_foreign(self, app, world):
        """Listing (even filtered to A's org) never returns A's secret to B."""
        for q in ("", f"?organization_id={world['org_a']}"):
            status, data = await _call(app, "get", f"/api/v1/secrets{q}", world["b"])
            assert status == 200
            assert world["secret_a"] not in [s["id"] for s in data["secrets"]]

    @pytest.mark.asyncio
    async def test_create_with_foreign_provider_or_org_is_404(self, app, world):
        """B cannot attach a secret to A's provider or A's organization."""
        base = {
            "name": "x",
            "provider_path": "/p",
            "secret_type": "password",
        }
        bodies = [
            {
                **base,
                "provider_id": world["sprov_a"],
                "organization_id": world["org_b"],
            },
            {
                **base,
                "provider_id": world["sprov_a"],
                "organization_id": world["org_a"],
            },
        ]
        for body in bodies:
            status, _ = await _call(
                app, "post", "/api/v1/secrets", world["b"], json=body
            )
            assert status == 404

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "method,suffix,kw",
        [
            ("get", "", {}),
            ("put", "", {"json": {"name": "stolen"}}),
            ("post", "/sync", {}),
            ("delete", "", {}),
        ],
    )
    async def test_foreign_provider_is_404(self, app, world, method, suffix, kw):
        """Per-provider routes answer 404 for another tenant's provider."""
        url = f"/api/v1/secrets/providers/{world['sprov_a']}{suffix}"
        status, _ = await _call(app, method, url, world["b"], **kw)
        assert status == 404
        assert await _row_exists(app, "secret_providers", world["sprov_a"])

    @pytest.mark.asyncio
    async def test_foreign_provider_connection_test_reveals_nothing(self, app, world):
        """Testing A's provider from B looks exactly like testing an unknown id."""
        foreign = await _call(
            app,
            "post",
            f"/api/v1/secrets/providers/{world['sprov_a']}/test",
            world["b"],
        )
        unknown = await _call(
            app, "post", "/api/v1/secrets/providers/2000000000/test", world["b"]
        )
        assert foreign == unknown
        assert foreign[0] == 400 and foreign[1]["success"] is False
        own = await _call(
            app,
            "post",
            f"/api/v1/secrets/providers/{world['sprov_a']}/test",
            world["a"],
        )
        assert own[0] == 200

    @pytest.mark.asyncio
    async def test_provider_list_and_create_scoped(self, app, world):
        """B never lists A's provider and cannot create one in A's org."""
        status, data = await _call(app, "get", "/api/v1/secrets/providers", world["b"])
        assert status == 200
        assert world["sprov_a"] not in [p["id"] for p in data["providers"]]
        status, _ = await _call(
            app,
            "post",
            "/api/v1/secrets/providers",
            world["b"],
            json={
                "name": "x",
                "provider": "aws_secrets_manager",
                "organization_id": world["org_a"],
                "config_json": {},
            },
        )
        assert status == 404


class TestKeysRoutes:
    """/api/v1/keys with tenant B's token against tenant A's rows."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "method,suffix,kw",
        [
            ("get", "", {}),
            ("post", "/enable", {}),
            ("post", "/disable", {}),
            ("post", "/rotate", {}),
            ("delete", "", {}),
            ("post", "/encrypt", {"json": {"plaintext": "p"}}),
            ("get", "/access-log", {}),
        ],
    )
    async def test_foreign_key_is_404(self, app, world, kms, method, suffix, kw):
        """Per-key routes answer 404 for another tenant's key; the provider is never called."""
        url = f"/api/v1/keys/{world['key_a']}{suffix}"
        status, _ = await _call(app, method, url, world["b"], **kw)
        assert status == 404
        assert kms.method_calls == []

    @pytest.mark.asyncio
    async def test_foreign_provider_routes_are_404(self, app, world, kms):
        """Provider read/update/delete/test and key creation on A's provider are 404 for B."""
        pid = world["kprov_a"]
        for method, url, kw in [
            ("get", f"/api/v1/keys/providers/{pid}", {}),
            ("put", f"/api/v1/keys/providers/{pid}", {"json": {"name": "x"}}),
            ("delete", f"/api/v1/keys/providers/{pid}", {}),
            ("post", "/api/v1/keys", {"json": {"provider_id": pid, "key_name": "k"}}),
        ]:
            status, _ = await _call(app, method, url, world["b"], **kw)
            assert status == 404, (method, url)
        assert kms.create_key.call_count == 0
        assert await _row_exists(app, "key_providers", pid)

    @pytest.mark.asyncio
    async def test_lists_exclude_foreign(self, app, world):
        """B's key and provider listings never contain A's rows."""
        _, keys = await _call(app, "get", "/api/v1/keys", world["b"])
        _, provs = await _call(app, "get", "/api/v1/keys/providers", world["b"])
        assert world["key_a"] not in [k["id"] for k in keys["keys"]]
        assert world["kprov_a"] not in [p["id"] for p in provs["providers"]]

    @pytest.mark.asyncio
    async def test_create_provider_in_foreign_org_is_404(self, app, world):
        """B cannot register a key provider in A's organization."""
        status, _ = await _call(
            app,
            "post",
            "/api/v1/keys/providers",
            world["b"],
            json={
                "name": "x",
                "provider_type": "aws_kms",
                "config": {},
                "organization_id": world["org_a"],
            },
        )
        assert status == 404

    @pytest.mark.asyncio
    async def test_owner_creates_provider_and_key(self, app, world, kms):
        """A creates a provider (real columns) and a key under it."""
        status, prov = await _call(
            app,
            "post",
            "/api/v1/keys/providers",
            world["a"],
            json={"name": "own", "provider_type": "AWS_KMS", "config": {"token": "t"}},
        )
        assert status == 201
        assert prov["provider"] == "aws_kms"
        assert prov["organization_id"] == world["org_a"]
        assert prov["config_json"]["token"] == "***REDACTED***"
        status, key = await _call(
            app,
            "post",
            "/api/v1/keys",
            world["a"],
            json={"provider_id": prov["id"], "key_name": "k1"},
        )
        assert status == 201
        assert key["organization_id"] == world["org_a"]


class TestBuiltinRoutes:
    """/api/v1/builtin-secrets must not trust a caller-supplied organization_id."""

    @pytest.mark.asyncio
    async def test_foreign_org_is_404_everywhere(self, app, world):
        """List/get/create/update/delete/test-connection on A's org are 404 for B."""
        org, name = world["org_a"], world["builtin_name"]
        base = "/api/v1/builtin-secrets"
        for method, url, kw in [
            ("get", f"{base}?organization_id={org}", {}),
            ("get", f"{base}/{name}?organization_id={org}", {}),
            (
                "post",
                base,
                {"json": {"name": "n", "value": "v", "organization_id": org}},
            ),
            ("put", f"{base}/{name}?organization_id={org}", {"json": {"value": "pwn"}}),
            ("delete", f"{base}/{name}?organization_id={org}", {}),
            ("post", f"{base}/test-connection", {"json": {"organization_id": org}}),
        ]:
            status, data = await _call(app, method, url, world["b"], **kw)
            assert status == 404, (method, url)
            assert "tenant-a-value" not in json.dumps(data)

        def _value():
            return (
                app.db(app.db.builtin_secrets.name == name)
                .select(
                    app.db.builtin_secrets.secret_value,
                    app.db.builtin_secrets.is_active,
                )
                .first()
            )

        row = await run_in_threadpool(_value)
        assert row.secret_value == "tenant-a-value" and row.is_active is True

    @pytest.mark.asyncio
    async def test_owner_lists_own_secret(self, app, world):
        """A lists its own org's built-in secrets."""
        status, data = await _call(
            app,
            "get",
            f"/api/v1/builtin-secrets?organization_id={world['org_a']}",
            world["a"],
        )
        assert status == 200
        assert world["builtin_name"] in [s["name"] for s in data["secrets"]]


class TestCertificateRoutes:
    """/api/v1/certificates tenant scoping and the resource-role check."""

    @pytest.mark.asyncio
    async def test_foreign_certificate_is_404(self, app, world):
        """B gets 404 for A's certificate on get/put/delete; it is never listed."""
        url = f"/api/v1/certificates/{world['cert_a']}"
        for method, kw in [
            ("get", {}),
            ("put", {"json": {"notes": "x"}}),
            ("delete", {}),
        ]:
            status, _ = await _call(app, method, url, world["b"], **kw)
            assert status == 404, method
        status, data = await _call(app, "get", "/api/v1/certificates", world["b"])
        assert status == 200
        assert world["cert_a"] not in [c["id"] for c in data["items"]]
        assert await _row_exists(app, "certificates", world["cert_a"])

    @pytest.mark.asyncio
    async def test_create_in_foreign_org_is_404(self, app, world):
        """B cannot create a certificate in A's organization."""
        today = date.today()
        status, _ = await _call(
            app,
            "post",
            "/api/v1/certificates",
            world["b"],
            json={
                "name": "x",
                "organization_id": world["org_a"],
                "cert_type": "server_cert",
                "creator": "letsencrypt",
                "issue_date": today.isoformat(),
                "expiration_date": (today + timedelta(days=30)).isoformat(),
            },
        )
        assert status == 404

    @pytest.mark.asyncio
    async def test_maintainer_on_owning_org_can_update_and_delete(self, app, world):
        """An org maintainer passes resource_role_required; without the role it is 403."""
        url = f"/api/v1/certificates/{world['cert_a']}"
        status, _ = await _call(app, "put", url, world["a"], json={"notes": "n"})
        assert status == 403

        def _grant():
            app.db.resource_roles.insert(
                identity_id=world["user_a"],
                resource_type="ORGANIZATION",
                resource_id=world["org_a"],
                role="maintainer",
                role_type="MAINTAINER",
            )
            app.db.commit()

        await run_in_threadpool(_grant)
        status, data = await _call(app, "put", url, world["a"], json={"notes": "n"})
        assert status == 200 and data["notes"] == "n"
        status, _ = await _call(app, "delete", url, world["a"])
        assert status == 204
        assert not await _row_exists(app, "certificates", world["cert_a"])


class TestServiceLayerFailsClosed:
    """The service layer cannot be used without a tenant."""

    def test_secrets_service_requires_tenant_argument(self):
        """SecretsService has no unscoped constructor."""
        with pytest.raises(TypeError):
            SecretsService(MagicMock())  # type: ignore[call-arg]

    @pytest.mark.parametrize("tenant", [None, 0, ""])
    def test_missing_tenant_raises(self, tenant):
        """Every helper treats None/0/'' as no tenant."""
        db = MagicMock()
        for call in (
            lambda: require_tenant(tenant),
            lambda: tenant_org_ids(db, tenant),
            lambda: org_in_tenant(db, 1, tenant),
            lambda: SecretsService(db, tenant_id=tenant).list_secrets(),
            lambda: KeysService(db, tenant_id=tenant).list_keys(),
        ):
            with pytest.raises(TenantScopeError):
                call()
        db.assert_not_called()

    @pytest.mark.asyncio
    async def test_org_in_tenant_rejects_foreign_and_junk(self, app, world):
        """org_in_tenant is True only for the tenant's own org ids."""
        db = app.db

        def _check():
            return (
                org_in_tenant(db, world["org_a"], world["tenant_a"]),
                org_in_tenant(db, world["org_a"], world["tenant_b"]),
                org_in_tenant(db, "abc", world["tenant_a"]),
                org_in_tenant(db, True, world["tenant_a"]),
                org_in_tenant(db, -1, world["tenant_a"]),
            )

        assert await run_in_threadpool(_check) == (True, False, False, False, False)
