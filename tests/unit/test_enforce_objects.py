"""Unit tests for Free-tier object-quota enforcement (Phase 2 Task 8).

Covers apps.api.common.licensing.enforce.check_limit("object", tenant_id) both
directly (mocked resolver/counter/flag, mirroring test_license_enforce.py) and
wired at a real create path (POST /api/v1/issues) -- see
docs/superpowers/plans/2026-08-20-license-enforcement-phase2.md Task 8.

check_limit itself is not modified here; these tests only prove the create
sites call it correctly.
"""

from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import pytest
from quart import current_app

from apps.api.common.licensing.enforce import check_limit
from apps.api.common.licensing.limits import LimitSet

# Free tier per the Global Constraints table: max_objects=1000.
_FREE_AT_OBJECT_LIMIT = LimitSet(
    max_global_admins=1,
    max_tenant_admins=0,
    max_teams=1,
    max_tenants=1,
    max_objects=1000,
    max_nodes_per_type=1,
)

# Enterprise: every dimension unlimited (None).
_ENTERPRISE_UNLIMITED = LimitSet(
    max_global_admins=None,
    max_tenant_admins=None,
    max_teams=None,
    max_tenants=None,
    max_objects=None,
    max_nodes_per_type=None,
)


def _patch_resolve(tier: str, limits: LimitSet):
    return patch(
        "apps.api.common.licensing.enforce.resolve_limits", return_value=(tier, limits)
    )


def _patch_count(value: int):
    return patch(
        "apps.api.common.licensing.enforce._count_for_kind", return_value=value
    )


def _patch_flag(enabled: bool):
    return patch("apps.api.common.licensing.enforce.flag_enabled", return_value=enabled)


@pytest.mark.asyncio
class TestCheckLimitObjectDirect:
    """check_limit("object", tenant_id) -- direct coverage, mirrors test_license_enforce.py."""

    async def test_free_at_limit_flag_on_blocks(self, app):
        """A Free tenant at exactly 1000 objects with the flag ON gets a 402."""
        async with app.app_context():
            with (
                _patch_resolve("community", _FREE_AT_OBJECT_LIMIT),
                _patch_count(1000),  # == max_objects
                _patch_flag(True),
            ):
                result = await check_limit("object", 1)

            assert result is not None
            response, status = result
            assert status == 402
            body = await response.get_json()
            assert body == {
                "error": "limit_reached",
                "limit": "object",
                "tier": "community",
                "upgrade": True,
            }

    async def test_enterprise_never_blocks(self, app):
        """Enterprise's max_objects=None short-circuits before even counting."""
        async with app.app_context():
            with (
                _patch_resolve("enterprise", _ENTERPRISE_UNLIMITED),
                _patch_count(999_999) as mock_count,
                _patch_flag(True),
            ):
                result = await check_limit("object", 1)

            assert result is None
            mock_count.assert_not_called()

    async def test_flag_off_observes_only(self, app):
        """Flag OFF: at-limit does not block, but logs license_limit_would_block."""
        async with app.app_context():
            with (
                _patch_resolve("community", _FREE_AT_OBJECT_LIMIT),
                _patch_count(1000),
                _patch_flag(False),
                patch("apps.api.common.licensing.enforce.logger") as mock_logger,
            ):
                result = await check_limit("object", 1)

            assert result is None
            mock_logger.warning.assert_called_once_with(
                "license_limit_would_block",
                kind="object",
                tenant_id=1,
                count=1000,
                limit=1000,
                tier="community",
            )

    async def test_under_limit_passes(self, app):
        """Under the limit, the flag is never even consulted."""
        async with app.app_context():
            with (
                _patch_resolve("community", _FREE_AT_OBJECT_LIMIT),
                _patch_count(999),
                _patch_flag(True) as mock_flag,
            ):
                result = await check_limit("object", 1)

            assert result is None
            mock_flag.assert_not_called()


