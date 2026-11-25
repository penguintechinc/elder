"""Unit tests for the auth, users and entity-lookup API blueprints.

Runs against the real Quart app and test database; tokens are real JWTs for
freshly seeded identities.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from werkzeug.security import generate_password_hash

PW = "Cov-B-Pass-Word-1"


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
    """Insert an active local identity whose username is an email."""
    name = f"covb-{uuid4().hex[:8]}@example.test"
    fields = dict(
        username=name,
        email=name,
        identity_type="human",
        auth_provider="local",
        password_hash=generate_password_hash(PW),
        is_active=True,
        is_superuser=superuser,
        mfa_enabled=False,
        must_change_password=False,
        portal_role=role,
        tenant_id=tid,
    )
    fields.update(extra)
    iid = db.identities.insert(**fields)
    db.commit()
    return iid


async def _h(app, iid: int, kind: str = "access") -> dict[str, str]:
    """Bearer headers for ``iid``."""
    from apps.api.auth.jwt_handler import generate_token

    async with app.app_context():
        tok = generate_token(app.db.identities[iid], kind)
    return {"Authorization": f"Bearer {tok}"}


@pytest.fixture
def world(app):
    """Seed tenant + admin + observer."""
    tid = _tenant(app.db)
    return {
        "tid": tid,
        "admin": _ident(app.db, tid, "admin"),
        "viewer": _ident(app.db, tid, "observer"),
        "root": _ident(app.db, tid, "admin", superuser=True),
    }


# ---------------------------------------------------------------------- auth


@pytest.mark.asyncio
async def test_register(app, async_client, world):
    """Registration validates input and rejects duplicates."""
    A = "/api/v1/auth/register"
    assert (await async_client.post(A, json={})).status_code == 400
    r = await async_client.post(
        A, json={"username": "nope", "password": "x", "email": "y"}
    )
    assert r.status_code == 422
    assert (await r.get_json())["details"]
    name = f"reg-{uuid4().hex[:8]}@example.test"
    body = {"username": name, "email": name, "password": PW, "full_name": "R"}
    r = await async_client.post(A, json=body)
    assert r.status_code == 201, await r.get_data()
    assert (await r.get_json())["user"]["email"] == name
    r = await async_client.post(A, json=body)
    assert r.status_code == 400
    other = f"reg-{uuid4().hex[:8]}@example.test"
    row = app.db(app.db.identities.username == name).select().first()
    app.db(app.db.identities.id == row.id).update(username=other)
    app.db.commit()
    r = await async_client.post(A, json=body)
    assert r.status_code == 400
    assert "Email" in (await r.get_json())["error"]


@pytest.mark.asyncio
async def test_guest_enabled_and_captcha(async_client, monkeypatch):
    """Guest flag follows the environment; captcha challenge is signed."""
    monkeypatch.delenv("ENABLE_GUEST_LOGIN", raising=False)
    r = await async_client.get("/api/v1/auth/guest-enabled")
    assert (await r.get_json()) == {"enabled": False}
    monkeypatch.setenv("ENABLE_GUEST_LOGIN", "true")
    monkeypatch.setenv("GUEST_USERNAME", "visitor")
    r = await async_client.get("/api/v1/auth/guest-enabled")
    assert (await r.get_json()) == {"enabled": True, "username": "visitor"}
    r = await async_client.get("/api/v1/auth/captcha-challenge")
    body = await r.get_json()
    assert body["algorithm"] == "SHA-256" and len(body["signature"]) == 64


@pytest.mark.asyncio
async def test_login_paths(app, async_client, world):
    """Login handles bad bodies, credentials, inactive accounts and success."""
    A = "/api/v1/auth/login"
    assert (await async_client.post(A, json={})).status_code == 400
    assert (
        await async_client.post(A, json={"username": "bad", "password": ""})
    ).status_code == 422
    row = app.db.identities[world["viewer"]]
    r = await async_client.post(
        A, json={"username": "ghost@example.test", "password": PW}
    )
    assert r.status_code == 401
    r = await async_client.post(
        A, json={"username": row.username, "password": "wrong-password"}
    )
    assert r.status_code == 401
    inactive = _ident(app.db, world["tid"], is_active=False)
    r = await async_client.post(
        A, json={"username": app.db.identities[inactive].username, "password": PW}
    )
    assert r.status_code == 401
    assert "inactive" in (await r.get_json())["error"]
    r = await async_client.post(A, json={"username": row.username, "password": PW})
    body = await r.get_json()
    assert r.status_code == 200 and body["token_type"] == "Bearer"
    assert "elder_session" in r.headers.get("Set-Cookie", "")
    assert body["user"]["id"] == world["viewer"]


@pytest.mark.asyncio
async def test_logout_me_and_refresh(app, async_client, world):
    """Logout, /me and token refresh behave for valid and invalid tokens."""
    h = await _h(app, world["viewer"])
    r = await async_client.post("/api/v1/auth/logout", headers=h)
    assert r.status_code == 200
    r = await async_client.get("/api/v1/auth/me", headers=h)
    assert r.status_code == 200 and (await r.get_json())["id"] == world["viewer"]
    assert (await async_client.get("/api/v1/auth/me")).status_code == 401

    R = "/api/v1/auth/refresh"
    assert (await async_client.post(R, json={})).status_code == 400
    assert (
        await async_client.post(R, json={"refresh_token": "junk"})
    ).status_code == 401
    refresh = (await _h(app, world["viewer"], "refresh"))["Authorization"].split()[1]
    r = await async_client.post(R, json={"refresh_token": refresh})
    assert r.status_code == 200 and "access_token" in await r.get_json()
    access = h["Authorization"].split()[1]
    assert (
        await async_client.post(R, json={"refresh_token": access})
    ).status_code == 401

    gone = _ident(app.db, world["tid"], is_active=False)
    tok = (await _h(app, gone, "refresh"))["Authorization"].split()[1]
    r = await async_client.post(R, json={"refresh_token": tok})
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_change_password(app, async_client, world):
    """Password change validates input and the current password."""
    A = "/api/v1/auth/change-password"
    h = await _h(app, world["viewer"])
    assert (await async_client.post(A, json={}, headers=h)).status_code == 400
    for body in (
        {"new_password": "longenough1"},
        {"current_password": PW},
        {"current_password": PW, "new_password": "short"},
    ):
        assert (await async_client.post(A, json=body, headers=h)).status_code == 400
    r = await async_client.post(
        A,
        json={"current_password": "nope-nope", "new_password": "longenough1"},
        headers=h,
    )
    assert r.status_code == 401
    r = await async_client.post(
        A, json={"current_password": PW, "new_password": "brand-new-pw-9"}, headers=h
    )
    assert r.status_code == 200


# --------------------------------------------------------------------- users


@pytest.mark.asyncio
async def test_users_list(app, async_client, world):
    """Admin listing is tenant-scoped; bad pagination is rejected."""
    U = "/api/v1/users"
    viewer = await _h(app, world["viewer"])
    assert (await async_client.get(U, headers=viewer)).status_code == 403
    h = await _h(app, world["admin"])
    r = await async_client.get(f"{U}?per_page=100", headers=h)
    body = await r.get_json()
    assert r.status_code == 200 and {i["tenant_id"] for i in body["items"]} == {
        world["tid"]
    }
    for qs in ("page=0", "per_page=0", "per_page=1001"):
        assert (await async_client.get(f"{U}?{qs}", headers=h)).status_code == 400
    root = await _h(app, world["root"])
    r = await async_client.get(U, headers=root)
    assert r.status_code == 200 and (await r.get_json())["total"] >= 3
    r = await async_client.get(f"{U}?page=999", headers=h)
    assert (await r.get_json())["items"] == []


@pytest.mark.asyncio
async def test_users_create(app, async_client, world):
    """User creation validates the body and guards superuser/tenant escalation."""
    U = "/api/v1/users"
    h = await _h(app, world["admin"])
    root = await _h(app, world["root"])
    assert (await async_client.post(U, json={}, headers=h)).status_code == 400
    assert (
        await async_client.post(U, json={"username": " ", "password": "x"}, headers=h)
    ).status_code == 400
    assert (
        await async_client.post(U, json={"username": "a"}, headers=h)
    ).status_code == 400
    r = await async_client.post(
        U, json={"username": "a", "password": "p", "is_superuser": "yes"}, headers=h
    )
    assert r.status_code == 400
    r = await async_client.post(
        U, json={"username": "a", "password": "p", "is_superuser": True}, headers=h
    )
    assert r.status_code == 403
    r = await async_client.post(
        U,
        json={"username": "a", "password": "p", "tenant_id": world["tid"] + 1},
        headers=h,
    )
    assert r.status_code == 403

    name = f"mk-{uuid4().hex[:8]}"
    r = await async_client.post(
        U,
        json={
            "username": name,
            "password": PW,
            "email": f"{name}@example.test",
            "full_name": "M",
        },
        headers=h,
    )
    assert r.status_code == 201, await r.get_data()
    created = await r.get_json()
    assert created["username"] == name
    r = await async_client.post(U, json={"username": name, "password": PW}, headers=h)
    assert r.status_code == 400
    r = await async_client.post(
        U,
        json={"username": name + "x", "password": PW, "email": f"{name}@example.test"},
        headers=h,
    )
    assert r.status_code == 400
    assert "Email" in (await r.get_json())["error"]

    r = await async_client.post(
        U,
        json={
            "username": f"su-{uuid4().hex[:8]}",
            "password": PW,
            "is_superuser": True,
            "tenant_id": world["tid"],
        },
        headers=root,
    )
    assert r.status_code == 201
    r = await async_client.post(
        U, json={"username": f"nt-{uuid4().hex[:8]}", "password": PW}, headers=root
    )
    assert r.status_code == 201


@pytest.mark.asyncio
async def test_users_update_and_delete(app, async_client, world):
    """User updates are validated and tenant-scoped; deletes guard self/tenant."""
    U = "/api/v1/users"
    h = await _h(app, world["admin"])
    root = await _h(app, world["root"])
    target = _ident(app.db, world["tid"])
    foreign = _ident(app.db, _tenant(app.db))

    assert (
        await async_client.patch(f"{U}/{target}", json={}, headers=h)
    ).status_code == 400
    assert (
        await async_client.patch(f"{U}/{target}", json={"bogus": 1}, headers=h)
    ).status_code == 400
    r = await async_client.patch(
        f"{U}/{target}",
        json={
            "full_name": "Renamed",
            "password": "new-password-123",
            "is_superuser": True,
        },
        headers=h,
    )
    assert r.status_code == 200
    row = app.db.identities[target]
    assert row.full_name == "Renamed" and row.is_superuser is False
    assert (
        await async_client.patch(f"{U}/{foreign}", json={"full_name": "x"}, headers=h)
    ).status_code == 404
    assert (
        await async_client.patch(f"{U}/99999999", json={"full_name": "x"}, headers=root)
    ).status_code == 404
    r = await async_client.patch(
        f"{U}/{foreign}", json={"full_name": "x", "is_superuser": False}, headers=root
    )
    assert r.status_code == 200

    assert (
        await async_client.delete(f"{U}/{world['admin']}", headers=h)
    ).status_code == 400
    assert (await async_client.delete(f"{U}/{foreign}", headers=h)).status_code == 404
    assert (await async_client.delete(f"{U}/99999999", headers=root)).status_code == 404


@pytest.mark.xfail(
    strict=True, reason="bug: delete_user uses `del db.identities[id]` (TableProxy)"
)
@pytest.mark.asyncio
async def test_users_delete_success(app, async_client, world):
    """Deleting an in-tenant user should succeed (currently a TypeError 500)."""
    h = await _h(app, world["admin"])
    target = _ident(app.db, world["tid"])
    r = await async_client.delete(f"/api/v1/users/{target}", headers=h)
    assert r.status_code == 200


# -------------------------------------------------------------------- lookup


def _entity(db, tid: int) -> int:
    """Insert an org and entity in tenant ``tid``; return the entity id."""
    now = datetime.now(UTC)
    org = db.organizations.insert(
        name=f"o-{uuid4().hex[:6]}", tenant_id=tid, created_at=now, updated_at=now
    )
    eid = db.entities.insert(
        name="e", type="compute", organization_id=org, created_at=now, updated_at=now
    )
    db.commit()
    return eid


@pytest.mark.asyncio
async def test_lookup_entity(app, async_client, world):
    """Entity lookup is tenant scoped, single and batched."""
    h = await _h(app, world["admin"])
    eid = _entity(app.db, world["tid"])
    foreign = _entity(app.db, _tenant(app.db))
    r = await async_client.get(f"/lookup/{eid}", headers=h)
    assert r.status_code == 200 and (await r.get_json())["id"] == eid
    assert (await async_client.get(f"/lookup/{foreign}", headers=h)).status_code == 404

    B = "/lookup/batch"
    assert (await async_client.post(B, json={}, headers=h)).status_code == 400
    assert (await async_client.post(B, json={"ids": []}, headers=h)).status_code == 400
    assert (
        await async_client.post(B, json={"ids": list(range(101))}, headers=h)
    ).status_code == 400
    r = await async_client.post(B, json={"ids": [eid, foreign, 99999999]}, headers=h)
    found = {x["id"]: x["found"] for x in (await r.get_json())["results"]}
    assert found == {eid: True, foreign: False, 99999999: False}

    empty_tid = _tenant(app.db)
    lonely = _ident(app.db, empty_tid, "admin")
    r = await async_client.post(B, json={"ids": [eid]}, headers=await _h(app, lonely))
    assert (await r.get_json())["results"][0]["found"] is False
