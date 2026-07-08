import pytest
"""
Unit tests for Organization API endpoints.

These tests use mocked authentication and database connections.
No external network calls or real database required.
"""

import json
from unittest.mock import patch

from apps.api.modules.infrastructure.models.organization import Organization


@pytest.mark.xfail(reason="Quart migration: app.app_context() does not support context manager protocol in sync context")
class TestOrganizationAPI:
    """Test Organization API endpoints."""

    @patch("apps.api.auth.decorators.verify_jwt")
    def test_list_organizations(self, mock_jwt, client, app):
        """Test GET /api/v1/organizations."""
        mock_jwt.return_value = {"user_id": 1, "username": "test"}

        with app.app_context():
            # Create test organizations
            org1 = Organization(name="Org 1")
            org2 = Organization(name="Org 2")
            from apps.api import db

            db.session.add_all([org1, org2])
            db.session.commit()

            response = client.get(
                "/api/v1/organizations", headers={"Authorization": "Bearer fake-token"}
            )

            assert response.status_code == 200
            data = json.loads(response.data)
            assert "items" in data or "organizations" in data
            assert len(data.get("items", data.get("organizations", []))) >= 2

    @patch("apps.api.auth.decorators.verify_jwt")
    def test_create_organization(self, mock_jwt, client):
        """Test POST /api/v1/organizations."""
        mock_jwt.return_value = {"user_id": 1, "username": "admin"}

        payload = {"name": "New Organization", "description": "A new test organization"}

        response = client.post(
            "/api/v1/organizations",
            data=json.dumps(payload),
            content_type="application/json",
            headers={"Authorization": "Bearer fake-token"},
        )

        assert response.status_code in [200, 201]
        data = json.loads(response.data)
        assert data["name"] == "New Organization"
        assert data["description"] == "A new test organization"

    @patch("apps.api.auth.decorators.verify_jwt")
    def test_get_organization(self, mock_jwt, client, app):
        """Test GET /api/v1/organizations/:id."""
        mock_jwt.return_value = {"user_id": 1, "username": "test"}

        with app.app_context():
            org = Organization(name="Get Me", description="Test org")
            from apps.api import db

            db.session.add(org)
            db.session.commit()
            org_id = org.id

            response = client.get(
                f"/api/v1/organizations/{org_id}",
                headers={"Authorization": "Bearer fake-token"},
            )

            assert response.status_code == 200
            data = json.loads(response.data)
            assert data["name"] == "Get Me"
            assert data["description"] == "Test org"

    @patch("apps.api.auth.decorators.verify_jwt")
    def test_update_organization(self, mock_jwt, client, app):
        """Test PATCH /api/v1/organizations/:id."""
        mock_jwt.return_value = {"user_id": 1, "username": "admin"}

        with app.app_context():
            org = Organization(name="Original Name")
            from apps.api import db

            db.session.add(org)
            db.session.commit()
            org_id = org.id

            payload = {"name": "Updated Name", "description": "Updated description"}

            response = client.patch(
                f"/api/v1/organizations/{org_id}",
                data=json.dumps(payload),
                content_type="application/json",
                headers={"Authorization": "Bearer fake-token"},
            )

            assert response.status_code == 200
            data = json.loads(response.data)
            assert data["name"] == "Updated Name"
            assert data["description"] == "Updated description"

    @patch("apps.api.auth.decorators.verify_jwt")
    def test_delete_organization(self, mock_jwt, client, app):
        """Test DELETE /api/v1/organizations/:id."""
        mock_jwt.return_value = {"user_id": 1, "username": "admin"}

        with app.app_context():
            org = Organization(name="Delete Me")
            from apps.api import db

            db.session.add(org)
            db.session.commit()
            org_id = org.id

            response = client.delete(
                f"/api/v1/organizations/{org_id}",
                headers={"Authorization": "Bearer fake-token"},
            )

            assert response.status_code in [200, 204]

            # Verify deletion
            deleted = Organization.query.get(org_id)
            assert deleted is None

    @patch("apps.api.auth.decorators.verify_jwt")
    def test_get_organization_children(self, mock_jwt, client, app):
        """Test GET /api/v1/organizations/:id/children."""
        mock_jwt.return_value = {"user_id": 1, "username": "test"}

        with app.app_context():
            parent = Organization(name="Parent")
            from apps.api import db

            db.session.add(parent)
            db.session.commit()

            child1 = Organization(name="Child 1", parent_id=parent.id)
            child2 = Organization(name="Child 2", parent_id=parent.id)
            db.session.add_all([child1, child2])
            db.session.commit()

            response = client.get(
                f"/api/v1/organizations/{parent.id}/children",
                headers={"Authorization": "Bearer fake-token"},
            )

            assert response.status_code == 200
            data = json.loads(response.data)
            assert len(data.get("items", data.get("children", []))) == 2

    def test_list_organizations_unauthorized(self, client):
        """Test unauthorized access to organizations."""
        response = client.get("/api/v1/organizations")
        assert response.status_code in [401, 403]

    @patch("apps.api.auth.decorators.verify_jwt")
    def test_create_organization_invalid_data(self, mock_jwt, client):
        """Test creating organization with invalid data."""
        mock_jwt.return_value = {"user_id": 1, "username": "admin"}

        # Missing required field
        payload = {"description": "Missing name field"}

        response = client.post(
            "/api/v1/organizations",
            data=json.dumps(payload),
            content_type="application/json",
            headers={"Authorization": "Bearer fake-token"},
        )

        assert response.status_code in [400, 422]

    @patch("apps.api.auth.decorators.verify_jwt")
    def test_get_nonexistent_organization(self, mock_jwt, client):
        """Test getting non-existent organization."""
        mock_jwt.return_value = {"user_id": 1, "username": "test"}

        response = client.get(
            "/api/v1/organizations/999999",
            headers={"Authorization": "Bearer fake-token"},
        )

        assert response.status_code == 404

    @patch("apps.api.auth.decorators.verify_jwt")
    def test_organization_pagination(self, mock_jwt, client, app):
        """Test organization list pagination."""
        mock_jwt.return_value = {"user_id": 1, "username": "test"}

        with app.app_context():
            # Create multiple organizations
            from apps.api import db

            for i in range(15):
                org = Organization(name=f"Org {i}")
                db.session.add(org)
            db.session.commit()

            response = client.get(
                "/api/v1/organizations?page=1&per_page=10",
                headers={"Authorization": "Bearer fake-token"},
            )

            assert response.status_code == 200
            data = json.loads(response.data)
            items = data.get("items", data.get("organizations", []))
            assert len(items) <= 10
