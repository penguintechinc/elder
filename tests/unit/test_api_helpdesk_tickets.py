"""
Unit tests for Helpdesk Tickets API endpoints.

Uses real JWT token-based authentication and real database.
Module enablement via ELDER_MODULE_HELPDESK=true in conftest.
"""

import json
import pytest
from datetime import datetime, timezone
from unittest.mock import patch, MagicMock
from uuid import uuid4
from quart import current_app


class TestHelpDeskTicketsAPI:
    """Test Helpdesk Tickets API endpoints."""

    @pytest.mark.asyncio
    @patch("apps.api.auth.decorators.get_current_user")
    async def test_list_tickets_empty(
        self, mock_get_user, async_client, generate_token, app
    ):
        """Test GET /api/v1/helpdesk/tickets with empty list."""
        mock_user = MagicMock()
        mock_user.id = 1
        mock_user.is_superuser = True
        mock_get_user.return_value = mock_user

        # Generate JWT token for tenant=1 with helpdesk:read scope
        token = generate_token(tenant_id=1, scopes=["helpdesk:read"])

        async with app.app_context():
            # Clean up any existing tickets for tenant 1
            db = current_app.db
            db(db.hd_tickets.tenant_id == 1).delete()
            db.commit()

        response = await async_client.get(
            "/api/v1/helpdesk/tickets",
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 200
        data = json.loads(await response.get_data())
        assert "items" in data
        assert data["items"] == []
        assert data["pagination"]["total"] == 0

    @pytest.mark.asyncio
    @patch("apps.api.auth.decorators.get_current_user")
    async def test_create_ticket(
        self, mock_get_user, async_client, generate_token, app
    ):
        """Test POST /api/v1/helpdesk/tickets."""
        mock_user = MagicMock()
        mock_user.id = 1
        mock_user.is_superuser = True
        mock_get_user.return_value = mock_user

        token = generate_token(tenant_id=1, scopes=["helpdesk:write"])

        async with app.app_context():
            # Create identity record for requester
            db = current_app.db
            now = datetime.now(timezone.utc)
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
            db.commit()

            payload = {
                "subject": "Test ticket",
                "priority": "high",
                "channel": "web",
                "requester_id": identity_id,
            }

            with patch(
                "apps.api.modules.helpdesk.routes.tickets.current_app"
            ) as mock_app:
                with patch(
                    "shared.utils.village_id.generate_village_id"
                ) as mock_village_id:
                    mock_app.db = current_app.db
                    mock_app.redis_client = MagicMock()
                    mock_village_id.return_value = f"test-vid-{uuid4().hex[:8]}"

                    response = await async_client.post(
                        "/api/v1/helpdesk/tickets",
                        json=payload,
                        headers={"Authorization": f"Bearer {token}"},
                    )

                    assert response.status_code == 201
                    data = json.loads(await response.get_data())
                    assert data["subject"] == "Test ticket"
                    assert data["priority"] == "high"
                    assert data["status"] == "new"
                    assert data["village_id"] is not None
                    assert "id" in data

    @pytest.mark.asyncio
    @patch("apps.api.auth.decorators.get_current_user")
    async def test_create_ticket_missing_subject(
        self, mock_get_user, async_client, generate_token
    ):
        """Test POST /api/v1/helpdesk/tickets with missing subject."""
        mock_user = MagicMock()
        mock_user.id = 1
        mock_user.is_superuser = True
        mock_get_user.return_value = mock_user

        token = generate_token(tenant_id=1, scopes=["helpdesk:write"])

        payload = {"priority": "high", "requester_id": 1}

        response = await async_client.post(
            "/api/v1/helpdesk/tickets",
            json=payload,
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 400

    @pytest.mark.asyncio
    @patch("apps.api.auth.decorators.get_current_user")
    async def test_get_ticket(self, mock_get_user, async_client, generate_token, app):
        """Test GET /api/v1/helpdesk/tickets/:id."""
        mock_user = MagicMock()
        mock_user.id = 1
        mock_user.is_superuser = True
        mock_get_user.return_value = mock_user

        token = generate_token(tenant_id=1, scopes=["helpdesk:read"])

        async with app.app_context():
            db = current_app.db
            db(db.hd_tickets.tenant_id == 1).delete()
            db.commit()

            # Create test ticket
            now = datetime.now(timezone.utc)
            ticket_id = db.hd_tickets.insert(
                tenant_id=1,
                village_id="test-v1",
                subject="Get Me",
                status="new",
                priority="high",
                channel="web",
                requester_identity_id=1,
                created_at=now,
                updated_at=now,
            )
            db.commit()

            response = await async_client.get(
                f"/api/v1/helpdesk/tickets/{ticket_id}",
                headers={"Authorization": f"Bearer {token}"},
            )

            assert response.status_code == 200
            data = json.loads(await response.get_data())
            assert data["subject"] == "Get Me"
            assert data["id"] == ticket_id

    @pytest.mark.asyncio
    @patch("apps.api.auth.decorators.get_current_user")
    async def test_get_ticket_not_found(
        self, mock_get_user, async_client, generate_token
    ):
        """Test GET /api/v1/helpdesk/tickets/:id with non-existent ticket."""
        mock_user = MagicMock()
        mock_user.id = 1
        mock_user.is_superuser = True
        mock_get_user.return_value = mock_user

        token = generate_token(tenant_id=1, scopes=["helpdesk:read"])

        response = await async_client.get(
            "/api/v1/helpdesk/tickets/99999",
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 404

    @pytest.mark.asyncio
    @patch("apps.api.auth.decorators.get_current_user")
    async def test_update_ticket(
        self, mock_get_user, async_client, generate_token, app
    ):
        """Test PATCH /api/v1/helpdesk/tickets/:id."""
        mock_user = MagicMock()
        mock_user.id = 1
        mock_user.is_superuser = True
        mock_get_user.return_value = mock_user

        token = generate_token(tenant_id=1, scopes=["helpdesk:write"])

        async with app.app_context():
            db = current_app.db
            now = datetime.now(timezone.utc)

            ticket_id = db.hd_tickets.insert(
                tenant_id=1,
                village_id="test-v2",
                subject="Original",
                status="new",
                priority="low",
                channel="web",
                requester_identity_id=1,
                created_at=now,
                updated_at=now,
            )
            db.commit()

            response = await async_client.patch(
                f"/api/v1/helpdesk/tickets/{ticket_id}",
                json={"subject": "Updated", "priority": "critical"},
                headers={"Authorization": f"Bearer {token}"},
            )

            assert response.status_code == 200
            data = json.loads(await response.get_data())
            assert data["subject"] == "Updated"
            assert data["priority"] == "critical"

    @pytest.mark.asyncio
    @patch("apps.api.auth.decorators.get_current_user")
    async def test_delete_ticket(
        self, mock_get_user, async_client, generate_token, app
    ):
        """Test DELETE /api/v1/helpdesk/tickets/:id."""
        mock_user = MagicMock()
        mock_user.id = 1
        mock_user.is_superuser = True
        mock_get_user.return_value = mock_user

        token = generate_token(tenant_id=1, scopes=["helpdesk:admin"])

        async with app.app_context():
            db = current_app.db
            now = datetime.now(timezone.utc)

            ticket_id = db.hd_tickets.insert(
                tenant_id=1,
                village_id="test-v3",
                subject="Delete Me",
                status="new",
                priority="low",
                channel="web",
                requester_identity_id=1,
                created_at=now,
                updated_at=now,
            )
            db.commit()

            response = await async_client.delete(
                f"/api/v1/helpdesk/tickets/{ticket_id}",
                headers={"Authorization": f"Bearer {token}"},
            )

            assert response.status_code == 204

    @pytest.mark.asyncio
    @patch("apps.api.auth.decorators.get_current_user")
    async def test_assign_ticket(
        self, mock_get_user, async_client, generate_token, app
    ):
        """Test POST /api/v1/helpdesk/tickets/:id/assign."""
        mock_user = MagicMock()
        mock_user.id = 1
        mock_user.is_superuser = True
        mock_get_user.return_value = mock_user

        token = generate_token(tenant_id=1, scopes=["helpdesk:write"])

        async with app.app_context():
            db = current_app.db
            now = datetime.now(timezone.utc)

            # Create identity records
            identity1_id = db.identities.insert(
                identity_type="human",
                username=f"user1_{uuid4().hex[:8]}",
                email="user1@example.com",
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
            identity2_id = db.identities.insert(
                identity_type="human",
                username=f"user2_{uuid4().hex[:8]}",
                email="user2@example.com",
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

            ticket_id = db.hd_tickets.insert(
                tenant_id=1,
                village_id="test-v4",
                subject="Assign Me",
                status="new",
                priority="high",
                channel="web",
                requester_identity_id=identity1_id,
                created_at=now,
                updated_at=now,
            )
            db.commit()

            response = await async_client.post(
                f"/api/v1/helpdesk/tickets/{ticket_id}/assign",
                json={"assignee_id": identity2_id},
                headers={"Authorization": f"Bearer {token}"},
            )

            assert response.status_code == 200
            data = json.loads(await response.get_data())
            assert data["assignee_id"] == identity2_id

    @pytest.mark.asyncio
    @patch("apps.api.auth.decorators.get_current_user")
    async def test_merge_tickets(
        self, mock_get_user, async_client, generate_token, app
    ):
        """Test POST /api/v1/helpdesk/tickets/:id/merge."""
        mock_user = MagicMock()
        mock_user.id = 1
        mock_user.is_superuser = True
        mock_get_user.return_value = mock_user

        token = generate_token(tenant_id=1, scopes=["helpdesk:write"])

        async with app.app_context():
            db = current_app.db
            now = datetime.now(timezone.utc)

            primary_id = db.hd_tickets.insert(
                tenant_id=1,
                village_id="test-merge-p",
                subject="Primary",
                status="new",
                priority="high",
                channel="web",
                requester_identity_id=1,
                created_at=now,
                updated_at=now,
            )

            secondary_id = db.hd_tickets.insert(
                tenant_id=1,
                village_id="test-merge-s",
                subject="Secondary",
                status="new",
                priority="low",
                channel="web",
                requester_identity_id=1,
                created_at=now,
                updated_at=now,
            )
            db.commit()

            response = await async_client.post(
                f"/api/v1/helpdesk/tickets/{primary_id}/merge",
                json={"merge_from_id": secondary_id},
                headers={"Authorization": f"Bearer {token}"},
            )

            assert response.status_code == 200
            data = json.loads(await response.get_data())
            assert data["primary_id"] == primary_id
            assert data["merged_id"] == secondary_id

            # Verify secondary is marked closed
            secondary = db(db.hd_tickets.id == secondary_id).select().first()
            assert secondary.status == "closed"


class TestHelpDeskMessagesAPI:
    """Test Helpdesk Messages API endpoints."""

    @pytest.mark.asyncio
    @patch("apps.api.auth.decorators.get_current_user")
    async def test_list_messages_empty(
        self, mock_get_user, async_client, generate_token, app
    ):
        """Test GET /api/v1/helpdesk/tickets/:id/messages."""
        mock_user = MagicMock()
        mock_user.id = 1
        mock_user.is_superuser = True
        mock_get_user.return_value = mock_user

        token = generate_token(tenant_id=1, scopes=["helpdesk:read"])

        async with app.app_context():
            db = current_app.db
            now = datetime.now(timezone.utc)

            ticket_id = db.hd_tickets.insert(
                tenant_id=1,
                village_id="test-msg",
                subject="Test",
                status="new",
                priority="high",
                channel="web",
                requester_identity_id=1,
                created_at=now,
                updated_at=now,
            )
            db.commit()

            response = await async_client.get(
                f"/api/v1/helpdesk/tickets/{ticket_id}/messages",
                headers={"Authorization": f"Bearer {token}"},
            )

            assert response.status_code == 200
            data = json.loads(await response.get_data())
            assert data["items"] == []

    @pytest.mark.asyncio
    @patch("apps.api.auth.decorators.get_current_user")
    async def test_add_message(self, mock_get_user, async_client, generate_token, app):
        """Test POST /api/v1/helpdesk/tickets/:id/messages."""
        mock_user = MagicMock()
        mock_user.id = 1
        mock_user.is_superuser = True
        mock_get_user.return_value = mock_user

        token = generate_token(tenant_id=1, scopes=["helpdesk:write"])

        async with app.app_context():
            db = current_app.db
            now = datetime.now(timezone.utc)

            ticket_id = db.hd_tickets.insert(
                tenant_id=1,
                village_id="test-msg2",
                subject="Test",
                status="new",
                priority="high",
                channel="web",
                requester_identity_id=1,
                created_at=now,
                updated_at=now,
            )
            db.commit()

            response = await async_client.post(
                f"/api/v1/helpdesk/tickets/{ticket_id}/messages",
                json={
                    "sender_id": 1,
                    "message_type": "reply",
                    "body_text": "Test message",
                },
                headers={"Authorization": f"Bearer {token}"},
            )

            assert response.status_code == 201
            data = json.loads(await response.get_data())
            assert data["body_text"] == "Test message"
            assert data["is_internal"] is False


class TestHelpDeskDashboardAPI:
    """Test Helpdesk Dashboard API endpoints."""

    @pytest.mark.asyncio
    @patch("apps.api.auth.decorators.get_current_user")
    async def test_dashboard_stats_empty(
        self, mock_get_user, async_client, generate_token, app
    ):
        """Test GET /api/v1/helpdesk/dashboard/stats."""
        mock_user = MagicMock()
        mock_user.id = 1
        mock_user.is_superuser = True
        mock_get_user.return_value = mock_user

        token = generate_token(tenant_id=1, scopes=["helpdesk:read"])

        async with app.app_context():
            # Clean up any existing tickets
            db = current_app.db
            db(db.hd_tickets.tenant_id == 1).delete()
            db.commit()

        response = await async_client.get(
            "/api/v1/helpdesk/dashboard/stats",
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 200
        data = json.loads(await response.get_data())
        assert data["total_tickets"] == 0
        assert "by_status" in data
        assert "by_priority" in data

    @pytest.mark.asyncio
    @patch("apps.api.auth.decorators.get_current_user")
    async def test_dashboard_stats_with_tickets(
        self, mock_get_user, async_client, generate_token, app
    ):
        """Test GET /api/v1/helpdesk/dashboard/stats with tickets."""
        mock_user = MagicMock()
        mock_user.id = 1
        mock_user.is_superuser = True
        mock_get_user.return_value = mock_user

        token = generate_token(tenant_id=1, scopes=["helpdesk:read"])

        async with app.app_context():
            db = current_app.db
            db(db.hd_tickets.tenant_id == 1).delete()
            db.commit()

            now = datetime.now(timezone.utc)

            db.hd_tickets.insert(
                tenant_id=1,
                village_id="test-dash-1",
                subject="High new",
                status="new",
                priority="high",
                channel="web",
                requester_identity_id=1,
                created_at=now,
                updated_at=now,
            )

            db.hd_tickets.insert(
                tenant_id=1,
                village_id="test-dash-2",
                subject="Critical open",
                status="open",
                priority="critical",
                channel="web",
                requester_identity_id=1,
                created_at=now,
                updated_at=now,
            )

            db.hd_tickets.insert(
                tenant_id=1,
                village_id="test-dash-3",
                subject="Low resolved",
                status="resolved",
                priority="low",
                channel="web",
                requester_identity_id=1,
                created_at=now,
                updated_at=now,
            )
            db.commit()

        response = await async_client.get(
            "/api/v1/helpdesk/dashboard/stats",
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 200
        data = json.loads(await response.get_data())
        assert data["total_tickets"] == 3
        assert data["open_tickets"] == 2  # new, open
        assert data["resolved_tickets"] == 1
