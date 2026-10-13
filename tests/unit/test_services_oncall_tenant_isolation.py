"""Tenant isolation regression tests for gh-237 (services_oncall module).

Several services_oncall route files resolved caller-supplied ids via a bare
`db.<table>[<id>]` bracket lookup with no tenant filter -- rotations,
escalation policies, participants, and overrides could all be read, updated,
or deleted cross-tenant by guessing a numeric id. Fixed via
`apps.api.utils.tenant_scoping.get_tenant_scoped()`.

Mutation routes here are decorated with `@resource_role_required("maintainer")`,
whose `resource_param` default ("id") doesn't match this module's actual path
params (`rotation_id`/`participant_id`/`policy_id`/`override_id`) -- a
pre-existing, separate bug that would 400 every non-superuser request before
ever reaching the code under test. Tests use `is_superuser=True` (the
decorator's documented bypass) so requests exercise the tenant-scoping fix
itself rather than that unrelated defect.
"""

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from quart import current_app


def _foreign_tenant(db) -> int:
    """Create and return a tenant distinct from tenant 1."""
    tid = db.tenants.insert(
        name="Other Tenant", slug=f"other-{uuid4().hex[:8]}", is_active=True
    )
    db.commit()
    return tid


def _org(db, tenant_id: int, name: str = "Org") -> int:
    now = datetime.now(UTC)
    org_id = db.organizations.insert(
        name=name, tenant_id=tenant_id, created_at=now, updated_at=now
    )
    db.commit()
    return org_id


