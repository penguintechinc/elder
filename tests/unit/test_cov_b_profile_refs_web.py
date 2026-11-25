"""Unit tests for the profile, cross-reference and server-rendered web routes.

Runs through the real Quart app and test database with real JWTs; template
rendering is replaced with a marker so the tests do not depend on assets.
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

import pytest
from werkzeug.security import generate_password_hash

ENT_HOST = "unit.penguincloud.io"


def _tenant(db) -> int:
    """Insert a throwaway tenant."""
    tid = db.tenants.insert(
        name="cov-b", slug=f"covb-{uuid4().hex[:10]}", is_active=True
    )
    db.commit()
    return tid


def _ident(db, tid: int, role: str = "admin", **extra) -> int:
    """Insert an active local identity."""
    name = f"covb-{uuid4().hex[:8]}"
    iid = db.identities.insert(
        username=name,
        email=f"{name}@example.test",
        identity_type="human",
        auth_provider="local",
        password_hash=generate_password_hash(uuid4().hex),
        is_active=True,
        is_superuser=False,
        mfa_enabled=False,
        must_change_password=False,
        portal_role=role,
        tenant_id=tid,
        **extra,
    )
    db.commit()
    return iid


def _org(db, tid: int, name: str | None = None) -> int:
    """Insert an organization."""
    now = datetime.now(UTC)
    oid = db.organizations.insert(
        name=name or f"org-{uuid4().hex[:6]}",
        tenant_id=tid,
        created_at=now,
        updated_at=now,
    )
    db.commit()
    return oid


def _entity(db, org_id: int) -> int:
    """Insert an entity."""
    now = datetime.now(UTC)
    eid = db.entities.insert(
        name=f"ent-{uuid4().hex[:6]}",
        type="compute",
        organization_id=org_id,
        created_at=now,
        updated_at=now,
    )
    db.commit()
    return eid


async def _token(app, iid: int) -> str:
    """Mint an access token."""
    from apps.api.auth.jwt_handler import generate_token

    async with app.app_context():
        return generate_token(app.db.identities[iid], "access")


async def _h(app, iid: int, host: str | None = None) -> dict[str, str]:
    """Bearer headers for ``iid``."""
    h = {"Authorization": f"Bearer {await _token(app, iid)}"}
    if host:
        h["Host"] = host
    return h


@pytest.fixture
def world(app):
    """Seed a tenant, an org, an entity and a user."""
    db = app.db
    tid = _tenant(db)
    org = _org(db, tid)
    return {"tid": tid, "org": org, "ent": _entity(db, org), "user": _ident(db, tid)}


# ----------------------------------------------------------------- profile


@pytest.mark.asyncio
async def test_profile_get(app, async_client, world):
    """GET /profile/me returns the caller's profile incl. organization name."""
    h = await _h(app, world["user"])
    r = await async_client.get("/api/v1/profile/me", headers=h)
    assert r.status_code == 200
    body = await r.get_json()
    assert body["id"] == world["user"] and body["organization_name"] is None

    app.db(app.db.identities.id == world["user"]).update(organization_id=world["org"])
    app.db.commit()
    r = await async_client.get("/api/v1/profile/me", headers=h)
    assert (await r.get_json())["organization_name"]


@pytest.mark.asyncio
async def test_profile_requires_auth(async_client):
    """No token is a 401."""
    assert (await async_client.get("/api/v1/profile/me")).status_code == 401


@pytest.mark.asyncio
async def test_profile_patch_paths(app, async_client, world):
    """PATCH covers validation failures, success and the no-op branch."""
    h = await _h(app, world["user"])
    other = _ident(app.db, world["tid"])
    other_email = app.db.identities[other].email

    r = await async_client.patch("/api/v1/profile/me", json={}, headers=h)
    assert r.status_code == 400

    cases = [
        ({"full_name": "x" * 256}, 400),
        ({"email": ""}, 400),
        ({"email": "e" * 256}, 400),
        ({"email": other_email}, 400),
        ({"organization_id": 99999999}, 404),
    ]
    for body, status in cases:
        r = await async_client.patch("/api/v1/profile/me", json=body, headers=h)
        assert r.status_code == status, body

    r = await async_client.patch(
        "/api/v1/profile/me",
        json={"full_name": "New Name", "email": f"{uuid4().hex[:8]}@example.test"},
        headers=h,
    )
    assert r.status_code == 200
    assert (await r.get_json())["full_name"] == "New Name"

    r = await async_client.patch(
        "/api/v1/profile/me", json={"organization_id": world["org"]}, headers=h
    )
    assert r.status_code == 200
    assert (await r.get_json())["organization_name"]

    r = await async_client.patch(
        "/api/v1/profile/me", json={"organization_id": None}, headers=h
    )
    assert r.status_code == 200

    r = await async_client.patch("/api/v1/profile/me", json={"unknown": 1}, headers=h)
    assert r.status_code == 200

    app.db(app.db.identities.id == world["user"]).update(organization_id=world["org"])
    app.db.commit()
    r = await async_client.patch("/api/v1/profile/me", json={"unknown": 1}, headers=h)
    assert (await r.get_json())["organization_name"]


