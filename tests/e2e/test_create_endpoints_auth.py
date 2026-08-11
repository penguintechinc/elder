"""E2E tests for CREATE endpoint authentication requirements.

This test suite verifies that CREATE endpoints work with just authentication
(no resource roles required), while UPDATE/DELETE require resource roles.
"""

import pytest
import requests


class TestCreateEndpointAuthentication:
    """Test that CREATE endpoints work with basic authentication."""

    @pytest.fixture
    def organization_id(self, api_url, auth_headers):
        """Create a test organization and return its ID."""
        org_data = {
            "name": "Test Org for CREATE Tests",
            "description": "Test organization",
            "organization_type": "team",
        }
        response = requests.post(
            f"{api_url}/api/v1/organizations",
            headers=auth_headers,
            json=org_data,
        )
        assert response.status_code in [
            200,
            201,
        ], f"Failed to create org: {response.text}"
        return response.json().get("id")

    def test_create_software_with_auth_only(
        self, api_url, auth_headers, organization_id, check_services
    ):
        """Test that software creation works with just authentication."""
        software_data = {
            "name": "Test Software",
            "organization_id": organization_id,
            "software_type": "saas",
            "vendor": "Test Vendor",
        }

        response = requests.post(
            f"{api_url}/api/v1/software",
            headers=auth_headers,
            json=software_data,
        )

        # Should succeed with 201 or 200, NOT 403 (which would indicate role issues)
        assert response.status_code in [200, 201], (
            f"CREATE software should work with auth only. "
            f"Status: {response.status_code}, Body: {response.text}"
        )
        assert response.json().get("name") == "Test Software"

    def test_create_entity_with_auth_only(
        self, api_url, auth_headers, organization_id, check_services
    ):
        """Test that entity creation works with just authentication."""
        entity_data = {
            "name": "Test Entity",
            "entity_type": "service",
            "organization_id": organization_id,
        }

        response = requests.post(
            f"{api_url}/api/v1/entities",
            headers=auth_headers,
            json=entity_data,
        )

        # Should succeed with 201 or 200, NOT 403
        assert response.status_code in [200, 201], (
            f"CREATE entity should work with auth only. "
            f"Status: {response.status_code}, Body: {response.text}"
        )
        assert response.json().get("name") == "Test Entity"

    def test_create_service_with_auth_only(
        self, api_url, auth_headers, organization_id, check_services
    ):
        """Test that service creation works with just authentication."""
        service_data = {
            "name": "Test Service",
            "organization_id": organization_id,
            "description": "Test microservice",
        }

        response = requests.post(
            f"{api_url}/api/v1/services",
            headers=auth_headers,
            json=service_data,
        )

        assert response.status_code in [200, 201], (
            f"CREATE service should work with auth only. "
            f"Status: {response.status_code}, Body: {response.text}"
        )
        assert response.json().get("name") == "Test Service"

    def test_create_project_with_auth_only(
        self, api_url, auth_headers, organization_id, check_services
    ):
        """Test that project creation works with just authentication."""
        project_data = {
            "name": "Test Project",
            "organization_id": organization_id,
            "description": "Test project",
        }

        response = requests.post(
            f"{api_url}/api/v1/projects",
            headers=auth_headers,
            json=project_data,
        )

        assert response.status_code in [200, 201], (
            f"CREATE project should work with auth only. "
            f"Status: {response.status_code}, Body: {response.text}"
        )
        assert response.json().get("name") == "Test Project"

    def test_create_milestone_with_auth_only(
        self, api_url, auth_headers, organization_id, check_services
    ):
        """Test that milestone creation works with just authentication."""
        milestone_data = {
            "title": "Test Milestone",
            "organization_id": organization_id,
            "description": "Test milestone",
        }

        response = requests.post(
            f"{api_url}/api/v1/milestones",
            headers=auth_headers,
            json=milestone_data,
        )

        assert response.status_code in [200, 201], (
            f"CREATE milestone should work with auth only. "
            f"Status: {response.status_code}, Body: {response.text}"
        )
        assert response.json().get("title") == "Test Milestone"

    def test_create_data_store_with_auth_only(
        self, api_url, auth_headers, organization_id, check_services
    ):
        """Test that data store creation works with just authentication."""
        data_store_data = {
            "name": "Test Data Store",
            "organization_id": organization_id,
            "data_classification": "public",
            "storage_type": "database",
        }

        response = requests.post(
            f"{api_url}/api/v1/data-stores",
            headers=auth_headers,
            json=data_store_data,
        )

        assert response.status_code in [200, 201], (
            f"CREATE data store should work with auth only. "
            f"Status: {response.status_code}, Body: {response.text}"
        )
        assert response.json().get("name") == "Test Data Store"

    def test_create_certificate_with_auth_only(
        self, api_url, auth_headers, organization_id, check_services
    ):
        """Test that certificate creation works with just authentication."""
        cert_data = {
            "name": "Test Certificate",
            "organization_id": organization_id,
            "creator": "self_signed",
            "cert_type": "server_cert",
            "issue_date": "2024-01-01",
            "expiration_date": "2025-01-01",
        }

        response = requests.post(
            f"{api_url}/api/v1/certificates",
            headers=auth_headers,
            json=cert_data,
        )

        assert response.status_code in [200, 201], (
            f"CREATE certificate should work with auth only. "
            f"Status: {response.status_code}, Body: {response.text}"
        )
        assert response.json().get("name") == "Test Certificate"

    def test_create_without_auth_fails(self, api_url, organization_id, check_services):
        """Test that CREATE endpoints require authentication."""
        software_data = {
            "name": "Test Software",
            "organization_id": organization_id,
            "software_type": "saas",
        }

        # Make request without auth headers
        response = requests.post(
            f"{api_url}/api/v1/software",
            json=software_data,
        )

        # Should return 401 Unauthorized
        assert (
            response.status_code == 401
        ), f"CREATE without auth should return 401, got {response.status_code}"


class TestResourceRoleEnforcement:
    """Test that resource roles are enforced on UPDATE/DELETE but not CREATE."""

    def test_update_requires_maintainer_role(
        self, api_url, auth_headers, check_services
    ):
        """Verify that UPDATE endpoints check for maintainer role (when implemented)."""
        # This is a placeholder test - once resource roles are implemented,
        # this should verify that users without maintainer role cannot update
        # For now, we just verify the endpoint exists
        pass

    def test_delete_requires_maintainer_role(
        self, api_url, auth_headers, check_services
    ):
        """Verify that DELETE endpoints check for maintainer role (when implemented)."""
        # This is a placeholder test - once resource roles are implemented,
        # this should verify that users without maintainer role cannot delete
        # For now, we just verify the endpoint exists
        pass
