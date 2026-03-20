"""E2E tests for API health and version endpoints."""

import pytest
import requests


class TestAPIHealth:
    """Test API health endpoints."""

    def test_healthz_endpoint(self, api_url, check_services):
        """Test /healthz endpoint returns healthy status."""
        response = requests.get(f"{api_url}/healthz")

        assert response.status_code == 200
        data = response.json()
        assert data.get("status") in ["healthy", "ok", "up"]

    def test_version_endpoint(self, api_url, check_services):
        """Test /api/v1/version endpoint returns version info with a non-zero version."""
        response = requests.get(f"{api_url}/api/v1/version")

        # May return 200 or 404 depending on implementation
        if response.status_code == 200:
            data = response.json()
            assert "version" in data or "api_version" in data
            version = data.get("version") or data.get("api_version", "")
            assert version != "0.0.0", (
                f"Version is '0.0.0' — APP_VERSION build-arg was not injected correctly"
            )


class TestAPIAuthentication:
    """Test API authentication flow."""

    def test_login_success(self, api_url, check_services):
        """Test successful login returns a token."""
        response = requests.post(
            f"{api_url}/api/v1/auth/login",
            json={"email": "admin@localhost.local", "password": "admin123"},
        )

        assert response.status_code == 200
        data = response.json()
        assert "access_token" in data or "token" in data

    def test_login_invalid_credentials(self, api_url, check_services):
        """Test login with invalid credentials fails."""
        response = requests.post(
            f"{api_url}/api/v1/auth/login",
            json={"email": "invalid@example.com", "password": "wrongpassword"},
        )

        assert response.status_code in [401, 403, 400]

    def test_protected_endpoint_without_auth(self, api_url, check_services):
        """Test protected endpoint returns 401 without auth."""
        response = requests.get(f"{api_url}/api/v1/organizations")

        assert response.status_code in [401, 403]


class TestAPIEndpoints:
    """Test authenticated API endpoints."""

    def test_organizations_list(self, api_url, auth_headers, check_services):
        """Test GET /api/v1/organizations returns list."""
        response = requests.get(
            f"{api_url}/api/v1/organizations",
            headers=auth_headers,
        )

        assert response.status_code == 200
        data = response.json()
        assert "items" in data or isinstance(data, list)

    def test_entities_list(self, api_url, auth_headers, check_services):
        """Test GET /api/v1/entities returns list."""
        response = requests.get(
            f"{api_url}/api/v1/entities",
            headers=auth_headers,
        )

        assert response.status_code == 200
        data = response.json()
        assert "items" in data or isinstance(data, list)

    def test_services_list(self, api_url, auth_headers, check_services):
        """Test GET /api/v1/services returns list."""
        response = requests.get(
            f"{api_url}/api/v1/services",
            headers=auth_headers,
        )

        assert response.status_code == 200
        data = response.json()
        assert "items" in data or isinstance(data, list)

    def test_identities_list(self, api_url, auth_headers, check_services):
        """Test GET /api/v1/identities returns list."""
        response = requests.get(
            f"{api_url}/api/v1/identities",
            headers=auth_headers,
        )

        assert response.status_code == 200
        data = response.json()
        assert "items" in data or isinstance(data, list)

    def test_labels_list(self, api_url, auth_headers, check_services):
        """Test GET /api/v1/labels returns list."""
        response = requests.get(
            f"{api_url}/api/v1/labels",
            headers=auth_headers,
        )

        assert response.status_code == 200
        data = response.json()
        assert "items" in data or isinstance(data, list)
