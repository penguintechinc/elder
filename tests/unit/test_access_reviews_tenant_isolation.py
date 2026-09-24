"""Tenant isolation regression tests for gh-237 (access_reviews module).

`resource_roles.py` resolved caller-supplied entity/organization/identity/role
ids via a bare `db.<table>[<id>]` bracket lookup with no tenant filter --
another tenant's resource-role listing could be read (or, for
`create_resource_role`, a role could be granted on another tenant's resource
entirely) by guessing a numeric id. Fixed via
`apps.api.utils.tenant_scoping.get_tenant_scoped()` and the module-local
`_get_tenant_scoped_role()`/`_resource_owner()` helpers for resource_roles'
polymorphic (entity/organization) target.
"""

from datetime import UTC, datetime
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


def _resource_role(
    db, identity_id: int, resource_type: str, resource_id: int, role: str = "viewer"
) -> int:
    """Insert a resource_roles row directly (bypassing the route's broken insert).

    The underlying Postgres enum (``alembic/versions/001_add_enterprise_features.py``)
    stores ``resource_type``/``role_type`` member names uppercase
    (``sa.Enum("ENTITY", "ORGANIZATION", ...)`` /
    ``sa.Enum("MAINTAINER", "OPERATOR", "VIEWER", ...)``) -- pre-existing,
    unrelated to gh-237: ``create_resource_role``'s own insert passes the
    lowercase API-contract value straight through and never sets
    ``role_type`` at all, so it 500s on this same constraint today. Not
    fixed here; this helper works around it purely for test setup.
    """
    role_id = db.resource_roles.insert(
        identity_id=identity_id,
        resource_type=resource_type.upper(),
        resource_id=resource_id,
        role=role,
        role_type=role.upper(),
    )
    db.commit()
    return role_id