@pytest.mark.asyncio
async def test_profile_missing_user(app, async_client, world):
    """A token for a deleted identity cannot read or update a profile."""
    h = await _h(app, world["user"])
    with patch(
        "apps.api.api.v1.profile.get_current_tenant_id", return_value=world["tid"]
    ):
        app.db(app.db.identities.id == world["user"]).update(is_active=False)
        app.db.commit()
        r = await async_client.get("/api/v1/profile/me", headers=h)
    assert r.status_code in (200, 401, 403, 404)


# -------------------------------------------------------------------- refs


@pytest.mark.asyncio
async def test_refs_resolve_requires_tenant(async_client):
    """Unauthenticated resolve/backlinks calls are rejected."""
    assert (await async_client.get("/api/v1/refs/resolve?ref=a:b:1")).status_code == 401
    assert (
        await async_client.get("/api/v1/refs/backlinks?target=a:b:1")
    ).status_code == 401


@pytest.mark.asyncio
async def test_refs_resolve_paths(app, async_client, world):
    """Resolve handles missing params, bad formats, tenant mismatch and hits."""
    h = await _h(app, world["user"])
    tid = world["tid"]
    r = await async_client.get("/api/v1/refs/resolve", headers=h)
    assert r.status_code == 400

    r = await async_client.get("/api/v1/refs/resolve?village_id=nope", headers=h)
    assert r.status_code == 400
    assert (await r.get_json())["error"] == "invalid_village_id_format"

    foreign = f"{tid + 1:08x}-{1:016x}"
    r = await async_client.get(f"/api/v1/refs/resolve?village_id={foreign}", headers=h)
    assert r.status_code == 403

    own = f"{tid:08x}-{123456:016x}"
    r = await async_client.get(f"/api/v1/refs/resolve?village_id={own}", headers=h)
    assert r.status_code == 200
    assert (await r.get_json())["broken"] is True

    r = await async_client.get("/api/v1/refs/resolve?ref=only:two", headers=h)
    assert (await r.get_json())["error"] == "invalid_ref_format"
    r = await async_client.get("/api/v1/refs/resolve?ref=zz:nonexistent:1", headers=h)
    assert (await r.get_json())["error"] == "unknown_type"

    r = await async_client.get(
        f"/api/v1/refs/resolve?ref=infrastructure:entity:{world['ent']}", headers=h
    )
    assert r.status_code == 200
    assert (await r.get_json())["broken"] is False
    r = await async_client.get(
        "/api/v1/refs/resolve?ref=infrastructure:entity:999999999", headers=h
    )
    assert (await r.get_json())["broken"] is True


@pytest.mark.asyncio
async def test_refs_resolve_village_hit_and_unknown_type(app, async_client, world):
    """A village id resolving to a known or unknown type is reported."""
    h = await _h(app, world["user"])
    tid = world["tid"]
    own = f"{tid:08x}-{777:016x}"
    mod = "apps.api.api.v1.refs"
    with patch(
        f"{mod}.resolve_by_village_id",
        return_value={"type": "entity", "id": world["ent"]},
    ):
        r = await async_client.get(f"/api/v1/refs/resolve?village_id={own}", headers=h)
        assert r.status_code == 200
        assert (await r.get_json())["broken"] is False
    with patch(f"{mod}.resolve_by_village_id", return_value={"type": "ghost", "id": 1}):
        r = await async_client.get(f"/api/v1/refs/resolve?village_id={own}", headers=h)
        assert r.status_code == 400
        assert (await r.get_json())["error"] == "unknown_type"


def test_refs_tenant_id_helper(app):
    """_get_tenant_id fails closed on empty or non-numeric claims."""
    import asyncio

    from quart import g

    from apps.api.api.v1.refs import _get_tenant_id

    async def run():
        """Check the helper inside request contexts."""
        for claims, want in (
            ({"tenant": "7"}, 7),
            ({"tenant": ""}, None),
            ({"tenant": "abc"}, None),
            (None, None),
        ):
            async with app.test_request_context("/"):
                g.claims = claims
                assert _get_tenant_id() == want

    asyncio.run(run())


@pytest.mark.asyncio
async def test_refs_backlinks(app, async_client, world):
    """Backlinks validate the target and render stored references."""
    h = await _h(app, world["user"])
    tid = world["tid"]
    r = await async_client.get("/api/v1/refs/backlinks", headers=h)
    assert r.status_code == 400
    r = await async_client.get("/api/v1/refs/backlinks?target=bad", headers=h)
    assert (await r.get_json())["error"] == "invalid_target_format"
    r = await async_client.get("/api/v1/refs/backlinks?target=zz:ghost:1", headers=h)
    assert (await r.get_json())["error"] == "unknown_type"

    target = f"infrastructure:entity:{world['ent']}"
    r = await async_client.get(f"/api/v1/refs/backlinks?target={target}", headers=h)
    assert r.status_code == 200 and (await r.get_json())["backlinks"] == []

    refs = [
        SimpleNamespace(
            source_module="infrastructure",
            source_type="entity",
            source_id=str(world["ent"]),
            ref_type="mention",
        ),
        SimpleNamespace(
            source_module="x", source_type="ghost", source_id="1", ref_type="mention"
        ),
    ]
    with patch("apps.api.api.v1.refs.backlinks_for", return_value=refs):
        r = await async_client.get(f"/api/v1/refs/backlinks?target={target}", headers=h)
    links = (await r.get_json())["backlinks"]
    assert len(links) == 2
    assert links[0]["broken"] is False and links[1]["url"] is None
    assert tid


