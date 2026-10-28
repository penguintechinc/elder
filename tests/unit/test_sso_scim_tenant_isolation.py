"""SCIM tenant-isolation regression tests (real Postgres, real routes).

regression: scim-cross-tenant -- ``regenerate_scim_token`` and ``get_scim_config``
in ``apps/api/api/v1/sso.py`` checked the caller's *role* but never that the
``<tenant_id>`` in the path was the caller's own tenant. A tenant-A admin could
rotate tenant B's SCIM bearer token (getting B's new secret, killing B's IdP
sync) and then use it to list B's users and deactivate B's admin. The SCIM
resource routes were also 500-ing on every create/update because the sync
``scim_auth_required`` decorator wrapped ``async def`` views.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock

import pytest
import pytest_asyncio
from quart import current_app
from sso_testkit import World, build_world, mint_token, teardown_world

NO_SCIM_BODY = {"error": "SCIM not configured"}


@pytest.fixture(autouse=True)
def _patch_login_required(mocker):
    """``POST /scim/config`` also carries ``@login_required``; stub identity auth."""
    mocker.patch(
        "apps.api.auth.decorators.get_current_user",
        return_value=MagicMock(id=1, is_superuser=False),
    )


@pytest_asyncio.fixture
async def world(app):
    """Two tenants (each with a SCIM config + users) and a set of portal callers."""
    async with app.app_context():
        w = build_world(
            app,
            issuer_a="https://idp-a.example.test",
            issuer_b="https://idp-b.example.test",
            issuer_g="https://idp-g.example.test",
        )
    w.tokens["sys_admin"] = _mint_sys_admin(app, w)
    yield w
    async with app.app_context():
        teardown_world(w)


def _mint_sys_admin(app, w: World) -> str:
    """A plain tenant admin of a tenant that has *no* SCIM config."""
    return mint_token(app, user_id=108, tenant_id=w.tenant_sys, tenant_role="admin")


def scim_auth(token: str) -> dict[str, str]:
    """SCIM bearer header."""
    return {"Authorization": f"Bearer {token}"}


async def _scim_row(app, tenant_id: int) -> Any:
    """The tenant's SCIM config row as a plain dict."""
    async with app.app_context():
        db = current_app.db
        row = db(db.scim_configurations.tenant_id == tenant_id).select().first()
        return row.as_dict() if row else None


async def _user_row(app, user_id: int) -> Any:
    """A portal user row as a plain dict."""
    async with app.app_context():
        db = current_app.db
        row = db(db.portal_users.id == user_id).select().first()
        return row.as_dict() if row else None


def _scim_user(email: str, **extra: Any) -> dict[str, Any]:
    """A minimal SCIM User resource."""
    return {
        "schemas": ["urn:ietf:params:scim:schemas:core:2.0:User"],
        "userName": email,
        "emails": [{"value": email, "primary": True}],
        **extra,
    }


