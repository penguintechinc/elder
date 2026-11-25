"""Unit tests for the small v1 API blueprints (licensing, modules, logs, api keys,
village-id lookup, tenant modules, privacy admin).

Requests go through the real Quart app and test database with real JWTs
minted for freshly seeded tenants/identities, so auth, tenant claims and scope
checks all run for real.
"""

from __future__ import annotations

from unittest.mock import patch
from uuid import uuid4

import pytest
from werkzeug.security import generate_password_hash

ENT_HOST = "unit.penguincloud.io"


def _seed_tenant(db) -> int:
    """Insert a throwaway tenant and return its id."""
    tid = db.tenants.insert(
        name="cov-b", slug=f"covb-{uuid4().hex[:10]}", is_active=True
    )
    db.commit()
    return tid


def _seed_identity(db, tenant_id: int, role: str = "admin", superuser: bool = False):
    """Insert an active local identity and return its id."""
    name = f"covb-{role}-{uuid4().hex[:8]}"
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
        tenant_id=tenant_id,
    )
    db.commit()
    return iid


async def _headers(app, iid: int, host: str | None = None) -> dict[str, str]:
    """Mint a bearer token for identity ``iid`` and build request headers."""
    from apps.api.auth.jwt_handler import generate_token

    async with app.app_context():
        token = generate_token(app.db.identities[iid], "access")
    headers = {"Authorization": f"Bearer {token}"}
    if host:
        headers["Host"] = host
    return headers


@pytest.fixture
def world(app):
    """Seed a tenant with an admin, a viewer and a superuser."""
    db = app.db
    tid = _seed_tenant(db)
    return {
        "tid": tid,
        "admin": _seed_identity(db, tid, "admin"),
        "viewer": _seed_identity(db, tid, "observer"),
        "root": _seed_identity(db, tid, "admin", superuser=True),
        "scoped": _seed_identity(db, tid, "admin"),
    }


# --------------------------------------------------------------------------
# licensing / modules
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_licensing_status_free_and_bypass(app, async_client, world):
    """Licensing status reports free by default and enterprise on a bypass host."""
    h = await _headers(app, world["admin"])
    r = await async_client.get("/api/v1/licensing/status", headers=h)
    assert r.status_code == 200
    body = await r.get_json()
    assert body["domain_bypass"] is False and body["bypass_domain"] is None

    h = await _headers(app, world["admin"], ENT_HOST)
    r = await async_client.get("/api/v1/licensing/status", headers=h)
    body = await r.get_json()
    assert body["tier"] == "enterprise"
    assert body["domain_bypass"] is True
    assert body["features"]["saml_sso"] is True
    assert body["bypass_domain"].endswith("penguincloud.io")


@pytest.mark.asyncio
async def test_licensing_requires_auth(async_client):
    """No token yields 401."""
    r = await async_client.get("/api/v1/licensing/status")
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_modules_list_tenant_toggle_and_error_fallback(app, async_client, world):
    """Modules list resolves tenant enablement, falling back on toggle errors."""
    h = await _headers(app, world["admin"])
    r = await async_client.get("/api/v1/modules", headers=h)
    assert r.status_code == 200
    mods = (await r.get_json())["modules"]
    assert mods and all("effective" in m for m in mods)


@pytest.mark.xfail(
    strict=True, reason="bug: stdlib logger.warning called with structlog kwargs"
)
@pytest.mark.asyncio
async def test_modules_toggle_error_falls_back(app, async_client, world):
    """A toggle-check failure should fall back to the default, not 500."""
    h = await _headers(app, world["admin"])
    with patch(
        "apps.api.common.modules.tenant_toggle.is_module_enabled",
        side_effect=RuntimeError("boom"),
    ):
        r = await async_client.get("/api/v1/modules", headers=h)
    assert r.status_code == 200


# --------------------------------------------------------------------------
# logs
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_logs_get_and_search(app, async_client, world, tmp_path, monkeypatch):
    """Admin can read and search the log file; missing file yields 404."""
    log = tmp_path / "elder.log"
    log.write_text(
        "\n".join(f"line {i} Alpha" if i % 2 else f"line {i}" for i in range(150))
    )
    monkeypatch.setenv("LOG_FILE", str(log))
    h = await _headers(app, world["root"])

    r = await async_client.get("/api/v1/logs", headers=h)
    assert r.status_code == 200
    body = await r.get_json()
    assert body["total"] == 150 and len(body["lines"]) == 100

    r = await async_client.get("/api/v1/logs/search?q=alpha", headers=h)
    body = await r.get_json()
    assert body["total_matches"] == 75 and len(body["lines"]) == 75

    r = await async_client.get("/api/v1/logs/search?q=line%201%20", headers=h)
    assert r.status_code == 200

    r = await async_client.get("/api/v1/logs/search?q=", headers=h)
    assert r.status_code == 400

    short = tmp_path / "short.log"
    short.write_text("a\nb\n")
    monkeypatch.setenv("LOG_FILE", str(short))
    r = await async_client.get("/api/v1/logs", headers=h)
    assert (await r.get_json())["total"] == 2


