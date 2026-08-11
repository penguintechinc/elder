"""Tests for tenant scoping on the `projects` table (universal-audits, task 1).

`projects` had no `tenant_id` and every lookup in `projects.py` was an
unscoped `db.projects[id]` bracket lookup, so any tenant could read, update,
or delete another tenant's project by guessing its numeric id. This module
covers the fix: a `tenant_id` column + tenant-scoped queries on every route.
"""

import json
from datetime import UTC, datetime, timezone
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from quart import current_app


@pytest.fixture(scope="function", autouse=True)
def _ensure_tenant_2_exists(app):
    """Guarantee a tenant row with id=2 exists before each test runs.

    Every test in this module references a second tenant by the literal id
    `2` (organizations/projects/milestones all carry a real FK to
    `tenants.id`) rather than creating one per test. Tenant id 1 ("Default")
    is auto-created by `shared.database`'s default-tenant bootstrap on app
    init, so historically tenant id 2 only existed by accident -- whichever
    other test file happened to run first in a full-suite session and
    called `db.tenants.insert()` for its own fixtures. Against a freshly
    reset database (or a targeted run of just this module), nothing else
    creates it, so this fixture creates it explicitly. It is idempotent
    (checked, not assumed) so it is a no-op in a full-suite run where
    pollution from earlier files already produced a tenant with id 2.
    """
    if not app.config.get("TESTING"):
        yield
        return

    try:
        from quart import current_app as ctx_app

        async def ensure():
            async with app.app_context():
                db = ctx_app.db
                if not db(db.tenants.id == 2).select().first():
                    now = datetime.now(UTC)
                    db.tenants.insert(
                        name="Tenant 2",
                        slug=f"tenant-2-{uuid4().hex[:8]}",
                        is_active=True,
                        created_at=now,
                        updated_at=now,
                    )
                    db.commit()

        import asyncio

        try:
            loop = asyncio.get_event_loop()
            if loop.is_closed():
                raise RuntimeError("Loop is closed")
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

        loop.run_until_complete(ensure())
    except Exception:
        # Mirrors conftest.enable_helpdesk_module: best-effort seeding, never
        # fails the test session if the DB is unavailable for some reason.
        pass

    yield


def _org_for_tenant(db, tenant_id: int, name: str = "Org") -> int:
    """Create an organization owned by the given tenant.

    `projects.organization_id` is a NOT NULL FK, so every project fixture
    needs a real organization row to point at.
    """
    now = datetime.now(UTC)
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
            now = datetime.now(UTC)
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
            now = datetime.now(UTC)
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
            now = datetime.now(UTC)
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
            now = datetime.now(UTC)
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
            now = datetime.now(UTC)
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
            now = datetime.now(UTC)
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


class TestMilestonesTenantColumn:
    """Verify the `milestones` table carries a `tenant_id` column."""

    @pytest.mark.asyncio
    async def test_milestones_table_has_tenant_id(self, app):
        async with app.app_context():
            db = current_app.db
            assert (
                "tenant_id" in db.milestones.table.columns
            ), "milestones must have a tenant_id column"