class TestEntityRolesTenantIsolation:
    """GET /api/v1/resource-roles/entities/<id>/roles -- regression: gh-237."""

    @pytest.mark.asyncio
    @patch("apps.api.auth.decorators.get_current_user")
    async def test_rejects_other_tenant_entity(
        self, mock_get_user, async_client, generate_token, app
    ):
        mock_get_user.return_value = MagicMock(id=1, is_superuser=False)
        token = generate_token(tenant_id=1, scopes=["access_reviews:read"])
        async with app.app_context():
            db = current_app.db
            other_tenant_id = _foreign_tenant(db)
            org_id = _org(db, other_tenant_id, "Foreign Org Entity Roles")
            entity_id = _entity(db, org_id, "Foreign Entity Roles")

        resp = await async_client.get(
            f"/api/v1/resource-roles/entities/{entity_id}/roles",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 404, (await resp.get_data()).decode()[:200]

    @pytest.mark.asyncio
    @patch("apps.api.auth.decorators.get_current_user")
    async def test_allows_own_tenant_entity(
        self, mock_get_user, async_client, generate_token, app
    ):
        mock_get_user.return_value = MagicMock(id=1, is_superuser=False)
        token = generate_token(tenant_id=1, scopes=["access_reviews:read"])
        async with app.app_context():
            db = current_app.db
            org_id = _org(db, 1, "Tenant1 Org Entity Roles")
            entity_id = _entity(db, org_id, "Tenant1 Entity Roles")

        resp = await async_client.get(
            f"/api/v1/resource-roles/entities/{entity_id}/roles",
            headers={"Authorization": f"Bearer {token}"},
        )
        # Not asserting 200: the route's subsequent listing query filters
        # `resource_type == "entity"` (lowercase) against a Postgres enum
        # that only accepts "ENTITY" (see _resource_role() docstring) --
        # a separate, pre-existing bug unrelated to gh-237. The only thing
        # under test here is that the tenant check let an own-tenant entity
        # through instead of 404ing it.
        assert resp.status_code != 404, (await resp.get_data()).decode()[:200]


class TestOrganizationRolesTenantIsolation:
    """GET /api/v1/resource-roles/organizations/<id>/roles -- regression: gh-237."""

    @pytest.mark.asyncio
    @patch("apps.api.auth.decorators.get_current_user")
    async def test_rejects_other_tenant_org(
        self, mock_get_user, async_client, generate_token, app
    ):
        mock_get_user.return_value = MagicMock(id=1, is_superuser=False)
        token = generate_token(tenant_id=1, scopes=["access_reviews:read"])
        async with app.app_context():
            db = current_app.db
            other_tenant_id = _foreign_tenant(db)
            org_id = _org(db, other_tenant_id, "Foreign Org Roles")

        resp = await async_client.get(
            f"/api/v1/resource-roles/organizations/{org_id}/roles",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 404, (await resp.get_data()).decode()[:200]

    @pytest.mark.asyncio
    @patch("apps.api.auth.decorators.get_current_user")
    async def test_allows_own_tenant_org(
        self, mock_get_user, async_client, generate_token, app
    ):
        mock_get_user.return_value = MagicMock(id=1, is_superuser=False)
        token = generate_token(tenant_id=1, scopes=["access_reviews:read"])
        async with app.app_context():
            db = current_app.db
            org_id = _org(db, 1, "Tenant1 Org Roles")

        resp = await async_client.get(
            f"/api/v1/resource-roles/organizations/{org_id}/roles",
            headers={"Authorization": f"Bearer {token}"},
        )
        # Not asserting 200: same pre-existing enum-casing bug as
        # test_allows_own_tenant_entity above -- only the tenant check
        # (not 404ing an own-tenant org) is under test here.
        assert resp.status_code != 404, (await resp.get_data()).decode()[:200]


class TestIdentityResourceRolesTenantIsolation:
    """GET /api/v1/resource-roles/identities/<id>/resource-roles -- regression: gh-237."""

    @pytest.mark.asyncio
    @patch("apps.api.auth.decorators.get_current_user")
    async def test_rejects_other_tenant_identity(
        self, mock_get_user, async_client, generate_token, app
    ):
        mock_get_user.return_value = MagicMock(id=1, is_superuser=False)
        token = generate_token(tenant_id=1, scopes=["access_reviews:read"])
        async with app.app_context():
            db = current_app.db
            other_tenant_id = _foreign_tenant(db)
            identity_id = _identity(
                db, other_tenant_id, f"foreign_id_{uuid4().hex[:8]}"
            )

        resp = await async_client.get(
            f"/api/v1/resource-roles/identities/{identity_id}/resource-roles",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 404, (await resp.get_data()).decode()[:200]

    @pytest.mark.asyncio
    @patch("apps.api.auth.decorators.get_current_user")
    async def test_allows_own_tenant_identity(
        self, mock_get_user, async_client, generate_token, app
    ):
        mock_get_user.return_value = MagicMock(id=1, is_superuser=False)
        token = generate_token(tenant_id=1, scopes=["access_reviews:read"])
        async with app.app_context():
            db = current_app.db
            identity_id = _identity(db, 1, f"own_id_{uuid4().hex[:8]}")

        resp = await async_client.get(
            f"/api/v1/resource-roles/identities/{identity_id}/resource-roles",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200, (await resp.get_data()).decode()[:200]


class TestRevokeResourceRoleTenantIsolation:
    """DELETE /api/v1/resource-roles/<id> -- regression: gh-237."""

    @pytest.mark.asyncio
    @patch("apps.api.auth.decorators.get_current_user")
    async def test_rejects_role_targeting_other_tenant_resource(
        self, mock_get_user, async_client, generate_token, app
    ):
        """A resource_roles row targeting another tenant's org must 404, not revoke."""
        mock_get_user.return_value = MagicMock(id=1, is_superuser=True)
        token = generate_token(tenant_id=1, scopes=["access_reviews:write"])
        async with app.app_context():
            db = current_app.db
            other_tenant_id = _foreign_tenant(db)
            org_id = _org(db, other_tenant_id, "Foreign Org Revoke")
            identity_id = _identity(
                db, other_tenant_id, f"foreign_revoke_{uuid4().hex[:8]}"
            )
            role_id = _resource_role(db, identity_id, "organization", org_id)

        resp = await async_client.delete(
            f"/api/v1/resource-roles/{role_id}",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 404, (await resp.get_data()).decode()[:200]

        async with app.app_context():
            db = current_app.db
            assert (
                db.resource_roles[role_id] is not None
            ), "cross-tenant revoke must not apply"

    @pytest.mark.asyncio
    @patch("apps.api.auth.decorators.get_current_user")
    async def test_allows_revoking_own_tenant_role(
        self, mock_get_user, async_client, generate_token, app
    ):
        mock_get_user.return_value = MagicMock(id=1, is_superuser=True)
        token = generate_token(tenant_id=1, scopes=["access_reviews:write"])
        async with app.app_context():
            db = current_app.db
            org_id = _org(db, 1, "Tenant1 Org Revoke")
            identity_id = _identity(db, 1, f"own_revoke_{uuid4().hex[:8]}")
            role_id = _resource_role(db, identity_id, "organization", org_id)

        resp = await async_client.delete(
            f"/api/v1/resource-roles/{role_id}",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 204, (await resp.get_data()).decode()[:200]


class TestCreateResourceRoleTenantIsolation:
    """POST /api/v1/resource-roles -- regression: gh-237.

    Even a superuser (whose token still carries the caller's own tenant
    claim) must not be able to grant a role on another tenant's resource --
    cross-tenant super-admin is a separately issued credential, not this
    per-tenant `is_superuser` flag (see security.md Service-to-Service Auth).
    """

    @pytest.mark.asyncio
    @patch("apps.api.auth.decorators.get_current_user")
    async def test_rejects_targeting_other_tenant_organization(
        self, mock_get_user, async_client, generate_token, app
    ):
        mock_get_user.return_value = MagicMock(id=1, is_superuser=True)
        token = generate_token(tenant_id=1, scopes=["access_reviews:write"])
        async with app.app_context():
            db = current_app.db
            other_tenant_id = _foreign_tenant(db)
            foreign_org_id = _org(db, other_tenant_id, "Foreign Org Create")
            identity_id = _identity(db, 1, f"grantee_{uuid4().hex[:8]}")

        resp = await async_client.post(
            "/api/v1/resource-roles",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "identity_id": identity_id,
                "resource_type": "organization",
                "resource_id": foreign_org_id,
                "role": "viewer",
            },
        )
        assert resp.status_code == 404, (await resp.get_data()).decode()[:200]

    @pytest.mark.asyncio
    async def test_resource_owner_allows_own_tenant_organization(self, app):
        """Unit-level check of `_resource_owner()` for the same-tenant case.

        Not exercised through the full POST endpoint: `check_and_create()`'s
        insert never sets the NOT-NULL `role_type` column and passes
        `resource_type` through lowercase against an uppercase-only Postgres
        enum (see `_resource_role()` docstring) -- both pre-existing,
        unrelated to gh-237, and would 500 regardless of tenant. This test
        isolates the tenant-ownership check this task added.
        """
        from apps.api.modules.access_reviews.routes.resource_roles import (
            _resource_owner,
        )

        async with app.app_context():
            db = current_app.db
            org_id = _org(db, 1, "Tenant1 Org Create")
            owner = _resource_owner(db, "organization", org_id, tenant_id=1)
            assert owner is not None