@pytest.mark.asyncio
class TestObjectQuotaWiredAtIssuesCreate:
    """End-to-end: POST /api/v1/issues respects check_limit("object", tenant_id).

    Patches enforce.py's resolver/counter/flag (not check_limit itself) so the
    real create_issue -> check_limit code path runs, without needing to seed
    1000 real rows.
    """

    @patch("apps.api.auth.decorators.get_current_user")
    async def test_free_at_limit_flag_on_returns_402(
        self, mock_get_user, async_client, generate_token, app
    ):
        mock_user = MagicMock()
        mock_user.id = 1
        mock_user.is_superuser = True
        mock_get_user.return_value = mock_user

        token = generate_token(tenant_id=1, scopes=["issues:write"])

        with (
            _patch_resolve("community", _FREE_AT_OBJECT_LIMIT),
            _patch_count(1000),
            _patch_flag(True),
        ):
            response = await async_client.post(
                "/api/v1/issues",
                json={"title": "Over quota issue", "organization_id": 1},
                headers={"Authorization": f"Bearer {token}"},
            )

        assert response.status_code == 402, (
            f"Expected 402, got {response.status_code}: "
            f"{(await response.get_data()).decode()[:200]}"
        )
        body = await response.get_json()
        assert body["error"] == "limit_reached"
        assert body["limit"] == "object"

    @patch("apps.api.auth.decorators.get_current_user")
    async def test_free_under_limit_flag_on_creates_issue(
        self, mock_get_user, async_client, generate_token, app
    ):
        mock_user = MagicMock()
        mock_user.id = 1
        mock_user.is_superuser = True
        mock_get_user.return_value = mock_user

        async with app.app_context():
            db = current_app.db
            now = datetime.now(UTC)
            org_id = db.organizations.insert(
                name="Object Quota Under Limit Org",
                tenant_id=1,
                created_at=now,
                updated_at=now,
            )
            db.commit()

        token = generate_token(tenant_id=1, scopes=["issues:write"])

        with (
            _patch_resolve("community", _FREE_AT_OBJECT_LIMIT),
            _patch_count(999),
            _patch_flag(True),
        ):
            response = await async_client.post(
                "/api/v1/issues",
                json={"title": "Under quota issue", "organization_id": org_id},
                headers={"Authorization": f"Bearer {token}"},
            )

        assert response.status_code == 201, (
            f"Expected 201, got {response.status_code}: "
            f"{(await response.get_data()).decode()[:200]}"
        )

    @patch("apps.api.auth.decorators.get_current_user")
    async def test_free_at_limit_flag_off_creates_issue_observe_only(
        self, mock_get_user, async_client, generate_token, app
    ):
        mock_user = MagicMock()
        mock_user.id = 1
        mock_user.is_superuser = True
        mock_get_user.return_value = mock_user

        async with app.app_context():
            db = current_app.db
            now = datetime.now(UTC)
            org_id = db.organizations.insert(
                name="Object Quota Observe Only Org",
                tenant_id=1,
                created_at=now,
                updated_at=now,
            )
            db.commit()

        token = generate_token(tenant_id=1, scopes=["issues:write"])

        with (
            _patch_resolve("community", _FREE_AT_OBJECT_LIMIT),
            _patch_count(1000),
            _patch_flag(False),
            patch("apps.api.common.licensing.enforce.logger") as mock_logger,
        ):
            response = await async_client.post(
                "/api/v1/issues",
                json={"title": "Observe only issue", "organization_id": org_id},
                headers={"Authorization": f"Bearer {token}"},
            )

        assert response.status_code == 201, (
            f"Expected 201 (observe-only allows), got {response.status_code}: "
            f"{(await response.get_data()).decode()[:200]}"
        )
        mock_logger.warning.assert_any_call(
            "license_limit_would_block",
            kind="object",
            tenant_id=1,
            count=1000,
            limit=1000,
            tier="community",
        )

    @patch("apps.api.auth.decorators.get_current_user")
    async def test_enterprise_never_blocks_issue_create(
        self, mock_get_user, async_client, generate_token, app
    ):
        mock_user = MagicMock()
        mock_user.id = 1
        mock_user.is_superuser = True
        mock_get_user.return_value = mock_user

        async with app.app_context():
            db = current_app.db
            now = datetime.now(UTC)
            org_id = db.organizations.insert(
                name="Object Quota Enterprise Org",
                tenant_id=1,
                created_at=now,
                updated_at=now,
            )
            db.commit()

        token = generate_token(tenant_id=1, scopes=["issues:write"])

        with (
            _patch_resolve("enterprise", _ENTERPRISE_UNLIMITED),
            _patch_count(999_999) as mock_count,
            _patch_flag(True),
        ):
            response = await async_client.post(
                "/api/v1/issues",
                json={"title": "Enterprise unlimited issue", "organization_id": org_id},
                headers={"Authorization": f"Bearer {token}"},
            )

        assert response.status_code == 201, (
            f"Expected 201, got {response.status_code}: "
            f"{(await response.get_data()).decode()[:200]}"
        )
        mock_count.assert_not_called()
