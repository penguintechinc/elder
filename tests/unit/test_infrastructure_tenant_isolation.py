"""Tenant isolation regression tests for gh-237.

`organization_tree.get_organization_tree_stats` and `graph.get_graph`
resolved their path/query-supplied id via a bare `db.<table>[<id>]` bracket
lookup with no tenant filter -- any authenticated caller could pull another
tenant's org-tree stats or entity subgraph by guessing its numeric id.
Fixed via `apps.api.utils.tenant_scoping.get_tenant_scoped()`.
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


class TestOrganizationTreeStatsTenantIsolation:
    """GET /api/v1/organizations/<id>/tree-stats -- regression: gh-237."""

    @pytest.mark.asyncio
    @patch("apps.api.auth.decorators.get_current_user")
    async def test_rejects_other_tenant_org(
        self, mock_get_user, async_client, generate_token, app
    ):
        """Another tenant's org id must 404, not return its tree stats."""
        mock_get_user.return_value = MagicMock(id=1, is_superuser=False)
        token = generate_token(tenant_id=1, scopes=["infrastructure:read"])
        async with app.app_context():
            db = current_app.db
            other_tenant_id = _foreign_tenant(db)
            foreign_org_id = _org(db, other_tenant_id, name="Foreign Org")

        resp = await async_client.get(
            f"/api/v1/organizations/{foreign_org_id}/tree-stats",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 404, (await resp.get_data()).decode()[:200]

    @pytest.mark.asyncio
    @patch("apps.api.auth.decorators.get_current_user")
    async def test_allows_own_tenant_org(
        self, mock_get_user, async_client, generate_token, app
    ):
        """The caller's own tenant's org id must still succeed."""
        mock_get_user.return_value = MagicMock(id=1, is_superuser=False)
        token = generate_token(tenant_id=1, scopes=["infrastructure:read"])
        async with app.app_context():
            db = current_app.db
            org_id = _org(db, 1, name="Tenant1 Org")

        resp = await async_client.get(
            f"/api/v1/organizations/{org_id}/tree-stats",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200, (await resp.get_data()).decode()[:200]

    @pytest.mark.asyncio
    @patch("apps.api.auth.decorators.get_current_user")
    async def test_missing_tenant_claim_404s(
        self, mock_get_user, async_client, generate_token, app
    ):
        """No tenant claim on the token must never fall back to unscoped access."""
        mock_get_user.return_value = MagicMock(id=1, is_superuser=False)
        token = generate_token(tenant_id=0, scopes=["infrastructure:read"])
        async with app.app_context():
            db = current_app.db
            org_id = _org(db, 1, name="Tenant1 Org 2")

        resp = await async_client.get(
            f"/api/v1/organizations/{org_id}/tree-stats",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 404


class TestGraphEntityCenterTenantIsolation:
    """GET /api/v1/graph?entity_id=<id> -- regression: gh-237."""

    @pytest.mark.asyncio
    @patch("apps.api.auth.decorators.get_current_user")
    async def test_rejects_other_tenant_entity(
        self, mock_get_user, async_client, generate_token, app
    ):
        """Another tenant's entity id must 404, not return its subgraph."""
        mock_get_user.return_value = MagicMock(id=1, is_superuser=False)
        token = generate_token(tenant_id=1, scopes=["infrastructure:read"])
        async with app.app_context():
            db = current_app.db
            other_tenant_id = _foreign_tenant(db)
            foreign_org_id = _org(db, other_tenant_id, name="Foreign Org For Entity")
            foreign_entity_id = _entity(db, foreign_org_id, name="Foreign Entity")

        resp = await async_client.get(
            f"/api/v1/graph?entity_id={foreign_entity_id}",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 404, (await resp.get_data()).decode()[:200]

    @pytest.mark.asyncio
    @patch("apps.api.auth.decorators.get_current_user")
    async def test_allows_own_tenant_entity(
        self, mock_get_user, async_client, generate_token, app
    ):
        """The caller's own tenant's entity id must still succeed."""
        mock_get_user.return_value = MagicMock(id=1, is_superuser=False)
        token = generate_token(tenant_id=1, scopes=["infrastructure:read"])
        async with app.app_context():
            db = current_app.db
            org_id = _org(db, 1, name="Tenant1 Org For Entity")
            entity_id = _entity(db, org_id, name="Tenant1 Entity")

        resp = await async_client.get(
            f"/api/v1/graph?entity_id={entity_id}",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200, (await resp.get_data()).decode()[:200]
