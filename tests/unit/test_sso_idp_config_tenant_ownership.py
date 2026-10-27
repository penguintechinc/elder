"""Tenant-ownership + secret-redaction regression tests for IdP config CRUD.

regression: idp-config-cross-tenant -- the SSO IdP configuration routes in
``apps/api/api/v1/sso.py`` (list/update/delete/create, backed by
``SAMLService``) only checked ``tenant_role == "admin"`` and never that the
config belonged to the caller's tenant. A tenant-A admin could list tenant B's
config (certificate + ``oidc_client_secret``), replace its SAML certificate /
OIDC issuer+client, or delete it, and global (``tenant_id IS NULL``) configs
were editable by any tenant admin. The OIDC login path resolves tenant from the
very same row, so the same hole was an account-takeover of tenant B via
``/oidc/callback`` (``TestOidcAccountTakeoverChain``).

These tests drive the real routes against a real Postgres (``DATABASE_URL``);
only the *external* OIDC identity provider (discovery/JWKS/token endpoint) is
faked, because it is not part of the code under test.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock
from uuid import uuid4

import jwt
import pytest
import pytest_asyncio
import requests
from cryptography.hazmat.primitives.asymmetric import rsa
from quart import current_app

from apps.api.services.sso import SAMLService
from apps.api.services.sso.idp_access import (
    IdPCaller,
    IdPConfigView,
    IdPForbiddenError,
    IdPNotFoundError,
)

SECRET_B = "SECRET-B-oidc-client-secret"
SECRET_G = "SECRET-G-oidc-client-secret"
CERT_A = "CERT-A-own-tenant-cert"
CERT_B = "CERT-B-victim-tenant-cert"
CERT_G = "CERT-G-global-cert"
ATTACKER_CERT = "CERT-ATTACKER-forged"

SAML_KEYS = {
    "id",
    "tenant_id",
    "idp_type",
    "name",
    "attribute_mappings",
    "jit_provisioning_enabled",
    "default_role",
    "is_active",
    "entity_id",
    "metadata_url",
    "sso_url",
    "slo_url",
    "certificate",
}
OIDC_KEYS = {
    "id",
    "tenant_id",
    "idp_type",
    "name",
    "attribute_mappings",
    "jit_provisioning_enabled",
    "default_role",
    "is_active",
    "oidc_client_id",
    "oidc_issuer_url",
    "oidc_scopes",
    "oidc_response_type",
    "oidc_token_endpoint_auth_method",
    "has_client_secret",
}


@dataclass(slots=True)
class World:
    """Ids and tokens for two tenants, a global scope and their IdP configs."""

    prefix: str
    tenant_a: int
    tenant_b: int
    tenant_sys: int
    a_saml: int
    b_saml: int
    b_oidc: int
    g_saml: int
    g_oidc: int
    victim_email: str
    tokens: dict[str, str] = field(default_factory=dict)

    def auth(self, who: str) -> dict[str, str]:
        """Bearer header for the named caller."""
        return {"Authorization": f"Bearer {self.tokens[who]}"}


def _mint_token(
    app,
    *,
    user_id: int,
    tenant_id: int | None,
    tenant_role: str | None,
    global_role: str | None = None,
) -> str:
    """Mint a portal-user JWT exactly like portal-auth does (HS256)."""
    secret = app.config.get("JWT_SECRET_KEY") or app.config.get("SECRET_KEY")
    now = datetime.now(UTC)
    payload = {
        "sub": str(user_id),
        "email": f"user{user_id}@example.test",
        "tenant_id": tenant_id,
        "tenant_role": tenant_role,
        "global_role": global_role,
        "type": "portal_user",
        "iat": now,
        "exp": now + timedelta(hours=1),
    }
    return jwt.encode(payload, secret, algorithm="HS256")


def _insert_idp(db, tenant_id: int | None, idp_type: str, name: str, **extra) -> int:
    """Insert an IdP config row directly (fixture data, not code under test)."""
    config_id = db.idp_configurations.insert(
        tenant_id=tenant_id,
        idp_type=idp_type,
        name=name,
        attribute_mappings={},
        jit_provisioning_enabled=True,
        default_role="reader",
        is_active=True,
        **extra,
    )
    db.commit()
    return config_id


def _fetch(db, config_id: int) -> dict[str, Any] | None:
    """Read an IdP config row straight from the DB as a plain dict."""
    row = db(db.idp_configurations.id == config_id).select().first()
    return row.as_dict() if row else None


@pytest.fixture(autouse=True)
def _patch_login_required(mocker):
    """``POST /idp`` also carries ``@login_required`` (identity auth); stub it."""
    mocker.patch(
        "apps.api.auth.decorators.get_current_user",
        return_value=MagicMock(id=1, is_superuser=False),
    )


@pytest_asyncio.fixture
async def world(app):
    """Two tenants + system tenant, IdP configs for each scope, and callers."""
    prefix = f"idp-test-{uuid4().hex[:8]}"
    async with app.app_context():
        db = current_app.db

        def tenant(label: str) -> int:
            tid = db.tenants.insert(
                name=f"{prefix}-{label}", slug=f"{prefix}-{label}", is_active=True
            )
            db.commit()
            return tid

        tenant_a, tenant_b, tenant_sys = tenant("a"), tenant("b"), tenant("sys")
        victim_email = f"victim-admin@{prefix}.test"
        db.portal_users.insert(
            tenant_id=tenant_b,
            email=victim_email,
            tenant_role="admin",
            is_active=True,
            email_verified=True,
        )
        db.commit()

        a_saml = _insert_idp(
            db, tenant_a, "saml", f"{prefix}-a-saml", certificate=CERT_A
        )
        b_saml = _insert_idp(
            db,
            tenant_b,
            "saml",
            f"{prefix}-b-saml",
            certificate=CERT_B,
            sso_url="https://idp-b.example.test/sso",
        )
        b_oidc = _insert_idp(
            db,
            tenant_b,
            "oidc",
            f"{prefix}-b-oidc",
            oidc_client_id="client-b",
            oidc_client_secret=SECRET_B,
            oidc_issuer_url="https://idp-b.example.test",
        )
        g_saml = _insert_idp(db, None, "saml", f"{prefix}-g-saml", certificate=CERT_G)
        g_oidc = _insert_idp(
            db,
            None,
            "oidc",
            f"{prefix}-g-oidc",
            oidc_client_id="client-g",
            oidc_client_secret=SECRET_G,
            oidc_issuer_url="https://idp-g.example.test",
        )

    w = World(
        prefix=prefix,
        tenant_a=tenant_a,
        tenant_b=tenant_b,
        tenant_sys=tenant_sys,
        a_saml=a_saml,
        b_saml=b_saml,
        b_oidc=b_oidc,
        g_saml=g_saml,
        g_oidc=g_oidc,
        victim_email=victim_email,
    )
    w.tokens = {
        "a_admin": _mint_token(
            app, user_id=101, tenant_id=tenant_a, tenant_role="admin"
        ),
        "b_admin": _mint_token(
            app, user_id=102, tenant_id=tenant_b, tenant_role="admin"
        ),
        "a_reader": _mint_token(
            app, user_id=103, tenant_id=tenant_a, tenant_role="reader"
        ),
        "global_admin": _mint_token(
            app,
            user_id=104,
            tenant_id=tenant_sys,
            tenant_role="admin",
            global_role="admin",
        ),
        "support": _mint_token(
            app,
            user_id=105,
            tenant_id=tenant_a,
            tenant_role=None,
            global_role="support",
        ),
        "support_tenant_admin": _mint_token(
            app,
            user_id=106,
            tenant_id=tenant_a,
            tenant_role="admin",
            global_role="support",
        ),
        "no_tenant_admin": _mint_token(
            app, user_id=107, tenant_id=None, tenant_role="admin"
        ),
    }
    yield w

    async with app.app_context():
        db = current_app.db
        db(db.idp_configurations.name.startswith(prefix)).delete()
        for tid in (tenant_a, tenant_b, tenant_sys):
            db(db.idp_configurations.tenant_id == tid).delete()
            db(db.portal_users.tenant_id == tid).delete()
            db(db.tenants.id == tid).delete()
        db.commit()


async def _row(app, config_id: int) -> dict[str, Any] | None:
    """Fetch an IdP config row in its own app context."""
    async with app.app_context():
        return _fetch(current_app.db, config_id)


def _all_keys(node: Any) -> set[str]:
    """Collect every dict key anywhere in a parsed JSON document."""
    keys: set[str] = set()
    if isinstance(node, dict):
        for k, v in node.items():
            keys.add(k)
            keys |= _all_keys(v)
    elif isinstance(node, list):
        for item in node:
            keys |= _all_keys(item)
    return keys


def _assert_no_secrets(raw: str) -> None:
    """No secret value and no ``oidc_client_secret`` key may appear in a body."""
    for secret in (SECRET_B, SECRET_G):
        assert secret not in raw
    assert "oidc_client_secret" not in _all_keys(json.loads(raw))


# --------------------------------------------------------------------------
# list
# --------------------------------------------------------------------------


class TestListIdpConfigs:
    """GET /api/v1/sso/idp -- regression: idp-config-cross-tenant."""

    @pytest.mark.asyncio
    async def test_tenant_admin_lists_only_own_tenant_configs(
        self, async_client, world
    ):
        resp = await async_client.get("/api/v1/sso/idp", headers=world.auth("a_admin"))
        assert resp.status_code == 200, (await resp.get_data()).decode()
        ids = {c["id"] for c in await resp.get_json()}
        # Own config only: not tenant B's, not the global ones.
        assert ids == {world.a_saml}

    @pytest.mark.asyncio
    async def test_tenant_admin_cannot_list_other_tenant_via_query(
        self, async_client, world
    ):
        resp = await async_client.get(
            "/api/v1/sso/idp",
            query_string={"tenant_id": world.tenant_b},
            headers=world.auth("a_admin"),
        )
        assert resp.status_code == 403
        raw = (await resp.get_data()).decode()
        assert CERT_B not in raw
        assert SECRET_B not in raw

    @pytest.mark.asyncio
    async def test_tenant_admin_can_pass_own_tenant_filter(self, async_client, world):
        resp = await async_client.get(
            "/api/v1/sso/idp",
            query_string={"tenant_id": world.tenant_a},
            headers=world.auth("a_admin"),
        )
        assert resp.status_code == 200
        assert {c["id"] for c in await resp.get_json()} == {world.a_saml}

    @pytest.mark.asyncio
    async def test_non_integer_tenant_filter_is_400(self, async_client, world):
        resp = await async_client.get(
            "/api/v1/sso/idp",
            query_string={"tenant_id": "abc"},
            headers=world.auth("a_admin"),
        )
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_global_admin_lists_every_scope(self, async_client, world):
        resp = await async_client.get(
            "/api/v1/sso/idp", headers=world.auth("global_admin")
        )
        assert resp.status_code == 200
        ids = {c["id"] for c in await resp.get_json()}
        assert {
            world.a_saml,
            world.b_saml,
            world.b_oidc,
            world.g_saml,
            world.g_oidc,
        } <= ids

    @pytest.mark.asyncio
    async def test_global_admin_tenant_filter_includes_global_excludes_others(
        self, async_client, world
    ):
        resp = await async_client.get(
            "/api/v1/sso/idp",
            query_string={"tenant_id": world.tenant_b},
            headers=world.auth("global_admin"),
        )
        assert resp.status_code == 200
        ids = {c["id"] for c in await resp.get_json()}
        assert {world.b_saml, world.b_oidc, world.g_saml, world.g_oidc} <= ids
        assert world.a_saml not in ids

    @pytest.mark.asyncio
    @pytest.mark.parametrize("who", ["a_reader", "support"])
    async def test_non_admins_cannot_list(self, async_client, world, who):
        resp = await async_client.get("/api/v1/sso/idp", headers=world.auth(who))
        assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_support_global_role_gets_no_cross_tenant_access(
        self, async_client, world
    ):
        """Only ``global_role == 'admin'`` is super-admin; 'support' is not."""
        resp = await async_client.get(
            "/api/v1/sso/idp",
            query_string={"tenant_id": world.tenant_b},
            headers=world.auth("support_tenant_admin"),
        )
        assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_admin_token_without_tenant_claim_fails_closed(
        self, async_client, world
    ):
        resp = await async_client.get(
            "/api/v1/sso/idp", headers=world.auth("no_tenant_admin")
        )
        assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_unauthenticated_is_401(self, async_client, world):
        resp = await async_client.get("/api/v1/sso/idp")
        assert resp.status_code == 401

    @pytest.mark.asyncio
    @pytest.mark.parametrize("who", ["a_admin", "b_admin", "global_admin"])
    async def test_list_never_returns_secrets(self, async_client, world, who):
        resp = await async_client.get("/api/v1/sso/idp", headers=world.auth(who))
        assert resp.status_code == 200
        _assert_no_secrets((await resp.get_data()).decode())

    @pytest.mark.asyncio
    async def test_own_tenant_certificate_and_secret_flag(self, async_client, world):
        resp = await async_client.get("/api/v1/sso/idp", headers=world.auth("b_admin"))
        body = {c["id"]: c for c in await resp.get_json()}
        assert body[world.b_saml]["certificate"] == CERT_B
        # Secret is reported as configured, never returned.
        assert body[world.b_oidc]["has_client_secret"] is True

    @pytest.mark.asyncio
    async def test_response_field_sets_are_exact(self, async_client, world):
        """Fails loudly if any column is ever added to the response."""
        resp = await async_client.get("/api/v1/sso/idp", headers=world.auth("b_admin"))
        body = {c["id"]: c for c in await resp.get_json()}
        assert set(body[world.b_saml]) == SAML_KEYS
        assert set(body[world.b_oidc]) == OIDC_KEYS


# --------------------------------------------------------------------------
# update
# --------------------------------------------------------------------------


class TestUpdateIdpConfig:
    """PUT /api/v1/sso/idp/<id> -- regression: idp-config-cross-tenant."""

    @pytest.mark.asyncio
    async def test_cross_tenant_certificate_swap_denied(self, app, async_client, world):
        resp = await async_client.put(
            f"/api/v1/sso/idp/{world.b_saml}",
            json={"certificate": ATTACKER_CERT},
            headers=world.auth("a_admin"),
        )
        assert resp.status_code == 404, (await resp.get_data()).decode()
        assert (await _row(app, world.b_saml))["certificate"] == CERT_B

    @pytest.mark.asyncio
    async def test_cross_tenant_oidc_rewrite_denied(self, app, async_client, world):
        resp = await async_client.put(
            f"/api/v1/sso/idp/{world.b_oidc}",
            json={
                "oidc_issuer_url": "https://attacker.example.test",
                "oidc_client_id": "attacker-client",
                "oidc_client_secret": "attacker-secret",
            },
            headers=world.auth("a_admin"),
        )
        assert resp.status_code == 404
        row = await _row(app, world.b_oidc)
        assert row["oidc_issuer_url"] == "https://idp-b.example.test"
        assert row["oidc_client_id"] == "client-b"
        assert row["oidc_client_secret"] == SECRET_B

    @pytest.mark.asyncio
    @pytest.mark.parametrize("which", ["g_saml", "g_oidc"])
    async def test_global_config_not_modifiable_by_tenant_admin(
        self, app, async_client, world, which
    ):
        config_id = getattr(world, which)
        resp = await async_client.put(
            f"/api/v1/sso/idp/{config_id}",
            json={"name": "pwned", "is_active": False},
            headers=world.auth("a_admin"),
        )
        assert resp.status_code == 404
        row = await _row(app, config_id)
        assert row["name"] != "pwned"
        assert row["is_active"] is True

    @pytest.mark.asyncio
    async def test_foreign_and_nonexistent_ids_are_indistinguishable(
        self, async_client, world
    ):
        """404 (not 403) for other-tenant ids: no existence oracle."""
        foreign = await async_client.put(
            f"/api/v1/sso/idp/{world.b_saml}",
            json={"name": "x"},
            headers=world.auth("a_admin"),
        )
        missing = await async_client.put(
            f"/api/v1/sso/idp/{world.b_saml + 100000}",
            json={"name": "x"},
            headers=world.auth("a_admin"),
        )
        assert foreign.status_code == missing.status_code == 404
        f_body = (await foreign.get_json())["error"].replace(str(world.b_saml), "<id>")
        m_body = (await missing.get_json())["error"].replace(
            str(world.b_saml + 100000), "<id>"
        )
        assert f_body == m_body

    @pytest.mark.asyncio
    async def test_own_tenant_update_works_and_redacts_secret(
        self, app, async_client, world
    ):
        resp = await async_client.put(
            f"/api/v1/sso/idp/{world.b_oidc}",
            json={
                "name": f"{world.prefix}-b-oidc-renamed",
                "oidc_client_secret": "rotated",
            },
            headers=world.auth("b_admin"),
        )
        assert resp.status_code == 200, (await resp.get_data()).decode()
        raw = (await resp.get_data()).decode()
        body = json.loads(raw)
        assert body["updated"] is True
        assert body["name"].endswith("renamed")
        assert body["has_client_secret"] is True
        # Neither the old nor the newly-written secret is echoed back.
        assert "rotated" not in raw
        _assert_no_secrets(raw)
        row = await _row(app, world.b_oidc)
        assert row["oidc_client_secret"] == "rotated"

    @pytest.mark.asyncio
    async def test_own_tenant_saml_update_returns_certificate(
        self, async_client, world
    ):
        resp = await async_client.put(
            f"/api/v1/sso/idp/{world.a_saml}",
            json={"certificate": "CERT-A-rotated"},
            headers=world.auth("a_admin"),
        )
        assert resp.status_code == 200
        assert (await resp.get_json())["certificate"] == "CERT-A-rotated"

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "which,field_name,value",
        [
            ("b_saml", "certificate", "CERT-B-by-global-admin"),
            ("g_saml", "certificate", "CERT-G-by-global-admin"),
        ],
    )
    async def test_global_admin_can_update_any_config(
        self, app, async_client, world, which, field_name, value
    ):
        config_id = getattr(world, which)
        resp = await async_client.put(
            f"/api/v1/sso/idp/{config_id}",
            json={field_name: value},
            headers=world.auth("global_admin"),
        )
        assert resp.status_code == 200, (await resp.get_data()).decode()
        assert (await _row(app, config_id))[field_name] == value

    @pytest.mark.asyncio
    async def test_tenant_id_override_rejected_for_tenant_admin(
        self, app, async_client, world
    ):
        resp = await async_client.put(
            f"/api/v1/sso/idp/{world.a_saml}",
            json={"tenant_id": world.tenant_b, "name": "moved"},
            headers=world.auth("a_admin"),
        )
        assert resp.status_code == 403
        row = await _row(app, world.a_saml)
        assert row["tenant_id"] == world.tenant_a
        assert row["name"] != "moved"

    @pytest.mark.asyncio
    async def test_tenant_id_equal_to_current_is_accepted(self, async_client, world):
        resp = await async_client.put(
            f"/api/v1/sso/idp/{world.a_saml}",
            json={"tenant_id": world.tenant_a, "name": f"{world.prefix}-same"},
            headers=world.auth("a_admin"),
        )
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_global_admin_cannot_rehome_config_either(
        self, app, async_client, world
    ):
        resp = await async_client.put(
            f"/api/v1/sso/idp/{world.b_saml}",
            json={"tenant_id": world.tenant_a},
            headers=world.auth("global_admin"),
        )
        assert resp.status_code == 403
        assert (await _row(app, world.b_saml))["tenant_id"] == world.tenant_b

    @pytest.mark.asyncio
    async def test_reader_cannot_update(self, async_client, world):
        resp = await async_client.put(
            f"/api/v1/sso/idp/{world.a_saml}",
            json={"name": "x"},
            headers=world.auth("a_reader"),
        )
        assert resp.status_code == 403

    @pytest.mark.asyncio
    @pytest.mark.parametrize("payload", [None, [], "text"])
    async def test_non_object_body_is_400(self, async_client, world, payload):
        resp = await async_client.put(
            f"/api/v1/sso/idp/{world.a_saml}",
            json=payload,
            headers=world.auth("a_admin"),
        )
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_body_key_collision_with_parameter_names_is_not_a_500(
        self, async_client, world
    ):
        """Body keys such as ``caller``/``config_id`` must not break the call."""
        resp = await async_client.put(
            f"/api/v1/sso/idp/{world.a_saml}",
            json={"caller": "x", "config_id": 1, "name": f"{world.prefix}-kw"},
            headers=world.auth("a_admin"),
        )
        assert resp.status_code == 200


# --------------------------------------------------------------------------
# delete
# --------------------------------------------------------------------------


class TestDeleteIdpConfig:
    """DELETE /api/v1/sso/idp/<id> -- regression: idp-config-cross-tenant."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("which", ["b_saml", "b_oidc", "g_saml", "g_oidc"])
    async def test_tenant_admin_cannot_delete_foreign_or_global(
        self, app, async_client, world, which
    ):
        config_id = getattr(world, which)
        resp = await async_client.delete(
            f"/api/v1/sso/idp/{config_id}", headers=world.auth("a_admin")
        )
        assert resp.status_code == 404
        assert await _row(app, config_id) is not None

    @pytest.mark.asyncio
    async def test_foreign_and_nonexistent_delete_bodies_match(
        self, async_client, world
    ):
        foreign = await async_client.delete(
            f"/api/v1/sso/idp/{world.b_saml}", headers=world.auth("a_admin")
        )
        missing_id = world.b_saml + 100000
        missing = await async_client.delete(
            f"/api/v1/sso/idp/{missing_id}", headers=world.auth("a_admin")
        )
        assert foreign.status_code == missing.status_code == 404
        f_err = (await foreign.get_json())["error"].replace(str(world.b_saml), "<id>")
        m_err = (await missing.get_json())["error"].replace(str(missing_id), "<id>")
        assert f_err == m_err

    @pytest.mark.asyncio
    async def test_own_tenant_delete_works(self, app, async_client, world):
        resp = await async_client.delete(
            f"/api/v1/sso/idp/{world.a_saml}", headers=world.auth("a_admin")
        )
        assert resp.status_code == 200
        assert (await resp.get_json()) == {"deleted": True}
        assert await _row(app, world.a_saml) is None

    @pytest.mark.asyncio
    @pytest.mark.parametrize("which", ["b_saml", "g_oidc"])
    async def test_global_admin_can_delete_any_config(
        self, app, async_client, world, which
    ):
        config_id = getattr(world, which)
        resp = await async_client.delete(
            f"/api/v1/sso/idp/{config_id}", headers=world.auth("global_admin")
        )
        assert resp.status_code == 200
        assert await _row(app, config_id) is None

    @pytest.mark.asyncio
    async def test_reader_cannot_delete(self, app, async_client, world):
        resp = await async_client.delete(
            f"/api/v1/sso/idp/{world.a_saml}", headers=world.auth("a_reader")
        )
        assert resp.status_code == 403
        assert await _row(app, world.a_saml) is not None


