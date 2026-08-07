"""Unit tests for Issues API endpoints.

Regression coverage for issue update input normalization (status/priority
case handling) — see fix/issues-ungate-and-patch-case.
"""

import json
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from quart import current_app


class TestIssuesAPI:
    """Test Issues API update-path input normalization."""

    @pytest.mark.asyncio
    @patch("apps.api.auth.decorators.get_current_user")
    async def test_patch_issue_with_lowercase_status(
        self, mock_get_user, async_client, generate_token, app
    ):
        """Regression: PATCH /issues/{id} with a lowercase status must normalize
        to the uppercase enum member and return 200, not 500.

        update_issue previously stored body.status raw while create_issue
        upper-cased it, so lowercase input (what the frontend sends) failed the
        DB enum column and raised an unhandled 500.
        """
        mock_user = MagicMock()
        mock_user.id = 1
        mock_user.is_superuser = True
        mock_get_user.return_value = mock_user

        token = generate_token(tenant_id=1, scopes=["issues:write"])

        async with app.app_context():
            db = current_app.db
            now = datetime.now(timezone.utc)

            org_id = db.organizations.insert(
                name="Test Org",
                tenant_id=1,
                created_at=now,
                updated_at=now,
            )
            issue_id = db.issues.insert(
                title="Test Issue",
                description="Test",
                status="OPEN",
                priority="MEDIUM",
                issue_type="OTHER",
                reporter_id=None,
                assignee_id=None,
                resource_type="organization",
                resource_id=org_id,
                is_incident=0,
                tenant_id=1,
                created_at=now,
                updated_at=now,
            )
            db.commit()

            response = await async_client.patch(
                f"/api/v1/issues/{issue_id}",
                json={"status": "in_progress", "priority": "high"},
                headers={"Authorization": f"Bearer {token}"},
            )

            assert response.status_code == 200, (
                f"Expected 200, got {response.status_code}: "
                f"{(await response.get_data()).decode()[:200]}"
            )
            data = json.loads(await response.get_data())
            assert data["status"] == "in_progress"
            assert data["priority"] == "high"

    @pytest.mark.asyncio
    @patch("apps.api.auth.decorators.get_current_user")
    async def test_patch_issue_close_sets_closed_at(
        self, mock_get_user, async_client, generate_token, app
    ):
        """PATCH /issues/{id} with a lowercase closing status sets closed_at."""
        mock_user = MagicMock()
        mock_user.id = 1
        mock_user.is_superuser = True
        mock_get_user.return_value = mock_user

        token = generate_token(tenant_id=1, scopes=["issues:write"])

        async with app.app_context():
            db = current_app.db
            now = datetime.now(timezone.utc)

            org_id = db.organizations.insert(
                name="Test Org Close",
                tenant_id=1,
                created_at=now,
                updated_at=now,
            )
            issue_id = db.issues.insert(
                title="Test Issue Close",
                description="Test",
                status="OPEN",
                priority="MEDIUM",
                issue_type="OTHER",
                reporter_id=None,
                assignee_id=None,
                resource_type="organization",
                resource_id=org_id,
                is_incident=0,
                closed_at=None,
                tenant_id=1,
                created_at=now,
                updated_at=now,
            )
            db.commit()

            response = await async_client.patch(
                f"/api/v1/issues/{issue_id}",
                json={"status": "closed"},
                headers={"Authorization": f"Bearer {token}"},
            )

            assert response.status_code == 200, (
                f"Expected 200, got {response.status_code}: "
                f"{(await response.get_data()).decode()[:200]}"
            )
            data = json.loads(await response.get_data())
            assert data["status"] == "closed"
            assert data.get("closed_at") is not None

    @pytest.mark.asyncio
    @patch("apps.api.auth.decorators.get_current_user")
    async def test_create_support_issue_urgent(
        self, mock_get_user, async_client, generate_token, app
    ):
        """POST /issues accepts issue_type="support" and priority="urgent".

        Support tickets are a type of issue (issues foundation task 3);
        urgent priority sits between high and critical.
        """
        mock_get_user.return_value = MagicMock(id=1, is_superuser=True)
        token = generate_token(tenant_id=1, scopes=["issues:write"])
        async with app.app_context():
            db = current_app.db
            now = datetime.now(timezone.utc)
            org_id = db.organizations.insert(
                name="Org", tenant_id=1, created_at=now, updated_at=now
            )
            db.commit()
        resp = await async_client.post(
            "/api/v1/issues",
            json={
                "title": "Cannot log in",
                "issue_type": "support",
                "priority": "urgent",
                "organization_id": org_id,
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 201, (await resp.get_data()).decode()[:200]
        data = json.loads(await resp.get_data())
        assert data["issue_type"] == "support"
        assert data["priority"] == "urgent"

    @pytest.mark.asyncio
    @patch("apps.api.auth.decorators.get_current_user")
    async def test_assign_issue_to_org_unit(
        self, mock_get_user, async_client, generate_token, app
    ):
        """PATCH /issues/{id} can assign an issue to an org unit (organizations.id).

        Polymorphic assignee (issues foundation task 4): assignee_type
        disambiguates whether assignee_id points at identities or
        organizations.
        """
        mock_get_user.return_value = MagicMock(id=1, is_superuser=True)
        token = generate_token(tenant_id=1, scopes=["issues:write"])
        async with app.app_context():
            db = current_app.db
            now = datetime.now(timezone.utc)
            ou_id = db.organizations.insert(
                name="Support Team",
                type="team",
                tenant_id=1,
                created_at=now,
                updated_at=now,
            )
            iid = db.issues.insert(
                title="Assign me",
                status="OPEN",
                priority="MEDIUM",
                issue_type="SUPPORT",
                is_incident=0,
                resource_type="organization",
                resource_id=ou_id,
                tenant_id=1,
                created_at=now,
                updated_at=now,
            )
            db.commit()
        resp = await async_client.patch(
            f"/api/v1/issues/{iid}",
            json={"assignee_type": "org_unit", "assignee_id": ou_id},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200, (await resp.get_data()).decode()[:200]
        data = json.loads(await resp.get_data())
        assert data["assignee_type"] == "org_unit"
        assert data["assignee_id"] == ou_id

    @pytest.mark.asyncio
    @patch("apps.api.auth.decorators.get_current_user")
    async def test_assign_issue_to_identity(
        self, mock_get_user, async_client, generate_token, app
    ):
        """PATCH /issues/{id} can assign an issue to an identity (identities.id).

        Explicit assignee_type="identity" is honored, and assignee_type is
        returned so callers can disambiguate the polymorphic assignee_id.
        """
        mock_get_user.return_value = MagicMock(id=1, is_superuser=True)
        token = generate_token(tenant_id=1, scopes=["issues:write"])
        async with app.app_context():
            db = current_app.db
            now = datetime.now(timezone.utc)
            unique_suffix = uuid4().hex[:8]
            identity_id = db.identities.insert(
                identity_type="human",
                username=f"assignee_user_{unique_suffix}",
                email=f"assignee_user_{unique_suffix}@example.com",
                tenant_id=1,
                auth_provider="local",
                is_active=True,
                is_superuser=False,
                mfa_enabled=False,
                must_change_password=False,
                portal_role="viewer",
                created_at=now,
                updated_at=now,
            )
            org_id = db.organizations.insert(
                name="Identity Assignee Org",
                tenant_id=1,
                created_at=now,
                updated_at=now,
            )
            iid = db.issues.insert(
                title="Assign me too",
                status="OPEN",
                priority="MEDIUM",
                issue_type="SUPPORT",
                is_incident=0,
                resource_type="organization",
                resource_id=org_id,
                tenant_id=1,
                created_at=now,
                updated_at=now,
            )
            db.commit()
        resp = await async_client.patch(
            f"/api/v1/issues/{iid}",
            json={"assignee_type": "identity", "assignee_id": identity_id},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200, (await resp.get_data()).decode()[:200]
        data = json.loads(await resp.get_data())
        assert data["assignee_type"] == "identity"
        assert data["assignee_id"] == identity_id

    @pytest.mark.asyncio
    @patch("apps.api.auth.decorators.get_current_user")
    async def test_support_fields_and_metadata(
        self, mock_get_user, async_client, generate_token, app
    ):
        """POST /issues persists+returns support fields (channel/category) and
        the universal metadata JSON bag (issues foundation task 5).
        """
        mock_get_user.return_value = MagicMock(id=1, is_superuser=True)
        token = generate_token(tenant_id=1, scopes=["issues:write"])
        async with app.app_context():
            db = current_app.db
            now = datetime.now(timezone.utc)
            org_id = db.organizations.insert(
                name="Org", tenant_id=1, created_at=now, updated_at=now
            )
            db.commit()
        resp = await async_client.post(
            "/api/v1/issues",
            json={
                "title": "Email in",
                "issue_type": "support",
                "priority": "high",
                "organization_id": org_id,
                "channel": "email",
                "category": "billing",
                "metadata": {"source_email": "cust@example.com"},
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 201, (await resp.get_data()).decode()[:200]
        data = json.loads(await resp.get_data())
        assert data["channel"] == "email"
        assert data["category"] == "billing"
        assert data["metadata"]["source_email"] == "cust@example.com"

    @pytest.mark.asyncio
    @patch("apps.api.auth.decorators.get_current_user")
    async def test_reporter_id_roundtrips(
        self, mock_get_user, async_client, generate_token, app
    ):
        """POST /issues then GET it back: reporter_id must equal the caller.

        Regression for issues foundation task 6: migration 001 created the
        physical `issues` columns as created_by_id/assigned_to_id, but the
        model has always used reporter_id/assignee_id. On a real database
        built by replaying Alembic history, a column-name mismatch would
        surface here as a 500 or a null reporter_id. The unit test DB is
        built via Base.metadata.create_all(), which already emits the
        model's column names, so this test is expected to pass without the
        migration 030 rename firing — it documents and guards the contract;
        migration 030's guarded rename is what fixes real (001-replayed) DBs.

        Uses caller id=1 (matching the other POST /issues tests in this
        file, e.g. test_create_support_issue_urgent): reporter_id carries a
        real FK to identities.id, and id=1 is the seeded default admin
        (shared/database/__init__.py) present on every fresh test DB. An
        arbitrary id with no matching identities row (e.g. 7) trips
        issues_reporter_id_fkey before the reconciliation this test guards
        is ever reached.
        """
        mock_get_user.return_value = MagicMock(id=1, is_superuser=True)
        token = generate_token(tenant_id=1, scopes=["issues:write"])
        async with app.app_context():
            db = current_app.db
            now = datetime.now(timezone.utc)
            org_id = db.organizations.insert(
                name="Org", tenant_id=1, created_at=now, updated_at=now
            )
            db.commit()
        resp = await async_client.post(
            "/api/v1/issues",
            json={"title": "R", "priority": "low", "organization_id": org_id},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 201, (await resp.get_data()).decode()[:200]
        data = json.loads(await resp.get_data())
        assert data["reporter_id"] == 1

        get_resp = await async_client.get(
            f"/api/v1/issues/{data['id']}",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert get_resp.status_code == 200, (await get_resp.get_data()).decode()[:200]
        get_data = json.loads(await get_resp.get_data())
        assert get_data["reporter_id"] == 1

    @pytest.mark.asyncio
    @patch("apps.api.auth.decorators.get_current_user")
    async def test_filter_by_status_lowercase(
        self, mock_get_user, async_client, generate_token, app
    ):
        """GET /issues?status=open returns issues with uppercase OPEN in DB.

        Regression: filter was comparing lowercase param to uppercase DB column,
        so ?status=open returned 0 results. Fix: .upper() the param before
        comparing to DB column.
        """
        mock_get_user.return_value = MagicMock(id=1, is_superuser=True)
        token = generate_token(tenant_id=1, scopes=["issues:read"])

        issue_title = f"Open Issue {uuid4().hex[:8]}"
        async with app.app_context():
            db = current_app.db
            now = datetime.now(timezone.utc)
            org_id = db.organizations.insert(
                name="Filter Test Org",
                tenant_id=1,
                created_at=now,
                updated_at=now,
            )
            # Create an issue with UPPERCASE status in DB
            db.issues.insert(
                title=issue_title,
                status="OPEN",
                priority="MEDIUM",
                issue_type="OTHER",
                reporter_id=None,
                assignee_id=None,
                resource_type="organization",
                resource_id=org_id,
                is_incident=0,
                tenant_id=1,
                created_at=now,
                updated_at=now,
            )
            db.commit()

        # Query with lowercase status param
        resp = await async_client.get(
            "/api/v1/issues?status=open",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        data = json.loads(await resp.get_data())
        # Should find the issue (not return empty list)
        assert data["total"] >= 1
        # Verify our specific issue is in results
        found = any(item["title"] == issue_title for item in data["items"])
        assert found, f"Issue {issue_title} not found in filtered results"
        # All returned items should have status lowercase
        assert all(item["status"] == "open" for item in data["items"])

        # Query with a different status should not include the open issue
        resp = await async_client.get(
            "/api/v1/issues?status=closed",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        data = json.loads(await resp.get_data())
        # Our open issue should not be in closed results
        found = any(item["title"] == issue_title for item in data["items"])
        assert not found, f"Open issue {issue_title} should not appear in closed filter"

    @pytest.mark.asyncio
    @patch("apps.api.auth.decorators.get_current_user")
    async def test_responses_return_lowercase_casing(
        self, mock_get_user, async_client, generate_token, app
    ):
        """GET/POST/PATCH /issues return status/priority/issue_type in lowercase.

        Regression: responses returned raw UPPERCASE from DB. Create and
        retrieve an issue, verify response fields are lowercase.
        """
        mock_get_user.return_value = MagicMock(id=1, is_superuser=True)
        token = generate_token(tenant_id=1, scopes=["issues:write", "issues:read"])

        async with app.app_context():
            db = current_app.db
            now = datetime.now(timezone.utc)
            org_id = db.organizations.insert(
                name="Case Test Org",
                tenant_id=1,
                created_at=now,
                updated_at=now,
            )
            db.commit()

        # POST: create issue response returns lowercase
        create_resp = await async_client.post(
            "/api/v1/issues",
            json={
                "title": "Case Test",
                "status": "open",
                "priority": "high",
                "issue_type": "support",
                "organization_id": org_id,
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert create_resp.status_code == 201
        create_data = json.loads(await create_resp.get_data())
        assert create_data["status"] == "open"
        assert create_data["priority"] == "high"
        assert create_data["issue_type"] == "support"
        issue_id = create_data["id"]

        # GET: retrieve issue response returns lowercase
        get_resp = await async_client.get(
            f"/api/v1/issues/{issue_id}",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert get_resp.status_code == 200
        get_data = json.loads(await get_resp.get_data())
        assert get_data["status"] == "open"
        assert get_data["priority"] == "high"
        assert get_data["issue_type"] == "support"

    @pytest.mark.asyncio
    @patch("apps.api.auth.decorators.get_current_user")
    async def test_patch_issue_type_persists(
        self, mock_get_user, async_client, generate_token, app
    ):
        """PATCH /issues/{id} with issue_type persists and returns it.

        Regression: UpdateIssueRequest accepted issue_type but the update
        closure had no block to write it to the DB. Fix: add issue_type
        block in update_fields, .upper() before persist.
        """
        mock_get_user.return_value = MagicMock(id=1, is_superuser=True)
        token = generate_token(tenant_id=1, scopes=["issues:write", "issues:read"])

        async with app.app_context():
            db = current_app.db
            now = datetime.now(timezone.utc)
            org_id = db.organizations.insert(
                name="Issue Type Test Org",
                tenant_id=1,
                created_at=now,
                updated_at=now,
            )
            issue_id = db.issues.insert(
                title="Type Change",
                status="OPEN",
                priority="MEDIUM",
                issue_type="OTHER",
                reporter_id=None,
                assignee_id=None,
                resource_type="organization",
                resource_id=org_id,
                is_incident=0,
                tenant_id=1,
                created_at=now,
                updated_at=now,
            )
            db.commit()

        # PATCH: update issue_type
        patch_resp = await async_client.patch(
            f"/api/v1/issues/{issue_id}",
            json={"issue_type": "support"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert patch_resp.status_code == 200
        patch_data = json.loads(await patch_resp.get_data())
        assert patch_data["issue_type"] == "support"

        # GET: verify the new type persisted
        get_resp = await async_client.get(
            f"/api/v1/issues/{issue_id}",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert get_resp.status_code == 200
        get_data = json.loads(await get_resp.get_data())
        assert get_data["issue_type"] == "support"
