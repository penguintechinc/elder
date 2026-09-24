"""Tenant isolation regression tests for gh-237 (backfill half A).

Covers a representative, high-severity subset of the ~150 unscoped
`db.<table>[<id>]` lookups backfilled in apps/api/modules/{infrastructure,
discovery,ipam,sbom,secrets,issues} and apps/api/api/v1/ -- each of these
previously let an authenticated caller read (or, for users.py, read/write/
delete) another tenant's data by guessing its numeric id. Fixed via
`apps.api.utils.tenant_scoping.get_tenant_scoped()`.
"""

from datetime import UTC, datetime
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from quart import current_app


def _foreign_tenant(db, name: str = "Other Tenant") -> int:
    """Create and return a tenant distinct from tenant 1."""
    tid = db.tenants.insert(name=name, slug=f"other-{uuid4().hex[:8]}", is_active=True)
    db.commit()
    return tid


def _org(db, tenant_id: int, name: str = "Org") -> int:
    now = datetime.now(UTC)
    org_id = db.organizations.insert(
        name=name, tenant_id=tenant_id, created_at=now, updated_at=now
    )
    db.commit()
    return org_id


def _entity(db, org_id: int, name: str = "Entity", entity_type: str = "compute") -> int:
    now = datetime.now(UTC)
    entity_id = db.entities.insert(
        name=name,
        type=entity_type,
        organization_id=org_id,
        created_at=now,
        updated_at=now,
    )
    db.commit()
    return entity_id


def _sbom_scan(db, tenant_id: int, status: str = "pending") -> int:
    scan_id = db.sbom_scans.insert(
        tenant_id=tenant_id,
        parent_type="service",
        parent_id=1,
        scan_type="git_clone",
        status=status,
        components_found=0,
        components_added=0,
        components_updated=0,
        components_removed=0,
    )
    db.commit()
    return scan_id


def _sbom_schedule(db, tenant_id: int, next_run_at) -> int:
    schedule_id = db.sbom_scan_schedules.insert(
        tenant_id=tenant_id,
        parent_type="service",
        parent_id=1,
        schedule_cron="0 0 * * *",
        is_active=True,
        next_run_at=next_run_at,
    )
    db.commit()
    return schedule_id


def _mock_user(**attrs) -> MagicMock:
    """MagicMock standing in for a PyDAL identity Row.

    `role_required` (apps/api/auth/decorators.py) reads the role via
    dict-style `user.get("portal_role", "observer")`, matching how a real
    PyDAL Row behaves -- `MagicMock(portal_role=...)` alone only sets
    attribute access, so `.get()` would otherwise return an unconfigured
    MagicMock (and fail to JSON-serialize on a 403 branch).
    """
    user = MagicMock(**attrs)
    user.get.side_effect = lambda key, default=None: attrs.get(key, default)
    return user


def _identity(db, tenant_id: int, username: str) -> int:
    identity_id = db.identities.insert(
        username=username,
        identity_type="human",
        auth_provider="local",
        is_active=True,
        is_superuser=False,
        mfa_enabled=False,
        must_change_password=False,
        portal_role="observer",
        tenant_id=tenant_id,
    )
    db.commit()
    return identity_id


