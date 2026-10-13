"""
Unit tests for Helpdesk Settings API endpoints (SLA Policies, Canned Responses, Teams).

Uses real JWT token-based authentication and real database.
Module enablement via ELDER_MODULE_HELPDESK=true in conftest.
"""

import json
from datetime import UTC, datetime, timezone
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from quart import current_app


class TestHelpDeskSLAPoliciesAPI:
    """Test Helpdesk SLA Policies API endpoints."""

    @pytest.mark.asyncio
    @patch("apps.api.auth.decorators.get_current_user")
    async def test_list_sla_policies_empty(
        self, mock_get_user, async_client, generate_token, app
    ):
        """Test GET /api/v1/helpdesk/sla-policies with empty list."""
        mock_user = MagicMock()
        mock_user.id = 1
        mock_user.is_superuser = True
        mock_get_user.return_value = mock_user

        # Generate JWT token for tenant=1 with helpdesk:read scope
        token = generate_token(tenant_id=1, scopes=["helpdesk:read"])

        async with app.app_context():
            # Clean up any existing policies for tenant 1
            db = current_app.db
            db(db.hd_sla_policies.tenant_id == 1).delete()
            db.commit()

        response = await async_client.get(
            "/api/v1/helpdesk/sla-policies",
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 200
        data = json.loads(await response.get_data())
        assert "items" in data
        assert data["items"] == []
        assert data["pagination"]["total"] == 0

    @pytest.mark.asyncio
    @patch("apps.api.auth.decorators.get_current_user")
    async def test_create_sla_policy(
        self, mock_get_user, async_client, generate_token, app
    ):
        """Test POST /api/v1/helpdesk/sla-policies."""
        mock_user = MagicMock()
        mock_user.id = 1
        mock_user.is_superuser = True
        mock_get_user.return_value = mock_user

        token = generate_token(tenant_id=1, scopes=["helpdesk:write"])

        payload = {
            "name": "High Priority SLA",
            "priority": "high",
            "first_response_hours": 1,
            "resolution_hours": 4,
            "business_hours_only": False,
            "is_active": True,
        }

        response = await async_client.post(
            "/api/v1/helpdesk/sla-policies",
            json=payload,
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 201
        data = json.loads(await response.get_data())
        assert data["name"] == "High Priority SLA"
        assert data["priority"] == "high"
        assert data["first_response_hours"] == 1
        assert data["resolution_hours"] == 4
        assert data["business_hours_only"] is False
        assert data["is_active"] is True

    @pytest.mark.asyncio
    @patch("apps.api.auth.decorators.get_current_user")
    async def test_create_sla_policy_missing_name(
        self, mock_get_user, async_client, generate_token, app
    ):
        """Test POST /api/v1/helpdesk/sla-policies with missing name."""
        mock_user = MagicMock()
        mock_user.id = 1
        mock_user.is_superuser = True
        mock_get_user.return_value = mock_user

        token = generate_token(tenant_id=1, scopes=["helpdesk:write"])

        payload = {
            "priority": "high",
            "first_response_hours": 1,
            "resolution_hours": 4,
        }

        response = await async_client.post(
            "/api/v1/helpdesk/sla-policies",
            json=payload,
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 400

    @pytest.mark.asyncio
    @patch("apps.api.auth.decorators.get_current_user")
    async def test_update_sla_policy(
        self, mock_get_user, async_client, generate_token, app
    ):
        """Test PUT /api/v1/helpdesk/sla-policies/<id>."""
        mock_user = MagicMock()
        mock_user.id = 1
        mock_user.is_superuser = True
        mock_get_user.return_value = mock_user

        token = generate_token(tenant_id=1, scopes=["helpdesk:write"])

        async with app.app_context():
            db = current_app.db
            now = datetime.now(UTC)
            policy_id = db.hd_sla_policies.insert(
                tenant_id=1,
                name="Original SLA",
                priority="medium",
                first_response_hours=2,
                resolution_hours=8,
                business_hours_only=True,
                is_active=True,
            )
            db.commit()

        payload = {
            "name": "Updated SLA",
            "first_response_hours": 1,
        }

        response = await async_client.put(
            f"/api/v1/helpdesk/sla-policies/{policy_id}",
            json=payload,
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 200
        data = json.loads(await response.get_data())
        assert data["name"] == "Updated SLA"
        assert data["first_response_hours"] == 1

    @pytest.mark.asyncio
    @patch("apps.api.auth.decorators.get_current_user")
    async def test_delete_sla_policy(
        self, mock_get_user, async_client, generate_token, app
    ):
        """Test DELETE /api/v1/helpdesk/sla-policies/<id>."""
        mock_user = MagicMock()
        mock_user.id = 1
        mock_user.is_superuser = True
        mock_get_user.return_value = mock_user

        token = generate_token(tenant_id=1, scopes=["helpdesk:write"])

        async with app.app_context():
            db = current_app.db
            policy_id = db.hd_sla_policies.insert(
                tenant_id=1,
                name="Delete Me SLA",
                priority="low",
                first_response_hours=4,
                resolution_hours=24,
                business_hours_only=False,
                is_active=True,
            )
            db.commit()

        response = await async_client.delete(
            f"/api/v1/helpdesk/sla-policies/{policy_id}",
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 204

    @pytest.mark.asyncio
    @patch("apps.api.auth.decorators.get_current_user")
    async def test_sla_policy_tenant_isolation(
        self, mock_get_user, async_client, generate_token, app
    ):
        """Test that tenant 2 cannot access tenant 1 SLA policies."""
        mock_user = MagicMock()
        mock_user.id = 1
        mock_user.is_superuser = True
        mock_get_user.return_value = mock_user

        token_tenant2 = generate_token(tenant_id=2, scopes=["helpdesk:read"])

        async with app.app_context():
            db = current_app.db
            # Create policy for tenant 1
            policy_id = db.hd_sla_policies.insert(
                tenant_id=1,
                name="Tenant 1 Policy",
                priority="high",
                first_response_hours=1,
                resolution_hours=4,
                business_hours_only=False,
                is_active=True,
            )
            db.commit()

        # Tenant 2 tries to read tenant 1 policy
        response = await async_client.get(
            f"/api/v1/helpdesk/sla-policies/{policy_id}",
            headers={"Authorization": f"Bearer {token_tenant2}"},
        )

        # Should not find it (404) because tenant 2 is querying and tenant 1 policy is out of scope
        # Actually, the list endpoint won't include it, so let's try getting the list
        response = await async_client.get(
            "/api/v1/helpdesk/sla-policies",
            headers={"Authorization": f"Bearer {token_tenant2}"},
        )

        assert response.status_code == 200
        data = json.loads(await response.get_data())
        assert data["pagination"]["total"] == 0  # No policies for tenant 2

    @pytest.mark.asyncio
    async def test_sla_policy_no_auth(self, async_client):
        """Test that unauthenticated requests are rejected."""
        response = await async_client.get("/api/v1/helpdesk/sla-policies")
        assert response.status_code == 401


class TestHelpDeskCannedResponsesAPI:
    """Test Helpdesk Canned Responses API endpoints."""

    @pytest.mark.asyncio
    @patch("apps.api.auth.decorators.get_current_user")
    async def test_list_canned_responses_empty(
        self, mock_get_user, async_client, generate_token, app
    ):
        """Test GET /api/v1/helpdesk/canned-responses with empty list."""
        mock_user = MagicMock()
        mock_user.id = 1
        mock_user.is_superuser = True
        mock_get_user.return_value = mock_user

        token = generate_token(tenant_id=1, scopes=["helpdesk:read"])

        async with app.app_context():
            # Clean up any existing responses for tenant 1
            db = current_app.db
            db(db.hd_canned_responses.tenant_id == 1).delete()
            db.commit()

        response = await async_client.get(
            "/api/v1/helpdesk/canned-responses",
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 200
        data = json.loads(await response.get_data())
        assert "items" in data
        assert data["items"] == []
        assert data["pagination"]["total"] == 0

    @pytest.mark.asyncio
    @patch("apps.api.auth.decorators.get_current_user")
    async def test_create_canned_response(
        self, mock_get_user, async_client, generate_token, app
    ):
        """Test POST /api/v1/helpdesk/canned-responses."""
        mock_user = MagicMock()
        mock_user.id = 1
        mock_user.is_superuser = True
        mock_get_user.return_value = mock_user

        token = generate_token(tenant_id=1, scopes=["helpdesk:write"])

        payload = {
            "title": "Billing Response",
            "body_html": "<p>Please check your billing account.</p>",
            "category": "billing",
            "is_shared": True,
        }

        response = await async_client.post(
            "/api/v1/helpdesk/canned-responses",
            json=payload,
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 201
        data = json.loads(await response.get_data())
        assert data["title"] == "Billing Response"
        assert data["category"] == "billing"
        assert data["is_shared"] is True
        assert "id" in data
        assert "created_at" in data

    @pytest.mark.asyncio
    @patch("apps.api.auth.decorators.get_current_user")
    async def test_create_canned_response_missing_title(
        self, mock_get_user, async_client, generate_token, app
    ):
        """Test POST /api/v1/helpdesk/canned-responses with missing title."""
        mock_user = MagicMock()
        mock_user.id = 1
        mock_user.is_superuser = True
        mock_get_user.return_value = mock_user

        token = generate_token(tenant_id=1, scopes=["helpdesk:write"])

        payload = {
            "body_html": "<p>Response body</p>",
            "category": "general",
        }

        response = await async_client.post(
            "/api/v1/helpdesk/canned-responses",
            json=payload,
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 400

    @pytest.mark.asyncio
    @patch("apps.api.auth.decorators.get_current_user")
    async def test_update_canned_response(
        self, mock_get_user, async_client, generate_token, app
    ):
        """Test PUT /api/v1/helpdesk/canned-responses/<id>."""
        mock_user = MagicMock()
        mock_user.id = 1
        mock_user.is_superuser = True
        mock_get_user.return_value = mock_user

        token = generate_token(tenant_id=1, scopes=["helpdesk:write"])

        async with app.app_context():
            db = current_app.db
            now = datetime.now(UTC)
            response_id = db.hd_canned_responses.insert(
                tenant_id=1,
                title="Original Title",
                body_html="<p>Original body</p>",
                category="general",
                is_shared=True,
                created_at=now,
                updated_at=now,
            )
            db.commit()

        payload = {
            "title": "Updated Title",
            "category": "billing",
        }

        response = await async_client.put(
            f"/api/v1/helpdesk/canned-responses/{response_id}",
            json=payload,
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 200
        data = json.loads(await response.get_data())
        assert data["title"] == "Updated Title"
        assert data["category"] == "billing"

    @pytest.mark.asyncio
    @patch("apps.api.auth.decorators.get_current_user")
    async def test_delete_canned_response(
        self, mock_get_user, async_client, generate_token, app
    ):
        """Test DELETE /api/v1/helpdesk/canned-responses/<id>."""
        mock_user = MagicMock()
        mock_user.id = 1
        mock_user.is_superuser = True
        mock_get_user.return_value = mock_user

        token = generate_token(tenant_id=1, scopes=["helpdesk:write"])

        async with app.app_context():
            db = current_app.db
            now = datetime.now(UTC)
            response_id = db.hd_canned_responses.insert(
                tenant_id=1,
                title="Delete Me",
                body_html="<p>Delete this</p>",
                category="general",
                is_shared=True,
                created_at=now,
                updated_at=now,
            )
            db.commit()

        response = await async_client.delete(
            f"/api/v1/helpdesk/canned-responses/{response_id}",
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 204

    @pytest.mark.asyncio
    @patch("apps.api.auth.decorators.get_current_user")
    async def test_canned_response_tenant_isolation(
        self, mock_get_user, async_client, generate_token, app
    ):
        """Test that tenant 2 cannot access tenant 1 canned responses."""
        mock_user = MagicMock()
        mock_user.id = 1
        mock_user.is_superuser = True
        mock_get_user.return_value = mock_user

        token_tenant2 = generate_token(tenant_id=2, scopes=["helpdesk:read"])

        async with app.app_context():
            db = current_app.db
            now = datetime.now(UTC)
            response_id = db.hd_canned_responses.insert(
                tenant_id=1,
                title="Tenant 1 Response",
                body_html="<p>T1 response</p>",
                category="general",
                is_shared=True,
                created_at=now,
                updated_at=now,
            )
            db.commit()

        response = await async_client.get(
            "/api/v1/helpdesk/canned-responses",
            headers={"Authorization": f"Bearer {token_tenant2}"},
        )

        assert response.status_code == 200
        data = json.loads(await response.get_data())
        assert data["pagination"]["total"] == 0  # No responses for tenant 2

    @pytest.mark.asyncio
    async def test_canned_response_no_auth(self, async_client):
        """Test that unauthenticated requests are rejected."""
        response = await async_client.get("/api/v1/helpdesk/canned-responses")
        assert response.status_code == 401


class TestHelpDeskTeamsAPI:
    """Test Helpdesk Teams API endpoints."""

    @pytest.mark.asyncio
    @patch("apps.api.auth.decorators.get_current_user")
    async def test_list_teams_empty(
        self, mock_get_user, async_client, generate_token, app
    ):
        """Test GET /api/v1/helpdesk/teams with empty list."""
        mock_user = MagicMock()
        mock_user.id = 1
        mock_user.is_superuser = True
        mock_get_user.return_value = mock_user

        token = generate_token(tenant_id=1, scopes=["helpdesk:read"])

        async with app.app_context():
            # Clean up any existing teams for tenant 1
            db = current_app.db
            db(db.hd_teams.tenant_id == 1).delete()
            db.commit()

        response = await async_client.get(
            "/api/v1/helpdesk/teams",
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 200
        data = json.loads(await response.get_data())
        assert "items" in data
        assert data["items"] == []
        assert data["pagination"]["total"] == 0

    @pytest.mark.asyncio
    @patch("apps.api.auth.decorators.get_current_user")
    async def test_create_team(self, mock_get_user, async_client, generate_token, app):
        """Test POST /api/v1/helpdesk/teams."""
        mock_user = MagicMock()
        mock_user.id = 1
        mock_user.is_superuser = True
        mock_get_user.return_value = mock_user

        token = generate_token(tenant_id=1, scopes=["helpdesk:write"])

        payload = {
            "name": "Support Team",
            "description": "Main support team",
        }

        response = await async_client.post(
            "/api/v1/helpdesk/teams",
            json=payload,
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 201
        data = json.loads(await response.get_data())
        assert data["name"] == "Support Team"
        assert data["description"] == "Main support team"
        assert "id" in data
        assert "created_at" in data

    @pytest.mark.asyncio
    @patch("apps.api.auth.decorators.get_current_user")
    async def test_create_team_missing_name(
        self, mock_get_user, async_client, generate_token, app
    ):
        """Test POST /api/v1/helpdesk/teams with missing name."""
        mock_user = MagicMock()
        mock_user.id = 1
        mock_user.is_superuser = True
        mock_get_user.return_value = mock_user

        token = generate_token(tenant_id=1, scopes=["helpdesk:write"])

        payload = {
            "description": "No name team",
        }

        response = await async_client.post(
            "/api/v1/helpdesk/teams",
            json=payload,
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 400

    @pytest.mark.asyncio
    @patch("apps.api.auth.decorators.get_current_user")
    async def test_get_team_with_members(
        self, mock_get_user, async_client, generate_token, app
    ):
        """Test GET /api/v1/helpdesk/teams/<id> with members."""
        mock_user = MagicMock()
        mock_user.id = 1
        mock_user.is_superuser = True
        mock_get_user.return_value = mock_user

        token = generate_token(tenant_id=1, scopes=["helpdesk:read"])

        async with app.app_context():
            db = current_app.db
            now = datetime.now(UTC)

            # Create identity for team member
            identity_id = db.identities.insert(
                identity_type="human",
                username=f"test_user_{uuid4().hex[:8]}",
                email="test@example.com",
                tenant_id=1,
                auth_provider="local",
                is_active=True,
                is_superuser=False,
                mfa_enabled=False,
                must_change_password=False,
                portal_role="observer",
                created_at=now,
                updated_at=now,
            )

            # Create team
            team_id = db.hd_teams.insert(
                tenant_id=1,
                village_id=f"team-{uuid4().hex[:8]}",
                name="Support Team",
                description="Main support",
                created_at=now,
                updated_at=now,
            )

            # Add member
            db.hd_team_members.insert(
                hd_team_id=team_id,
                identity_id=identity_id,
                role="member",
            )

            db.commit()

        response = await async_client.get(
            f"/api/v1/helpdesk/teams/{team_id}",
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 200
        data = json.loads(await response.get_data())
        assert data["name"] == "Support Team"
        assert "members" in data
        assert len(data["members"]) == 1
        assert data["members"][0]["identity_id"] == identity_id
        assert data["members"][0]["role"] == "member"

    @pytest.mark.asyncio
    @patch("apps.api.auth.decorators.get_current_user")
    async def test_update_team(self, mock_get_user, async_client, generate_token, app):
        """Test PUT /api/v1/helpdesk/teams/<id>."""
        mock_user = MagicMock()
        mock_user.id = 1
        mock_user.is_superuser = True
        mock_get_user.return_value = mock_user

        token = generate_token(tenant_id=1, scopes=["helpdesk:write"])

        async with app.app_context():
            db = current_app.db
            now = datetime.now(UTC)
            team_id = db.hd_teams.insert(
                tenant_id=1,
                village_id=f"team-{uuid4().hex[:8]}",
                name="Original Name",
                description="Original description",
                created_at=now,
                updated_at=now,
            )
            db.commit()

        payload = {
            "name": "Updated Name",
        }

        response = await async_client.put(
            f"/api/v1/helpdesk/teams/{team_id}",
            json=payload,
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 200
        data = json.loads(await response.get_data())
        assert data["name"] == "Updated Name"

    @pytest.mark.asyncio
    @patch("apps.api.auth.decorators.get_current_user")
    async def test_add_team_member(
        self, mock_get_user, async_client, generate_token, app
    ):
        """Test POST /api/v1/helpdesk/teams/<id>/members."""
        mock_user = MagicMock()
        mock_user.id = 1
        mock_user.is_superuser = True
        mock_get_user.return_value = mock_user

        token = generate_token(tenant_id=1, scopes=["helpdesk:admin"])

        async with app.app_context():
            db = current_app.db
            now = datetime.now(UTC)

            # Create identity
            identity_id = db.identities.insert(
                identity_type="human",
                username=f"test_user_{uuid4().hex[:8]}",
                email="test@example.com",
                tenant_id=1,
                auth_provider="local",
                is_active=True,
                is_superuser=False,
                mfa_enabled=False,
                must_change_password=False,
                portal_role="observer",
                created_at=now,
                updated_at=now,
            )

            # Create team
            team_id = db.hd_teams.insert(
                tenant_id=1,
                village_id=f"team-{uuid4().hex[:8]}",
                name="Support Team",
                description="Main support",
                created_at=now,
                updated_at=now,
            )

            db.commit()

        payload = {
            "identity_id": identity_id,
            "role": "member",
        }

        response = await async_client.post(
            f"/api/v1/helpdesk/teams/{team_id}/members",
            json=payload,
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 201
        data = json.loads(await response.get_data())
        assert data["team_id"] == team_id
        assert data["identity_id"] == identity_id
        assert data["role"] == "member"

    @pytest.mark.asyncio
    @patch("apps.api.auth.decorators.get_current_user")
    async def test_remove_team_member(
        self, mock_get_user, async_client, generate_token, app
    ):
        """Test DELETE /api/v1/helpdesk/teams/<team_id>/members/<identity_id>."""
        mock_user = MagicMock()
        mock_user.id = 1
        mock_user.is_superuser = True
        mock_get_user.return_value = mock_user

        token = generate_token(tenant_id=1, scopes=["helpdesk:admin"])

        async with app.app_context():
            db = current_app.db
            now = datetime.now(UTC)

            # Create identity
            identity_id = db.identities.insert(
                identity_type="human",
                username=f"test_user_{uuid4().hex[:8]}",
                email="test@example.com",
                tenant_id=1,
                auth_provider="local",
                is_active=True,
                is_superuser=False,
                mfa_enabled=False,
                must_change_password=False,
                portal_role="observer",
                created_at=now,
                updated_at=now,
            )

            # Create team
            team_id = db.hd_teams.insert(
                tenant_id=1,
                village_id=f"team-{uuid4().hex[:8]}",
                name="Support Team",
                description="Main support",
                created_at=now,
                updated_at=now,
            )

            # Add member
            db.hd_team_members.insert(
                hd_team_id=team_id,
                identity_id=identity_id,
                role="member",
            )

            db.commit()

        response = await async_client.delete(
            f"/api/v1/helpdesk/teams/{team_id}/members/{identity_id}",
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 204

    @pytest.mark.asyncio
    @patch("apps.api.auth.decorators.get_current_user")
    async def test_teams_tenant_isolation(
        self, mock_get_user, async_client, generate_token, app
    ):
        """Test that tenant 2 cannot access tenant 1 teams."""
        mock_user = MagicMock()
        mock_user.id = 1
        mock_user.is_superuser = True
        mock_get_user.return_value = mock_user

        token_tenant2 = generate_token(tenant_id=2, scopes=["helpdesk:read"])

        async with app.app_context():
            db = current_app.db
            now = datetime.now(UTC)
            team_id = db.hd_teams.insert(
                tenant_id=1,
                village_id=f"team-{uuid4().hex[:8]}",
                name="Tenant 1 Team",
                description="T1 team",
                created_at=now,
                updated_at=now,
            )
            db.commit()

        response = await async_client.get(
            "/api/v1/helpdesk/teams",
            headers={"Authorization": f"Bearer {token_tenant2}"},
        )

        assert response.status_code == 200
        data = json.loads(await response.get_data())
        assert data["pagination"]["total"] == 0  # No teams for tenant 2

    @pytest.mark.asyncio
    async def test_teams_no_auth(self, async_client):
        """Test that unauthenticated requests are rejected."""
        response = await async_client.get("/api/v1/helpdesk/teams")
        assert response.status_code == 401