class TestMilestonesTenantIsolation:
    """Verify milestone reads/writes are scoped to the caller's tenant.

    `milestones` had no `tenant_id` and every lookup in `milestones.py` was
    an unscoped `db.milestones[id]` bracket lookup, so any tenant could
    read, update, or delete another tenant's milestone by guessing its
    numeric id. This class covers the fix.
    """

    @pytest.mark.asyncio
    @patch("apps.api.auth.decorators.get_current_user")
    async def test_get_milestone_rejects_other_tenant(
        self, mock_get_user, async_client, generate_token, app
    ):
        mock_get_user.return_value = MagicMock(id=1, is_superuser=False)
        token = generate_token(tenant_id=1, scopes=["issues:read"])
        async with app.app_context():
            db = current_app.db
            now = datetime.now(UTC)
            org_id = _org_for_tenant(db, tenant_id=2)
            mid = db.milestones.insert(
                title="T2 milestone",
                status="open",
                organization_id=org_id,
                tenant_id=2,
                created_at=now,
                updated_at=now,
            )
            db.commit()
        resp = await async_client.get(
            f"/api/v1/milestones/{mid}",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 404

    @pytest.mark.asyncio
    @patch("apps.api.auth.decorators.get_current_user")
    async def test_get_milestone_same_tenant_succeeds(
        self, mock_get_user, async_client, generate_token, app
    ):
        mock_get_user.return_value = MagicMock(id=1, is_superuser=False)
        token = generate_token(tenant_id=1, scopes=["issues:read"])
        async with app.app_context():
            db = current_app.db
            now = datetime.now(UTC)
            org_id = _org_for_tenant(db, tenant_id=1)
            mid = db.milestones.insert(
                title="T1 milestone",
                status="open",
                organization_id=org_id,
                tenant_id=1,
                created_at=now,
                updated_at=now,
            )
            db.commit()
        resp = await async_client.get(
            f"/api/v1/milestones/{mid}",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        body = json.loads(await resp.get_data())
        assert body["title"] == "T1 milestone"

    @pytest.mark.asyncio
    @patch("apps.api.auth.decorators.get_current_user")
    async def test_list_milestones_excludes_other_tenant(
        self, mock_get_user, async_client, generate_token, app
    ):
        mock_get_user.return_value = MagicMock(id=1, is_superuser=False)
        token = generate_token(tenant_id=1, scopes=["issues:read"])
        async with app.app_context():
            db = current_app.db
            now = datetime.now(UTC)
            org1_id = _org_for_tenant(db, tenant_id=1)
            org2_id = _org_for_tenant(db, tenant_id=2)
            db.milestones.insert(
                title="T1 list milestone",
                status="open",
                organization_id=org1_id,
                tenant_id=1,
                created_at=now,
                updated_at=now,
            )
            db.milestones.insert(
                title="T2 list milestone",
                status="open",
                organization_id=org2_id,
                tenant_id=2,
                created_at=now,
                updated_at=now,
            )
            db.commit()
        resp = await async_client.get(
            "/api/v1/milestones?per_page=100",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        titles = [m["title"] for m in json.loads(await resp.get_data())["items"]]
        assert "T1 list milestone" in titles
        assert (
            "T2 list milestone" not in titles
        ), "tenant 1 must not see tenant 2 milestones"

    @pytest.mark.asyncio
    @patch("apps.api.auth.decorators.get_current_user")
    async def test_update_milestone_rejects_other_tenant(
        self, mock_get_user, async_client, generate_token, app
    ):
        # is_superuser=True: see comment in
        # TestProjectsTenantIsolation.test_update_project_rejects_other_tenant
        # -- bypasses the same pre-existing resource_role_required
        # resource_type-inference bug, unrelated to tenant scoping.
        mock_get_user.return_value = MagicMock(id=1, is_superuser=True)
        token = generate_token(tenant_id=1, scopes=["issues:write"])
        async with app.app_context():
            db = current_app.db
            now = datetime.now(UTC)
            org_id = _org_for_tenant(db, tenant_id=2)
            mid = db.milestones.insert(
                title="T2 milestone update",
                status="open",
                organization_id=org_id,
                tenant_id=2,
                created_at=now,
                updated_at=now,
            )
            db.commit()
        resp = await async_client.put(
            f"/api/v1/milestones/{mid}",
            json={"title": "hijacked"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 404
        async with app.app_context():
            db = current_app.db
            row = db.milestones[mid]
            assert (
                row.title == "T2 milestone update"
            ), "cross-tenant update must not succeed"

    @pytest.mark.asyncio
    @patch("apps.api.auth.decorators.get_current_user")
    async def test_delete_milestone_rejects_other_tenant(
        self, mock_get_user, async_client, generate_token, app
    ):
        # is_superuser=True: see comment above -- bypasses the same
        # pre-existing resource_role_required bug, unrelated to tenant
        # scoping.
        mock_get_user.return_value = MagicMock(id=1, is_superuser=True)
        token = generate_token(tenant_id=1, scopes=["issues:write"])
        async with app.app_context():
            db = current_app.db
            now = datetime.now(UTC)
            org_id = _org_for_tenant(db, tenant_id=2)
            mid = db.milestones.insert(
                title="T2 milestone delete",
                status="open",
                organization_id=org_id,
                tenant_id=2,
                created_at=now,
                updated_at=now,
            )
            db.commit()
        resp = await async_client.delete(
            f"/api/v1/milestones/{mid}",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 404
        async with app.app_context():
            db = current_app.db
            still_there = db.milestones[mid]
            assert still_there is not None, "cross-tenant delete must not succeed"

    @pytest.mark.asyncio
    @patch("apps.api.auth.decorators.get_current_user")
    async def test_create_milestone_sets_caller_tenant(
        self, mock_get_user, async_client, generate_token, app
    ):
        mock_get_user.return_value = MagicMock(id=1, is_superuser=False)
        token = generate_token(tenant_id=1, scopes=["issues:write"])
        async with app.app_context():
            db = current_app.db
            org_id = _org_for_tenant(db, tenant_id=1)
        resp = await async_client.post(
            "/api/v1/milestones",
            json={"title": "New milestone", "organization_id": org_id},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 201, (await resp.get_data()).decode()[:200]
        mid = json.loads(await resp.get_data())["id"]
        async with app.app_context():
            db = current_app.db
            row = db.milestones[mid]
            assert row.tenant_id == 1


class TestMilestoneIssuesTenantIsolation:
    """Verify `GET /milestones/<id>/issues` does not leak cross-tenant data.

    Covers both the milestone lookup itself (404 on a foreign/missing
    milestone) and the returned issue list (filtered to the caller's
    tenant even if the link table were ever populated cross-tenant).
    """

    @pytest.mark.asyncio
    @patch("apps.api.auth.decorators.get_current_user")
    async def test_milestone_issues_404_on_other_tenant_milestone(
        self, mock_get_user, async_client, generate_token, app
    ):
        mock_get_user.return_value = MagicMock(id=1, is_superuser=False)
        token = generate_token(tenant_id=1, scopes=["issues:read"])
        async with app.app_context():
            db = current_app.db
            now = datetime.now(UTC)
            org_id = _org_for_tenant(db, tenant_id=2)
            mid = db.milestones.insert(
                title="T2 ms",
                status="open",
                organization_id=org_id,
                tenant_id=2,
                created_at=now,
                updated_at=now,
            )
            db.commit()
        resp = await async_client.get(
            f"/api/v1/milestones/{mid}/issues",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 404

    @pytest.mark.asyncio
    @patch("apps.api.auth.decorators.get_current_user")
    async def test_milestone_issues_404_on_missing_milestone(
        self, mock_get_user, async_client, generate_token, app
    ):
        mock_get_user.return_value = MagicMock(id=1, is_superuser=False)
        token = generate_token(tenant_id=1, scopes=["issues:read"])
        resp = await async_client.get(
            "/api/v1/milestones/999999/issues",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 404

    @pytest.mark.asyncio
    @patch("apps.api.auth.decorators.get_current_user")
    async def test_milestone_issues_same_tenant_excludes_other_tenant_issues(
        self, mock_get_user, async_client, generate_token, app
    ):
        """Same-tenant milestone + its issues return 200 with only this
        tenant's issues, even if the link table were ever cross-populated.
        """
        mock_get_user.return_value = MagicMock(id=1, is_superuser=False)
        token = generate_token(tenant_id=1, scopes=["issues:read"])
        async with app.app_context():
            db = current_app.db
            now = datetime.now(UTC)
            org1_id = _org_for_tenant(db, tenant_id=1)
            org2_id = _org_for_tenant(db, tenant_id=2)

            mid = db.milestones.insert(
                title="T1 ms with issues",
                status="open",
                organization_id=org1_id,
                tenant_id=1,
                created_at=now,
                updated_at=now,
            )

            own_issue_id = db.issues.insert(
                title="T1 own issue",
                description="Test",
                status="OPEN",
                priority="MEDIUM",
                issue_type="OTHER",
                reporter_id=None,
                assignee_id=None,
                resource_type="organization",
                resource_id=org1_id,
                is_incident=0,
                tenant_id=1,
                created_at=now,
                updated_at=now,
            )
            foreign_issue_id = db.issues.insert(
                title="T2 foreign issue",
                description="Test",
                status="OPEN",
                priority="MEDIUM",
                issue_type="OTHER",
                reporter_id=None,
                assignee_id=None,
                resource_type="organization",
                resource_id=org2_id,
                is_incident=0,
                tenant_id=2,
                created_at=now,
                updated_at=now,
            )
            db.issue_milestone_links.insert(
                issue_id=own_issue_id, milestone_id=mid, created_at=now
            )
            # Simulate a cross-tenant link ever existing (defense in depth):
            # the response must still exclude it even though the link row
            # itself points a tenant-2 issue at a tenant-1 milestone.
            db.issue_milestone_links.insert(
                issue_id=foreign_issue_id, milestone_id=mid, created_at=now
            )
            db.commit()

        resp = await async_client.get(
            f"/api/v1/milestones/{mid}/issues",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        titles = [i["title"] for i in json.loads(await resp.get_data())["issues"]]
        assert "T1 own issue" in titles
        assert (
            "T2 foreign issue" not in titles
        ), "tenant 1 must not see tenant 2's issue via a shared milestone link"


class TestLinkIssueToProjectTenantIsolation:
    """Verify `POST /issues/<id>/projects` rejects cross-tenant project links.

    `link_issue_to_project` used to look up the target project with an
    unscoped `db.projects[body.project_id]` bracket lookup, so a caller in
    tenant 1 could confirm (and link to) a tenant-2 project by guessing its
    numeric id -- a cross-tenant IDOR oracle. The fix scopes the lookup to
    `(db.projects.id == project_id) & (db.projects.tenant_id == tenant_id)`.
    """

    @pytest.mark.asyncio
    @patch("apps.api.auth.decorators.get_current_user")
    async def test_link_issue_to_project_rejects_other_tenant(
        self, mock_get_user, async_client, generate_token, app
    ):
        mock_get_user.return_value = MagicMock(id=1, is_superuser=True)
        token = generate_token(tenant_id=1, scopes=["issues:write"])
        async with app.app_context():
            db = current_app.db
            now = datetime.now(UTC)
            org1_id = _org_for_tenant(db, tenant_id=1)
            org2_id = _org_for_tenant(db, tenant_id=2)

            issue_id = db.issues.insert(
                title="T1 issue for link",
                description="Test",
                status="OPEN",
                priority="MEDIUM",
                issue_type="OTHER",
                reporter_id=None,
                assignee_id=None,
                resource_type="organization",
                resource_id=org1_id,
                is_incident=0,
                tenant_id=1,
                created_at=now,
                updated_at=now,
            )
            other_project_id = db.projects.insert(
                name="T2 proj for link",
                status="active",
                organization_id=org2_id,
                tenant_id=2,
                created_at=now,
                updated_at=now,
            )
            db.commit()

        resp = await async_client.post(
            f"/api/v1/issues/{issue_id}/projects",
            json={"project_id": other_project_id},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 404

        async with app.app_context():
            db = current_app.db
            link = (
                db(
                    (db.issue_project_links.issue_id == issue_id)
                    & (db.issue_project_links.project_id == other_project_id)
                )
                .select()
                .first()
            )
            assert link is None, "cross-tenant link must not be created"