class TestScimConfigRoutesTenantBinding:
    """GET/POST /scim/config* -- regression: scim-cross-tenant."""

    @pytest.mark.asyncio
    async def test_tenant_admin_cannot_regenerate_other_tenants_token(
        self, app, async_client, world
    ):
        before = await _scim_row(app, world.tenant_b)
        resp = await async_client.post(
            f"/api/v1/sso/scim/config/{world.tenant_b}/regenerate-token",
            headers=world.auth("a_admin"),
        )
        assert resp.status_code == 404, (await resp.get_data()).decode()
        raw = (await resp.get_data()).decode()
        assert world.scim_token_b not in raw
        assert "bearer_token" not in raw
        # B's secret is untouched and still authenticates B's IdP.
        assert (await _scim_row(app, world.tenant_b))["bearer_token"] == before[
            "bearer_token"
        ]
        listing = await async_client.get(
            f"/api/v1/sso/scim/{world.tenant_b}/Users",
            headers=scim_auth(world.scim_token_b),
        )
        assert listing.status_code == 200

    @pytest.mark.asyncio
    async def test_tenant_admin_cannot_read_other_tenants_config(
        self, async_client, world
    ):
        resp = await async_client.get(
            f"/api/v1/sso/scim/config/{world.tenant_b}",
            headers=world.auth("a_admin"),
        )
        assert resp.status_code == 404
        assert await resp.get_json() == NO_SCIM_BODY

    @pytest.mark.asyncio
    async def test_cross_tenant_404_body_equals_missing_config_body(
        self, async_client, world
    ):
        """No existence oracle: 'other tenant' and 'no SCIM here' look identical."""
        missing_get = await async_client.get(
            f"/api/v1/sso/scim/config/{world.tenant_sys}",
            headers=world.auth("sys_admin"),
        )
        cross_get = await async_client.get(
            f"/api/v1/sso/scim/config/{world.tenant_b}",
            headers=world.auth("a_admin"),
        )
        assert missing_get.status_code == cross_get.status_code == 404
        assert (await missing_get.get_data()) == (await cross_get.get_data())

        missing_regen = await async_client.post(
            f"/api/v1/sso/scim/config/{world.tenant_sys}/regenerate-token",
            headers=world.auth("sys_admin"),
        )
        cross_regen = await async_client.post(
            f"/api/v1/sso/scim/config/{world.tenant_b}/regenerate-token",
            headers=world.auth("a_admin"),
        )
        assert missing_regen.status_code == cross_regen.status_code == 404
        assert (await missing_regen.get_data()) == (await cross_regen.get_data())

    @pytest.mark.asyncio
    async def test_non_admin_cannot_read_or_regenerate_even_own_tenant(
        self, async_client, world
    ):
        got = await async_client.get(
            f"/api/v1/sso/scim/config/{world.tenant_a}",
            headers=world.auth("a_reader"),
        )
        regen = await async_client.post(
            f"/api/v1/sso/scim/config/{world.tenant_a}/regenerate-token",
            headers=world.auth("a_reader"),
        )
        assert got.status_code == 403
        assert regen.status_code == 403

    @pytest.mark.asyncio
    async def test_tenant_admin_reads_own_config_without_secret(
        self, async_client, world
    ):
        resp = await async_client.get(
            f"/api/v1/sso/scim/config/{world.tenant_a}",
            headers=world.auth("a_admin"),
        )
        assert resp.status_code == 200
        body = await resp.get_json()
        assert body["tenant_id"] == world.tenant_a
        assert "bearer_token" not in body
        assert world.scim_token_a not in json.dumps(body)

    @pytest.mark.asyncio
    async def test_tenant_admin_regenerates_own_token_once(
        self, app, async_client, world
    ):
        resp = await async_client.post(
            f"/api/v1/sso/scim/config/{world.tenant_a}/regenerate-token",
            headers=world.auth("a_admin"),
        )
        assert resp.status_code == 200
        new_token = (await resp.get_json())["bearer_token"]
        assert new_token and new_token != world.scim_token_a
        # New token works for A; the superseded one is dead.
        ok = await async_client.get(
            f"/api/v1/sso/scim/{world.tenant_a}/Users", headers=scim_auth(new_token)
        )
        old = await async_client.get(
            f"/api/v1/sso/scim/{world.tenant_a}/Users",
            headers=scim_auth(world.scim_token_a),
        )
        assert ok.status_code == 200
        assert old.status_code == 401
        # ...and the secret is never echoed by a later read.
        again = await async_client.get(
            f"/api/v1/sso/scim/config/{world.tenant_a}",
            headers=world.auth("a_admin"),
        )
        assert new_token not in (await again.get_data()).decode()

    @pytest.mark.asyncio
    async def test_global_admin_may_cross_tenants(self, async_client, world):
        got = await async_client.get(
            f"/api/v1/sso/scim/config/{world.tenant_b}",
            headers=world.auth("global_admin"),
        )
        regen = await async_client.post(
            f"/api/v1/sso/scim/config/{world.tenant_b}/regenerate-token",
            headers=world.auth("global_admin"),
        )
        assert got.status_code == 200
        assert regen.status_code == 200
        assert (await regen.get_json())["bearer_token"] != world.scim_token_b

    @pytest.mark.asyncio
    async def test_support_global_role_is_not_a_super_admin(self, async_client, world):
        resp = await async_client.get(
            f"/api/v1/sso/scim/config/{world.tenant_b}",
            headers=world.auth("support_tenant_admin"),
        )
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_admin_without_tenant_claim_fails_closed(self, async_client, world):
        resp = await async_client.get(
            f"/api/v1/sso/scim/config/{world.tenant_a}",
            headers=world.auth("no_tenant_admin"),
        )
        assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_unauthenticated_is_401(self, async_client, world):
        for method, path in (
            ("get", f"/api/v1/sso/scim/config/{world.tenant_a}"),
            ("post", f"/api/v1/sso/scim/config/{world.tenant_a}/regenerate-token"),
        ):
            resp = await getattr(async_client, method)(path)
            assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_tenant_admin_create_is_pinned_to_own_tenant(
        self, app, async_client, world
    ):
        """A body ``tenant_id`` of another tenant is ignored for non-global admins."""
        async with app.app_context():
            db = current_app.db
            before = db(db.scim_configurations.tenant_id == world.tenant_b).count()
        resp = await async_client.post(
            "/api/v1/sso/scim/config",
            json={"tenant_id": world.tenant_b},
            headers=world.auth("a_admin"),
        )
        assert resp.status_code == 201
        assert (await resp.get_json())["tenant_id"] == world.tenant_a
        async with app.app_context():
            db = current_app.db
            assert (
                db(db.scim_configurations.tenant_id == world.tenant_b).count() == before
            )

    @pytest.mark.asyncio
    async def test_global_admin_create_for_unknown_tenant_is_404(
        self, async_client, world
    ):
        resp = await async_client.post(
            "/api/v1/sso/scim/config",
            json={"tenant_id": 2_000_000_000},
            headers=world.auth("global_admin"),
        )
        assert resp.status_code == 404

    @pytest.mark.asyncio
    @pytest.mark.parametrize("bad", ["abc", True, -3, 0, 1.5])
    async def test_global_admin_create_rejects_non_positive_int_tenant(
        self, async_client, world, bad
    ):
        resp = await async_client.post(
            "/api/v1/sso/scim/config",
            json={"tenant_id": bad},
            headers=world.auth("global_admin"),
        )
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_global_admin_create_for_known_tenant(self, async_client, world):
        resp = await async_client.post(
            "/api/v1/sso/scim/config",
            json={"tenant_id": world.tenant_sys},
            headers=world.auth("global_admin"),
        )
        assert resp.status_code == 201
        assert (await resp.get_json())["bearer_token"].startswith("scim_")

    @pytest.mark.asyncio
    async def test_create_requires_admin(self, async_client, world):
        resp = await async_client.post(
            "/api/v1/sso/scim/config", json={}, headers=world.auth("a_reader")
        )
        assert resp.status_code == 403


