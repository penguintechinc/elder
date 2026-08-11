"""
Unit tests for Organization API endpoints.

These tests use mocked authentication and database connections.
No external network calls or real database required.
"""

import json
from datetime import UTC, datetime, timezone
from unittest.mock import MagicMock, patch

import pytest
from quart import current_app


class TestOrganizationAPI:
    """Test Organization API endpoints."""

    @pytest.mark.asyncio
    @patch("apps.api.auth.decorators.get_current_user")
    async def test_list_organizations(self, mock_get_user, async_client, app):
        """Test GET /api/v1/organizations."""
        # Mock current user
        mock_user = MagicMock()
        mock_user.id = 1
        mock_user.username = "test"
        mock_user.is_superuser = True
        mock_get_user.return_value = mock_user

        async with app.app_context():
            # Create test organizations using penguin-dal API
            db = current_app.db
            now = datetime.now(UTC)

            db.organizations.insert(name="Org 1", created_at=now, updated_at=now)
            db.organizations.insert(name="Org 2", created_at=now, updated_at=now)
            db.commit()

            response = await async_client.get(
                "/api/v1/organizations", headers={"Authorization": "Bearer fake-token"}
            )

            assert response.status_code == 200
            data = json.loads(await response.get_data())
            assert "items" in data or "organizations" in data
            assert len(data.get("items", data.get("organizations", []))) >= 2

    @pytest.mark.asyncio
    @patch("apps.api.auth.decorators.get_current_user")
    async def test_create_organization(self, mock_get_user, async_client):
        """Test POST /api/v1/organizations."""
        # Mock current user
        mock_user = MagicMock()
        mock_user.id = 1
        mock_user.tenant_id = 1
        mock_user.username = "admin"
        mock_user.is_superuser = True
        mock_get_user.return_value = mock_user

        payload = {"name": "New Organization", "description": "A new test organization"}

        response = await async_client.post(
            "/api/v1/organizations",
            json=payload,
            headers={"Authorization": "Bearer fake-token"},
        )

        assert response.status_code in [200, 201]
        data = json.loads(await response.get_data())
        assert data["name"] == "New Organization"
        assert data["description"] == "A new test organization"

    @pytest.mark.asyncio
    @patch("apps.api.auth.decorators.get_current_user")
    async def test_get_organization(self, mock_get_user, async_client, app):
        """Test GET /api/v1/organizations/:id."""
        # Mock current user
        mock_user = MagicMock()
        mock_user.id = 1
        mock_user.username = "test"
        mock_user.is_superuser = True
        mock_get_user.return_value = mock_user

        async with app.app_context():
            db = current_app.db
            now = datetime.now(UTC)

            org_id = db.organizations.insert(
                name="Get Me", description="Test org", created_at=now, updated_at=now
            )
            db.commit()

            response = await async_client.get(
                f"/api/v1/organizations/{org_id}",
                headers={"Authorization": "Bearer fake-token"},
            )

            assert response.status_code == 200
            data = json.loads(await response.get_data())
            assert data["name"] == "Get Me"
            assert data["description"] == "Test org"

    @pytest.mark.asyncio
    @patch("apps.api.auth.decorators.get_current_user")
    async def test_update_organization(self, mock_get_user, async_client, app):
        """Test PATCH /api/v1/organizations/:id."""
        # Mock current user
        mock_user = MagicMock()
        mock_user.id = 1
        mock_user.tenant_id = 1
        mock_user.username = "admin"
        mock_user.is_superuser = True
        mock_get_user.return_value = mock_user

        async with app.app_context():
            db = current_app.db
            now = datetime.now(UTC)

            org_id = db.organizations.insert(
                name="Original Name", created_at=now, updated_at=now
            )
            db.commit()

            payload = {"name": "Updated Name", "description": "Updated description"}

            response = await async_client.patch(
                f"/api/v1/organizations/{org_id}",
                json=payload,
                headers={"Authorization": "Bearer fake-token"},
            )

            assert response.status_code == 200
            data = json.loads(await response.get_data())
            assert data["name"] == "Updated Name"
            assert data["description"] == "Updated description"

    @pytest.mark.asyncio
    @patch("apps.api.auth.decorators.get_current_user")
    async def test_delete_organization(self, mock_get_user, async_client, app):
        """Test DELETE /api/v1/organizations/:id."""
        # Mock current user
        mock_user = MagicMock()
        mock_user.id = 1
        mock_user.tenant_id = 1
        mock_user.username = "admin"
        mock_user.is_superuser = True
        mock_get_user.return_value = mock_user

        async with app.app_context():
            db = current_app.db
            now = datetime.now(UTC)

            org_id = db.organizations.insert(
                name="Delete Me", created_at=now, updated_at=now
            )
            db.commit()

            response = await async_client.delete(
                f"/api/v1/organizations/{org_id}",
                headers={"Authorization": "Bearer fake-token"},
            )

            assert response.status_code in [200, 204]

            # Verify deletion using penguin-dal
            deleted = db(db.organizations.id == org_id).select().first()
            assert deleted is None

    @pytest.mark.asyncio
    @patch("apps.api.auth.decorators.get_current_user")
    async def test_get_organization_children(self, mock_get_user, async_client, app):
        """Test GET /api/v1/organizations/:id/children with tenant scoping."""
        async with app.app_context():
            db = current_app.db
            now = datetime.now(UTC)

            # Get or create default tenant
            tenant = db(db.tenants.slug == "system").select().first()
            if not tenant:
                tenant = db(db.tenants.slug == "default").select().first()
            tenant_id = tenant.id if tenant else None

            # Mock current user with tenant_id to match test data
            mock_user = MagicMock()
            mock_user.id = 1
            mock_user.username = "test"
            mock_user.is_superuser = True
            mock_user.tenant_id = tenant_id  # Must match tenant_id of organizations
            mock_get_user.return_value = mock_user

            parent_id = db.organizations.insert(
                name="Parent", tenant_id=tenant_id, created_at=now, updated_at=now
            )
            db.commit()

            # Verify parent exists
            parent = db.organizations[parent_id]
            assert (
                parent is not None
            ), f"Parent organization {parent_id} not found after insert"

            db.organizations.insert(
                name="Child 1",
                parent_id=parent_id,
                tenant_id=tenant_id,
                created_at=now,
                updated_at=now,
            )
            db.organizations.insert(
                name="Child 2",
                parent_id=parent_id,
                tenant_id=tenant_id,
                created_at=now,
                updated_at=now,
            )
            db.commit()

            response = await async_client.get(
                f"/api/v1/organizations/{parent_id}/children",
                headers={"Authorization": "Bearer fake-token"},
            )

            assert (
                response.status_code == 200
            ), f"Expected 200, got {response.status_code}: {await response.get_data()}"
            data = json.loads(await response.get_data())
            # The endpoint returns a list directly, not wrapped in a dict
            assert isinstance(data, list)
            assert len(data) == 2

    @pytest.mark.asyncio
    async def test_list_organizations_unauthorized(self, async_client):
        """Test unauthorized access to organizations."""
        response = await async_client.get("/api/v1/organizations")
        assert response.status_code in [401, 403]

    @pytest.mark.asyncio
    @patch("apps.api.auth.decorators.get_current_user")
    async def test_create_organization_invalid_data(self, mock_get_user, async_client):
        """Test creating organization with invalid data."""
        # Mock current user
        mock_user = MagicMock()
        mock_user.id = 1
        mock_user.tenant_id = 1
        mock_user.username = "admin"
        mock_user.is_superuser = True
        mock_get_user.return_value = mock_user

        # Missing required field
        payload = {"description": "Missing name field"}

        response = await async_client.post(
            "/api/v1/organizations",
            json=payload,
            headers={"Authorization": "Bearer fake-token"},
        )

        assert response.status_code in [400, 422]

    @pytest.mark.asyncio
    @patch("apps.api.auth.decorators.get_current_user")
    async def test_get_nonexistent_organization(self, mock_get_user, async_client):
        """Test getting non-existent organization."""
        # Mock current user
        mock_user = MagicMock()
        mock_user.id = 1
        mock_user.username = "test"
        mock_user.is_superuser = True
        mock_get_user.return_value = mock_user

        response = await async_client.get(
            "/api/v1/organizations/999999",
            headers={"Authorization": "Bearer fake-token"},
        )

        assert response.status_code == 404

    @pytest.mark.asyncio
    @patch("apps.api.auth.decorators.get_current_user")
    async def test_organization_pagination(self, mock_get_user, async_client, app):
        """Test organization list pagination."""
        # Mock current user
        mock_user = MagicMock()
        mock_user.id = 1
        mock_user.username = "test"
        mock_user.is_superuser = True
        mock_get_user.return_value = mock_user

        async with app.app_context():
            # Create multiple organizations
            db = current_app.db
            now = datetime.now(UTC)

            for i in range(15):
                db.organizations.insert(name=f"Org {i}", created_at=now, updated_at=now)
            db.commit()

            response = await async_client.get(
                "/api/v1/organizations?page=1&per_page=10",
                headers={"Authorization": "Bearer fake-token"},
            )

            assert response.status_code == 200
            data = json.loads(await response.get_data())
            items = data.get("items", data.get("organizations", []))
            assert len(items) <= 10

    @staticmethod
    def _get_or_create_tenant(db, slug: str, name: str) -> int:
        """Get or create a tenant row by slug, returning its id."""
        tenant = db(db.tenants.slug == slug).select().first()
        if tenant:
            return tenant.id
        tenant_id = db.tenants.insert(name=name, slug=slug, is_active=True)
        db.commit()
        return tenant_id

    @pytest.mark.asyncio
    async def test_get_organization_graph_unauthenticated(self, async_client):
        """GET /organizations/:id/graph with no auth token must 401.

        regression: org-graph-auth-scope
        """
        response = await async_client.get("/api/v1/organizations/1/graph")
        assert response.status_code == 401

    @pytest.mark.asyncio
    @patch("apps.api.auth.decorators.get_current_user")
    async def test_get_organization_graph_cross_tenant_forbidden(
        self, mock_get_user, async_client, app
    ):
        """A caller must not read another tenant's organization graph.

        regression: org-graph-auth-scope
        """
        async with app.app_context():
            db = current_app.db
            now = datetime.now(UTC)

            tenant_a_id = self._get_or_create_tenant(
                db, "org-graph-tenant-a", "Org Graph Tenant A"
            )
            tenant_b_id = self._get_or_create_tenant(
                db, "org-graph-tenant-b", "Org Graph Tenant B"
            )

            other_org_id = db.organizations.insert(
                name="Other Tenant Org",
                tenant_id=tenant_b_id,
                created_at=now,
                updated_at=now,
            )
            db.commit()

            # Caller authenticated against tenant A, requesting tenant B's org.
            mock_user = MagicMock()
            mock_user.id = 1
            mock_user.username = "test"
            mock_user.is_superuser = True
            mock_user.tenant_id = tenant_a_id
            mock_get_user.return_value = mock_user

            response = await async_client.get(
                f"/api/v1/organizations/{other_org_id}/graph",
                headers={"Authorization": "Bearer fake-token"},
            )

            assert response.status_code in (403, 404)
            data = json.loads(await response.get_data())
            # Must not leak the other tenant's org data in the response body.
            assert "Other Tenant Org" not in json.dumps(data)

    @pytest.mark.asyncio
    @patch("apps.api.auth.decorators.get_current_user")
    async def test_get_organization_graph_same_tenant_ok(
        self, mock_get_user, async_client, app
    ):
        """A caller can read their own tenant's organization graph.

        regression: org-graph-auth-scope
        """
        async with app.app_context():
            db = current_app.db
            now = datetime.now(UTC)

            tenant_id = self._get_or_create_tenant(
                db, "org-graph-tenant-own", "Org Graph Tenant Own"
            )

            org_id = db.organizations.insert(
                name="My Own Org",
                tenant_id=tenant_id,
                created_at=now,
                updated_at=now,
            )
            db.commit()

            mock_user = MagicMock()
            mock_user.id = 1
            mock_user.username = "test"
            mock_user.is_superuser = True
            mock_user.tenant_id = tenant_id
            mock_get_user.return_value = mock_user

            response = await async_client.get(
                f"/api/v1/organizations/{org_id}/graph",
                headers={"Authorization": "Bearer fake-token"},
            )

            assert response.status_code == 200
            data = json.loads(await response.get_data())
            assert data["center_node"] == f"org-{org_id}"
            node_ids = {node["id"] for node in data["nodes"]}
            assert f"org-{org_id}" in node_ids
