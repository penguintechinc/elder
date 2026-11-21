"""Unit tests for user/admin creation license enforcement (Task 6).

Covers apps/api/api/v1/identities.py::_identity_admin_kind (the role->kind
determination wired into create_identity -- always inert through that
endpoint today, see its docstring) and
apps/api/services/portal_auth/service.py::_admin_kind + the
check_limit wiring inside PortalAuthService.create_portal_user (the live
path, since tenant_role/global_role are caller-supplied there rather than
hardcoded).
"""

from datetime import UTC, datetime
from unittest.mock import patch
from uuid import uuid4

import pytest
from quart import current_app

from apps.api.api.v1.identities import _identity_admin_kind
from apps.api.common.licensing.limits import LimitSet
from apps.api.services.portal_auth.service import PortalAuthService, _admin_kind

# Community/Free tier defaults (see docs/superpowers/plans/2026-08-20-license-
# enforcement-phase2.md Global Constraints table): 1 global admin, 0 tenant
# admins allowed.
_FREE_LIMITS = LimitSet(
    max_global_admins=1,
    max_tenant_admins=0,
    max_teams=1,
    max_tenants=1,
    max_objects=1000,
    max_nodes_per_type=1,
)


def _make_tenant(db, prefix: str = "lic-users") -> int:
    now = datetime.now(UTC)
    return db.tenants.insert(
        name=f"License Users Test {prefix}",
        slug=f"{prefix}-{uuid4().hex[:8]}",
        is_active=True,
        created_at=now,
        updated_at=now,
    )


def _make_portal_user(db, tenant_id: int, **overrides) -> int:
    now = datetime.now(UTC)
    fields = {
        "tenant_id": tenant_id,
        "email": f"portal-{uuid4().hex[:8]}@example.com",
        "is_active": True,
        "email_verified": True,
        "created_at": now,
        "updated_at": now,
    }
    fields.update(overrides)
    return db.portal_users.insert(**fields)


def _patch_resolve():
    return patch(
        "apps.api.common.licensing.enforce.resolve_limits",
        return_value=("community", _FREE_LIMITS),
    )


def _patch_flag(enabled: bool):
    return patch("apps.api.common.licensing.enforce.flag_enabled", return_value=enabled)


class TestIdentityAdminKind:
    """_identity_admin_kind: pure role->kind mapping mirroring counters.py."""

    def test_superuser_is_global_admin(self):
        assert _identity_admin_kind(True, "observer") == "global_admin"

    def test_portal_role_admin_is_tenant_admin(self):
        assert _identity_admin_kind(False, "admin") == "tenant_admin"

    def test_observer_is_unlimited_member(self):
        assert _identity_admin_kind(False, "observer") is None

    def test_superuser_wins_even_with_admin_portal_role(self):
        assert _identity_admin_kind(True, "admin") == "global_admin"


class TestPortalAdminKind:
    """_admin_kind: pure role->kind mapping mirroring counters.py."""

    def test_global_role_admin_is_global_admin(self):
        assert _admin_kind("reader", "admin") == "global_admin"

    def test_global_role_superadmin_is_global_admin(self):
        assert _admin_kind("reader", "superadmin") == "global_admin"

    def test_tenant_role_admin_is_tenant_admin(self):
        assert _admin_kind("admin", None) == "tenant_admin"

    def test_reader_is_unlimited_member(self):
        assert _admin_kind("reader", None) is None

    def test_maintainer_is_unlimited_member(self):
        assert _admin_kind("maintainer", None) is None


@pytest.mark.asyncio
class TestCreatePortalUserEnforcement:
    """PortalAuthService.create_portal_user: check_limit wiring for admin roles."""

    async def test_second_global_admin_blocked_on_free_flag_on(self, app):
        async with app.app_context():
            db = current_app.db
            tenant_id = _make_tenant(db)
            _make_portal_user(db, tenant_id, global_role="admin", password_hash="x")
            db.commit()

            with _patch_resolve(), _patch_flag(True):
                result = await PortalAuthService.create_portal_user(
                    tenant_id=tenant_id,
                    email=f"admin2-{uuid4().hex[:8]}@example.com",
                    password="SecurePass123",
                    global_role="admin",
                )

            assert result.get("status_code") == 402
            assert "error" in result

    async def test_tenant_admin_blocked_on_free_flag_on(self, app):
        """Free tier's max_tenant_admins=0 -- even the very first blocks."""
        async with app.app_context():
            db = current_app.db
            tenant_id = _make_tenant(db)

            with _patch_resolve(), _patch_flag(True):
                result = await PortalAuthService.create_portal_user(
                    tenant_id=tenant_id,
                    email=f"tadmin-{uuid4().hex[:8]}@example.com",
                    password="SecurePass123",
                    tenant_role="admin",
                )

            assert result.get("status_code") == 402
            assert "error" in result

    async def test_member_creation_always_allowed(self, app):
        """Members are unlimited on every tier -- never gated, even at the
        admin limit with the flag ON."""
        async with app.app_context():
            db = current_app.db
            tenant_id = _make_tenant(db)
            _make_portal_user(db, tenant_id, global_role="admin", password_hash="x")
            db.commit()

            with _patch_resolve(), _patch_flag(True):
                result = await PortalAuthService.create_portal_user(
                    tenant_id=tenant_id,
                    email=f"member-{uuid4().hex[:8]}@example.com",
                    password="SecurePass123",
                    tenant_role="reader",
                )

            assert "error" not in result
            assert result["tenant_role"] == "reader"

    async def test_flag_off_allows_and_logs_would_block(self, app, caplog):
        """Observe-only: at the admin limit but flag OFF -- creation still
        succeeds, and a license_limit_would_block WARN is emitted."""
        async with app.app_context():
            db = current_app.db
            tenant_id = _make_tenant(db)

            with (
                _patch_resolve(),
                _patch_flag(False),
                caplog.at_level("WARNING"),
            ):
                result = await PortalAuthService.create_portal_user(
                    tenant_id=tenant_id,
                    email=f"tadmin-{uuid4().hex[:8]}@example.com",
                    password="SecurePass123",
                    tenant_role="admin",
                )

            assert "error" not in result
            assert result["tenant_role"] == "admin"
            assert "license_limit_would_block" in caplog.text