# --------------------------------------------------------------------------
# create
# --------------------------------------------------------------------------


class TestCreateIdpConfig:
    """POST /api/v1/sso/idp -- regression: idp-config-cross-tenant."""

    @pytest.mark.asyncio
    async def test_tenant_admin_create_binds_to_own_tenant_and_redacts(
        self, app, async_client, world
    ):
        resp = await async_client.post(
            "/api/v1/sso/idp",
            json={
                "name": f"{world.prefix}-new-oidc",
                "idp_type": "oidc",
                "oidc_client_id": "new-client",
                "oidc_client_secret": "new-secret-value",
                "oidc_issuer_url": "https://idp-new.example.test",
            },
            headers=world.auth("a_admin"),
        )
        assert resp.status_code == 201, (await resp.get_data()).decode()
        raw = (await resp.get_data()).decode()
        body = json.loads(raw)
        assert body["tenant_id"] == world.tenant_a
        assert body["has_client_secret"] is True
        assert "new-secret-value" not in raw
        assert "oidc_client_secret" not in _all_keys(body)
        assert set(body) == OIDC_KEYS
        assert (await _row(app, body["id"]))["tenant_id"] == world.tenant_a

    @pytest.mark.asyncio
    async def test_tenant_admin_create_with_own_tenant_id_ok(self, async_client, world):
        resp = await async_client.post(
            "/api/v1/sso/idp",
            json={"name": f"{world.prefix}-own", "tenant_id": world.tenant_a},
            headers=world.auth("a_admin"),
        )
        assert resp.status_code == 201
        assert (await resp.get_json())["tenant_id"] == world.tenant_a

    @pytest.mark.asyncio
    @pytest.mark.parametrize("override", ["other_tenant", None, "5"])
    async def test_tenant_id_override_rejected_for_tenant_admin(
        self, app, async_client, world, override
    ):
        """Foreign id, explicit null (= global) and a wrong-typed id all 403."""
        tenant_id = world.tenant_b if override == "other_tenant" else override
        name = f"{world.prefix}-override-{override}"
        resp = await async_client.post(
            "/api/v1/sso/idp",
            json={"name": name, "tenant_id": tenant_id},
            headers=world.auth("a_admin"),
        )
        assert resp.status_code == 403
        async with app.app_context():
            db = current_app.db
            assert db(db.idp_configurations.name == name).count() == 0

    @pytest.mark.asyncio
    async def test_global_admin_can_create_for_any_tenant_and_global(
        self, async_client, world
    ):
        for_b = await async_client.post(
            "/api/v1/sso/idp",
            json={"name": f"{world.prefix}-for-b", "tenant_id": world.tenant_b},
            headers=world.auth("global_admin"),
        )
        assert for_b.status_code == 201
        assert (await for_b.get_json())["tenant_id"] == world.tenant_b

        global_cfg = await async_client.post(
            "/api/v1/sso/idp",
            json={"name": f"{world.prefix}-global"},
            headers=world.auth("global_admin"),
        )
        assert global_cfg.status_code == 201
        assert (await global_cfg.get_json())["tenant_id"] is None

    @pytest.mark.asyncio
    async def test_global_admin_wrong_typed_tenant_id_is_400(self, async_client, world):
        resp = await async_client.post(
            "/api/v1/sso/idp",
            json={"name": f"{world.prefix}-bad", "tenant_id": "abc"},
            headers=world.auth("global_admin"),
        )
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_service_level_refusal_maps_to_403(self, async_client, world, mocker):
        """Defense in depth: if the service refuses, the route answers 403."""
        mocker.patch.object(
            SAMLService,
            "create_idp_config",
            side_effect=IdPForbiddenError("refused by service"),
        )
        resp = await async_client.post(
            "/api/v1/sso/idp",
            json={"name": f"{world.prefix}-refused"},
            headers=world.auth("a_admin"),
        )
        assert resp.status_code == 403
        assert (await resp.get_json())["error"] == "refused by service"

    @pytest.mark.asyncio
    async def test_reader_cannot_create(self, async_client, world):
        resp = await async_client.post(
            "/api/v1/sso/idp",
            json={"name": f"{world.prefix}-reader"},
            headers=world.auth("a_reader"),
        )
        assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_create_requires_name_and_object_body(self, async_client, world):
        no_name = await async_client.post(
            "/api/v1/sso/idp", json={"idp_type": "saml"}, headers=world.auth("a_admin")
        )
        assert no_name.status_code == 400
        not_object = await async_client.post(
            "/api/v1/sso/idp", json=["x"], headers=world.auth("a_admin")
        )
        assert not_object.status_code == 400