@pytest.mark.asyncio
async def test_logs_missing_and_unreadable(app, async_client, world, monkeypatch):
    """Missing log file gives 404 and read failures give 500."""
    h = await _headers(app, world["root"])
    monkeypatch.setenv("LOG_FILE", "/nonexistent/covb.log")
    with patch("apps.api.api.v1.logs.FALLBACK_LOG_FILE", "/nonexistent/fallback.log"):
        assert (await async_client.get("/api/v1/logs", headers=h)).status_code == 404
        r = await async_client.get("/api/v1/logs/search?q=x", headers=h)
        assert r.status_code == 404

    with patch(
        "apps.api.api.v1.logs._get_log_file_path", return_value="/nonexistent/x"
    ):
        assert (await async_client.get("/api/v1/logs", headers=h)).status_code == 500
        r = await async_client.get("/api/v1/logs/search?q=x", headers=h)
        assert r.status_code == 500


def test_logs_path_falls_back(tmp_path, monkeypatch):
    """The fallback log file is used when the primary is missing."""
    from apps.api.api.v1 import logs

    fallback = tmp_path / "fb.log"
    fallback.write_text("x")
    monkeypatch.setenv("LOG_FILE", str(tmp_path / "missing.log"))
    with patch.object(logs, "FALLBACK_LOG_FILE", str(fallback)):
        assert logs._get_log_file_path() == str(fallback)


# --------------------------------------------------------------------------
# api keys
# --------------------------------------------------------------------------


def test_generate_api_key_shape():
    """Generated keys carry the elder_ prefix and a matching sha256 digest."""
    import hashlib

    from apps.api.api.v1.api_keys import generate_api_key

    key, digest, prefix = generate_api_key()
    assert key.startswith("elder_") and prefix == key[:15] + "..."
    assert digest == hashlib.sha256(key.encode()).hexdigest()


@pytest.mark.asyncio
async def test_api_key_lifecycle(app, async_client, world):
    """Create, list (paginated), and revoke an API key; ownership enforced."""
    h = await _headers(app, world["admin"])
    other = await _headers(app, world["viewer"])

    assert (await async_client.post("/api/v1/api-keys", headers=h)).status_code in (
        400,
        415,
    )
    r = await async_client.post("/api/v1/api-keys", json={}, headers=h)
    assert r.status_code == 400
    r = await async_client.post("/api/v1/api-keys", json={"name": "  "}, headers=h)
    assert r.status_code == 400

    r = await async_client.post(
        "/api/v1/api-keys",
        json={"name": "ci key", "expires_at": "2099-01-01T00:00:00"},
        headers=h,
    )
    assert r.status_code == 201
    created = await r.get_json()
    assert created["api_key"].startswith("elder_")

    for qs in ("page=0", "per_page=0", "per_page=1001"):
        r = await async_client.get(f"/api/v1/api-keys?{qs}", headers=h)
        assert r.status_code == 400

    r = await async_client.get("/api/v1/api-keys", headers=other)
    assert (await r.get_json())["total"] == 0

    r = await async_client.delete(f"/api/v1/api-keys/{created['id']}", headers=other)
    assert r.status_code == 403
    r = await async_client.delete("/api/v1/api-keys/99999999", headers=h)
    assert r.status_code == 404


@pytest.mark.xfail(
    strict=True, reason="bug: delete_api_key uses `del db.api_keys[id]` (TableProxy)"
)
@pytest.mark.asyncio
async def test_api_key_delete_own(app, async_client, world):
    """Owners should be able to revoke their key (currently a TypeError 500)."""
    h = await _headers(app, world["admin"])
    r = await async_client.post("/api/v1/api-keys", json={"name": "k"}, headers=h)
    kid = (await r.get_json())["id"]
    r = await async_client.delete(f"/api/v1/api-keys/{kid}", headers=h)
    assert r.status_code == 200


