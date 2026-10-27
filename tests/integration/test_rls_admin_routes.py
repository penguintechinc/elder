"""End-to-end RLS tests for cross-tenant admin routes and worker discovery.

Real JWT auth, the real claims bridge (``populate_claims``), real Redis and the
real penguin-dal under the NOSUPERUSER application role. A superuser / global
admin acting on *another* tenant must enter an explicit, audited SUPER_ADMIN
scope; acting on their own tenant needs none.
"""

import json
import os
import secrets
import time
import uuid
from datetime import UTC, datetime
from types import SimpleNamespace

import jwt
import pytest
from rls_support import T1, T2, _run, _seed
from sqlalchemy import text

from shared.database import rls
from shared.database.rls import SystemScopeReason

pytestmark = [
    pytest.mark.integration,
    pytest.mark.usefixtures("_reset_rls_state"),
]

JWT_SECRET = secrets.token_hex(32)  # per-run, never a committed literal


def _bearer(claims: dict) -> dict:
    """Return an Authorization header carrying a signed, unexpired token."""
    claims = {**claims, "exp": int(time.time()) + 600}
    return {
        "Authorization": "Bearer " + jwt.encode(claims, JWT_SECRET, algorithm="HS256")
    }


@pytest.fixture()
def admin_app(dal, rls_env):
    """Quart app with the real claims bridge and the two admin blueprints."""
    from quart import Quart

    from apps.api.api.v1.tenant_modules import bp as modules_bp
    from apps.api.api.v1.tenants import bp as tenants_bp
    from apps.api.main import _register_before_request

    with rls_env.admin.begin() as conn:
        conn.execute(text("SET session_replication_role = replica"))
        identity_id = _seed(
            conn,
            "identities",
            tenant_id=T1,
            username=f"root-{uuid.uuid4().hex[:6]}",
            is_active=True,
            is_superuser=True,
        )
        conn.execute(text("DELETE FROM tenant_modules WHERE tenant_id = :t"), {"t": T2})
        _seed(
            conn,
            "tenant_modules",
            tenant_id=T2,
            module_name="issues",
            enabled=True,
            settings=json.dumps({"k": 1}),
        )

    app = Quart(__name__)
    app.config.update(
        JWT_SECRET_KEY=JWT_SECRET,
        SECRET_KEY="x",
        JWT_ALGORITHM="HS256",
        REDIS_URL=os.environ.get("REDIS_URL", "redis://localhost:56379/0"),
        TESTING=True,
    )
    app.db = dal
    app.extensions["elder_modules"] = {"issues": SimpleNamespace(default_enabled=True)}
    _register_before_request(app)
    app.register_blueprint(modules_bp, url_prefix="/api/v1")
    app.register_blueprint(tenants_bp, url_prefix="/api/v1/tenants")
    app.identity_id = identity_id
    return app


def _superuser(app) -> dict:
    return _bearer(
        {"sub": str(app.identity_id), "type": "access", "tenant": str(T1), "scope": []}
    )


def _portal_admin(tenant_id: int) -> dict:
    return _bearer(
        {
            "sub": "5",
            "type": "portal_user",
            "tenant_id": tenant_id,
            "global_role": "admin",
            "tenant_role": "admin",
        }
    )


@pytest.mark.asyncio
async def test_superuser_lists_another_tenants_modules_with_audited_scope(admin_app):
    audited = []
    rls.register_audit_sink(audited.append)
    response = await admin_app.test_client().get(
        f"/api/v1/tenants/{T2}/modules", headers=_superuser(admin_app)
    )
    assert response.status_code == 200
    data = (await response.get_json())["data"]
    assert data == [{"module_name": "issues", "enabled": True, "settings": {"k": 1}}]
    assert [(s.reason, s.purpose, s.actor) for s in audited] == [
        (
            SystemScopeReason.SUPER_ADMIN,
            "tenant_modules.list",
            f"identity:{admin_app.identity_id}",
        )
    ]


@pytest.mark.asyncio
async def test_superuser_sets_another_tenants_module(admin_app, rls_env):
    rls.register_audit_sink(lambda scope: None)
    response = await admin_app.test_client().put(
        f"/api/v1/tenants/{T2}/modules",
        json={"module_name": "issues", "enabled": False},
        headers=_superuser(admin_app),
    )
    assert response.status_code == 200
    rows, _ = _run(
        rls_env.admin,
        "SELECT enabled FROM tenant_modules WHERE tenant_id = :t AND module_name = 'issues'",
        t=T2,
    )
    assert rows == [(False,)]


@pytest.mark.asyncio
async def test_cross_tenant_module_access_is_refused_without_an_audit_sink(admin_app):
    """Fail closed: a SUPER_ADMIN scope that cannot be audited is never granted."""
    response = await admin_app.test_client().get(
        f"/api/v1/tenants/{T2}/modules", headers=_superuser(admin_app)
    )
    assert response.status_code == 500


@pytest.mark.asyncio
async def test_portal_admin_tenant_detail_and_stats_cross_tenant_vs_own(admin_app):
    audited = []
    rls.register_audit_sink(audited.append)
    client = admin_app.test_client()
    headers = _portal_admin(T1)

    own = await client.get(f"/api/v1/tenants/{T1}", headers=headers)
    assert own.status_code == 200
    assert (await own.get_json())["usage"]["organizations"] >= 2
    assert audited == []

    other = await client.get(f"/api/v1/tenants/{T2}", headers=headers)
    assert other.status_code == 200
    assert (await other.get_json())["usage"]["organizations"] >= 2

    stats = await client.get(f"/api/v1/tenants/{T2}/stats", headers=headers)
    assert stats.status_code == 200
    assert (await stats.get_json())["organizations"] >= 2
    assert [(s.reason, s.purpose, s.actor) for s in audited] == [
        (SystemScopeReason.SUPER_ADMIN, "tenants.get_tenant", "portal_user:5"),
        (SystemScopeReason.SUPER_ADMIN, "tenants.get_tenant_stats", "portal_user:5"),
    ]


def test_discovery_run_stores_resources_pinned_to_the_orgs_tenant(dal, rls_env):
    """run_discovery resolves the org's tenant under scope, then writes tenant-pinned."""
    from apps.worker.discovery.service import DiscoveryService

    rows, _ = _run(
        rls_env.admin,
        "SELECT id FROM organizations WHERE tenant_id = :t LIMIT 1",
        t=T2,
    )
    org_id = rows[0][0]
    with rls_env.admin.begin() as conn:
        conn.execute(text("SET session_replication_role = replica"))
        job_id = _seed(
            conn,
            "discovery_jobs",
            provider="aws",
            config_json=json.dumps({"_organization_id": org_id}),
        )

    seen = []
    service = DiscoveryService(dal)
    service._get_discovery_client = lambda _job: SimpleNamespace(
        discover_all=lambda: {
            "resources_count": 0,
            "discovery_time": datetime.now(UTC),
        }
    )

    def fake_store(organization_id, results):
        seen.append(
            (organization_id, rls.current_tenant_id(), rls.current_system_scope())
        )
        return {"edges_created": 0, "unresolved_edges": 0}

    service._store_discovered_resources = fake_store
    result = service.run_discovery(job_id)
    assert result["success"] is True
    assert seen == [(org_id, T2, None)]