# --------------------------------------------------------------------------
# service / data-access layer (independent of the routes)
# --------------------------------------------------------------------------


class TestServiceLayerEnforcesOwnership:
    """The data layer refuses on its own -- a future route cannot bypass it."""

    @pytest.mark.asyncio
    async def test_scoped_service_calls_cannot_reach_foreign_rows(self, app, world):
        caller = IdPCaller(tenant_id=world.tenant_a)
        async with app.app_context():
            for target in (world.b_saml, world.g_saml):
                with pytest.raises(IdPNotFoundError):
                    SAMLService.update_idp_config(target, caller, {"name": "x"})
                with pytest.raises(IdPNotFoundError):
                    SAMLService.delete_idp_config(target, caller)
            db = current_app.db
            assert _fetch(db, world.b_saml)["certificate"] == CERT_B
            assert _fetch(db, world.g_saml) is not None

    @pytest.mark.asyncio
    async def test_non_global_caller_without_tenant_fails_closed(self, app, world):
        nobody = IdPCaller(tenant_id=None)
        async with app.app_context():
            with pytest.raises(IdPForbiddenError):
                SAMLService.list_idp_configs(nobody)
            with pytest.raises(IdPForbiddenError):
                SAMLService.update_idp_config(world.a_saml, nobody, {"name": "x"})
            with pytest.raises(IdPForbiddenError):
                SAMLService.delete_idp_config(world.a_saml, nobody)
            with pytest.raises(IdPForbiddenError):
                SAMLService.create_idp_config(nobody, name=f"{world.prefix}-nobody")

    @pytest.mark.asyncio
    async def test_create_forces_own_tenant_for_non_global(self, app, world):
        caller = IdPCaller(tenant_id=world.tenant_a)
        async with app.app_context():
            with pytest.raises(IdPForbiddenError):
                SAMLService.create_idp_config(
                    caller, name=f"{world.prefix}-svc", tenant_id=world.tenant_b
                )
            created = SAMLService.create_idp_config(
                caller, name=f"{world.prefix}-svc-ok"
            )
        assert created["tenant_id"] == world.tenant_a

    @pytest.mark.asyncio
    async def test_global_admin_service_calls_cross_scopes(self, app, world):
        caller = IdPCaller(tenant_id=world.tenant_sys, is_global_admin=True)
        async with app.app_context():
            listed = SAMLService.list_idp_configs(caller)
            assert {world.a_saml, world.b_saml, world.g_saml} <= {
                c["id"] for c in listed
            }
            updated = SAMLService.update_idp_config(
                world.b_saml, caller, {"name": f"{world.prefix}-by-global"}
            )
            assert updated["updated"] is True
            assert SAMLService.delete_idp_config(world.g_saml, caller) == {
                "deleted": True
            }

    def test_view_omits_secret_and_unknown_types_get_base_fields(self):
        record = SimpleNamespace(
            id=1,
            tenant_id=2,
            idp_type="ldap",
            name="n",
            attribute_mappings=None,
            jit_provisioning_enabled=True,
            default_role="reader",
            is_active=True,
            entity_id="e",
            metadata_url=None,
            sso_url=None,
            slo_url=None,
            certificate="C",
            oidc_client_id="c",
            oidc_client_secret="TOP-SECRET",
            oidc_issuer_url="i",
            oidc_scopes=None,
            oidc_response_type=None,
            oidc_token_endpoint_auth_method=None,
        )
        view = IdPConfigView.from_record(record)
        assert view.has_client_secret is True
        assert "TOP-SECRET" not in repr(view)
        assert set(view.to_dict()) == SAML_KEYS - {
            "entity_id",
            "metadata_url",
            "sso_url",
            "slo_url",
            "certificate",
        }