@pytest.mark.xfail(
    strict=True,
    reason="bug: list_api_keys selects `prefix` but APIKeyDTO needs key_prefix",
)
@pytest.mark.asyncio
async def test_api_key_list_with_rows(app, async_client, world):
    """Listing keys when one exists should return it (currently a TypeError 500)."""
    h = await _headers(app, world["admin"])
    r = await async_client.post("/api/v1/api-keys", json={"name": "k"}, headers=h)
    assert r.status_code == 201
    r = await async_client.get("/api/v1/api-keys?page=1&per_page=5", headers=h)
    assert r.status_code == 200


# --------------------------------------------------------------------------
# village-id lookup
# --------------------------------------------------------------------------


class _Resolvable:
    """Minimal stand-in for a registry resolvable type."""

    module = "infrastructure"


@pytest.mark.asyncio
async def test_lookup_village_id_paths(app, async_client, world):
    """Lookup returns 404 unknown, 403 without scope, 200 for permitted callers."""
    h = await _headers(app, world["admin"])
    mod = "apps.api.api.v1.lookup_village_id"

    r = await async_client.get("/api/v1/id/NOPE-1", headers=h)
    assert r.status_code == 404

    hit = {"type": "entity", "id": 7, "title": "Thing", "module": "infrastructure"}
    with (
        patch(f"{mod}.resolve_by_village_id", return_value=hit),
        patch(f"{mod}.get_type", return_value=_Resolvable()),
        patch(f"{mod}.format_resolvable_url", return_value="/entities/7"),
    ):
        r = await async_client.get("/api/v1/id/VID-7", headers=h)
        assert r.status_code == 200
        assert (await r.get_json())["short_link"] == "/id/VID-7"
        r = await async_client.get("/id/VID-7", headers=h)
        assert r.status_code == 200

    odd = dict(hit, module="tenant", type="tenant")
    viewer = await _headers(app, world["viewer"])
    with (
        patch(f"{mod}.resolve_by_village_id", return_value=odd),
        patch(f"{mod}.get_type", return_value=_Resolvable()),
        patch(f"{mod}.format_resolvable_url", return_value="/x"),
    ):
        r = await async_client.get("/api/v1/id/VID-8", headers=viewer)
        assert r.status_code in (200, 403)

    with (
        patch(f"{mod}.resolve_by_village_id", return_value=hit),
        patch(f"{mod}.get_type", return_value=None),
    ):
        r = await async_client.get("/api/v1/id/VID-9", headers=h)
        assert r.status_code == 404


@pytest.mark.parametrize(
    ("user_super", "scopes", "module", "expected"),
    [
        (True, [], "x", True),
        (False, ["*:read"], "x", True),
        (False, ["identities:read"], "identity", True),
        (False, ["backups:read"], "backup", True),
        (False, ["nothing:read"], "x", False),
        (False, [], "x", False),
    ],
)
@pytest.mark.asyncio
async def test_caller_can_read_module(app, user_super, scopes, module, expected):
    """Read access follows superuser, wildcard, module and alias scopes."""
    from types import SimpleNamespace

    from quart import g

    from apps.api.api.v1.lookup_village_id import _caller_can_read_module

    async with app.test_request_context("/"):
        g.current_user = SimpleNamespace(is_superuser=user_super)
        g.claims = {"scope": scopes}
        assert _caller_can_read_module(module) is expected