class TestScimBearerTokenIsTenantBound:
    """SCIM resource routes -- a token for tenant X never reads/writes tenant Y."""

    @pytest.mark.asyncio
    async def test_token_for_one_tenant_is_rejected_on_another(
        self, async_client, world
    ):
        for path, token in (
            (f"/api/v1/sso/scim/{world.tenant_b}/Users", world.scim_token_a),
            (f"/api/v1/sso/scim/{world.tenant_a}/Users", world.scim_token_b),
        ):
            resp = await async_client.get(path, headers=scim_auth(token))
            assert resp.status_code == 401

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "header",
        [None, "", "Basic abc", "Bearer", "Bearer ", "Bearer not-a-token"],
    )
    async def test_missing_or_malformed_token_is_401(self, async_client, world, header):
        headers = {} if header is None else {"Authorization": header}
        resp = await async_client.get(
            f"/api/v1/sso/scim/{world.tenant_a}/Users", headers=headers
        )
        assert resp.status_code == 401
        body = await resp.get_json()
        assert body["schemas"] == ["urn:ietf:params:scim:api:messages:2.0:Error"]

    @pytest.mark.asyncio
    async def test_inactive_config_token_is_rejected(self, app, async_client, world):
        async with app.app_context():
            db = current_app.db
            db(db.scim_configurations.tenant_id == world.tenant_a).update(
                is_active=False
            )
            db.commit()
        resp = await async_client.get(
            f"/api/v1/sso/scim/{world.tenant_a}/Users",
            headers=scim_auth(world.scim_token_a),
        )
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_list_only_returns_own_tenant_users(self, async_client, world):
        resp = await async_client.get(
            f"/api/v1/sso/scim/{world.tenant_a}/Users",
            headers=scim_auth(world.scim_token_a),
        )
        assert resp.status_code == 200
        body = await resp.get_json()
        emails = {r["userName"] for r in body["Resources"]}
        assert world.a_user_email in emails
        assert world.b_admin_email not in emails
        assert world.b_member_email not in emails
        assert body["totalResults"] == len(emails) == 2

    @pytest.mark.asyncio
    async def test_cannot_read_other_tenants_user_by_id(self, async_client, world):
        resp = await async_client.get(
            f"/api/v1/sso/scim/{world.tenant_a}/Users/{world.b_member_id}",
            headers=scim_auth(world.scim_token_a),
        )
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_cannot_update_other_tenants_user(self, app, async_client, world):
        resp = await async_client.put(
            f"/api/v1/sso/scim/{world.tenant_a}/Users/{world.b_member_id}",
            json=_scim_user("hijacked@evil.test", active=False),
            headers=scim_auth(world.scim_token_a),
        )
        assert resp.status_code == 404
        row = await _user_row(app, world.b_member_id)
        assert row["email"] == world.b_member_email
        assert row["is_active"] is True

    @pytest.mark.asyncio
    async def test_cannot_delete_other_tenants_user(self, app, async_client, world):
        """regression: scim-cross-tenant -- the reproduced 'deactivate B's admin'."""
        resp = await async_client.delete(
            f"/api/v1/sso/scim/{world.tenant_a}/Users/{world.b_admin_id}",
            headers=scim_auth(world.scim_token_a),
        )
        assert resp.status_code == 404
        assert (await _user_row(app, world.b_admin_id))["is_active"] is True

    @pytest.mark.asyncio
    async def test_cannot_create_into_other_tenant(self, app, async_client, world):
        resp = await async_client.post(
            f"/api/v1/sso/scim/{world.tenant_b}/Users",
            json=_scim_user("planted@evil.test"),
            headers=scim_auth(world.scim_token_a),
        )
        assert resp.status_code == 401
        async with app.app_context():
            db = current_app.db
            assert db(db.portal_users.email == "planted@evil.test").count() == 0


