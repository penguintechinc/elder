"""E2E tests for API CRUD operations."""

import pytest
import requests


class TestOrganizationCRUD:
    """Test Organization CRUD operations."""

    def test_create_organization(self, api_url, auth_headers, check_services):
        """Test creating a new organization."""
        org_data = {
            "name": "E2E Test Organization",
            "description": "Created by E2E tests",
            "organization_type": "team",
        }

        response = requests.post(
            f"{api_url}/api/v1/organizations",
            headers=auth_headers,
            json=org_data,
        )

        assert response.status_code in [200, 201]
        data = response.json()
        assert data.get("name") == org_data["name"]
        return data.get("id")

    def test_read_organization(self, api_url, auth_headers, check_services):
        """Test reading an organization."""
        # First create one
        create_response = requests.post(
            f"{api_url}/api/v1/organizations",
            headers=auth_headers,
            json={"name": "E2E Read Test Org", "organization_type": "team"},
        )

        if create_response.status_code in [200, 201]:
            org_id = create_response.json().get("id")

            # Now read it
            response = requests.get(
                f"{api_url}/api/v1/organizations/{org_id}",
                headers=auth_headers,
            )

            assert response.status_code == 200
            data = response.json()
            assert data.get("id") == org_id

    def test_update_organization(self, api_url, auth_headers, check_services):
        """Test updating an organization."""
        # First create one
        create_response = requests.post(
            f"{api_url}/api/v1/organizations",
            headers=auth_headers,
            json={"name": "E2E Update Test Org", "organization_type": "team"},
        )

        if create_response.status_code in [200, 201]:
            org_id = create_response.json().get("id")

            # Now update it
            response = requests.put(
                f"{api_url}/api/v1/organizations/{org_id}",
                headers=auth_headers,
                json={"name": "E2E Update Test Org - Modified"},
            )

            assert response.status_code == 200
            data = response.json()
            assert "Modified" in data.get("name", "")

    def test_delete_organization(self, api_url, auth_headers, check_services):
        """Test deleting an organization."""
        # First create one
        create_response = requests.post(
            f"{api_url}/api/v1/organizations",
            headers=auth_headers,
            json={"name": "E2E Delete Test Org", "organization_type": "team"},
        )

        if create_response.status_code in [200, 201]:
            org_id = create_response.json().get("id")

            # Now delete it
            response = requests.delete(
                f"{api_url}/api/v1/organizations/{org_id}",
                headers=auth_headers,
            )

            assert response.status_code in [200, 204]


class TestEntityCRUD:
    """Test Entity CRUD operations."""

    def test_create_entity(self, api_url, auth_headers, check_services):
        """Test creating a new entity."""
        entity_data = {
            "name": "E2E Test Entity",
            "entity_type": "server",
            "description": "Created by E2E tests",
        }

        response = requests.post(
            f"{api_url}/api/v1/entities",
            headers=auth_headers,
            json=entity_data,
        )

        assert response.status_code in [200, 201]
        data = response.json()
        assert data.get("name") == entity_data["name"]

    def test_read_entity(self, api_url, auth_headers, check_services):
        """Test reading an entity."""
        # First create one
        create_response = requests.post(
            f"{api_url}/api/v1/entities",
            headers=auth_headers,
            json={"name": "E2E Read Test Entity", "entity_type": "server"},
        )

        if create_response.status_code in [200, 201]:
            entity_id = create_response.json().get("id")

            # Now read it
            response = requests.get(
                f"{api_url}/api/v1/entities/{entity_id}",
                headers=auth_headers,
            )

            assert response.status_code == 200
            data = response.json()
            assert data.get("id") == entity_id

    def test_entity_search(self, api_url, auth_headers, check_services):
        """Test searching entities."""
        response = requests.get(
            f"{api_url}/api/v1/entities",
            headers=auth_headers,
            params={"search": "E2E"},
        )

        assert response.status_code == 200

    def test_entity_filter_by_type(self, api_url, auth_headers, check_services):
        """Regression test for #97: entity_type filter must not return 500.

        PostgreSQL enum values must match the lowercase .value strings that
        PyDAL passes at runtime.  Prior to the fix, SQLAlchemy created the
        entitytype enum with uppercase member names (e.g. 'NETWORK') while
        PyDAL queries passed lowercase ('network'), causing a 500.
        """
        for entity_type in ["datacenter", "vpc", "subnet", "compute", "network", "user", "security_issue"]:
            response = requests.get(
                f"{api_url}/api/v1/entities",
                headers=auth_headers,
                params={"entity_type": entity_type},
            )
            assert response.status_code == 200, (
                f"GET /api/v1/entities?entity_type={entity_type} returned "
                f"{response.status_code}: {response.text[:200]}"
            )


class TestServiceCRUD:
    """Test Service CRUD operations."""

    def test_create_service(self, api_url, auth_headers, check_services):
        """Test creating a new service."""
        service_data = {
            "name": "E2E Test Service",
            "description": "Created by E2E tests",
            "is_public": False,
        }

        response = requests.post(
            f"{api_url}/api/v1/services",
            headers=auth_headers,
            json=service_data,
        )

        assert response.status_code in [200, 201]
        data = response.json()
        assert data.get("name") == service_data["name"]

    def test_service_with_organization(self, api_url, auth_headers, check_services):
        """Test creating a service linked to an organization."""
        # First get an existing organization
        orgs_response = requests.get(
            f"{api_url}/api/v1/organizations",
            headers=auth_headers,
        )

        if orgs_response.status_code == 200:
            orgs = orgs_response.json().get("items", [])
            if orgs:
                org_id = orgs[0].get("id")

                service_data = {
                    "name": "E2E Service with Org",
                    "organization_id": org_id,
                }

                response = requests.post(
                    f"{api_url}/api/v1/services",
                    headers=auth_headers,
                    json=service_data,
                )

                assert response.status_code in [200, 201]


class TestLabelCRUD:
    """Test Label CRUD operations."""

    def test_create_label(self, api_url, auth_headers, check_services):
        """Test creating a new label."""
        import random
        import string

        suffix = "".join(random.choices(string.ascii_lowercase, k=6))
        label_data = {
            "key": f"e2e-test-{suffix}",
            "value": "test-value",
            "color": "#ff0000",
        }

        response = requests.post(
            f"{api_url}/api/v1/labels",
            headers=auth_headers,
            json=label_data,
        )

        assert response.status_code in [200, 201]
        data = response.json()
        assert data.get("key") == label_data["key"]

    def test_list_labels(self, api_url, auth_headers, check_services):
        """Test listing labels."""
        response = requests.get(
            f"{api_url}/api/v1/labels",
            headers=auth_headers,
        )

        assert response.status_code == 200
        data = response.json()
        assert "items" in data or isinstance(data, list)