# --------------------------------------------------------------------------
# OIDC account-takeover chain (the same rows drive /oidc/callback)
# --------------------------------------------------------------------------


@dataclass(slots=True)
class FakeIdp:
    """Stand-in for an *external* OIDC provider (discovery, JWKS, token)."""

    issuer: str
    client_id: str
    client_secret: str
    kid: str
    codes: dict[str, str]
    key: Any = field(default_factory=lambda: rsa.generate_private_key(65537, 2048))

    @property
    def token_endpoint(self) -> str:
        """Token endpoint URL advertised in discovery."""
        return f"{self.issuer}/token"

    @property
    def jwks_uri(self) -> str:
        """JWKS URL advertised in discovery."""
        return f"{self.issuer}/jwks"

    def discovery(self) -> dict[str, str]:
        """OIDC discovery document."""
        return {
            "issuer": self.issuer,
            "token_endpoint": self.token_endpoint,
            "jwks_uri": self.jwks_uri,
        }

    def jwks(self) -> dict[str, Any]:
        """JWKS with this provider's single signing key."""
        jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(self.key.public_key()))
        jwk.update({"kid": self.kid, "alg": "RS256", "use": "sig"})
        return {"keys": [jwk]}

    def issue_id_token(self, code: str) -> str:
        """Mint an ID token for ``code`` (raises like a real IdP if unknown)."""
        if code not in self.codes:
            raise ValueError("invalid_grant")
        now = datetime.now(UTC)
        claims = {
            "iss": self.issuer,
            "aud": self.client_id,
            "sub": f"sub-{code}",
            "email": self.codes[code],
            "iat": now,
            "exp": now + timedelta(minutes=5),
        }
        return jwt.encode(
            claims, self.key, algorithm="RS256", headers={"kid": self.kid}
        )