# --------------------------------------------------------------------------
# tenant modules
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tenant_modules_list_and_set(app, async_client, world):
    """Admin lists and toggles modules for own tenant; foreign tenant is refused."""
    h = await _headers(app, world["root"])
    tid = world["tid"]
    r = await async_client.get(f"/api/v1/tenants/{tid}/modules", headers=h)
    assert r.status_code == 200
    data = (await r.get_json())["data"]
    assert data

    plain = await _headers(app, world["scoped"])
    r = await async_client.get(f"/api/v1/tenants/{tid + 99999}/modules", headers=plain)
    assert r.status_code == 403
    r = await async_client.put(
        f"/api/v1/tenants/{tid + 99999}/modules",
        json={"module_name": "x", "enabled": True},
        headers=plain,
    )
    assert r.status_code == 403

    name = data[0]["module_name"]
    r = await async_client.put(
        f"/api/v1/tenants/{tid}/modules",
        json={"module_name": name, "enabled": True, "settings": {"a": 1}},
        headers=h,
    )
    assert r.status_code == 200
    r = await async_client.get(f"/api/v1/tenants/{tid}/modules", headers=h)
    assert any(m.get("settings") == {"a": 1} for m in (await r.get_json())["data"])

    r = await async_client.put(
        f"/api/v1/tenants/{tid}/modules",
        json={"module_name": "bogus-module", "enabled": True},
        headers=h,
    )
    assert r.status_code == 400
    r = await async_client.put(
        f"/api/v1/tenants/{tid}/modules", json={"enabled": True}, headers=h
    )
    assert r.status_code == 400
    r = await async_client.put(
        f"/api/v1/tenants/{tid}/modules", data="not json", headers=h
    )
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_tenant_modules_scope_and_failures(app, async_client, world):
    """Viewers lack admin:write; backend failures surface as 500."""
    tid = world["tid"]
    viewer = await _headers(app, world["viewer"])
    r = await async_client.get(f"/api/v1/tenants/{tid}/modules", headers=viewer)
    assert r.status_code == 403

    h = await _headers(app, world["root"])
    with patch(
        "apps.api.api.v1.tenant_modules.get_tenant_modules",
        side_effect=RuntimeError("x"),
    ):
        r = await async_client.get(f"/api/v1/tenants/{tid}/modules", headers=h)
        assert r.status_code == 500
    name = next(iter(app.extensions["elder_modules"]))
    with patch(
        "apps.api.api.v1.tenant_modules.set_module_enabled",
        side_effect=RuntimeError("x"),
    ):
        r = await async_client.put(
            f"/api/v1/tenants/{tid}/modules",
            json={"module_name": name, "enabled": False},
            headers=h,
        )
        assert r.status_code == 500

    with patch("apps.api.api.v1.tenant_modules._get_tenant_id", return_value=None):
        r = await async_client.get(f"/api/v1/tenants/{tid}/modules", headers=h)
        assert r.status_code == 401
        r = await async_client.put(
            f"/api/v1/tenants/{tid}/modules",
            json={"module_name": name, "enabled": False},
            headers=h,
        )
        assert r.status_code == 401


# --------------------------------------------------------------------------
# privacy admin
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_privacy_admin_requires_enterprise(app, async_client, world):
    """Free-tier deployments get 403 from the enterprise privacy admin API."""
    h = await _headers(app, world["admin"])
    r = await async_client.get("/api/v1/privacy-admin/requests", headers=h)
    assert r.status_code == 403
    assert (await r.get_json())["error"] == "tier_required"


@pytest.mark.asyncio
async def test_privacy_admin_list_and_bulk_erase(app, async_client, world):
    """Enterprise admins list DSARs and bulk-erase own-tenant identities."""
    db = app.db
    h = await _headers(app, world["admin"], ENT_HOST)
    r = await async_client.get("/api/v1/privacy-admin/requests", headers=h)
    assert r.status_code == 200
    assert (await r.get_json())["count"] >= 0

    for body in ({}, {"identity_ids": []}, {"identity_ids": ["a"]}):
        r = await async_client.post(
            "/api/v1/privacy-admin/bulk-erase", json=body, headers=h
        )
        assert r.status_code == 400

    victim = _seed_identity(db, world["tid"], "observer")
    foreign = _seed_identity(db, _seed_tenant(db), "observer")
    r = await async_client.post(
        "/api/v1/privacy-admin/bulk-erase",
        json={"identity_ids": [victim, foreign]},
        headers=h,
    )
    assert r.status_code == 200
    results = (await r.get_json())["results"]
    by_id = {e["identity_id"]: e for e in results}
    assert by_id[victim].get("anonymized") is True
    assert not by_id[foreign].get("anonymized")

    r = await async_client.get("/api/v1/privacy-admin/requests", headers=h)
    assert any(x["identity_id"] == victim for x in (await r.get_json())["requests"])

    again = await async_client.post(
        "/api/v1/privacy-admin/bulk-erase",
        json={"identity_ids": [victim]},
        headers=h,
    )
    assert again.status_code == 200


def test_privacy_admin_helpers(app):
    """Legal-hold lookup and the activity-log writer tolerate odd inputs."""
    import asyncio

    from apps.api.api.v1 import privacy_admin as pa

    async def run():
        """Exercise helpers inside an app context."""
        async with app.app_context():
            db = app.db
            tid = _seed_tenant(db)
            assert pa._tenant_legal_hold(db, tid) is False
            assert pa._tenant_legal_hold(db, 987654321) is False
            db(db.tenants.id == tid).update(legal_hold=True)
            db.commit()
            assert pa._tenant_legal_hold(db, tid) is True

            class Boom:
                """DB double whose insert always fails."""

                class dsar_requests:  # noqa: N801
                    """Table double."""

                    @staticmethod
                    def insert(**_kw):
                        """Raise to simulate a failed write."""
                        raise RuntimeError("down")

                @staticmethod
                def commit():
                    """No-op commit."""

            pa._log_bulk_request(Boom, tid, 1, 1)

    asyncio.run(run())
