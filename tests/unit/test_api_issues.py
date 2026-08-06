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
            assert data["status"] == "IN_PROGRESS"
            assert data["priority"] == "HIGH"

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
            assert data["status"] == "CLOSED"
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
        assert data["issue_type"] == "SUPPORT"
        assert data["priority"] == "URGENT"

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
