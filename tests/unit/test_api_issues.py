"""Unit tests for Issues API endpoints.

Regression coverage for issue update input normalization (status/priority
case handling) — see fix/issues-ungate-and-patch-case.
"""

import json
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

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