class _Resp:
    """Minimal ``requests.Response`` stand-in."""

    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        """Always 200."""

    def json(self) -> dict[str, Any]:
        """Return the canned JSON document."""
        return self._payload


def _install_fake_idps(monkeypatch, idps: list[FakeIdp]) -> None:
    """Route OIDCService's outbound HTTP/OAuth calls to the fake providers."""
    documents: dict[str, Any] = {}
    by_token_endpoint: dict[str, FakeIdp] = {}
    for idp in idps:
        documents[f"{idp.issuer}/.well-known/openid-configuration"] = idp.discovery
        documents[idp.jwks_uri] = idp.jwks
        by_token_endpoint[idp.token_endpoint] = idp

    def fake_get(url: str, **_kwargs):
        if url not in documents:
            raise requests.ConnectionError(f"unreachable: {url}")
        return _Resp(documents[url]())

    class FakeSession:
        def __init__(self, client_id, client_secret, token_endpoint_auth_method=None):
            self.client_id = client_id
            self.client_secret = client_secret

        def fetch_token(self, url, grant_type, code, redirect_uri):
            idp = by_token_endpoint[url]
            if (self.client_id, self.client_secret) != (
                idp.client_id,
                idp.client_secret,
            ):
                raise ValueError("invalid_client")
            return {
                "id_token": idp.issue_id_token(code),
                "access_token": "idp-access-token",
                "token_type": "Bearer",
            }

    monkeypatch.setattr("apps.api.services.sso.oidc_service.requests.get", fake_get)
    monkeypatch.setattr("apps.api.services.sso.oidc_service.OAuth2Session", FakeSession)


