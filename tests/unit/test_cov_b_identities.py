"""Unit tests for the identities and identity-groups API.

Exercises the real Quart app and test database with real JWTs for a
superuser (passes permission checks) and an observer (does not).
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from werkzeug.security import generate_password_hash

BASE = "/api/v1/identities"


def _tenant(db) -> int:
    """Insert a throwaway tenant."""
    tid = db.tenants.insert(
        name="cov-b", slug=f"covb-{uuid4().hex[:10]}", is_active=True
    )
    db.commit()
    return tid


def _ident(
    db, tid: int, role: str = "observer", superuser: bool = False, **extra
) -> int:
    """Insert an active local identity."""
    name = f"covb-{uuid4().hex[:8]}"
    iid = db.identities.insert(
        username=name,
        email=f"{name}@example.test",
        identity_type="human",
        auth_provider="local",
        password_hash=generate_password_hash(uuid4().hex),
        is_active=True,
        is_superuser=superuser,
        mfa_enabled=False,
        must_change_password=False,
        portal_role=role,
        tenant_id=tid,
        **extra,
    )
    db.commit()
    return iid


async def _h(app, iid: int) -> dict[str, str]:
    """Bearer headers for ``iid``."""
    from apps.api.auth.jwt_handler import generate_token

    async with app.app_context():
        tok = generate_token(app.db.identities[iid], "access")
    return {"Authorization": f"Bearer {tok}"}


@pytest.fixture
def world(app):
    """Seed a tenant, a superuser and an observer."""
    db = app.db
    tid = _tenant(db)
    return {
        "tid": tid,
        "root": _ident(db, tid, "admin", superuser=True),
        "viewer": _ident(db, tid, "observer"),
    }


def test_admin_kind_and_row_mapping(app):
    """Admin-kind resolution and row->DTO mapping behave as documented."""
    from datetime import UTC, datetime

    from apps.api.api.v1.identities import _identity_admin_kind, _identity_row_to_dto

    assert _identity_admin_kind(True, "observer") == "global_admin"
    assert _identity_admin_kind(False, "admin") == "tenant_admin"
    assert _identity_admin_kind(False, "observer") is None
    assert _identity_admin_kind(False, None) is None

    class Row:
        """Row double exposing as_dict()."""

        def as_dict(self):
            """Return a minimal identity dict."""
            now = datetime.now(UTC)
            return {
                "id": 1,
                "username": "u",
                "full_name": "U",
                "auth_provider": "local",
                "created_at": now,
                "updated_at": now,
            }

    dto = _identity_row_to_dto(Row())
    assert dto.provider == "local" and dto.display_name == "U" and dto.is_active


@pytest.mark.asyncio
async def test_identity_permissions(app, async_client, world):
    """Anonymous callers get 401 and observers 403 on management routes."""
    assert (await async_client.get(BASE)).status_code == 401
    h = await _h(app, world["viewer"])
    assert (
        await async_client.get(f"{BASE}/{world['viewer']}", headers=h)
    ).status_code == 403
    r = await async_client.post(
        BASE,
        json={"username": "x", "identity_type": "human", "auth_provider": "saml"},
        headers=h,
    )
    assert r.status_code == 403
    assert (
        await async_client.delete(f"{BASE}/{world['viewer']}", headers=h)
    ).status_code == 403


@pytest.mark.asyncio
async def test_list_identities_filters(app, async_client, world):
    """The list endpoint supports search, type, active and provider filters."""
    h = await _h(app, world["root"])
    me = app.db.identities[world["root"]]
    r = await async_client.get(BASE, headers=h)
    assert r.status_code == 200
    body = await r.get_json()
    assert body["total"] >= 1 and body["pages"] >= 1

    r = await async_client.get(f"{BASE}?search={me.username}&per_page=5", headers=h)
    items = (await r.get_json())["items"]
    assert any(i["username"] == me.username for i in items)
    r = await async_client.get(f"{BASE}?name={me.username}", headers=h)
    assert (await r.get_json())["total"] >= 1
    r = await async_client.get(
        f"{BASE}?identity_type=human&is_active=true&auth_provider=local"
        "&auth_provider_id=nothing-matches",
        headers=h,
    )
    assert (await r.get_json())["total"] == 0
    r = await async_client.get(f"{BASE}?is_active=false&identity_type=bot", headers=h)
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_create_get_update_delete_identity(app, async_client, world):
    """Identity lifecycle: create, duplicate, read, update, delete rules."""
    h = await _h(app, world["root"])
    uname = f"new-{uuid4().hex[:8]}"
    payload = {
        "username": uname,
        "identity_type": "human",
        "auth_provider": "local",
        "password": "Sup3r-Secret-Value",
        "email": f"{uname}@example.test",
        "tenant_id": world["tid"],
    }
    r = await async_client.post(BASE, json=payload, headers=h)
    assert r.status_code == 201, await r.get_data()
    created = await r.get_json()
    iid = created["id"]
    assert created["tenant_id"] == world["tid"]

    r = await async_client.post(BASE, json=payload, headers=h)
    assert r.status_code == 400

    no_tenant = {**payload, "username": f"n2-{uuid4().hex[:8]}", "email": None}
    no_tenant.pop("tenant_id")
    r = await async_client.post(BASE, json=no_tenant, headers=h)
    assert r.status_code == 201
    assert (await r.get_json())["tenant_id"] == world["tid"]

    r = await async_client.post(
        BASE,
        json={"username": "bad", "identity_type": "human", "auth_provider": "local"},
        headers=h,
    )
    assert r.status_code == 400

    assert (await async_client.get(f"{BASE}/{iid}", headers=h)).status_code == 200
    assert (await async_client.get(f"{BASE}/99999999", headers=h)).status_code == 404

    r = await async_client.patch(
        f"{BASE}/{iid}",
        json={
            "email": f"upd-{uuid4().hex[:6]}@example.test",
            "full_name": "Upd Name",
            "password": "An0ther-Secret-Value",
            "is_active": False,
            "mfa_enabled": False,
        },
        headers=h,
    )
    assert r.status_code == 200
    assert (await r.get_json())["is_active"] is False
    r = await async_client.put(f"{BASE}/{iid}", json={}, headers=h)
    assert r.status_code == 200
    r = await async_client.patch(f"{BASE}/{iid}", json={"password": ""}, headers=h)
    assert r.status_code == 200
    r = await async_client.patch(
        f"{BASE}/{iid}", json={"portal_role": "editor"}, headers=h
    )
    assert r.status_code in (200, 400, 422)
    r = await async_client.patch(f"{BASE}/99999999", json={"full_name": "x"}, headers=h)
    assert r.status_code == 404

    r = await async_client.delete(f"{BASE}/{world['root']}", headers=h)
    assert r.status_code == 400
    assert (await async_client.delete(f"{BASE}/99999999", headers=h)).status_code == 404
    assert (await async_client.delete(f"{BASE}/{iid}", headers=h)).status_code == 204
    assert (await async_client.get(f"{BASE}/{iid}", headers=h)).status_code == 404


@pytest.mark.asyncio
async def test_identity_tenant_isolation(app, async_client, world):
    """Identities of another tenant are invisible and untouchable."""
    foreign = _ident(app.db, _tenant(app.db))
    h = await _h(app, world["root"])
    assert (await async_client.get(f"{BASE}/{foreign}", headers=h)).status_code == 404
    r = await async_client.patch(
        f"{BASE}/{foreign}", json={"full_name": "x"}, headers=h
    )
    assert r.status_code == 404
    assert (
        await async_client.delete(f"{BASE}/{foreign}", headers=h)
    ).status_code == 404


@pytest.mark.xfail(
    strict=True,
    reason="bug: create_group omits NOT NULL review_enabled/review_auto_apply columns",
)
@pytest.mark.asyncio
async def test_create_group(app, async_client, world):
    """Creating a group should succeed (currently a NOT NULL violation, 500)."""
    h = await _h(app, world["root"])
    r = await async_client.post(
        BASE + "/groups", json={"name": f"grp-{uuid4().hex[:8]}"}, headers=h
    )
    assert r.status_code == 201


@pytest.mark.asyncio
async def test_group_lifecycle_and_membership(app, async_client, world):
    """Groups can be created, listed, updated, joined, left and deleted."""
    h = await _h(app, world["root"])
    name = f"grp-{uuid4().hex[:8]}"
    now = datetime.now(UTC)
    gid = app.db.identity_groups.insert(
        name=name,
        is_active=True,
        review_enabled=False,
        review_auto_apply=False,
        created_at=now,
        updated_at=now,
    )
    app.db.commit()
    r = await async_client.post(BASE + "/groups", json={"name": name}, headers=h)
    assert r.status_code == 400

    r = await async_client.get(BASE + "/groups?per_page=5", headers=h)
    assert r.status_code == 200 and (await r.get_json())["total"] >= 1
    assert (
        await async_client.get(f"{BASE}/groups/{gid}", headers=h)
    ).status_code == 200
    assert (
        await async_client.get(f"{BASE}/groups/99999999", headers=h)
    ).status_code == 404

    r = await async_client.patch(
        f"{BASE}/groups/{gid}",
        json={
            "name": name + "-b",
            "description": "d",
            "ldap_dn": "cn=x",
            "saml_group": "g",
            "is_active": True,
        },
        headers=h,
    )
    assert r.status_code == 200
    r = await async_client.patch(
        f"{BASE}/groups/99999999", json={"name": "z"}, headers=h
    )
    assert r.status_code == 404

    r = await async_client.post(f"{BASE}/groups/{gid}/members/99999999", headers=h)
    assert r.status_code == 404
    r = await async_client.post(
        f"{BASE}/groups/99999999/members/{world['viewer']}", headers=h
    )
    assert r.status_code == 404
    foreign = _ident(app.db, _tenant(app.db))
    r = await async_client.post(f"{BASE}/groups/{gid}/members/{foreign}", headers=h)
    assert r.status_code == 404

    assert (
        await async_client.delete(f"{BASE}/groups/{gid}", headers=h)
    ).status_code == 204
    assert (
        await async_client.delete(f"{BASE}/groups/{gid}", headers=h)
    ).status_code == 404


@pytest.mark.xfail(
    strict=True,
    reason="bug: membership routes use db.identity_group_members, table is identity_group_memberships",
)
@pytest.mark.asyncio
async def test_group_membership_roundtrip(app, async_client, world):
    """Adding then removing a member should work (currently a 500)."""
    h = await _h(app, world["root"])
    now = datetime.now(UTC)
    gid = app.db.identity_groups.insert(
        name=f"grp-{uuid4().hex[:8]}",
        is_active=True,
        review_enabled=False,
        review_auto_apply=False,
        created_at=now,
        updated_at=now,
    )
    app.db.commit()
    member = f"{BASE}/groups/{gid}/members/{world['viewer']}"
    assert (await async_client.post(member, headers=h)).status_code == 200
    assert (await async_client.post(member, headers=h)).status_code == 400
    assert (await async_client.delete(member, headers=h)).status_code == 204
    assert (await async_client.delete(member, headers=h)).status_code == 404
