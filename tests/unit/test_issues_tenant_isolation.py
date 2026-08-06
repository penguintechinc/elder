"""Tests for tenant scoping on the `issues` table (issues foundation, tasks 1-2)."""

import json
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from quart import current_app


class TestIssuesTenantColumn:
    """Verify the `issues` table carries a `tenant_id` column."""

    @pytest.mark.asyncio
    async def test_issues_table_has_tenant_id(self, app):
        """`issues` must have a `tenant_id` column for tenant scoping.

        The installed penguin-dal `TableProxy` exposes columns via attribute
        access (`db.issues.tenant_id`) and the underlying SQLAlchemy `Table`
        via the `.table` property -- it has no pydal-style `.fields` list, so
        column presence is checked against `db.issues.table.columns`.
        """
        async with app.app_context():
            db = current_app.db
            assert (
                "tenant_id" in db.issues.table.columns
            ), "issues must have a tenant_id column"


def _foreign_tenant(db) -> int:
    """Create and return a second tenant distinct from tenant 1.

    `issues.tenant_id` carries a real FK to `tenants.id`, so cross-tenant
    fixtures must insert an actual tenant row rather than hardcode a literal
    id (mirrors the helpdesk isolation tests' pattern).
    """
    tid = db.tenants.insert(
        name="Other Tenant", slug=f"other-{uuid4().hex[:8]}", is_active=True
    )
    db.commit()
    return tid


def _other_tenant_issue(db, title: str = "T2") -> int:
    """Create an issue owned by a freshly created (non-1) tenant.

    Shared fixture for task-7 sub-resource isolation tests: each of them only
    needs a single issue that belongs to a tenant other than the caller's, to
    verify the parent-issue tenant gate rejects it before the sub-resource is
    ever touched.
    """
    other_tenant_id = _foreign_tenant(db)
    now = datetime.now(timezone.utc)
    issue_id = db.issues.insert(
        title=title,
        status="OPEN",
        priority="LOW",
        issue_type="OTHER",
        is_incident=0,
        resource_type="organization",
        resource_id=1,
        tenant_id=other_tenant_id,
        created_at=now,
        updated_at=now,
    )
    db.commit()
    return issue_id


