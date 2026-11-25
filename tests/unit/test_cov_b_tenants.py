"""Unit tests for the tenant management API (portal-token authenticated).

Real Quart app and test database; portal-user tokens are signed with the app
key so the role and tenant claims drive the permission branches for real.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import jwt
import pytest

BASE = "/api/v1/tenants"
ENT = "unit.penguincloud.io"


def _tenant(db, **extra) -> int:
    """Insert a tenant row and return its id."""
    now = datetime.now(UTC)
    tid = db.tenants.insert(
        name=f"T {uuid4().hex[:6]}",
        slug=f"covb-{uuid4().hex[:10]}",
        is_active=True,
        created_at=now,
        updated_at=now,
        **extra,
    )
    db.commit()
    return tid


def _portal_user(db, tid: int, **extra) -> int:
    """Insert a portal user in tenant ``tid``."""
    now = datetime.now(UTC)
    uid = db.portal_users.insert(
        email=f"{uuid4().hex[:8]}@example.test",
        email_verified=True,
        is_active=True,
        tenant_id=tid,
        created_at=now,
        updated_at=now,
        **extra,
    )
    db.commit()
    return uid


def _hdr(
    app, sub=1, tenant_id=1, global_role=None, tenant_role="reader", scope="", host=None
):
    """Build headers with a signed portal-user token."""
    now = datetime.now(UTC)
    claims = {
        "sub": str(sub),
        "email": "p@example.test",
        "tenant_id": tenant_id,
        "tenant_role": tenant_role,
        "global_role": global_role,
        "roles": ["observer"],
        "scope": scope,
        "type": "portal_user",
        "principal_kind": "portal_user",
        "iat": now,
        "exp": now + timedelta(minutes=10),
    }
    secret = app.config["JWT_SECRET_KEY"] or app.config["SECRET_KEY"]
    tok = jwt.encode(claims, secret, algorithm=app.config["JWT_ALGORITHM"])
    h = {"Authorization": f"Bearer {tok}"}
    if host:
        h["Host"] = host
    return h


@pytest.fixture
def world(app):
    """Seed a tenant with one portal user."""
    tid = _tenant(app.db)
    return {"tid": tid, "uid": _portal_user(app.db, tid, full_name="P")}


@pytest.mark.asyncio
async def test_requires_token(async_client):
    """Anonymous calls are rejected."""
    assert (await async_client.get(BASE)).status_code == 401


@pytest.mark.asyncio
async def test_list_tenants(app, async_client, world):
    """Only global admin/support can list; filters apply."""
    assert (await async_client.get(BASE, headers=_hdr(app))).status_code == 403
    h = _hdr(app, global_role="support")
    r = await async_client.get(BASE, headers=h)
    assert r.status_code == 200 and len(await r.get_json()) >= 1
    r = await async_client.get(
        f"{BASE}?is_active=false&subscription_tier=community", headers=h
    )
    assert r.status_code == 200
    r = await async_client.get(f"{BASE}?is_active=true", headers=h)
    assert any(t["id"] == world["tid"] for t in await r.get_json())


@pytest.mark.asyncio
async def test_get_tenant(app, async_client, world):
    """Tenant details enforce admin scope and report usage."""
    tid = world["tid"]
    app.db(app.db.tenants.id == tid).update(license_key="k" * 30)
    app.db.commit()
    r = await async_client.get(f"{BASE}/{tid}", headers=_hdr(app, tenant_id=tid))
    assert r.status_code == 403
    r = await async_client.get(
        f"{BASE}/{tid}", headers=_hdr(app, tenant_id=tid + 1, tenant_role="admin")
    )
    assert r.status_code == 403
    r = await async_client.get(
        f"{BASE}/{tid}", headers=_hdr(app, tenant_id=tid, tenant_role="admin")
    )
    body = await r.get_json()
    assert r.status_code == 200 and body["license_key"].endswith("...")
    assert body["usage"]["portal_users"] == 1
    r = await async_client.get(
        f"{BASE}/99999999", headers=_hdr(app, global_role="admin")
    )
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_create_tenant(app, async_client):
    """Creation needs global admin, valid body and respects licence limits."""
    reader = _hdr(app, host=ENT)
    assert (await async_client.post(BASE, json={}, headers=reader)).status_code == 403
    h = _hdr(app, global_role="admin", host=ENT)
    r = await async_client.post(BASE, json={"name": ""}, headers=h)
    assert r.status_code == 400
    assert (await r.get_json())["error"] == "Validation failed"
    slug = f"covb-{uuid4().hex[:10]}"
    r = await async_client.post(
        BASE, json={"name": "New T", "slug": slug, "data_retention_days": 30}, headers=h
    )
    assert r.status_code == 201, await r.get_data()
    assert (await r.get_json())["village_id"]
    r = await async_client.post(BASE, json={"name": "Dup", "slug": slug}, headers=h)
    assert r.status_code == 400

    free = _hdr(app, global_role="admin")
    r = await async_client.post(
        BASE, json={"name": "X", "slug": f"covb-{uuid4().hex[:10]}"}, headers=free
    )
    assert r.status_code in (201, 402, 403, 409)


@pytest.mark.asyncio
async def test_update_tenant(app, async_client, world):
    """Tenant admins edit a subset of fields; global admins edit all."""
    tid = world["tid"]
    other = _hdr(app, tenant_id=tid + 1, tenant_role="admin")
    r = await async_client.put(f"{BASE}/{tid}", json={"name": "x"}, headers=other)
    assert r.status_code == 403
    tadmin = _hdr(app, tenant_id=tid, tenant_role="admin")
    r = await async_client.put(
        f"{BASE}/{tid}",
        json={"name": "Renamed", "slug": "ignored-for-tenant-admin"},
        headers=tadmin,
    )
    assert r.status_code == 200
    row = app.db(app.db.tenants.id == tid).select().first()
    assert row.name == "Renamed" and row.slug != "ignored-for-tenant-admin"

    root = _hdr(app, global_role="admin")
    new_slug = f"covb-{uuid4().hex[:10]}"
    r = await async_client.put(
        f"{BASE}/{tid}",
        json={"slug": new_slug, "data_retention_days": 12, "is_active": True},
        headers=root,
    )
    assert r.status_code == 200
    assert app.db(app.db.tenants.id == tid).select().first().slug == new_slug
    r = await async_client.put(f"{BASE}/{tid}", json={}, headers=root)
    assert r.status_code == 200
    r = await async_client.put(
        f"{BASE}/{tid}", json={"data_retention_days": 0}, headers=root
    )
    assert r.status_code == 400
    r = await async_client.put(f"{BASE}/99999999", json={"name": "x"}, headers=root)
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_delete_tenant(app, async_client, world):
    """Deleting deactivates tenants but never the system tenant."""
    tid = world["tid"]
    assert (
        await async_client.delete(f"{BASE}/{tid}", headers=_hdr(app))
    ).status_code == 403
    root = _hdr(app, global_role="admin")
    assert (await async_client.delete(f"{BASE}/1", headers=root)).status_code == 400
    assert (
        await async_client.delete(f"{BASE}/99999999", headers=root)
    ).status_code == 404
    assert (await async_client.delete(f"{BASE}/{tid}", headers=root)).status_code == 200
    assert app.db(app.db.tenants.id == tid).select().first().is_active is False


@pytest.mark.asyncio
async def test_tenant_users(app, async_client, world):
    """Portal user listing, update and soft delete honour roles and tenancy."""
    tid, uid = world["tid"], world["uid"]
    assert (
        await async_client.get(f"{BASE}/{tid}/users", headers=_hdr(app))
    ).status_code == 403
    tadmin = _hdr(app, tenant_id=tid, tenant_role="admin")
    r = await async_client.get(f"{BASE}/{tid}/users", headers=tadmin)
    users = await r.get_json()
    assert r.status_code == 200 and users[0]["id"] == uid
    assert users[0]["mfa_enabled"] is False

    reader = _hdr(app, tenant_id=tid)
    r = await async_client.put(
        f"{BASE}/{tid}/users/{uid}", json={"full_name": "x"}, headers=reader
    )
    assert r.status_code == 403
    r = await async_client.put(
        f"{BASE}/{tid}/users/99999999", json={"full_name": "x"}, headers=tadmin
    )
    assert r.status_code == 404
    r = await async_client.put(f"{BASE}/{tid}/users/{uid}", json={}, headers=tadmin)
    assert r.status_code == 400
    r = await async_client.put(
        f"{BASE}/{tid}/users/{uid}",
        json={"full_name": "Renamed", "global_role": "admin", "bogus": 1},
        headers=tadmin,
    )
    assert r.status_code == 200
    row = app.db(app.db.portal_users.id == uid).select().first()
    assert row.full_name == "Renamed" and row.global_role != "admin"
    root = _hdr(app, global_role="admin")
    r = await async_client.put(
        f"{BASE}/{tid}/users/{uid}", json={"global_role": "support"}, headers=root
    )
    assert r.status_code == 200
    r = await async_client.put(
        f"{BASE}/{tid}/users/{uid}", json={"bogus": 1}, headers=root
    )
    assert r.status_code == 200

    assert (
        await async_client.delete(f"{BASE}/{tid}/users/{uid}", headers=reader)
    ).status_code == 403
    assert (
        await async_client.delete(f"{BASE}/{tid}/users/99999999", headers=tadmin)
    ).status_code == 404
    assert (
        await async_client.delete(f"{BASE}/{tid}/users/{uid}", headers=tadmin)
    ).status_code == 200
    assert app.db(app.db.portal_users.id == uid).select().first().is_active is False


@pytest.mark.asyncio
async def test_tenant_stats(app, async_client, world):
    """Stats are admin-only and 404 for unknown tenants."""
    tid = world["tid"]
    assert (
        await async_client.get(f"{BASE}/{tid}/stats", headers=_hdr(app))
    ).status_code == 403
    r = await async_client.get(
        f"{BASE}/{tid}/stats", headers=_hdr(app, tenant_id=tid, tenant_role="admin")
    )
    body = await r.get_json()
    assert r.status_code == 200 and body["portal_users"]["total"] == 1
    r = await async_client.get(
        f"{BASE}/99999999/stats", headers=_hdr(app, global_role="support")
    )
    assert r.status_code == 404


def test_mfa_policy_admin_helper():
    """MFA admin detection honours roles and scopes."""
    from apps.api.api.v1.tenants import _is_mfa_policy_admin as f

    assert f({"global_role": "super_admin"})
    assert f({"tenant_role": "admin"})
    assert f({"scope": "x settings:write"})
    assert f({"scope": "*:admin"})
    assert not f({"scope": "a:read", "tenant_role": "reader"})
    assert not f({})


@pytest.mark.asyncio
async def test_mfa_policy_get_and_update(app, async_client, world):
    """MFA policy read/update covers admin, tier, validation and defaults."""
    tid, uid = world["tid"], world["uid"]
    h = _hdr(app, sub=uid, tenant_id=tid, tenant_role="admin")
    r = await async_client.get(f"{BASE}/current/mfa-policy", headers=h)
    body = await r.get_json()
    assert r.status_code == 200 and body["can_manage"] is False
    ent = _hdr(app, sub=uid, tenant_id=tid, tenant_role="admin", host=ENT)
    r = await async_client.get(f"{BASE}/current/mfa-policy", headers=ent)
    assert (await r.get_json())["can_manage"] is True
    r = await async_client.get(
        f"{BASE}/current/mfa-policy", headers=_hdr(app, sub=uid, tenant_id=99999999)
    )
    assert r.status_code == 404

    reader = _hdr(app, sub=uid, tenant_id=tid, host=ENT)
    assert (
        await async_client.put(
            f"{BASE}/current/mfa-policy", json={"enforced": True}, headers=reader
        )
    ).status_code == 403
    r = await async_client.put(
        f"{BASE}/current/mfa-policy", json={"enforced": True}, headers=h
    )
    assert (
        r.status_code == 403 and (await r.get_json())["required_tier"] == "enterprise"
    )

    r = await async_client.put(
        f"{BASE}/current/mfa-policy", data="notjson", headers=ent
    )
    assert r.status_code == 400
    r = await async_client.put(
        f"{BASE}/current/mfa-policy", json={"enforced": "maybe"}, headers=ent
    )
    assert r.status_code == 400
    r = await async_client.put(
        f"{BASE}/current/mfa-policy",
        json={"enforced": True, "grace_period_days": 3},
        headers=ent,
    )
    first = await r.get_json()
    assert r.status_code == 200 and first["grace_period_days"] == 3
    r = await async_client.put(
        f"{BASE}/current/mfa-policy", json={"enforced": True}, headers=ent
    )
    again = await r.get_json()
    assert (
        again["enforced_at"] == first["enforced_at"] and again["grace_period_days"] == 3
    )
    r = await async_client.put(
        f"{BASE}/current/mfa-policy", json={"enforced": False}, headers=ent
    )
    assert (await r.get_json())["enforced_at"] is None

    app.db(app.db.tenants.id == tid).update(settings={"mfa_grace_period_days": "oops"})
    app.db.commit()
    r = await async_client.put(
        f"{BASE}/current/mfa-policy", json={"enforced": False}, headers=ent
    )
    assert (await r.get_json())["grace_period_days"] == 7

    r = await async_client.put(
        f"{BASE}/current/mfa-policy",
        json={"enforced": True},
        headers=_hdr(app, sub=uid, tenant_id=99999999, tenant_role="admin", host=ENT),
    )
    assert r.status_code == 404