def _identity(db, tenant_id: int, username: str) -> int:
    identity_id = db.identities.insert(
        identity_type="human",
        username=username,
        email=f"{username}@example.com",
        tenant_id=tenant_id,
        auth_provider="local",
        is_active=True,
        is_superuser=False,
        mfa_enabled=False,
        must_change_password=False,
        portal_role="observer",
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    db.commit()
    return identity_id


def _rotation(db, tenant_id: int, organization_id: int, name: str = "Rotation") -> int:
    now = datetime.now(UTC)
    rotation_id = db.on_call_rotations.insert(
        name=name,
        is_active=True,
        scope_type="organization",
        organization_id=organization_id,
        schedule_type="manual",
        tenant_id=tenant_id,
        created_at=now,
        updated_at=now,
    )
    db.commit()
    return rotation_id


def _participant(db, rotation_id: int, identity_id: int, order_index: int = 1) -> int:
    now = datetime.now(UTC)
    participant_id = db.on_call_rotation_participants.insert(
        rotation_id=rotation_id,
        identity_id=identity_id,
        order_index=order_index,
        is_active=True,
        created_at=now,
        updated_at=now,
    )
    db.commit()
    return participant_id


def _override(
    db, rotation_id: int, original_identity_id: int, override_identity_id: int
) -> int:
    now = datetime.now(UTC)
    override_id = db.on_call_overrides.insert(
        rotation_id=rotation_id,
        original_identity_id=original_identity_id,
        override_identity_id=override_identity_id,
        start_datetime=now,
        end_datetime=now + timedelta(hours=1),
        created_at=now,
    )
    db.commit()
    return override_id


def _escalation_policy(db, rotation_id: int, identity_id: int, level: int = 1) -> int:
    now = datetime.now(UTC)
    policy_id = db.on_call_escalation_policies.insert(
        rotation_id=rotation_id,
        level=level,
        escalation_type="identity",
        identity_id=identity_id,
        created_at=now,
        updated_at=now,
    )
    db.commit()
    return policy_id


class TestRotationTenantIsolation:
    """PUT/DELETE /api/v1/on-call/rotations/<id> -- regression: gh-237."""

    @pytest.mark.asyncio
    @patch("apps.api.auth.decorators.get_current_user")
    async def test_update_rejects_other_tenant_rotation(
        self, mock_get_user, async_client, generate_token, app
    ):
        """Another tenant's rotation id must 404, not accept the update."""
        mock_get_user.return_value = MagicMock(id=1, is_superuser=True)
        token = generate_token(tenant_id=1, scopes=["services_oncall:write"])
        async with app.app_context():
            db = current_app.db
            other_tenant_id = _foreign_tenant(db)
            org_id = _org(db, other_tenant_id, "Foreign Org")
            rotation_id = _rotation(db, other_tenant_id, org_id, "Foreign Rotation")

        resp = await async_client.put(
            f"/api/v1/on-call/rotations/{rotation_id}",
            headers={"Authorization": f"Bearer {token}"},
            json={"name": "Hijacked"},
        )
        assert resp.status_code == 404, (await resp.get_data()).decode()[:200]

        async with app.app_context():
            db = current_app.db
            row = db.on_call_rotations[rotation_id]
            assert row.name == "Foreign Rotation", "cross-tenant update must not apply"

    @pytest.mark.asyncio
    @patch("apps.api.auth.decorators.get_current_user")
    async def test_update_allows_own_tenant_rotation(
        self, mock_get_user, async_client, generate_token, app
    ):
        """The caller's own tenant's rotation id must still succeed."""
        mock_get_user.return_value = MagicMock(id=1, is_superuser=True)
        token = generate_token(tenant_id=1, scopes=["services_oncall:write"])
        async with app.app_context():
            db = current_app.db
            org_id = _org(db, 1, "Tenant1 Org")
            rotation_id = _rotation(db, 1, org_id, "Tenant1 Rotation")

        resp = await async_client.put(
            f"/api/v1/on-call/rotations/{rotation_id}",
            headers={"Authorization": f"Bearer {token}"},
            json={"name": "Renamed"},
        )
        assert resp.status_code == 200, (await resp.get_data()).decode()[:200]

    @pytest.mark.asyncio
    @patch("apps.api.auth.decorators.get_current_user")
    async def test_delete_rejects_other_tenant_rotation(
        self, mock_get_user, async_client, generate_token, app
    ):
        """Another tenant's rotation id must 404, not be deleted."""
        mock_get_user.return_value = MagicMock(id=1, is_superuser=True)
        token = generate_token(tenant_id=1, scopes=["services_oncall:write"])
        async with app.app_context():
            db = current_app.db
            other_tenant_id = _foreign_tenant(db)
            org_id = _org(db, other_tenant_id, "Foreign Org For Delete")
            rotation_id = _rotation(
                db, other_tenant_id, org_id, "Foreign Rotation Delete"
            )

        resp = await async_client.delete(
            f"/api/v1/on-call/rotations/{rotation_id}",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 404, (await resp.get_data()).decode()[:200]

        async with app.app_context():
            db = current_app.db
            assert (
                db.on_call_rotations[rotation_id] is not None
            ), "cross-tenant delete must not apply"


class TestEscalationPolicyTenantIsolation:
    """PUT/DELETE /api/v1/on-call/rotations/escalations/<policy_id> -- regression: gh-237."""

    @pytest.mark.asyncio
    @patch("apps.api.auth.decorators.get_current_user")
    async def test_update_rejects_other_tenant_policy(
        self, mock_get_user, async_client, generate_token, app
    ):
        mock_get_user.return_value = MagicMock(id=1, is_superuser=True)
        token = generate_token(tenant_id=1, scopes=["services_oncall:write"])
        async with app.app_context():
            db = current_app.db
            other_tenant_id = _foreign_tenant(db)
            org_id = _org(db, other_tenant_id, "Foreign Org Policy")
            rotation_id = _rotation(
                db, other_tenant_id, org_id, "Foreign Rotation Policy"
            )
            identity_id = _identity(
                db, other_tenant_id, f"foreign_esc_{uuid4().hex[:8]}"
            )
            policy_id = _escalation_policy(db, rotation_id, identity_id)

        resp = await async_client.put(
            f"/api/v1/on-call/rotations/escalations/{policy_id}",
            headers={"Authorization": f"Bearer {token}"},
            json={"level": 2},
        )
        assert resp.status_code == 404, (await resp.get_data()).decode()[:200]

    @pytest.mark.asyncio
    @patch("apps.api.auth.decorators.get_current_user")
    async def test_delete_rejects_other_tenant_policy(
        self, mock_get_user, async_client, generate_token, app
    ):
        mock_get_user.return_value = MagicMock(id=1, is_superuser=True)
        token = generate_token(tenant_id=1, scopes=["services_oncall:write"])
        async with app.app_context():
            db = current_app.db
            other_tenant_id = _foreign_tenant(db)
            org_id = _org(db, other_tenant_id, "Foreign Org Policy Del")
            rotation_id = _rotation(
                db, other_tenant_id, org_id, "Foreign Rotation Policy Del"
            )
            identity_id = _identity(
                db, other_tenant_id, f"foreign_esc_del_{uuid4().hex[:8]}"
            )
            policy_id = _escalation_policy(db, rotation_id, identity_id)

        resp = await async_client.delete(
            f"/api/v1/on-call/rotations/escalations/{policy_id}",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 404, (await resp.get_data()).decode()[:200]

        async with app.app_context():
            db = current_app.db
            assert (
                db.on_call_escalation_policies[policy_id] is not None
            ), "cross-tenant delete must not apply"

    @pytest.mark.asyncio
    @patch("apps.api.auth.decorators.get_current_user")
    async def test_update_allows_own_tenant_policy(
        self, mock_get_user, async_client, generate_token, app
    ):
        mock_get_user.return_value = MagicMock(id=1, is_superuser=True)
        token = generate_token(tenant_id=1, scopes=["services_oncall:write"])
        async with app.app_context():
            db = current_app.db
            org_id = _org(db, 1, "Tenant1 Org Policy")
            rotation_id = _rotation(db, 1, org_id, "Tenant1 Rotation Policy")
            identity_id = _identity(db, 1, f"own_esc_{uuid4().hex[:8]}")
            policy_id = _escalation_policy(db, rotation_id, identity_id)

        resp = await async_client.put(
            f"/api/v1/on-call/rotations/escalations/{policy_id}",
            headers={"Authorization": f"Bearer {token}"},
            json={"level": 3},
        )
        assert resp.status_code == 200, (await resp.get_data()).decode()[:200]


class TestParticipantTenantIsolation:
    """PUT/DELETE .../participants/<participant_id> -- regression: gh-237."""

    @pytest.mark.asyncio
    @patch("apps.api.auth.decorators.get_current_user")
    async def test_update_rejects_other_tenant_participant(
        self, mock_get_user, async_client, generate_token, app
    ):
        mock_get_user.return_value = MagicMock(id=1, is_superuser=True)
        token = generate_token(tenant_id=1, scopes=["services_oncall:write"])
        async with app.app_context():
            db = current_app.db
            other_tenant_id = _foreign_tenant(db)
            org_id = _org(db, other_tenant_id, "Foreign Org Part")
            rotation_id = _rotation(
                db, other_tenant_id, org_id, "Foreign Rotation Part"
            )
            identity_id = _identity(
                db, other_tenant_id, f"foreign_part_{uuid4().hex[:8]}"
            )
            participant_id = _participant(db, rotation_id, identity_id)

        resp = await async_client.put(
            f"/api/v1/on-call/rotations/{rotation_id}/participants/{participant_id}",
            headers={"Authorization": f"Bearer {token}"},
            json={"order_index": 5},
        )
        assert resp.status_code == 404, (await resp.get_data()).decode()[:200]

    @pytest.mark.asyncio
    @patch("apps.api.auth.decorators.get_current_user")
    async def test_delete_rejects_other_tenant_participant(
        self, mock_get_user, async_client, generate_token, app
    ):
        mock_get_user.return_value = MagicMock(id=1, is_superuser=True)
        token = generate_token(tenant_id=1, scopes=["services_oncall:write"])
        async with app.app_context():
            db = current_app.db
            other_tenant_id = _foreign_tenant(db)
            org_id = _org(db, other_tenant_id, "Foreign Org Part Del")
            rotation_id = _rotation(
                db, other_tenant_id, org_id, "Foreign Rotation Part Del"
            )
            identity_id = _identity(
                db, other_tenant_id, f"foreign_part_del_{uuid4().hex[:8]}"
            )
            participant_id = _participant(db, rotation_id, identity_id)

        resp = await async_client.delete(
            f"/api/v1/on-call/rotations/{rotation_id}/participants/{participant_id}",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 404, (await resp.get_data()).decode()[:200]

        async with app.app_context():
            db = current_app.db
            assert (
                db.on_call_rotation_participants[participant_id] is not None
            ), "cross-tenant delete must not apply"

    @pytest.mark.asyncio
    @patch("apps.api.auth.decorators.get_current_user")
    async def test_update_allows_own_tenant_participant(
        self, mock_get_user, async_client, generate_token, app
    ):
        mock_get_user.return_value = MagicMock(id=1, is_superuser=True)
        token = generate_token(tenant_id=1, scopes=["services_oncall:write"])
        async with app.app_context():
            db = current_app.db
            org_id = _org(db, 1, "Tenant1 Org Part")
            rotation_id = _rotation(db, 1, org_id, "Tenant1 Rotation Part")
            identity_id = _identity(db, 1, f"own_part_{uuid4().hex[:8]}")
            participant_id = _participant(db, rotation_id, identity_id)

        resp = await async_client.put(
            f"/api/v1/on-call/rotations/{rotation_id}/participants/{participant_id}",
            headers={"Authorization": f"Bearer {token}"},
            json={"order_index": 2},
        )
        assert resp.status_code == 200, (await resp.get_data()).decode()[:200]


class TestOverrideTenantIsolation:
    """PUT/DELETE /api/v1/on-call/rotations/overrides/<override_id> -- regression: gh-237."""

    @pytest.mark.asyncio
    @patch("apps.api.auth.decorators.get_current_user")
    async def test_update_rejects_other_tenant_override(
        self, mock_get_user, async_client, generate_token, app
    ):
        mock_get_user.return_value = MagicMock(id=1, is_superuser=True)
        token = generate_token(tenant_id=1, scopes=["services_oncall:write"])
        async with app.app_context():
            db = current_app.db
            other_tenant_id = _foreign_tenant(db)
            org_id = _org(db, other_tenant_id, "Foreign Org Override")
            rotation_id = _rotation(
                db, other_tenant_id, org_id, "Foreign Rotation Override"
            )
            original_id = _identity(
                db, other_tenant_id, f"foreign_orig_{uuid4().hex[:8]}"
            )
            override_id_identity = _identity(
                db, other_tenant_id, f"foreign_override_{uuid4().hex[:8]}"
            )
            override_id = _override(db, rotation_id, original_id, override_id_identity)

        resp = await async_client.put(
            f"/api/v1/on-call/rotations/overrides/{override_id}",
            headers={"Authorization": f"Bearer {token}"},
            json={"reason": "hijacked"},
        )
        assert resp.status_code == 404, (await resp.get_data()).decode()[:200]

    @pytest.mark.asyncio
    @patch("apps.api.auth.decorators.get_current_user")
    async def test_delete_rejects_other_tenant_override(
        self, mock_get_user, async_client, generate_token, app
    ):
        mock_get_user.return_value = MagicMock(id=1, is_superuser=True)
        token = generate_token(tenant_id=1, scopes=["services_oncall:write"])
        async with app.app_context():
            db = current_app.db
            other_tenant_id = _foreign_tenant(db)
            org_id = _org(db, other_tenant_id, "Foreign Org Override Del")
            rotation_id = _rotation(
                db, other_tenant_id, org_id, "Foreign Rotation Override Del"
            )
            original_id = _identity(
                db, other_tenant_id, f"foreign_orig_del_{uuid4().hex[:8]}"
            )
            override_id_identity = _identity(
                db, other_tenant_id, f"foreign_override_del_{uuid4().hex[:8]}"
            )
            override_id = _override(db, rotation_id, original_id, override_id_identity)

        resp = await async_client.delete(
            f"/api/v1/on-call/rotations/overrides/{override_id}",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 404, (await resp.get_data()).decode()[:200]

        async with app.app_context():
            db = current_app.db
            assert (
                db.on_call_overrides[override_id] is not None
            ), "cross-tenant delete must not apply"

    @pytest.mark.asyncio
    @patch("apps.api.auth.decorators.get_current_user")
    async def test_update_allows_own_tenant_override(
        self, mock_get_user, async_client, generate_token, app
    ):
        mock_get_user.return_value = MagicMock(id=1, is_superuser=True)
        token = generate_token(tenant_id=1, scopes=["services_oncall:write"])
        async with app.app_context():
            db = current_app.db
            org_id = _org(db, 1, "Tenant1 Org Override")
            rotation_id = _rotation(db, 1, org_id, "Tenant1 Rotation Override")
            original_id = _identity(db, 1, f"own_orig_{uuid4().hex[:8]}")
            override_id_identity = _identity(db, 1, f"own_override_{uuid4().hex[:8]}")
            override_id = _override(db, rotation_id, original_id, override_id_identity)

        resp = await async_client.put(
            f"/api/v1/on-call/rotations/overrides/{override_id}",
            headers={"Authorization": f"Bearer {token}"},
            json={"reason": "legit"},
        )
        assert resp.status_code == 200, (await resp.get_data()).decode()[:200]
