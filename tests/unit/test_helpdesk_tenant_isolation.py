"""Cross-tenant isolation regression tests for helpdesk routes.

Regression: automated security review flagged IDOR — body-provided identity
ids accepted without a tenant check (team member, ticket assignee/requester).
Fixed; these lock it.
"""

import json
from datetime import UTC, datetime, timezone
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from quart import current_app


def _foreign_tenant(db) -> int:
    """Create and return a second tenant distinct from tenant 1."""
    tid = db.tenants.insert(
        name="Other Tenant", slug=f"other-{uuid4().hex[:8]}", is_active=True
    )
    db.commit()
    return tid


def _seed_identity(db, tenant_id: int) -> int:
    """Insert a bare identity in the given tenant, return its id."""
    now = datetime.now(UTC)
    ident = db.identities.insert(
        identity_type="human",
        username=f"u_{uuid4().hex[:8]}",
        email=f"{uuid4().hex[:8]}@example.com",
        tenant_id=tenant_id,
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
    return ident


class TestHelpdeskCrossTenantIsolation:
    @pytest.mark.asyncio
    @patch("apps.api.auth.decorators.get_current_user")
    async def test_add_team_member_rejects_foreign_identity(
        self, mock_get_user, async_client, generate_token, app
    ):
        """Adding an identity from another tenant to a team must 404 (IDOR)."""
        mock_get_user.return_value = MagicMock(is_superuser=True)
        token = generate_token(tenant_id=1, scopes=["helpdesk:admin"])

        async with app.app_context():
            db = current_app.db
            foreign_id = _seed_identity(db, _foreign_tenant(db))
            team_id = db.hd_teams.insert(
                tenant_id=1,
                village_id=f"t-{uuid4().hex[:8]}",
                name="T1 Team",
                created_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
            )
            db.commit()

        resp = await async_client.post(
            f"/api/v1/helpdesk/teams/{team_id}/members",
            json={"identity_id": foreign_id, "role": "member"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 404

    @pytest.mark.asyncio
    @patch("apps.api.auth.decorators.get_current_user")
    async def test_create_ticket_rejects_foreign_assignee(
        self, mock_get_user, async_client, generate_token, app
    ):
        """Creating a ticket with a foreign-tenant assignee must 400 (IDOR)."""
        mock_get_user.return_value = MagicMock(id=1, is_superuser=True)
        token = generate_token(tenant_id=1, scopes=["helpdesk:write"])

        async with app.app_context():
            db = current_app.db
            requester = _seed_identity(db, tenant_id=1)
            foreign_assignee = _seed_identity(db, _foreign_tenant(db))

            payload = {
                "subject": "x",
                "requester_id": requester,
                "assignee_id": foreign_assignee,
            }
            with patch(
                "apps.api.modules.helpdesk.routes.tickets.current_app"
            ) as mock_app:
                with patch("shared.utils.village_id.generate_village_id") as mv:
                    mock_app.db = current_app.db
                    mock_app.redis_client = MagicMock()
                    mv.return_value = f"vid-{uuid4().hex[:8]}"
                    resp = await async_client.post(
                        "/api/v1/helpdesk/tickets",
                        json=payload,
                        headers={"Authorization": f"Bearer {token}"},
                    )
        assert resp.status_code == 400
