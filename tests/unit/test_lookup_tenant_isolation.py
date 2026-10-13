"""Tenant isolation + auth regression tests for gh-237.

``apps/api/api/v1/lookup.py`` had NO authentication at all -- any caller,
unauthenticated, could GET /lookup/<id> or POST /lookup/batch and receive
another tenant's entity data (hostname/IP/OS attributes per the route's own
docstring example). Fixed by requiring a valid JWT (``infrastructure:read``
scope) and scoping every lookup to the caller's tenant via
``apps.api.utils.tenant_scoping.get_tenant_scoped()``.

# regression: gh-237
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


class TestLookupEntityAuth:
    """GET /lookup/<id> must reject unauthenticated callers outright."""

    @pytest.mark.asyncio
    async def test_unauthenticated_returns_401(self, async_client, app):
        """No Authorization header at all -- must 401, never leak the row."""
        async with app.app_context():
            db = current_app.db
            org_id = _org(db, 1, name="Tenant1 Org Auth")
            entity_id = _entity(db, org_id, name="Secret Server")

        resp = await async_client.get(f"/lookup/{entity_id}")
        assert resp.status_code == 401, (await resp.get_data()).decode()[:200]


class TestLookupBatchAuth:
    """POST /lookup/batch must reject unauthenticated callers outright."""

    @pytest.mark.asyncio
    async def test_unauthenticated_returns_401(self, async_client, app):
        async with app.app_context():
            db = current_app.db
            org_id = _org(db, 1, name="Tenant1 Org Batch Auth")
            entity_id = _entity(db, org_id, name="Secret Batch Server")

        resp = await async_client.post("/lookup/batch", json={"ids": [entity_id]})
        assert resp.status_code == 401, (await resp.get_data()).decode()[:200]


class TestLookupEntityTenantIsolation:
    """GET /lookup/<id> -- regression: gh-237."""

    @pytest.mark.asyncio
    @patch("apps.api.auth.decorators.get_current_user")
    async def test_rejects_other_tenant_entity(
        self, mock_get_user, async_client, generate_token, app
    ):
        """Another tenant's entity id must 404, not return its attributes."""
        mock_get_user.return_value = MagicMock(id=1, is_superuser=False)
        token = generate_token(tenant_id=1, scopes=["infrastructure:read"])
        async with app.app_context():
            db = current_app.db
            other_tenant_id = _foreign_tenant(db)
            foreign_org_id = _org(db, other_tenant_id, name="Foreign Org")
            foreign_entity_id = _entity(db, foreign_org_id, name="Foreign Web Server")

        resp = await async_client.get(
            f"/lookup/{foreign_entity_id}",
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
            org_id = _org(db, 1, name="Tenant1 Org Own")
            entity_id = _entity(db, org_id, name="Tenant1 Web Server")

        resp = await async_client.get(
            f"/lookup/{entity_id}",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200, (await resp.get_data()).decode()[:200]
        body = await resp.get_json()
        assert body["id"] == entity_id

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
            org_id = _org(db, 1, name="Tenant1 Org NoClaim")
            entity_id = _entity(db, org_id, name="Tenant1 Web Server NoClaim")

        resp = await async_client.get(
            f"/lookup/{entity_id}",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 404


class TestLookupBatchTenantIsolation:
    """POST /lookup/batch -- regression: gh-237."""

    @pytest.mark.asyncio
    @patch("apps.api.auth.decorators.get_current_user")
    async def test_excludes_other_tenant_entities(
        self, mock_get_user, async_client, generate_token, app
    ):
        """A foreign tenant's id in the batch must come back found=false,
        indistinguishable from a nonexistent id -- never the foreign entity's
        attributes, and never a way to enumerate other tenants' ids."""
        mock_get_user.return_value = MagicMock(id=1, is_superuser=False)
        token = generate_token(tenant_id=1, scopes=["infrastructure:read"])
        async with app.app_context():
            db = current_app.db
            own_org_id = _org(db, 1, name="Tenant1 Org Batch")
            own_entity_id = _entity(db, own_org_id, name="Tenant1 Batch Server")

            other_tenant_id = _foreign_tenant(db)
            foreign_org_id = _org(db, other_tenant_id, name="Foreign Org Batch")
            foreign_entity_id = _entity(db, foreign_org_id, name="Foreign Batch Server")

        resp = await async_client.post(
            "/lookup/batch",
            json={"ids": [own_entity_id, foreign_entity_id]},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200, (await resp.get_data()).decode()[:200]
        body = await resp.get_json()
        results = {r["id"]: r for r in body["results"]}

        assert results[own_entity_id]["found"] is True
        assert results[own_entity_id]["entity"]["id"] == own_entity_id

        assert results[foreign_entity_id]["found"] is False
        assert results[foreign_entity_id]["entity"] is None