class TestEntityGetTenantIsolation:
    """GET /api/v1/entities/<id> -- regression: gh-237.

    entities has no tenant_id of its own (org_fk mode via organizations).
    """

    @pytest.mark.asyncio
    @patch("apps.api.auth.decorators.get_current_user")
    async def test_rejects_other_tenant_entity(
        self, mock_get_user, async_client, generate_token, app
    ):
        mock_get_user.return_value = MagicMock(id=1, is_superuser=False)
        token = generate_token(tenant_id=1, scopes=["infrastructure:read"])
        async with app.app_context():
            db = current_app.db
            other_tenant_id = _foreign_tenant(db)
            foreign_org_id = _org(
                db, other_tenant_id, name="Foreign Org for Entity GET"
            )
            foreign_entity_id = _entity(db, foreign_org_id, name="Foreign Entity GET")

        resp = await async_client.get(
            f"/api/v1/entities/{foreign_entity_id}",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 404, (await resp.get_data()).decode()

    @pytest.mark.asyncio
    @patch("apps.api.auth.decorators.get_current_user")
    async def test_allows_own_tenant_entity(
        self, mock_get_user, async_client, generate_token, app
    ):
        mock_get_user.return_value = MagicMock(id=1, is_superuser=False)
        token = generate_token(tenant_id=1, scopes=["infrastructure:read"])
        async with app.app_context():
            db = current_app.db
            org_id = _org(db, 1, name="Tenant1 Org for Entity GET")
            entity_id = _entity(db, org_id, name="Tenant1 Entity GET")

        resp = await async_client.get(
            f"/api/v1/entities/{entity_id}",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200, (await resp.get_data()).decode()


class TestSbomPendingScansTenantIsolation:
    """GET /api/v1/sbom/scans/pending -- regression: gh-237.

    Most severe fix in this backfill: this endpoint previously returned
    EVERY tenant's pending scans -- including resolved scanner credential
    tokens -- to any caller with sbom:read, regardless of which tenant
    created the scan.
    """

    @pytest.mark.asyncio
    @patch("apps.api.auth.decorators.get_current_user")
    async def test_excludes_other_tenant_pending_scans(
        self, mock_get_user, async_client, generate_token, app
    ):
        mock_get_user.return_value = MagicMock(id=1, is_superuser=False)
        token = generate_token(tenant_id=1, scopes=["sbom:read"])
        async with app.app_context():
            db = current_app.db
            other_tenant_id = _foreign_tenant(db, name="Other Tenant Pending Scans")
            foreign_scan_id = _sbom_scan(db, other_tenant_id)
            own_scan_id = _sbom_scan(db, 1)

        resp = await async_client.get(
            "/api/v1/sbom/scans/pending",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200, (await resp.get_data()).decode()
        body = await resp.get_json()
        ids = {item["id"] for item in body}
        assert own_scan_id in ids
        assert foreign_scan_id not in ids


class TestSbomDueSchedulesTenantIsolation:
    """GET /api/v1/sbom/schedules/due -- regression: gh-237.

    Same class of leak as sbom/scans/pending: previously returned every
    tenant's due schedules (including credential_id/credential_mapping)
    to any caller with sbom:read.
    """

    @pytest.mark.asyncio
    @patch("apps.api.auth.decorators.get_current_user")
    async def test_excludes_other_tenant_due_schedules(
        self, mock_get_user, async_client, generate_token, app
    ):
        mock_get_user.return_value = MagicMock(id=1, is_superuser=False)
        token = generate_token(tenant_id=1, scopes=["sbom:read"])
        past = datetime(2020, 1, 1, tzinfo=UTC)
        async with app.app_context():
            db = current_app.db
            other_tenant_id = _foreign_tenant(db, name="Other Tenant Due Schedules")
            foreign_schedule_id = _sbom_schedule(db, other_tenant_id, past)
            own_schedule_id = _sbom_schedule(db, 1, past)

        resp = await async_client.get(
            "/api/v1/sbom/schedules/due",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200, (await resp.get_data()).decode()
        body = await resp.get_json()
        ids = {item["id"] for item in body}
        assert own_schedule_id in ids
        assert foreign_schedule_id not in ids


class TestIdentityGetTenantIsolation:
    """GET /api/v1/identities/<id> -- regression: gh-237.

    view_users/manage_users are role-scope checks (check_permission), not
    tenant-aware -- a non-superuser tenant admin could previously read
    another tenant's identity by guessing its numeric id.
    """

    @pytest.mark.asyncio
    @patch("apps.api.auth.decorators.get_current_user")
    async def test_rejects_other_tenant_identity(
        self, mock_get_user, async_client, generate_token, app
    ):
        mock_get_user.return_value = MagicMock(
            id=1, is_superuser=False, tenant_id=1, portal_role="admin"
        )
        token = generate_token(tenant_id=1, scopes=[])
        async with app.app_context():
            db = current_app.db
            other_tenant_id = _foreign_tenant(db, name="Other Tenant Identity GET")
            foreign_identity_id = _identity(
                db, other_tenant_id, f"foreign-{uuid4().hex[:8]}"
            )

        resp = await async_client.get(
            f"/api/v1/identities/{foreign_identity_id}",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 404, (await resp.get_data()).decode()

    @pytest.mark.asyncio
    @patch("apps.api.auth.decorators.get_current_user")
    async def test_allows_own_tenant_identity(
        self, mock_get_user, async_client, generate_token, app
    ):
        mock_get_user.return_value = MagicMock(
            id=1, is_superuser=False, tenant_id=1, portal_role="admin"
        )
        token = generate_token(tenant_id=1, scopes=[])
        async with app.app_context():
            db = current_app.db
            identity_id = _identity(db, 1, f"own-{uuid4().hex[:8]}")

        resp = await async_client.get(
            f"/api/v1/identities/{identity_id}",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200, (await resp.get_data()).decode()


class TestUsersListTenantIsolation:
    """GET /api/v1/users -- regression: gh-237.

    Most severe fix in api/v1: role_required("admin") allows a per-tenant
    portal_role=="admin" user (not just global superusers) -- without the
    fix, ANY tenant admin could list every tenant's users, and the sibling
    PATCH/DELETE endpoints let them edit (including is_active/portal_role)
    or delete another tenant's user, or set is_active on their own account
    across tenant boundaries.
    """

    @pytest.mark.asyncio
    @patch("apps.api.auth.decorators.get_current_user")
    async def test_excludes_other_tenant_users(
        self, mock_get_user, async_client, generate_token, app
    ):
        caller = _mock_user(id=1, is_superuser=False, tenant_id=1, portal_role="admin")
        mock_get_user.return_value = caller
        token = generate_token(tenant_id=1, scopes=[])
        async with app.app_context():
            db = current_app.db
            tenant1_total_before = db(db.identities.tenant_id == 1).count()
            other_tenant_id = _foreign_tenant(db, name="Other Tenant Users List")
            foreign_identity_id = _identity(
                db, other_tenant_id, f"foreign-users-{uuid4().hex[:8]}"
            )
            _identity(db, 1, f"own-users-{uuid4().hex[:8]}")

        resp = await async_client.get(
            "/api/v1/users?per_page=1000",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200, (await resp.get_data()).decode()
        body = await resp.get_json()
        # The `total` count (not just the paginated `items` page) must
        # reflect only the caller's own tenant -- proves the newly-created
        # foreign-tenant identity isn't counted, regardless of how many
        # pre-existing identities pagination would otherwise hide it behind.
        assert body["total"] == tenant1_total_before + 1
        ids = {item["id"] for item in body["items"]}
        assert foreign_identity_id not in ids

    @pytest.mark.asyncio
    @patch("apps.api.auth.decorators.get_current_user")
    async def test_delete_rejects_other_tenant_user(
        self, mock_get_user, async_client, generate_token, app
    ):
        caller = _mock_user(id=1, is_superuser=False, tenant_id=1, portal_role="admin")
        mock_get_user.return_value = caller
        token = generate_token(tenant_id=1, scopes=[])
        async with app.app_context():
            db = current_app.db
            other_tenant_id = _foreign_tenant(db, name="Other Tenant User Delete")
            foreign_identity_id = _identity(
                db, other_tenant_id, f"foreign-del-{uuid4().hex[:8]}"
            )

        resp = await async_client.delete(
            f"/api/v1/users/{foreign_identity_id}",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 404, (await resp.get_data()).decode()

        async with app.app_context():
            db = current_app.db
            still_there = db.identities[foreign_identity_id]
            assert still_there is not None, "cross-tenant delete must be a no-op"