class TestOidcAccountTakeoverChain:
    """regression: idp-config-cross-tenant -- OIDC angle (CONFIRMED pre-fix).

    Pre-fix chain: tenant-A admin PUTs tenant B's OIDC config (issuer/client
    -> attacker-controlled IdP), then ``/oidc/callback?idp_id=<B's config>``
    validates the attacker's ID token against the attacker's JWKS and JIT
    returns tenant B's existing admin user -> Elder JWTs for that user.
    """

    @staticmethod
    def _idps(world: World) -> tuple[FakeIdp, FakeIdp]:
        real_b = FakeIdp(
            issuer="https://idp-b.example.test",
            client_id="client-b",
            client_secret=SECRET_B,
            kid="b-key",
            codes={"b-code": world.victim_email},
        )
        attacker = FakeIdp(
            issuer="https://attacker.example.test",
            client_id="attacker-client",
            client_secret="attacker-secret",
            kid="attacker-key",
            codes={"evil-code": world.victim_email},
        )
        return real_b, attacker

    @staticmethod
    async def _callback(async_client, idp_id: int, code: str):
        return await async_client.get(
            "/api/v1/sso/oidc/callback",
            query_string={
                "code": code,
                "idp_id": idp_id,
                "redirect_uri": "https://elder.example.test/cb",
            },
        )

    @pytest.mark.asyncio
    async def test_positive_control_real_idp_login_works(
        self, async_client, world, monkeypatch
    ):
        """Harness sanity: B's genuine IdP logs B's admin in (so the negative
        tests below fail for the right reason, not a broken fake)."""
        real_b, attacker = self._idps(world)
        _install_fake_idps(monkeypatch, [real_b, attacker])
        resp = await self._callback(async_client, world.b_oidc, "b-code")
        assert resp.status_code == 200, (await resp.get_data()).decode()
        user = (await resp.get_json())["user"]
        assert user["tenant_id"] == world.tenant_b
        assert user["tenant_role"] == "admin"

    @pytest.mark.asyncio
    async def test_attacker_idp_cannot_be_planted_into_other_tenants_config(
        self, app, async_client, world, monkeypatch
    ):
        real_b, attacker = self._idps(world)
        _install_fake_idps(monkeypatch, [real_b, attacker])

        # Step 1: tenant-A admin tries to repoint B's OIDC config at their IdP.
        put = await async_client.put(
            f"/api/v1/sso/idp/{world.b_oidc}",
            json={
                "oidc_issuer_url": attacker.issuer,
                "oidc_client_id": attacker.client_id,
                "oidc_client_secret": attacker.client_secret,
            },
            headers=world.auth("a_admin"),
        )
        assert put.status_code == 404, (await put.get_data()).decode()

        # Step 2: log in as B's admin using the attacker IdP's code.
        resp = await self._callback(async_client, world.b_oidc, "evil-code")
        assert resp.status_code == 400, (await resp.get_data()).decode()
        body = await resp.get_json()
        assert "access_token" not in body and "user" not in body

        # B's config is untouched and B's genuine IdP still works.
        row = await _row(app, world.b_oidc)
        assert row["oidc_issuer_url"] == real_b.issuer
        assert row["oidc_client_id"] == "client-b"
        assert row["oidc_client_secret"] == SECRET_B
        ok = await self._callback(async_client, world.b_oidc, "b-code")
        assert ok.status_code == 200

    @pytest.mark.asyncio
    async def test_global_oidc_config_cannot_be_hijacked_by_tenant_admin(
        self, app, async_client, world
    ):
        put = await async_client.put(
            f"/api/v1/sso/idp/{world.g_oidc}",
            json={"oidc_issuer_url": "https://attacker.example.test"},
            headers=world.auth("a_admin"),
        )
        assert put.status_code == 404
        assert (await _row(app, world.g_oidc))[
            "oidc_issuer_url"
        ] == "https://idp-g.example.test"
