"""Auth + tenant-isolation regression tests for the discovery scanner endpoints.

``GET /api/v1/discovery/jobs/pending``, ``POST .../jobs/<id>/start``, and
``POST .../jobs/<id>/complete`` previously had NO auth decorator at all in a
file where every other route in ``apps/api/modules/discovery/routes/discovery.py``
uses ``@require_scope``. They are mounted under ``/api/v1/discovery`` and
``web/nginx.conf`` proxies ``/api/`` publicly, so they were internet-reachable
and allowed arbitrary cross-tenant reads/writes (list every tenant's pending
jobs, mark any tenant's job running, submit fake results for any tenant's job).

regression: gh-237
"""

from datetime import UTC, datetime
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest


def _tenant(db, name: str) -> int:
    """Create and return a tenant distinct from tenant 1."""
    tid = db.tenants.insert(
        name=name, slug=f"discovery-{uuid4().hex[:8]}", is_active=True
    )
    db.commit()
    return tid


def _org(db, tenant_id: int, name: str = "Org") -> int:
    now = datetime.now(UTC)
    org_id = db.organizations.insert(
        name=name, tenant_id=tenant_id, created_at=now, updated_at=now
    )
    db.commit()
    return org_id


def _discovery_job(db, organization_id: int, provider: str = "network") -> int:
    """Insert a discovery job owned by organization_id.

    discovery_jobs has no organization_id column (see create_job() in
    apps/worker/discovery/service.py) -- the owning org is stashed in
    config_json["_organization_id"], matching how the real create path
    stores it.
    """
    now = datetime.now(UTC)
    job_id = db.discovery_jobs.insert(
        name=f"job-{uuid4().hex[:8]}",
        provider=provider,
        config_json={"_organization_id": organization_id},
        schedule_interval=0,
        enabled=True,
        created_at=now,
        updated_at=now,
    )
    db.commit()
    return job_id


class TestDiscoveryPendingJobsAuthGate:
    """GET /api/v1/discovery/jobs/pending -- regression: gh-237."""

    @pytest.mark.asyncio
    async def test_unauthenticated_returns_401(self, async_client):
        """Unauthenticated GET must be 401, never list jobs."""
        resp = await async_client.get("/api/v1/discovery/jobs/pending")
        assert resp.status_code == 401

    @pytest.mark.asyncio
    @patch("apps.api.auth.decorators.get_current_user")
    async def test_excludes_other_tenant_pending_jobs(
        self, mock_get_user, async_client, generate_token, app
    ):
        """A tenant-1 caller must never see another tenant's pending jobs."""
        mock_get_user.return_value = MagicMock(id=1, is_superuser=False)
        token = generate_token(tenant_id=1, scopes=["discovery:read"])

        db = app.db
        other_tenant_id = _tenant(db, "Discovery Pending Other Tenant")
        other_org_id = _org(db, other_tenant_id, "Other Org Pending")
        own_org_id = _org(db, 1, "Own Org Pending")

        foreign_job_id = _discovery_job(db, other_org_id)
        own_job_id = _discovery_job(db, own_org_id)

        resp = await async_client.get(
            "/api/v1/discovery/jobs/pending",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200, (await resp.get_data()).decode()
        body = await resp.get_json()
        ids = {j["id"] for j in body["jobs"]}
        assert own_job_id in ids
        assert foreign_job_id not in ids


class TestDiscoveryStartJobAuthGate:
    """POST /api/v1/discovery/jobs/<id>/start -- regression: gh-237."""

    @pytest.mark.asyncio
    async def test_unauthenticated_returns_401(self, async_client):
        """Unauthenticated POST must be 401, never mutate job state."""
        resp = await async_client.post("/api/v1/discovery/jobs/1/start")
        assert resp.status_code == 401

    @pytest.mark.asyncio
    @patch("apps.api.auth.decorators.get_current_user")
    async def test_cross_tenant_write_rejected(
        self, mock_get_user, async_client, generate_token, app
    ):
        """A tenant-1 caller must not be able to start another tenant's job."""
        mock_get_user.return_value = MagicMock(id=1, is_superuser=False)
        token = generate_token(tenant_id=1, scopes=["discovery:write"])

        db = app.db
        other_tenant_id = _tenant(db, "Discovery Start Other Tenant")
        other_org_id = _org(db, other_tenant_id, "Other Org Start")
        foreign_job_id = _discovery_job(db, other_org_id)

        resp = await async_client.post(
            f"/api/v1/discovery/jobs/{foreign_job_id}/start",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 404, (await resp.get_data()).decode()

    @pytest.mark.asyncio
    @patch("apps.api.auth.decorators.get_current_user")
    async def test_same_tenant_start_succeeds(
        self, mock_get_user, async_client, generate_token, app
    ):
        """Sanity: starting the caller's own tenant's job still works."""
        mock_get_user.return_value = MagicMock(id=1, is_superuser=False)
        token = generate_token(tenant_id=1, scopes=["discovery:write"])

        db = app.db
        own_org_id = _org(db, 1, "Own Org Start")
        own_job_id = _discovery_job(db, own_org_id)

        resp = await async_client.post(
            f"/api/v1/discovery/jobs/{own_job_id}/start",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200, (await resp.get_data()).decode()


class TestDiscoveryCompleteJobAuthGate:
    """POST /api/v1/discovery/jobs/<id>/complete -- regression: gh-237."""

    @pytest.mark.asyncio
    async def test_unauthenticated_returns_401(self, async_client):
        """Unauthenticated POST must be 401, never record results."""
        resp = await async_client.post(
            "/api/v1/discovery/jobs/1/complete", json={"success": True}
        )
        assert resp.status_code == 401

    @pytest.mark.asyncio
    @patch("apps.api.auth.decorators.get_current_user")
    async def test_cross_tenant_write_rejected(
        self, mock_get_user, async_client, generate_token, app
    ):
        """A tenant-1 caller must not be able to submit results for another
        tenant's job."""
        mock_get_user.return_value = MagicMock(id=1, is_superuser=False)
        token = generate_token(tenant_id=1, scopes=["discovery:write"])

        db = app.db
        other_tenant_id = _tenant(db, "Discovery Complete Other Tenant")
        other_org_id = _org(db, other_tenant_id, "Other Org Complete")
        foreign_job_id = _discovery_job(db, other_org_id)

        resp = await async_client.post(
            f"/api/v1/discovery/jobs/{foreign_job_id}/complete",
            headers={"Authorization": f"Bearer {token}"},
            json={"success": True, "results": {}},
        )
        assert resp.status_code == 404, (await resp.get_data()).decode()

    @pytest.mark.asyncio
    @patch("apps.api.auth.decorators.get_current_user")
    async def test_same_tenant_complete_succeeds(
        self, mock_get_user, async_client, generate_token, app
    ):
        """Sanity: completing the caller's own tenant's job still works."""
        mock_get_user.return_value = MagicMock(id=1, is_superuser=False)
        token = generate_token(tenant_id=1, scopes=["discovery:write"])

        db = app.db
        own_org_id = _org(db, 1, "Own Org Complete")
        own_job_id = _discovery_job(db, own_org_id)

        resp = await async_client.post(
            f"/api/v1/discovery/jobs/{own_job_id}/complete",
            headers={"Authorization": f"Bearer {token}"},
            json={"success": True, "results": {}},
        )
        assert resp.status_code == 200, (await resp.get_data()).decode()
