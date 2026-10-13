"""Unit tests for Task 5 wiring: tenant-create + node-registration license enforcement.

Covers apps/api/api/v1/tenants.py::create_tenant (hard-counted globally via
check_limit("tenant", None)) and apps/api/main.py::_init_service_node_registration
(soft/observe-only -- check_limit("node", None) is called for its WARN-log side
effect only; a pod is never refused startup regardless of the flag).
See docs/superpowers/plans/2026-08-20-license-enforcement-phase2.md Task 5.
"""

from datetime import UTC, datetime, timedelta
from unittest.mock import patch
from uuid import uuid4

import jwt
import pytest

from apps.api.common.licensing.limits import LimitSet

_AT_LIMIT_TENANT = LimitSet(
    max_global_admins=1,
    max_tenant_admins=0,
    max_teams=1,
    max_tenants=1,
    max_objects=1000,
    max_nodes_per_type=1,
)


def _patch_resolve(tier: str, limits: LimitSet):
    return patch(
        "apps.api.common.licensing.enforce.resolve_limits",
        return_value=(tier, limits),
    )


def _patch_count(value: int):
    return patch(
        "apps.api.common.licensing.enforce._count_for_kind", return_value=value
    )


def _patch_flag(enabled: bool):
    return patch("apps.api.common.licensing.enforce.flag_enabled", return_value=enabled)


def _portal_admin_token(app, tenant_id: int = 1) -> str:
    """Mint a portal_user JWT with global_role=admin (see portal_auth.py:419)."""
    secret_key = app.config.get("JWT_SECRET_KEY") or app.config.get("SECRET_KEY")
    now = datetime.now(UTC)
    payload = {
        "sub": "1",
        "email": "admin@example.com",
        "tenant_id": tenant_id,
        "tenant_role": None,
        "global_role": "admin",
        "type": "portal_user",
        "iat": now,
        "exp": now + timedelta(hours=1),
    }
    return jwt.encode(payload, secret_key, algorithm="HS256")


@pytest.mark.asyncio
class TestCreateTenantEnforcement:
    """POST /api/v1/tenants: check_limit("tenant", None) runs before the insert."""

    async def test_flag_on_at_limit_blocks_with_402(self, async_client, app):
        token = _portal_admin_token(app)
        with (
            _patch_resolve("community", _AT_LIMIT_TENANT),
            _patch_count(1),  # count == max_tenants (1) -- at the limit
            _patch_flag(True),
        ):
            response = await async_client.post(
                "/api/v1/tenants",
                headers={"Authorization": f"Bearer {token}"},
                json={
                    "name": "Blocked Tenant",
                    "slug": f"blocked-{uuid4().hex[:8]}",
                },
            )

        assert response.status_code == 402
        body = await response.get_json()
        assert body == {
            "error": "limit_reached",
            "limit": "tenant",
            "tier": "community",
            "upgrade": True,
        }

    async def test_flag_off_observes_only_and_creates(self, async_client, app):
        token = _portal_admin_token(app)
        slug = f"allowed-{uuid4().hex[:8]}"
        with (
            _patch_resolve("community", _AT_LIMIT_TENANT),
            _patch_count(1),  # at limit, but flag is OFF -- observe-only
            _patch_flag(False),
        ):
            response = await async_client.post(
                "/api/v1/tenants",
                headers={"Authorization": f"Bearer {token}"},
                json={"name": "Allowed Tenant", "slug": slug},
            )

        assert response.status_code == 201
        body = await response.get_json()
        assert body["slug"] == slug

    async def test_under_limit_creates_without_checking_flag(self, async_client, app):
        token = _portal_admin_token(app)
        slug = f"underlimit-{uuid4().hex[:8]}"
        with (
            _patch_resolve("community", _AT_LIMIT_TENANT),
            _patch_count(0),  # under max_tenants (1)
            _patch_flag(True) as mock_flag,
        ):
            response = await async_client.post(
                "/api/v1/tenants",
                headers={"Authorization": f"Bearer {token}"},
                json={"name": "Underlimit Tenant", "slug": slug},
            )

        assert response.status_code == 201
        mock_flag.assert_not_called()


class TestNodeStartupRegistration:
    """apps/api/main.py::_init_service_node_registration: soft node gate.

    Node is never allowed to block a pod from starting, even at/over the
    limit with the flag ON -- check_limit("node", None) is called purely for
    its WARN-log side effect (`license_limit_would_block` /
    `license_limit_blocked`, already covered by test_license_enforce.py); its
    return value must be discarded by the wiring, not acted on.
    """

    def test_check_limit_called_before_register_and_never_blocks(self, app):
        """Even a 402-returning check_limit must not stop register_node."""
        from apps.api.main import _init_service_node_registration

        calls: list[tuple[str, int | None]] = []

        async def fake_check_limit(kind, tenant_id):
            calls.append((kind, tenant_id))
            # Simulate the framework's real over-limit-and-flag-ON behavior.
            return ({"error": "limit_reached", "limit": kind}, 402)

        registered: dict[str, bool] = {}

        def fake_register_node(db, service_type, pod_id):
            registered["called"] = True
            return 1

        with (
            patch(
                "apps.api.common.licensing.enforce.check_limit",
                new=fake_check_limit,
            ),
            patch(
                "apps.api.models.service_node.register_node",
                new=fake_register_node,
            ),
        ):
            _init_service_node_registration(app)

        assert calls == [("node", None)]
        assert registered.get("called") is True

    def test_registration_survives_check_limit_exception(self, app):
        """A raising check_limit is still best-effort -- never crash startup."""
        from apps.api.main import _init_service_node_registration

        async def raising_check_limit(kind, tenant_id):
            raise RuntimeError("license server unreachable")

        with patch(
            "apps.api.common.licensing.enforce.check_limit",
            new=raising_check_limit,
        ):
            # Must not raise -- _init_service_node_registration is
            # try/except-wrapped end to end (best-effort startup step).
            _init_service_node_registration(app)