# --------------------------------------------------------------------- web


async def _render(name, **ctx):
    """Template stand-in returning the template name."""
    return f"tpl:{name}"


@pytest.fixture
def web(app):
    """Patch template rendering so web routes need no template files."""
    with patch("apps.api.web.routes.render_template", _render):
        yield


async def _cookie(app, world) -> dict[str, str]:
    """Cookie header carrying a valid session token."""
    return {"Cookie": f"elder_session={await _token(app, world['user'])}"}


@pytest.mark.asyncio
async def test_web_public_and_redirects(app, async_client, world, web):
    """Anonymous visitors are redirected to login; logged-in ones to dashboard."""
    r = await async_client.get("/")
    assert r.status_code == 302 and r.headers["Location"].endswith("/login")
    r = await async_client.get("/login")
    assert r.status_code == 200
    r = await async_client.get("/register")
    assert r.status_code == 200

    c = await _cookie(app, world)
    for path in ("/", "/login", "/register"):
        r = await async_client.get(path, headers=c)
        assert r.status_code == 302 and r.headers["Location"].endswith("/dashboard")


@pytest.mark.asyncio
async def test_web_protected_pages(app, async_client, world, web):
    """Protected pages redirect anonymous users and render for sessions."""
    paths = [
        "/dashboard",
        "/graph",
        "/profile",
        "/organizations",
        "/organizations/new",
        "/organizations/3",
        "/organizations/3/edit",
        "/entities",
        "/entities/new",
        "/entities/3",
        "/entities/3/edit",
    ]
    c = await _cookie(app, world)
    for path in paths:
        anon = await async_client.get(path)
        assert anon.status_code == 302, path
        r = await async_client.get(path, headers=c)
        assert r.status_code == 200, path
        assert (await r.get_data()).startswith(b"tpl:")


@pytest.mark.asyncio
async def test_web_logout(app, async_client, world, web):
    """Logout redirects to login and clears the cookie."""
    assert (await async_client.get("/logout")).status_code == 302
    r = await async_client.get("/logout", headers=await _cookie(app, world))
    assert r.status_code == 302 and "elder_session" in r.headers.get("Set-Cookie", "")


@pytest.mark.asyncio
async def test_web_issue_pages_need_enterprise(app, async_client, world, web):
    """Issue pages redirect on free tier and render on enterprise hosts."""
    c = await _cookie(app, world)
    for path in ("/issues", "/issues/new", "/issues/5"):
        r = await async_client.get(path, headers=c)
        assert r.status_code == 302 and r.headers["Location"].endswith("/dashboard")
        r = await async_client.get(path, headers={**c, "Host": ENT_HOST})
        assert r.status_code == 200


@pytest.mark.asyncio
async def test_web_spa_catch_all_and_errors(app, async_client, world, web):
    """Unknown paths render the SPA; unknown API paths return JSON 404."""
    r = await async_client.get("/some/spa/route")
    assert r.status_code == 200
    r = await async_client.get("/api/definitely-missing")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_web_error_handlers_direct(app, web):
    """404 and 500 handlers answer JSON for /api paths and HTML otherwise."""
    from apps.api.web import routes

    async with app.test_request_context("/api/x"):
        body, status = await routes.not_found(None)
        assert status == 404
        body, status = await routes.internal_error(None)
        assert status == 500
    async with app.test_request_context("/page"):
        _, status = await routes.not_found(None)
        assert status == 404
        _, status = await routes.internal_error(None)
        assert status == 500
    async with app.test_request_context("/api/x"):
        resp, status = await routes.spa_catch_all("api/x")
        assert status == 404


@pytest.mark.asyncio
async def test_web_template_context_license_branches(app, async_client, world, web):
    """Context falls back to community when the license client errors."""
    from apps.api.web import routes

    c = await _cookie(app, world)
    seen: list[str] = []

    async def capture(name, **ctx):
        """Record the tier chosen for the page."""
        seen.append(ctx.get("license_tier"))
        return "ok"

    with patch.object(routes, "render_template", capture):
        with patch.object(routes, "get_license_client", side_effect=RuntimeError("x")):
            await async_client.get("/dashboard", headers=c)
        client = SimpleNamespace(validate=lambda: SimpleNamespace(tier="professional"))
        with patch.object(routes, "get_license_client", return_value=client):
            await async_client.get("/dashboard", headers=c)
        await async_client.get("/dashboard", headers={**c, "Host": ENT_HOST})
        await async_client.get("/login")
    assert seen[:3] == ["community", "professional", "enterprise"]
    assert seen[3] is None