class TestIssuesTenantIsolation:
    """Verify issue reads/writes are scoped to the caller's tenant (task 2)."""

    @pytest.mark.asyncio
    @patch("apps.api.auth.decorators.get_current_user")
    async def test_list_excludes_other_tenant(
        self, mock_get_user, async_client, generate_token, app
    ):
        mock_get_user.return_value = MagicMock(id=1, is_superuser=False)
        token = generate_token(tenant_id=1, scopes=["issues:read"])
        async with app.app_context():
            db = current_app.db
            other_tenant_id = _foreign_tenant(db)
            now = datetime.now(timezone.utc)
            db.issues.insert(
                title="T1 issue",
                status="OPEN",
                priority="MEDIUM",
                issue_type="OTHER",
                is_incident=0,
                resource_type="organization",
                resource_id=1,
                tenant_id=1,
                created_at=now,
                updated_at=now,
            )
            db.issues.insert(
                title="T2 issue",
                status="OPEN",
                priority="MEDIUM",
                issue_type="OTHER",
                is_incident=0,
                resource_type="organization",
                resource_id=1,
                tenant_id=other_tenant_id,
                created_at=now,
                updated_at=now,
            )
            db.commit()
        resp = await async_client.get(
            "/api/v1/issues?per_page=100",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        titles = [i["title"] for i in json.loads(await resp.get_data())["items"]]
        assert "T1 issue" in titles
        assert "T2 issue" not in titles, "tenant 1 must not see tenant 2 issues"

    @pytest.mark.asyncio
    @patch("apps.api.auth.decorators.get_current_user")
    async def test_get_issue_excludes_other_tenant(
        self, mock_get_user, async_client, generate_token, app
    ):
        """GET /issues/<id> for another tenant's issue must 404, not leak it."""
        mock_get_user.return_value = MagicMock(id=1, is_superuser=False)
        token = generate_token(tenant_id=1, scopes=["issues:read"])
        async with app.app_context():
            db = current_app.db
            other_tenant_id = _foreign_tenant(db)
            now = datetime.now(timezone.utc)
            other_issue_id = db.issues.insert(
                title="T2 only issue",
                status="OPEN",
                priority="MEDIUM",
                issue_type="OTHER",
                is_incident=0,
                resource_type="organization",
                resource_id=1,
                tenant_id=other_tenant_id,
                created_at=now,
                updated_at=now,
            )
            db.commit()
        resp = await async_client.get(
            f"/api/v1/issues/{other_issue_id}",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 404

    @pytest.mark.asyncio
    @patch("apps.api.auth.decorators.get_current_user")
    async def test_update_issue_excludes_other_tenant(
        self, mock_get_user, async_client, generate_token, app
    ):
        """PATCH /issues/<id> for another tenant's issue must 404, not modify it."""
        mock_get_user.return_value = MagicMock(id=1, is_superuser=False)
        token = generate_token(tenant_id=1, scopes=["issues:write"])
        async with app.app_context():
            db = current_app.db
            other_tenant_id = _foreign_tenant(db)
            now = datetime.now(timezone.utc)
            other_issue_id = db.issues.insert(
                title="T2 only issue",
                status="OPEN",
                priority="MEDIUM",
                issue_type="OTHER",
                is_incident=0,
                resource_type="organization",
                resource_id=1,
                tenant_id=other_tenant_id,
                created_at=now,
                updated_at=now,
            )
            db.commit()
        resp = await async_client.patch(
            f"/api/v1/issues/{other_issue_id}",
            json={"status": "closed"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 404

    @pytest.mark.asyncio
    @patch("apps.api.auth.decorators.get_current_user")
    async def test_delete_issue_excludes_other_tenant(
        self, mock_get_user, async_client, generate_token, app
    ):
        """DELETE /issues/<id> for another tenant's issue must 404, not delete it."""
        mock_get_user.return_value = MagicMock(id=1, is_superuser=False)
        token = generate_token(tenant_id=1, scopes=["issues:write"])
        async with app.app_context():
            db = current_app.db
            other_tenant_id = _foreign_tenant(db)
            now = datetime.now(timezone.utc)
            other_issue_id = db.issues.insert(
                title="T2 only issue",
                status="OPEN",
                priority="MEDIUM",
                issue_type="OTHER",
                is_incident=0,
                resource_type="organization",
                resource_id=1,
                tenant_id=other_tenant_id,
                created_at=now,
                updated_at=now,
            )
            db.commit()
        resp = await async_client.delete(
            f"/api/v1/issues/{other_issue_id}",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 404
        async with app.app_context():
            db = current_app.db
            still_there = db.issues[other_issue_id]
            assert still_there is not None, "cross-tenant delete must not succeed"

    @pytest.mark.asyncio
    @patch("apps.api.auth.decorators.get_current_user")
    async def test_create_issue_sets_caller_tenant(
        self, mock_get_user, async_client, generate_token, app
    ):
        """POST /issues must stamp the created row with the caller's tenant."""
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
            "/api/v1/issues",
            json={"title": "New issue", "organization_id": org_id},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 201, (await resp.get_data()).decode()[:200]
        issue_id = json.loads(await resp.get_data())["id"]
        async with app.app_context():
            db = current_app.db
            row = db.issues[issue_id]
            assert row.tenant_id == 1


class TestIssueSubResourceTenantIsolation:
    """Every issue sub-resource lookup must be gated by the parent issue's
    tenant (task 7) -- comments, labels, entity links, and project/milestone
    linking all resolved the issue via a bare `db.issues[id]` bracket lookup
    with no tenant filter, so a tenant-1 caller could read or mutate a
    tenant-2 issue's sub-resources given only its numeric id.
    """

    @pytest.mark.asyncio
    @patch("apps.api.auth.decorators.get_current_user")
    async def test_comments_scoped_by_issue_tenant(
        self, mock_get_user, async_client, generate_token, app
    ):
        """GET /issues/<id>/comments for another tenant's issue must 404."""
        mock_get_user.return_value = MagicMock(id=1, is_superuser=False)
        token = generate_token(tenant_id=1, scopes=["issues:read"])
        async with app.app_context():
            db = current_app.db
            iid = _other_tenant_issue(db, title="T2 comments")
        resp = await async_client.get(
            f"/api/v1/issues/{iid}/comments",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 404

    @pytest.mark.asyncio
    @patch("apps.api.auth.decorators.get_current_user")
    async def test_labels_scoped_by_issue_tenant(
        self, mock_get_user, async_client, generate_token, app
    ):
        """GET /issues/<id>/labels for another tenant's issue must 404."""
        mock_get_user.return_value = MagicMock(id=1, is_superuser=False)
        token = generate_token(tenant_id=1, scopes=["issues:read"])
        async with app.app_context():
            db = current_app.db
            iid = _other_tenant_issue(db, title="T2 labels")
        resp = await async_client.get(
            f"/api/v1/issues/{iid}/labels",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 404

    @pytest.mark.asyncio
    @patch("apps.api.auth.decorators.get_current_user")
    async def test_add_label_scoped_by_issue_tenant(
        self, mock_get_user, async_client, generate_token, app
    ):
        """POST /issues/<id>/labels against another tenant's issue must 404."""
        mock_get_user.return_value = MagicMock(id=1, is_superuser=False)
        token = generate_token(tenant_id=1, scopes=["issues:write"])
        async with app.app_context():
            db = current_app.db
            iid = _other_tenant_issue(db, title="T2 add-label")
        resp = await async_client.post(
            f"/api/v1/issues/{iid}/labels",
            json={"label_id": 1},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 404

    @pytest.mark.asyncio
    @patch("apps.api.auth.decorators.get_current_user")
    async def test_entity_links_scoped_by_issue_tenant(
        self, mock_get_user, async_client, generate_token, app
    ):
        """GET /issues/<id>/links for another tenant's issue must 404."""
        mock_get_user.return_value = MagicMock(id=1, is_superuser=False)
        token = generate_token(tenant_id=1, scopes=["issues:read"])
        async with app.app_context():
            db = current_app.db
            iid = _other_tenant_issue(db, title="T2 links")
        resp = await async_client.get(
            f"/api/v1/issues/{iid}/links",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 404

    @pytest.mark.asyncio
    @patch("apps.api.auth.decorators.get_current_user")
    async def test_create_entity_link_scoped_by_issue_tenant(
        self, mock_get_user, async_client, generate_token, app
    ):
        """POST /issues/<id>/links against another tenant's issue must 404."""
        mock_get_user.return_value = MagicMock(id=1, is_superuser=False)
        token = generate_token(tenant_id=1, scopes=["issues:write"])
        async with app.app_context():
            db = current_app.db
            iid = _other_tenant_issue(db, title="T2 create-link")
        resp = await async_client.post(
            f"/api/v1/issues/{iid}/links",
            json={"entity_id": 1},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 404

    @pytest.mark.asyncio
    @patch("apps.api.auth.decorators.get_current_user")
    async def test_link_project_scoped_by_issue_tenant(
        self, mock_get_user, async_client, generate_token, app
    ):
        """POST /issues/<id>/projects against another tenant's issue must 404."""
        mock_get_user.return_value = MagicMock(id=1, is_superuser=False)
        token = generate_token(tenant_id=1, scopes=["issues:write"])
        async with app.app_context():
            db = current_app.db
            iid = _other_tenant_issue(db, title="T2 link-project")
        resp = await async_client.post(
            f"/api/v1/issues/{iid}/projects",
            json={"project_id": 1},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 404

    @pytest.mark.asyncio
    @patch("apps.api.auth.decorators.get_current_user")
    async def test_link_milestone_scoped_by_issue_tenant(
        self, mock_get_user, async_client, generate_token, app
    ):
        """POST /issues/<id>/milestones against another tenant's issue must 404."""
        mock_get_user.return_value = MagicMock(id=1, is_superuser=False)
        token = generate_token(tenant_id=1, scopes=["issues:write"])
        async with app.app_context():
            db = current_app.db
            iid = _other_tenant_issue(db, title="T2 link-milestone")
        resp = await async_client.post(
            f"/api/v1/issues/{iid}/milestones",
            json={"milestone_id": 1},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 404
