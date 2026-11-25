"""Unit tests for the remote-worker administration and heartbeat API.

Real Quart app and test database; admin calls use real identity JWTs, the
platform tenant is pointed at a seeded tenant so super-admin paths run for
real, and heartbeats use provisioned ``ewk_`` tokens or service JWTs.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import jwt
import pytest
from werkzeug.security import generate_password_hash

BASE = "/api/v1/admin/workers"
NOW = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)


def _tenant(db) -> tuple[int, str]:
    """Insert a tenant and return ``(id, slug)``."""
    slug = f"covb-{uuid4().hex[:10]}"
    tid = db.tenants.insert(name="cov-b", slug=slug, is_active=True)
    db.commit()
    return tid, slug


def _ident(db, tid: int, role: str = "admin", superuser: bool = False) -> int:
    """Insert an identity."""
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
    """Seed a tenant (set as the platform tenant) with admin, observer and superuser."""
    db = app.db
    tid, slug = _tenant(db)
    old = app.config.get("PLATFORM_TENANT_SLUG")
    app.config["PLATFORM_TENANT_SLUG"] = slug
    other_tid, _ = _tenant(db)
    yield {
        "tid": tid,
        "other": other_tid,
        "admin": _ident(db, other_tid, "admin"),
        "viewer": _ident(db, other_tid, "observer"),
        "root": _ident(db, tid, "admin", superuser=True),
        "stray_root": _ident(db, other_tid, "admin", superuser=True),
    }
    app.config["PLATFORM_TENANT_SLUG"] = old


def _worker(db, tenant_id, **extra) -> int:
    """Insert a remote worker row directly."""
    now = datetime.now(UTC)
    fields = dict(
        worker_id=f"wrk-{uuid4().hex[:10]}",
        tenant_id=tenant_id,
        worker_type="runworker",
        name="w",
        is_enabled=True,
        created_at=now,
        updated_at=now,
    )
    fields.update(extra)
    wid = db.remote_workers.insert(**fields)
    db.commit()
    return wid


# ----------------------------------------------------------------- helpers


def test_json_and_datetime_helpers():
    """JSON/date normalisers accept dicts, strings and junk."""
    from apps.api.api.v1.admin_workers import (
        _iso_or_none,
        _parse_datetime_utc,
        _parse_json_field,
    )

    assert _parse_json_field(None, 1) == 1
    assert _parse_json_field({"a": 1}) == {"a": 1}
    assert _parse_json_field([1]) == [1]
    assert _parse_json_field('{"a": 2}') == {"a": 2}
    assert _parse_json_field("bad", "d") == "d"
    assert _parse_json_field(5, "d") == "d"

    naive = datetime(2026, 1, 1)
    assert _parse_datetime_utc(naive).tzinfo is UTC
    aware = datetime(2026, 1, 1, 5, tzinfo=timezone_plus(2))
    assert _parse_datetime_utc(aware).hour == 3
    assert _parse_datetime_utc("2026-01-01T00:00:00Z").year == 2026
    assert _parse_datetime_utc("2026-01-01T00:00:00").tzinfo is UTC
    assert _parse_datetime_utc("2026-01-01T05:00:00+02:00").hour == 3
    assert _parse_datetime_utc("not a date") is None
    assert _parse_datetime_utc("  ") is None
    assert _parse_datetime_utc(12) is None
    assert _parse_datetime_utc(None) is None
    assert _iso_or_none(None) is None
    assert _iso_or_none(naive).startswith("2026-01-01")


def timezone_plus(hours: int):
    """Build a fixed-offset timezone."""
    from datetime import timezone

    return timezone(timedelta(hours=hours))


def test_poll_interval_and_status():
    """Status derives from heartbeat age against the poll-interval threshold."""
    from apps.api.api.v1.admin_workers import (
        DEFAULT_POLL_INTERVAL_SECONDS,
        _extract_poll_interval,
        compute_worker_status,
    )

    def row(**kw):
        """Build a worker row double."""
        base = dict(
            revoked_at=None, is_enabled=True, last_heartbeat_at=None, config_json=None
        )
        base.update(kw)
        return SimpleNamespace(**base)

    assert _extract_poll_interval(row()) == DEFAULT_POLL_INTERVAL_SECONDS
    assert _extract_poll_interval(row(config_json={"poll_interval_seconds": 5})) == 5.0
    assert _extract_poll_interval(row(config_json={"poll_interval": "7"})) == 7.0
    assert _extract_poll_interval(row(config_json={"poll_interval": True})) == 30.0
    assert _extract_poll_interval(row(config_json={"poll_interval": -1})) == 30.0
    assert _extract_poll_interval(row(config_json={"poll_interval": "x"})) == 30.0
    assert _extract_poll_interval(row(config_json="[1]")) == 30.0

    assert compute_worker_status(row(revoked_at=NOW)) == "revoked"
    assert compute_worker_status(row(is_enabled=False)) == "revoked"
    assert compute_worker_status(row()) == "offline"
    hb = NOW - timedelta(seconds=100)
    assert compute_worker_status(row(last_heartbeat_at=hb), NOW) == "online"
    assert (
        compute_worker_status(row(last_heartbeat_at=NOW - timedelta(seconds=1000)), NOW)
        == "stale"
    )
    assert (
        compute_worker_status(row(last_heartbeat_at=NOW - timedelta(seconds=5000)), NOW)
        == "offline"
    )
    assert (
        compute_worker_status(row(last_heartbeat_at=NOW + timedelta(seconds=9)), NOW)
        == "online"
    )
    assert compute_worker_status(row(last_heartbeat_at=NOW.isoformat())) in (
        "online",
        "stale",
        "offline",
    )


def test_serialize_and_visibility():
    """Serialisation never leaks the token hash; visibility follows tenancy."""
    from apps.api.api.v1.admin_workers import _row_visible_to_caller, _serialize_worker

    row = SimpleNamespace(
        id=1,
        worker_id="w",
        tenant_id=None,
        worker_type="runworker",
        name="n",
        cluster=None,
        token_prefix="ewk_x",
        config_json='{"a": 1}',
        capabilities_json="[1]",
        hostname=None,
        version=None,
        is_enabled=1,
        last_heartbeat_at=NOW,
        revoked_at=None,
        created_by_identity_id=None,
        created_at=NOW,
        updated_at=NOW,
        token_hash="secret",
    )
    dto = _serialize_worker(row, NOW)
    assert dto["kind"] == "platform_wide" and "token_hash" not in dto
    assert dto["config"] == {"a": 1} and dto["capabilities"] == [1]
    row.tenant_id = 4
    row.config_json = "oops"
    row.capabilities_json = 5
    dto = _serialize_worker(row)
    assert dto["kind"] == "tenant" and dto["config"] == {} and dto["capabilities"] == {}

    assert _row_visible_to_caller(row, caller_tenant_id=None, super_admin=True)
    assert not _row_visible_to_caller(row, caller_tenant_id=None, super_admin=False)
    assert _row_visible_to_caller(row, caller_tenant_id=4, super_admin=False)
    assert not _row_visible_to_caller(row, caller_tenant_id=5, super_admin=False)


@pytest.mark.asyncio
async def test_token_snippets_and_platform_tenant(app, world):
    """Onboarding snippets include cluster env; platform tenant resolves with fallback."""
    from apps.api.api.v1 import admin_workers as aw

    token, digest, prefix = aw._generate_worker_token()
    assert token.startswith("ewk_") and prefix == token[:12] and len(digest) == 64

    async with app.test_request_context("/"):
        snip = aw._build_onboarding_snippets(
            token=token, worker_id="w", worker_type="runworker", cluster="c1"
        )
        assert snip["docker_env"]["RUNS_WORKER_CLUSTER"] == "c1"
        assert "worker.cluster=c1" in snip["helm_set"]
        snip = aw._build_onboarding_snippets(
            token=token, worker_id="w", worker_type="scanner", cluster="c2"
        )
        assert snip["docker_env"]["ELDER_WORKER_CLUSTER"] == "c2"
        snip = aw._build_onboarding_snippets(
            token=token, worker_id="w", worker_type="scanner", cluster=None
        )
        assert "ELDER_WORKER_CLUSTER" not in snip["docker_env"]

    async with app.app_context():
        db = app.db
        assert aw._platform_tenant_id(db) == world["tid"]
        old = app.config["PLATFORM_TENANT_SLUG"]
        app.config["PLATFORM_TENANT_SLUG"] = "does-not-exist"
        try:
            assert aw._platform_tenant_id(db) is None
            app.config["PLATFORM_TENANT_ALLOW_DEFAULT_FALLBACK"] = True
            assert aw._platform_tenant_id(db) is not None
        finally:
            app.config["PLATFORM_TENANT_ALLOW_DEFAULT_FALLBACK"] = False
            app.config["PLATFORM_TENANT_SLUG"] = old
        assert (
            aw._caller_is_super_admin(db, caller_tenant_id=None, is_superuser=True)
            is False
        )
        assert (
            aw._caller_is_super_admin(
                db, caller_tenant_id=world["tid"], is_superuser=False
            )
            is False
        )
        assert (
            aw._caller_is_super_admin(
                db, caller_tenant_id=world["tid"], is_superuser=True
            )
            is True
        )


@pytest.mark.asyncio
async def test_caller_context_values(app):
    """Caller context extracts tenant, superuser flag and a valid identity id."""
    from types import SimpleNamespace as NS

    from quart import g

    from apps.api.api.v1.admin_workers import _caller_context

    for user, want in (
        (NS(id=5, is_superuser=True), (5, True)),
        (NS(id=True, is_superuser=False), (None, False)),
        (NS(id=-1, is_superuser=False), (None, False)),
        (None, (None, False)),
    ):
        async with app.test_request_context("/"):
            g.claims = {"tenant": "9"}
            g.current_user = user
            tenant, sup, ident = _caller_context()
            assert tenant == 9 and (ident, sup) == want


# ------------------------------------------------------------------- list


@pytest.mark.asyncio
async def test_list_workers(app, async_client, world):
    """Listing is tenant scoped, validated, filtered and paginated."""
    db = app.db
    mine = _worker(
        db, world["other"], cluster="east", last_heartbeat_at=datetime.now(UTC)
    )
    _worker(
        db,
        world["other"],
        worker_type="scanner",
        revoked_at=datetime.now(UTC),
        is_enabled=False,
    )
    _worker(db, world["tid"])
    _worker(db, None)
    h = await _h(app, world["admin"])

    r = await async_client.get(BASE, headers=h)
    body = await r.get_json()
    assert r.status_code == 200 and body["total"] == 2
    assert body["counts_by_status"]["revoked"] == 1
    for qs, expect in (
        ("worker_type=scanner", 1),
        ("cluster=east", 1),
        ("status=online", 1),
        ("status=revoked&worker_type=scanner", 1),
    ):
        r = await async_client.get(f"{BASE}?{qs}", headers=h)
        assert (await r.get_json())["total"] == expect, qs
    r = await async_client.get(f"{BASE}?per_page=1&page=2", headers=h)
    assert len((await r.get_json())["items"]) == 1
    for qs in ("worker_type=zzz", "status=zzz", "page=x", "per_page=x"):
        assert (await async_client.get(f"{BASE}?{qs}", headers=h)).status_code == 400

    root = await _h(app, world["root"])
    r = await async_client.get(BASE, headers=root)
    assert (await r.get_json())["total"] >= 4
    assert mine

    viewer = await _h(app, world["viewer"])
    assert (await async_client.get(BASE, headers=viewer)).status_code in (200, 403)
    assert (await async_client.get(BASE)).status_code == 401


# ------------------------------------------------------------------ create


@pytest.mark.asyncio
async def test_create_worker_validation(app, async_client, world):
    """Create validates every field before touching the database."""
    h = await _h(app, world["admin"])
    root = await _h(app, world["root"])
    assert (await async_client.post(BASE, data="x", headers=h)).status_code == 400
    ok = {"name": "w", "worker_type": "runworker"}
    bad = [
        ({"worker_type": "runworker"}, h),
        ({"name": "  ", "worker_type": "runworker"}, h),
        ({"name": "n" * 256, "worker_type": "runworker"}, h),
        ({"name": "n"}, h),
        ({"name": "n", "worker_type": "zzz"}, h),
        ({**ok, "worker_id": 5}, h),
        ({**ok, "worker_id": "x" * 65}, h),
        ({**ok, "cluster": 5}, h),
        ({**ok, "cluster": "c" * 129}, h),
        ({**ok, "config": []}, h),
        ({**ok, "capabilities": "x"}, h),
        ({**ok, "platform_wide": "yes"}, h),
        ({**ok, "platform_wide": True, "tenant_id": 1}, root),
        ({**ok, "platform_wide": True}, h),
        ({**ok}, root),
        ({**ok, "tenant_id": True}, root),
        ({**ok, "tenant_id": 0}, root),
        ({**ok, "tenant_id": "a"}, root),
        ({**ok, "tenant_id": world["tid"]}, h),
    ]
    for body, hdr in bad:
        r = await async_client.post(BASE, json=body, headers=hdr)
        assert r.status_code in (400, 403), body


@pytest.mark.asyncio
async def test_create_worker_success_and_conflicts(app, async_client, world):
    """Tenant admins and super-admins mint workers; duplicate ids conflict."""
    h = await _h(app, world["admin"])
    wid = f"wrk-{uuid4().hex[:8]}"
    body = {
        "name": " Worker ",
        "worker_type": "scanner",
        "worker_id": wid,
        "cluster": " c1 ",
        "config": {"poll_interval_seconds": 5},
        "capabilities": ["x"],
    }
    r = await async_client.post(BASE, json=body, headers=h)
    data = await r.get_json()
    assert r.status_code == 201 and data["token"].startswith("ewk_")
    assert (
        data["worker"]["tenant_id"] == world["other"]
        and data["worker"]["cluster"] == "c1"
    )
    assert "token_hash" not in data["worker"]
    assert (await async_client.post(BASE, json=body, headers=h)).status_code == 409

    r = await async_client.post(
        BASE,
        json={"name": "auto", "worker_type": "runworker", "cluster": ""},
        headers=h,
    )
    assert r.status_code == 201 and (await r.get_json())["worker"][
        "worker_id"
    ].startswith("wrk-run-")

    root = await _h(app, world["root"])
    r = await async_client.post(
        BASE,
        json={"name": "g", "worker_type": "runworker", "platform_wide": True},
        headers=root,
    )
    assert (
        r.status_code == 201
        and (await r.get_json())["worker"]["kind"] == "platform_wide"
    )
    r = await async_client.post(
        BASE,
        json={"name": "t", "worker_type": "runworker", "tenant_id": world["other"]},
        headers=root,
    )
    assert r.status_code == 201


@pytest.mark.asyncio
async def test_worker_requires_tenant_context(app, async_client, world):
    """Callers without a tenant claim are refused outright."""
    from unittest.mock import patch

    h = await _h(app, world["admin"])
    with patch(
        "apps.api.api.v1.admin_workers.get_current_tenant_id", return_value=None
    ):
        for method, url in (("get", BASE), ("get", f"{BASE}/1")):
            assert (
                await getattr(async_client, method)(url, headers=h)
            ).status_code == 403
        assert (await async_client.post(BASE, json={}, headers=h)).status_code == 403
        assert (
            await async_client.put(f"{BASE}/1", json={}, headers=h)
        ).status_code == 403
        assert (
            await async_client.post(f"{BASE}/1/rotate-token", headers=h)
        ).status_code == 403
        assert (await async_client.delete(f"{BASE}/1", headers=h)).status_code == 403


# ------------------------------------------------ get / update / rotate / revoke


@pytest.mark.asyncio
async def test_worker_lifecycle(app, async_client, world):
    """Get, update, rotate and revoke honour tenancy and validation."""
    db = app.db
    wid = _worker(db, world["other"], token_hash="x" * 64, token_prefix="ewk_oldprefi")
    foreign = _worker(db, world["tid"])
    h = await _h(app, world["admin"])

    r = await async_client.get(f"{BASE}/{wid}", headers=h)
    assert r.status_code == 200 and (await r.get_json())["worker"]["id"] == wid
    assert (await async_client.get(f"{BASE}/{foreign}", headers=h)).status_code == 404
    assert (await async_client.get(f"{BASE}/99999999", headers=h)).status_code == 404

    U = f"{BASE}/{wid}"
    assert (await async_client.put(U, data="x", headers=h)).status_code == 400
    for body in (
        {"name": ""},
        {"name": "n" * 256},
        {"cluster": 5},
        {"cluster": "c" * 129},
        {"config": []},
        {"capabilities": "x"},
        {"is_enabled": "no"},
    ):
        assert (
            await async_client.put(U, json=body, headers=h)
        ).status_code == 400, body
    r = await async_client.put(
        U,
        json={
            "name": " New ",
            "cluster": " cl ",
            "config": {"a": 1},
            "capabilities": ["z"],
            "is_enabled": False,
        },
        headers=h,
    )
    w = (await r.get_json())["worker"]
    assert (
        r.status_code == 200
        and w["name"] == "New"
        and w["cluster"] == "cl"
        and w["is_enabled"] is False
    )
    r = await async_client.put(
        U, json={"cluster": None, "config": None, "capabilities": None}, headers=h
    )
    assert r.status_code == 200
    assert (
        await async_client.put(f"{BASE}/{foreign}", json={"name": "x"}, headers=h)
    ).status_code == 404

    r = await async_client.post(f"{U}/rotate-token", headers=h)
    data = await r.get_json()
    assert r.status_code == 200 and data["token"].startswith("ewk_")
    assert (
        await async_client.post(f"{BASE}/99999999/rotate-token", headers=h)
    ).status_code == 404

    r = await async_client.delete(U, headers=h)
    assert (
        r.status_code == 200 and (await r.get_json())["worker"]["status"] == "revoked"
    )
    assert (await async_client.post(f"{U}/rotate-token", headers=h)).status_code == 409
    assert (await async_client.delete(f"{BASE}/99999999", headers=h)).status_code == 404


# --------------------------------------------------------------- heartbeat


def _svc_token(
    app, sub: str, tenant: str = "system", kind: str = "service_worker"
) -> str:
    """Sign a service-worker JWT."""
    from apps.api.auth.jwt_handler import SERVICE_WORKER_ALLOWED_SCOPES

    now = datetime.now(UTC)
    allowed = sorted(SERVICE_WORKER_ALLOWED_SCOPES.get(sub, {"x:y"}))
    payload = {
        "sub": sub,
        "type": kind,
        "tenant": tenant,
        "scope": allowed[:1],
        "iss": app.config.get("JWT_ISSUER", "elder-api"),
        "aud": app.config.get("JWT_AUDIENCE", "elder-api"),
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=5)).timestamp()),
    }
    secret = app.config["JWT_SECRET_KEY"] or app.config["SECRET_KEY"]
    return jwt.encode(payload, secret, algorithm=app.config["JWT_ALGORITHM"])


@pytest.mark.asyncio
async def test_heartbeat_provisioned_worker(app, async_client, world):
    """Provisioned ewk_ tokens heartbeat; revoked or unknown tokens are rejected."""
    from apps.api.api.v1.admin_workers import _generate_worker_token

    HB = f"{BASE}/heartbeat"
    assert (await async_client.post(HB, json={})).status_code == 401
    token, digest, prefix = _generate_worker_token()
    wid = _worker(
        app.db,
        world["other"],
        token_hash=digest,
        token_prefix=prefix,
        config_json={"x": 1},
    )
    auth = {"Authorization": f"Bearer {token}"}

    assert (await async_client.post(HB, json=[1], headers=auth)).status_code == 400
    r = await async_client.post(
        HB,
        json={
            "hostname": " h ",
            "version": "1.2",
            "capabilities": ["a"],
            "cluster": "c9",
            "worker_id": "x",
        },
        headers=auth,
    )
    body = await r.get_json()
    assert r.status_code == 200 and body["config"] == {"x": 1}
    row = app.db(app.db.remote_workers.id == wid).select().first()
    assert (
        row.hostname == "h"
        and row.cluster == "c9"
        and row.last_heartbeat_at is not None
    )
    r = await async_client.post(HB, headers=auth)
    assert r.status_code == 200
    r = await async_client.post(
        HB,
        json={"capabilities": "bad", "cluster": "ignored", "name": " n "},
        headers=auth,
    )
    assert r.status_code == 200

    other = {"Authorization": "Bearer ewk_not-a-real-token"}
    assert (await async_client.post(HB, json={}, headers=other)).status_code == 401

    app.db(app.db.remote_workers.id == wid).update(
        revoked_at=datetime.now(UTC), is_enabled=False
    )
    app.db.commit()
    assert (await async_client.post(HB, json={}, headers=auth)).status_code == 403

    app.db(app.db.remote_workers.id == wid).update(
        token_hash="gone", token_prefix=prefix
    )
    app.db.commit()
    assert (await async_client.post(HB, json={}, headers=auth)).status_code == 403

    token2, digest2, prefix2 = _generate_worker_token()
    cand = _worker(
        app.db,
        world["other"],
        token_hash="z" * 64,
        token_prefix="ewk_distinct1",
        is_enabled=False,
    )
    del cand
    r = await async_client.post(
        HB,
        json={
            "worker_id": app.db(app.db.remote_workers.token_prefix == "ewk_distinct1")
            .select()
            .first()
            .worker_id
        },
        headers={"Authorization": f"Bearer {token2}"},
    )
    assert r.status_code == 403
    assert prefix2


@pytest.mark.asyncio
async def test_heartbeat_platform_wide_provisioned(app, async_client, world):
    """Platform-wide provisioned workers heartbeat too."""
    from apps.api.api.v1.admin_workers import _generate_worker_token

    token, digest, prefix = _generate_worker_token()
    _worker(app.db, None, token_hash=digest, token_prefix=prefix)
    r = await async_client.post(
        f"{BASE}/heartbeat", json={}, headers={"Authorization": f"Bearer {token}"}
    )
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_heartbeat_in_cluster_service_worker(app, async_client, world):
    """In-cluster service workers upsert their record; bad subjects are refused."""
    HB = f"{BASE}/heartbeat"
    wid = f"pod-{uuid4().hex[:8]}"
    good = {"Authorization": f"Bearer {_svc_token(app, 'svc-runworker')}"}
    r = await async_client.post(
        HB, json={"worker_id": wid, "hostname": "h", "name": "pod"}, headers=good
    )
    assert r.status_code == 200 and (await r.get_json())["worker_id"] == wid
    r = await async_client.post(
        HB,
        json={
            "worker_id": wid,
            "version": "9",
            "capabilities": {"a": 1},
            "cluster": "k",
        },
        headers=good,
    )
    assert r.status_code == 200
    r = await async_client.post(HB, json={"hostname": "host-only"}, headers=good)
    assert r.status_code == 200 and (await r.get_json())["worker_id"] == "host-only"
    r = await async_client.post(HB, json={}, headers=good)
    assert (await r.get_json())["worker_id"] == "svc-runworker"

    bad_sub = {"Authorization": f"Bearer {_svc_token(app, 'svc-integrations')}"}
    assert (await async_client.post(HB, json={}, headers=bad_sub)).status_code == 403
    tenant = {"Authorization": f"Bearer {_svc_token(app, 'svc-scanner', tenant='5')}"}
    assert (await async_client.post(HB, json={}, headers=tenant)).status_code == 403
    assert (
        await async_client.post(HB, json={}, headers={"Authorization": "Bearer junk"})
    ).status_code == 401

    prov = _worker(app.db, world["other"], worker_id="owned-by-tenant")
    assert prov
    r = await async_client.post(HB, json={"worker_id": "owned-by-tenant"}, headers=good)
    assert r.status_code == 403
    off = _worker(app.db, None, worker_id="revoked-cluster", is_enabled=False)
    assert off
    r = await async_client.post(HB, json={"worker_id": "revoked-cluster"}, headers=good)
    assert r.status_code == 403
