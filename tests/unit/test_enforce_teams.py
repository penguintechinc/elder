"""Unit tests wiring check_limit("team", ...) into organization creation.

Covers Task 7 of docs/superpowers/plans/2026-08-20-license-enforcement-phase2.md:
the live create-org route is
apps.api.modules.infrastructure.routes.organizations_pydal.create_organization
(registered via the "infrastructure" module manifest) -- NOT
apps/api/api/v1/organizations.py, which is unregistered dead code (see
tests/unit/test_crm_entity_types.py::test_create_customer_company_org).

No license_client is configured in the test app, so resolve_limits() falls
back to the community/Free tier -- max_teams=1 -- giving each test a real,
un-mocked limit to hit.
"""

import json
from datetime import UTC, datetime
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from quart import current_app


def _make_tenant(db, prefix: str = "team-lic-test") -> int:
    """Create a fresh, uniquely-slugged tenant (never the shared default)."""
    now = datetime.now(UTC)
    tenant_id = db.tenants.insert(
        name=f"Team License Test {prefix}",
        slug=f"{prefix}-{uuid4().hex[:8]}",
        is_active=True,
        created_at=now,
        updated_at=now,
    )
    db.commit()
    return tenant_id


def _make_team_org(db, tenant_id: int) -> int:
    now = datetime.now(UTC)
    org_id = db.organizations.insert(
        name=f"Existing Team {uuid4().hex[:8]}",
        tenant_id=tenant_id,
        type="team",
        created_at=now,
        updated_at=now,
    )
    db.commit()
    return org_id


def _patch_flag(enabled: bool):
    return patch("apps.api.common.licensing.enforce.flag_enabled", return_value=enabled)


@pytest.mark.asyncio
class TestTeamCreationLicenseGate:
    """POST /api/v1/organizations with organization_type='team' is license-gated."""

    @patch("apps.api.auth.decorators.get_current_user")
    async def test_second_team_blocked_at_free_limit_when_flag_on(
        self, mock_get_user, async_client, app
    ):
        """Free tier caps max_teams=1. At the limit + flag ON -> 402, no insert."""
        async with app.app_context():
            db = current_app.db
            tenant_id = _make_tenant(db)
            _make_team_org(db, tenant_id)  # already at the Free limit (1)

            mock_user = MagicMock()
            mock_user.id = 1
            mock_user.tenant_id = tenant_id
            mock_user.is_superuser = True
            mock_get_user.return_value = mock_user

            before = db(db.organizations.tenant_id == tenant_id).count()

            with _patch_flag(True):
                resp = await async_client.post(
                    "/api/v1/organizations",
                    json={"name": "Second Team", "organization_type": "team"},
                    headers={"Authorization": "Bearer fake-token"},
                )

            assert resp.status_code == 402, (await resp.get_data()).decode()[:200]
            body = json.loads(await resp.get_data())
            assert body["error"] == "limit_reached"
            assert body["limit"] == "team"

            after = db(db.organizations.tenant_id == tenant_id).count()
            assert after == before, "blocked request must not insert a row"

    @patch("apps.api.auth.decorators.get_current_user")
    async def test_first_team_allowed_under_limit(
        self, mock_get_user, async_client, app
    ):
        """A tenant with zero teams is under the Free limit (1) -> allowed."""
        async with app.app_context():
            db = current_app.db
            tenant_id = _make_tenant(db)

            mock_user = MagicMock()
            mock_user.id = 1
            mock_user.tenant_id = tenant_id
            mock_user.is_superuser = True
            mock_get_user.return_value = mock_user

            with _patch_flag(True):
                resp = await async_client.post(
                    "/api/v1/organizations",
                    json={"name": "First Team", "organization_type": "team"},
                    headers={"Authorization": "Bearer fake-token"},
                )

            assert resp.status_code == 201, (await resp.get_data()).decode()[:200]
            data = json.loads(await resp.get_data())
            assert data["type"] == "team"

    @patch("apps.api.auth.decorators.get_current_user")
    async def test_non_team_org_not_gated_even_at_team_limit(
        self, mock_get_user, async_client, app
    ):
        """A non-team org type is never counted/gated against max_teams."""
        async with app.app_context():
            db = current_app.db
            tenant_id = _make_tenant(db)
            _make_team_org(db, tenant_id)  # tenant is already at the team limit

            mock_user = MagicMock()
            mock_user.id = 1
            mock_user.tenant_id = tenant_id
            mock_user.is_superuser = True
            mock_get_user.return_value = mock_user

            with _patch_flag(True) as mock_flag:
                resp = await async_client.post(
                    "/api/v1/organizations",
                    json={"name": "Regular Org", "organization_type": "organization"},
                    headers={"Authorization": "Bearer fake-token"},
                )

            assert resp.status_code == 201, (await resp.get_data()).decode()[:200]
            data = json.loads(await resp.get_data())
            assert data["type"] == "organization"
            # check_limit is never invoked for a non-team create -- the flag
            # lookup (which only happens inside check_limit, at/over the
            # limit) must not have been reached.
            mock_flag.assert_not_called()

    @patch("apps.api.auth.decorators.get_current_user")
    async def test_second_team_allowed_and_logs_when_flag_off(
        self, mock_get_user, async_client, app, caplog
    ):
        """At the limit but flag OFF -> observe-only: allowed + WARN logged."""
        async with app.app_context():
            db = current_app.db
            tenant_id = _make_tenant(db)
            _make_team_org(db, tenant_id)  # already at the Free limit (1)

            mock_user = MagicMock()
            mock_user.id = 1
            mock_user.tenant_id = tenant_id
            mock_user.is_superuser = True
            mock_get_user.return_value = mock_user

            with _patch_flag(False), caplog.at_level("WARNING"):
                resp = await async_client.post(
                    "/api/v1/organizations",
                    json={"name": "Second Team", "organization_type": "team"},
                    headers={"Authorization": "Bearer fake-token"},
                )

            assert resp.status_code == 201, (await resp.get_data()).decode()[:200]
            data = json.loads(await resp.get_data())
            assert data["type"] == "team"
            assert any(
                "license_limit_would_block" in record.message
                or "license_limit_would_block" in str(record.msg)
                for record in caplog.records
            )
