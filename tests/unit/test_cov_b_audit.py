"""Unit tests for the audit API blueprints (retention policies and enterprise logs).

Identity routes use real access tokens; portal routes use real portal-user
tokens signed with the app key, all against the test database.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import patch
from uuid import uuid4

import jwt
import pytest
from werkzeug.security import generate_password_hash


def _tenant(db) -> int:
    """Insert a throwaway tenant."""
    tid = db.tenants.insert(
        name="cov-b", slug=f"covb-{uuid4().hex[:10]}", is_active=True
    )
    db.commit()
    return tid


def _ident(db, tid: int, role: str) -> int:
    """Insert an active local identity with a portal role."""
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
    )
    db.commit()
    return iid


async def _h(app, iid: int) -> dict[str, str]:
    """Bearer headers for an identity."""
    from apps.api.auth.jwt_handler import generate_token

    async with app.app_context():
        tok = generate_token(app.db.identities[iid], "access")
    return {"Authorization": f"Bearer {tok}"}


def _portal(
    app, tenant_id: int, global_role=None, tenant_role="reader"
) -> dict[str, str]:
    """Headers carrying a signed portal-user token."""
    now = datetime.now(UTC)
    claims = {
        "sub": "1",
        "email": "portal@example.test",
        "tenant_id": tenant_id,
        "tenant_role": tenant_role,
        "global_role": global_role,
        "roles": ["observer"],
        "scope": [],
        "type": "portal_user",
        "principal_kind": "portal_user",
        "iat": now,
        "exp": now + timedelta(minutes=10),
    }
    secret = app.config["JWT_SECRET_KEY"] or app.config["SECRET_KEY"]
    tok = jwt.encode(claims, secret, algorithm=app.config["JWT_ALGORITHM"])
    return {"Authorization": f"Bearer {tok}"}


@pytest.fixture
def world(app):
    """Seed a tenant with an admin and an observer."""
    tid = _tenant(app.db)
    return {
        "tid": tid,
        "admin": _ident(app.db, tid, "admin"),
        "viewer": _ident(app.db, tid, "observer"),
    }


# --------------------------------------------------------- retention policies


def _seed_policy(db, rt: str) -> int:
    """Insert a retention policy row directly."""
    now = datetime.now(UTC)
    pid = db.audit_retention_policies.insert(
        resource_type=rt,
        retention_days=30,
        enabled=True,
        created_at=now,
        updated_at=now,
    )
    db.commit()
    return pid


@pytest.mark.asyncio
async def test_retention_policy_crud(app, async_client, world):
    """Admins validate, read, update and delete retention policies."""
    h = await _h(app, world["admin"])
    rt = f"res-{uuid4().hex[:8]}"

    r = await async_client.post("/api/v1/audit/retention-policies", json={}, headers=h)
    assert r.status_code == 400
    r = await async_client.post(
        "/api/v1/audit/retention-policies", json={"name": "x"}, headers=h
    )
    assert r.status_code == 400

    pid = _seed_policy(app.db, rt)
    dup = {"name": rt, "resource_type": rt, "retention_days": 30}
    r = await async_client.post("/api/v1/audit/retention-policies", json=dup, headers=h)
    assert r.status_code == 400

    r = await async_client.get("/api/v1/audit/retention-policies", headers=h)
    assert r.status_code == 200 and (await r.get_json())["count"] >= 1
    r = await async_client.get(f"/api/v1/audit/retention-policies/{pid}", headers=h)
    assert r.status_code == 200
    r = await async_client.get("/api/v1/audit/retention-policies/99999999", headers=h)
    assert r.status_code == 404

    r = await async_client.put(
        f"/api/v1/audit/retention-policies/{pid}",
        json={"retention_days": 60},
        headers=h,
    )
    assert r.status_code == 200
    assert (await r.get_json())["retention_days"] == 60
    r = await async_client.put(
        f"/api/v1/audit/retention-policies/{pid}", json={}, headers=h
    )
    assert r.status_code == 400
    r = await async_client.put(
        "/api/v1/audit/retention-policies/99999999",
        json={"retention_days": 1},
        headers=h,
    )
    assert r.status_code == 404

    r = await async_client.delete(f"/api/v1/audit/retention-policies/{pid}", headers=h)
    assert r.status_code == 200
    r = await async_client.delete(f"/api/v1/audit/retention-policies/{pid}", headers=h)
    assert r.status_code == 404


@pytest.mark.xfail(
    strict=True,
    reason="bug: cleanup filters with `enabled is True` (Python identity, not SQL)",
)
@pytest.mark.asyncio
async def test_audit_cleanup_dry_run(app, async_client, world):
    """Cleanup should report the dry-run plan (currently a 500)."""
    h = await _h(app, world["admin"])
    _seed_policy(app.db, f"res-{uuid4().hex[:8]}")
    r = await async_client.post("/api/v1/audit/cleanup", headers=h)
    assert r.status_code == 200
    assert (await r.get_json())["dry_run"] is True


@pytest.mark.xfail(
    strict=True,
    reason="bug: create inserts description/event_types columns the table lacks",
)
@pytest.mark.asyncio
async def test_retention_policy_create(app, async_client, world):
    """Creating a policy should succeed (currently a CompileError -> 400)."""
    h = await _h(app, world["admin"])
    rt = f"res-{uuid4().hex[:8]}"
    r = await async_client.post(
        "/api/v1/audit/retention-policies",
        json={"name": rt, "resource_type": rt, "retention_days": 30},
        headers=h,
    )
    assert r.status_code == 201


@pytest.mark.xfail(
    strict=True, reason="bug: update writes is_active but the column is `enabled`"
)
@pytest.mark.asyncio
async def test_retention_policy_update_is_active(app, async_client, world):
    """Updating is_active should work (currently an unknown column -> 400)."""
    h = await _h(app, world["admin"])
    pid = _seed_policy(app.db, f"res-{uuid4().hex[:8]}")
    r = await async_client.put(
        f"/api/v1/audit/retention-policies/{pid}", json={"is_active": False}, headers=h
    )
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_retention_policy_admin_gate(app, async_client, world):
    """Non-admins cannot mutate policies."""
    h = await _h(app, world["viewer"])
    r = await async_client.post(
        "/api/v1/audit/retention-policies",
        json={"name": "n", "retention_days": 1},
        headers=h,
    )
    assert r.status_code == 403
    assert (
        await async_client.delete("/api/v1/audit/retention-policies/1", headers=h)
    ).status_code == 403
    assert (
        await async_client.post("/api/v1/audit/cleanup", headers=h)
    ).status_code == 403


@pytest.mark.asyncio
async def test_retention_policy_error_paths(app, async_client, world):
    """Backend failures are mapped to generic error responses."""
    h = await _h(app, world["admin"])
    boom = RuntimeError("db down")

    class Broken:
        """DB double whose queries and commits fail."""

        def commit(self):
            """Fail the commit."""
            raise boom

        def rollback(self):
            """Accept rollback."""

        def __call__(self, *_a, **_k):
            """Fail any query."""
            raise boom

        @property
        def audit_retention_policies(self):
            """Fail table access."""
            raise boom

    from types import SimpleNamespace

    with patch("apps.api.api.v1.audit.current_app", SimpleNamespace(db=Broken())):
        for method, path in (
            ("get", "/api/v1/audit/retention-policies"),
            ("get", "/api/v1/audit/retention-policies/1"),
            ("post", "/api/v1/audit/cleanup"),
        ):
            r = await getattr(async_client, method)(path, headers=h)
            assert r.status_code == 500, path
        r = await async_client.delete("/api/v1/audit/retention-policies/1", headers=h)
        assert r.status_code == 500
        r = await async_client.put(
            "/api/v1/audit/retention-policies/1", json={"retention_days": 1}, headers=h
        )
        assert r.status_code == 400
        r = await async_client.post(
            "/api/v1/audit/retention-policies",
            json={"name": "n", "retention_days": 1},
            headers=h,
        )
        assert r.status_code == 400


# --------------------------------------------------------- enterprise audit


@pytest.mark.asyncio
async def test_enterprise_requires_portal_token(async_client):
    """Missing or invalid portal tokens are rejected."""
    assert (await async_client.get("/api/v1/audit/logs")).status_code in (401, 404)


@pytest.fixture
def ent_prefix(app):
    """Locate the URL prefix the enterprise audit blueprint is mounted at."""
    for rule in app.url_map.iter_rules():
        if rule.endpoint.startswith("audit_enterprise.") and rule.endpoint.endswith(
            "query_logs"
        ):
            return rule.rule[: -len("/logs")]
    pytest.skip("enterprise audit blueprint is not mounted")


@pytest.mark.asyncio
async def test_enterprise_query_logs(app, async_client, world, ent_prefix):
    """Log queries honour filters, date parsing and tenant restriction."""
    tid = world["tid"]
    h = _portal(app, tid, global_role="admin")
    r = await async_client.get(
        f"{ent_prefix}/logs?resource_type=x&resource_id=1&action=a&success=true"
        "&limit=5&offset=0&start_date=2026-01-01T00:00:00Z&end_date=2026-12-31T00:00:00Z",
        headers=h,
    )
    assert r.status_code == 200
    r = await async_client.get(f"{ent_prefix}/logs?success=false", headers=h)
    assert r.status_code == 200
    for bad in ("start_date=nope", "end_date=nope"):
        r = await async_client.get(f"{ent_prefix}/logs?{bad}", headers=h)
        assert r.status_code == 400
    plain = _portal(app, tid)
    with patch("apps.api.api.v1.audit_enterprise.AuditService.query_logs") as q:
        q.return_value = {"logs": []}
        r = await async_client.get(f"{ent_prefix}/logs?tenant_id=999", headers=plain)
        assert r.status_code == 200
        assert q.call_args.kwargs["tenant_id"] == tid


@pytest.mark.xfail(
    strict=True,
    reason="bug: tenant/category/portal_user filters ILIKE a JSON column on Postgres",
)
@pytest.mark.asyncio
async def test_enterprise_query_logs_json_filters(app, async_client, world, ent_prefix):
    """Filtering by tenant/category/portal user should not error."""
    h = _portal(app, world["tid"], global_role="admin")
    r = await async_client.get(
        f"{ent_prefix}/logs?tenant_id={world['tid']}&category=c&portal_user_id=1",
        headers=h,
    )
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_enterprise_reports(app, async_client, world, ent_prefix):
    """Reports enforce admin access, default dates and error mapping."""
    tid = world["tid"]
    admin = _portal(app, tid, tenant_role="admin")
    reader = _portal(app, tid)
    r = await async_client.get(f"{ent_prefix}/reports/user_access", headers=reader)
    assert r.status_code == 403
    r = await async_client.get(f"{ent_prefix}/reports/user_access", headers=admin)
    assert r.status_code == 200
    r = await async_client.get(
        f"{ent_prefix}/reports/failed_auth?start_date=2026-01-01&end_date=2026-02-01",
        headers=admin,
    )
    assert r.status_code == 200
    r = await async_client.get(f"{ent_prefix}/reports/bogus_type", headers=admin)
    assert r.status_code == 400
    r = await async_client.get(
        f"{ent_prefix}/reports/user_access?start_date=zz&end_date=yy", headers=admin
    )
    assert r.status_code == 400
    glob = _portal(app, tid, global_role="support")
    r = await async_client.get(
        f"{ent_prefix}/reports/user_access?tenant_id={tid}", headers=glob
    )
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_enterprise_retention_cleanup_export(
    app, async_client, world, ent_prefix
):
    """Retention, cleanup and export honour roles and report service errors."""
    tid = world["tid"]
    reader = _portal(app, tid)
    admin = _portal(app, tid, global_role="admin")
    tadmin = _portal(app, tid, tenant_role="admin")
    mod = "apps.api.api.v1.audit_enterprise.AuditService"

    with patch(f"{mod}.get_retention_policy", return_value={"retention_days": 9}):
        r = await async_client.get(f"{ent_prefix}/retention", headers=reader)
        assert r.status_code == 200
        r = await async_client.get(f"{ent_prefix}/retention?tenant_id=5", headers=admin)
        assert r.status_code == 200
    with patch(f"{mod}.get_retention_policy", return_value={"error": "none"}):
        r = await async_client.get(f"{ent_prefix}/retention", headers=reader)
        assert r.status_code == 404

    r = await async_client.post(f"{ent_prefix}/cleanup", json={}, headers=reader)
    assert r.status_code == 403
    with patch(f"{mod}.cleanup_old_logs", return_value={"deleted": 0}) as c:
        r = await async_client.post(
            f"{ent_prefix}/cleanup", json={"tenant_id": 77}, headers=admin
        )
        assert r.status_code == 200 and c.call_args.args[0] == 77
        r = await async_client.post(f"{ent_prefix}/cleanup", headers=admin)
        assert r.status_code == 200
        r = await async_client.post(
            f"{ent_prefix}/cleanup", json={"tenant_id": 77}, headers=tadmin
        )
        assert c.call_args.args[0] == tid
    with patch(f"{mod}.cleanup_old_logs", return_value={"error": "x"}):
        r = await async_client.post(f"{ent_prefix}/cleanup", json={}, headers=admin)
        assert r.status_code == 404

    r = await async_client.get(f"{ent_prefix}/export", headers=reader)
    assert r.status_code == 403
    logs = {
        "logs": [
            {
                "id": 1,
                "action": "login",
                "resource_type": "user",
                "resource_id": 2,
                "success": True,
                "ip_address": "10.0.0.1",
                "created_at": "2026-01-01",
            }
        ]
    }
    with patch(f"{mod}.query_logs", return_value=logs):
        r = await async_client.get(f"{ent_prefix}/export", headers=admin)
        assert r.status_code == 200 and (await r.get_json()) == logs
        r = await async_client.get(
            f"{ent_prefix}/export?format=csv&start_date=2026-01-01&end_date=2026-02-01",
            headers=admin,
        )
        assert r.status_code == 200
        assert r.headers["Content-Type"].startswith("text/csv")
        assert b"login" in await r.get_data()
        r = await async_client.get(f"{ent_prefix}/export?start_date=bad", headers=admin)
        assert r.status_code == 400
        r = await async_client.get(f"{ent_prefix}/export?end_date=bad", headers=admin)
        assert r.status_code == 400