class TestScimResourceRoutesWork:
    """regression: scim-async-decorator -- create/update used to 500 (coroutine)."""

    @pytest.mark.asyncio
    async def test_create_user_succeeds_for_the_right_tenant(
        self, app, async_client, world
    ):
        email = f"new-user@{world.prefix}.test"
        resp = await async_client.post(
            f"/api/v1/sso/scim/{world.tenant_a}/Users",
            json=_scim_user(email, displayName="New User"),
            headers=scim_auth(world.scim_token_a),
        )
        assert resp.status_code == 201, (await resp.get_data()).decode()
        body = await resp.get_json()
        assert body["userName"] == email
        async with app.app_context():
            db = current_app.db
            row = db(db.portal_users.email == email).select().first()
            assert row.tenant_id == world.tenant_a
            assert row.tenant_role == "reader"
            assert row.global_role is None

    @pytest.mark.asyncio
    async def test_update_user_succeeds_for_the_right_tenant(
        self, app, async_client, world
    ):
        resp = await async_client.put(
            f"/api/v1/sso/scim/{world.tenant_a}/Users/{world.a_user_id}",
            json=_scim_user(world.a_user_email, displayName="Renamed", active=False),
            headers=scim_auth(world.scim_token_a),
        )
        assert resp.status_code == 200, (await resp.get_data()).decode()
        row = await _user_row(app, world.a_user_id)
        assert row["full_name"] == "Renamed"
        assert row["is_active"] is False

    @pytest.mark.asyncio
    async def test_get_and_delete_user_for_the_right_tenant(
        self, app, async_client, world
    ):
        got = await async_client.get(
            f"/api/v1/sso/scim/{world.tenant_a}/Users/{world.a_user_id}",
            headers=scim_auth(world.scim_token_a),
        )
        assert got.status_code == 200
        assert (await got.get_json())["userName"] == world.a_user_email
        deleted = await async_client.delete(
            f"/api/v1/sso/scim/{world.tenant_a}/Users/{world.a_user_id}",
            headers=scim_auth(world.scim_token_a),
        )
        assert deleted.status_code == 204
        assert (await _user_row(app, world.a_user_id))["is_active"] is False

    @pytest.mark.asyncio
    async def test_create_conflicts_and_validation(self, async_client, world):
        dup = await async_client.post(
            f"/api/v1/sso/scim/{world.tenant_a}/Users",
            json=_scim_user(world.a_user_email),
            headers=scim_auth(world.scim_token_a),
        )
        assert dup.status_code == 409
        no_email = await async_client.post(
            f"/api/v1/sso/scim/{world.tenant_a}/Users",
            json={"schemas": [], "displayName": "x"},
            headers=scim_auth(world.scim_token_a),
        )
        assert no_email.status_code == 400
        empty = await async_client.post(
            f"/api/v1/sso/scim/{world.tenant_a}/Users",
            data="",
            headers={
                **scim_auth(world.scim_token_a),
                "Content-Type": "application/json",
            },
        )
        assert empty.status_code in (400, 415)

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "payload",
        [
            {"emails": "not-a-list"},
            {"emails": ["not-a-dict"]},
            {"emails": [{"value": 12345, "primary": True}]},
            {"active": "yes"},
            ["not", "an", "object"],
        ],
    )
    async def test_malformed_scim_bodies_are_400_never_500(
        self, async_client, world, payload
    ):
        create = await async_client.post(
            f"/api/v1/sso/scim/{world.tenant_a}/Users",
            json=payload,
            headers=scim_auth(world.scim_token_a),
        )
        update = await async_client.put(
            f"/api/v1/sso/scim/{world.tenant_a}/Users/{world.a_user_id}",
            json=payload,
            headers=scim_auth(world.scim_token_a),
        )
        assert create.status_code == 400
        assert update.status_code == 400

    @pytest.mark.asyncio
    async def test_update_to_an_existing_email_is_409(self, async_client, world):
        other = f"other@{world.prefix}.test"
        created = await async_client.post(
            f"/api/v1/sso/scim/{world.tenant_a}/Users",
            json=_scim_user(other),
            headers=scim_auth(world.scim_token_a),
        )
        assert created.status_code == 201
        resp = await async_client.put(
            f"/api/v1/sso/scim/{world.tenant_a}/Users/{world.a_user_id}",
            json=_scim_user(other),
            headers=scim_auth(world.scim_token_a),
        )
        assert resp.status_code == 409

    @pytest.mark.asyncio
    async def test_scim_cannot_modify_platform_privileged_accounts(
        self, app, async_client, world
    ):
        """A tenant's SCIM token must not deactivate/rewrite a global-admin row."""
        put = await async_client.put(
            f"/api/v1/sso/scim/{world.tenant_a}/Users/{world.a_global_admin_id}",
            json=_scim_user("rewritten@evil.test", active=False),
            headers=scim_auth(world.scim_token_a),
        )
        delete = await async_client.delete(
            f"/api/v1/sso/scim/{world.tenant_a}/Users/{world.a_global_admin_id}",
            headers=scim_auth(world.scim_token_a),
        )
        assert put.status_code == 403
        assert delete.status_code == 403
        row = await _user_row(app, world.a_global_admin_id)
        assert row["email"] == world.a_global_admin_email
        assert row["is_active"] is True
        assert row["global_role"] == "admin"

    @pytest.mark.asyncio
    async def test_list_paging_parameters_are_clamped(self, async_client, world):
        resp = await async_client.get(
            f"/api/v1/sso/scim/{world.tenant_a}/Users",
            query_string={"startIndex": -5, "count": 10_000_000},
            headers=scim_auth(world.scim_token_a),
        )
        assert resp.status_code == 200
        body = await resp.get_json()
        assert body["startIndex"] == 1
        assert body["itemsPerPage"] <= 200

    @pytest.mark.asyncio
    async def test_list_filter_by_username(self, async_client, world):
        resp = await async_client.get(
            f"/api/v1/sso/scim/{world.tenant_a}/Users",
            query_string={"filter": f'userName eq "{world.a_user_email}"'},
            headers=scim_auth(world.scim_token_a),
        )
        body = await resp.get_json()
        assert [r["userName"] for r in body["Resources"]] == [world.a_user_email]
        assert body["totalResults"] == 1
