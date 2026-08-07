"""Tests for tenant scoping on the `projects` table (universal-audits, task 1).

`projects` had no `tenant_id` and every lookup in `projects.py` was an
unscoped `db.projects[id]` bracket lookup, so any tenant could read, update,
or delete another tenant's project by guessing its numeric id. This module
covers the fix: a `tenant_id` column + tenant-scoped queries on every route.
"""

import json
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from quart import current_app


def _org_for_tenant(db, tenant_id: int, name: str = "Org") -> int:
    """Create an organization owned by the given tenant.

    `projects.organization_id` is a NOT NULL FK, so every project fixture
    needs a real organization row to point at.
    """
    now = datetime.now(timezone.utc)
    org_id = db.organizations.insert(
        name=f"{name} {uuid4().hex[:8]}",
        tenant_id=tenant_id,
        created_at=now,
        updated_at=now,
    )
    db.commit()
    return org_id


class TestProjectsTenantColumn:
    """Verify the `projects` table carries a `tenant_id` column."""

    @pytest.mark.asyncio
    async def test_projects_table_has_tenant_id(self, app):
        async with app.app_context():
            db = current_app.db
            assert (
                "tenant_id" in db.projects.table.columns
            ), "projects must have a tenant_id column"


class TestProjectsTenantIsolation:
    """Verify project reads/writes are scoped to the caller's tenant."""

    @pytest.mark.asyncio
    @patch("apps.api.auth.decorators.get_current_user")
    async def test_get_project_rejects_other_tenant(
        self, mock_get_user, async_client, generate_token, app
    ):
        mock_get_user.return_value = MagicMock(id=1, is_superuser=False)
        token = generate_token(tenant_id=1, scopes=["issues:read"])
        async with app.app_context():
            db = current_app.db
            now = datetime.now(timezone.utc)
            org_id = _org_for_tenant(db, tenant_id=2)
            pid = db.projects.insert(
                name="T2 proj",
                status="active",
                organization_id=org_id,
                tenant_id=2,
                created_at=now,
                updated_at=now,
            )
            db.commit()
        resp = await async_client.get(
            f"/api/v1/projects/{pid}",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 404

    @pytest.mark.asyncio
    @patch("apps.api.auth.decorators.get_current_user")
    async def test_get_project_same_tenant_succeeds(
        self, mock_get_user, async_client, generate_token, app
    ):
        mock_get_user.return_value = MagicMock(id=1, is_superuser=False)
        token = generate_token(tenant_id=1, scopes=["issues:read"])
        async with app.app_context():
            db = current_app.db
            now = datetime.now(timezone.utc)
            org_id = _org_for_tenant(db, tenant_id=1)
            pid = db.projects.insert(
                name="T1 proj",
                status="active",
                organization_id=org_id,
                tenant_id=1,
                created_at=now,
                updated_at=now,
            )
            db.commit()
        resp = await async_client.get(
            f"/api/v1/projects/{pid}",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        body = json.loads(await resp.get_data())
        assert body["name"] == "T1 proj"

    @pytest.mark.asyncio
    @patch("apps.api.auth.decorators.get_current_user")
    async def test_list_excludes_other_tenant(
        self, mock_get_user, async_client, generate_token, app
    ):
        mock_get_user.return_value = MagicMock(id=1, is_superuser=False)
        token = generate_token(tenant_id=1, scopes=["issues:read"])
        async with app.app_context():
            db = current_app.db
            now = datetime.now(timezone.utc)
            org1_id = _org_for_tenant(db, tenant_id=1)
            org2_id = _org_for_tenant(db, tenant_id=2)
            db.projects.insert(
                name="T1 list proj",
                status="active",
                organization_id=org1_id,
                tenant_id=1,
                created_at=now,
                updated_at=now,
            )
            db.projects.insert(
                name="T2 list proj",
                status="active",
                organization_id=org2_id,
                tenant_id=2,
                created_at=now,
                updated_at=now,
            )
            db.commit()
        resp = await async_client.get(
            "/api/v1/projects?per_page=100",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        names = [p["name"] for p in json.loads(await resp.get_data())["items"]]
        assert "T1 list proj" in names
        assert "T2 list proj" not in names, "tenant 1 must not see tenant 2 projects"

    @pytest.mark.asyncio
    @patch("apps.api.auth.decorators.get_current_user")
    async def test_update_project_rejects_other_tenant(
        self, mock_get_user, async_client, generate_token, app
    ):
        # is_superuser=True: bypasses a pre-existing, unrelated bug in
        # resource_role_required (apps/api/auth/decorators.py) whose
        # resource_type inference only recognizes "/entities/" and
        # "/organizations/" in request.path, so it 400s ("Unable to
        # determine resource type") for ANY non-superuser hitting
        # /api/v1/projects/<id> today, independent of tenant. That bug
        # predates this change and is out of scope for the IDOR fix; using
        # a superuser here isolates the assertion to the tenant-scoping
        # logic this task adds inside update_project itself.
        mock_get_user.return_value = MagicMock(id=1, is_superuser=True)
        token = generate_token(tenant_id=1, scopes=["issues:write"])
        async with app.app_context():
            db = current_app.db
            now = datetime.now(timezone.utc)
            org_id = _org_for_tenant(db, tenant_id=2)
            pid = db.projects.insert(
                name="T2 proj update",
                status="active",
                organization_id=org_id,
                tenant_id=2,
                created_at=now,
                updated_at=now,
            )
            db.commit()
        resp = await async_client.put(
            f"/api/v1/projects/{pid}",
            json={"name": "hijacked"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 404
        async with app.app_context():
            db = current_app.db
            row = db.projects[pid]
            assert row.name == "T2 proj update", "cross-tenant update must not succeed"

    @pytest.mark.asyncio
    @patch("apps.api.auth.decorators.get_current_user")
    async def test_delete_project_rejects_other_tenant(
        self, mock_get_user, async_client, generate_token, app
    ):
        # is_superuser=True: see comment in
        # test_update_project_rejects_other_tenant above -- bypasses the
        # same pre-existing resource_role_required resource_type-inference
        # bug, unrelated to tenant scoping.
        mock_get_user.return_value = MagicMock(id=1, is_superuser=True)
        token = generate_token(tenant_id=1, scopes=["issues:write"])
        async with app.app_context():
            db = current_app.db
            now = datetime.now(timezone.utc)
            org_id = _org_for_tenant(db, tenant_id=2)
            pid = db.projects.insert(
                name="T2 proj delete",
                status="active",
                organization_id=org_id,
                tenant_id=2,
                created_at=now,
                updated_at=now,
            )
            db.commit()
        resp = await async_client.delete(
            f"/api/v1/projects/{pid}",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 404
        async with app.app_context():
            db = current_app.db
            still_there = db.projects[pid]
            assert still_there is not None, "cross-tenant delete must not succeed"

    @pytest.mark.asyncio
    @patch("apps.api.auth.decorators.get_current_user")
    async def test_create_project_sets_caller_tenant(
        self, mock_get_user, async_client, generate_token, app
    ):
        mock_get_user.return_value = MagicMock(id=1, is_superuser=False)
        token = generate_token(tenant_id=1, scopes=["issues:write"])
        async with app.app_context():
            db = current_app.db
            now = datetime.now(timezone.utc)
            org_id = db.organizations.insert(
                name="Tenant1 Org",
                tenant_id=1,
                created_at=now,
                updated_at=now,
            )
            db.commit()
        resp = await async_client.post(
            "/api/v1/projects",
            json={"name": "New project", "organization_id": org_id},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 201, (await resp.get_data()).decode()[:200]
        pid = json.loads(await resp.get_data())["id"]
        async with app.app_context():
            db = current_app.db
            row = db.projects[pid]
            assert row.tenant_id == 1
