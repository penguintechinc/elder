"""
Integration tests for email validation in authentication API endpoints.

Tests the actual API endpoints to ensure email validation is enforced:
- /api/v1/auth/register
- /api/v1/auth/login
- /api/v1/portal-auth/register
- /api/v1/portal-auth/login
"""

import pytest


@pytest.mark.asyncio
class TestAuthEmailValidationAPI:
    """Test email validation in /api/v1/auth endpoints."""

    async def test_register_with_valid_email(self, client, app):
        """Test registration succeeds with valid email as username."""
        response = await client.post(
            "/api/v1/auth/register",
            json={
                "username": "newuser@example.com",
                "email": "newuser@example.com",
                "password": "password123",
                "full_name": "New User",
            },
        )

        # Should succeed (201) or fail if user exists (400)
        assert response.status_code in [201, 400]
        if response.status_code == 201:
            data = await response.get_json()
            assert "user" in data
            assert data["user"]["email"] == "newuser@example.com"

    async def test_register_with_invalid_email(self, client, app):
        """Test registration fails with non-email username."""
        response = await client.post(
            "/api/v1/auth/register",
            json={
                "username": "invaliduser",  # Not an email
                "email": "valid@example.com",
                "password": "password123",
            },
        )

        assert response.status_code == 422
        data = await response.get_json()
        assert "error" in data
        assert "validation" in data["error"].lower()

    async def test_register_with_mismatched_email(self, client, app):
        """Test registration fails when email doesn't match username."""
        response = await client.post(
            "/api/v1/auth/register",
            json={
                "username": "user1@example.com",
                "email": "user2@example.com",  # Different
                "password": "password123",
            },
        )

        assert response.status_code == 422
        data = await response.get_json()
        assert "error" in data

    async def test_login_with_valid_email(self, client, app):
        """Test login with valid email as username."""
        # Login with default admin account
        response = await client.post(
            "/api/v1/auth/login",
            json={
                "username": "admin@localhost.local",
                "password": "admin123",
            },
        )

        # Should succeed or fail based on credentials
        # but not due to validation error
        assert response.status_code in [200, 401]

    async def test_login_with_invalid_email(self, client, app):
        """Test login fails with non-email username."""
        response = await client.post(
            "/api/v1/auth/login",
            json={
                "username": "admin",  # Not an email
                "password": "admin123",
            },
        )

        assert response.status_code == 422
        data = await response.get_json()
        assert "error" in data
        assert "validation" in data["error"].lower()

    async def test_login_with_empty_username(self, client, app):
        """Test login fails with empty username."""
        response = await client.post(
            "/api/v1/auth/login",
            json={
                "username": "",
                "password": "password123",
            },
        )

        assert response.status_code == 422
        data = await response.get_json()
        assert "error" in data


@pytest.mark.asyncio
class TestPortalAuthEmailValidationAPI:
    """Test email validation in /api/v1/portal-auth endpoints."""

    async def test_portal_register_with_valid_email(self, client, app):
        """Test portal registration with valid email."""
        response = await client.post(
            "/api/v1/portal-auth/register",
            json={
                "email": "portaluser@example.com",
                "password": "password123",
                "full_name": "Portal User",
                "tenant": "Global",
            },
        )

        # Should succeed (201) or fail if user exists (400)
        # or fail if tenant doesn't exist (400)
        assert response.status_code in [201, 400]

    async def test_portal_register_with_invalid_email(self, client, app):
        """Test portal registration fails with invalid email."""
        response = await client.post(
            "/api/v1/portal-auth/register",
            json={
                "email": "notanemail",
                "password": "password123",
                "tenant": "Global",
            },
        )

        assert response.status_code == 422
        data = await response.get_json()
        assert "error" in data
        assert "validation" in data["error"].lower()

    async def test_portal_login_with_valid_email(self, client, app):
        """Test portal login with valid email."""
        response = await client.post(
            "/api/v1/portal-auth/login",
            json={
                "email": "admin@localhost.local",
                "password": "admin123",
                "tenant": "Global",
            },
        )

        # Should succeed or fail based on credentials
        # but not due to validation error
        assert response.status_code in [200, 400, 401]

    async def test_portal_login_with_invalid_email(self, client, app):
        """Test portal login fails with non-email format."""
        response = await client.post(
            "/api/v1/portal-auth/login",
            json={
                "email": "admin",  # Not an email
                "password": "admin123",
                "tenant": "Global",
            },
        )

        assert response.status_code == 422
        data = await response.get_json()
        assert "error" in data
        assert "validation" in data["error"].lower()

    async def test_portal_login_with_malformed_email(self, client, app):
        """Test portal login fails with malformed email."""
        response = await client.post(
            "/api/v1/portal-auth/login",
            json={
                "email": "user@",  # Malformed
                "password": "password123",
                "tenant": "Global",
            },
        )

        assert response.status_code == 422
        data = await response.get_json()
        assert "error" in data

    async def test_short_password_rejected(self, client, app):
        """Test registration fails with password less than 8 characters."""
        response = await client.post(
            "/api/v1/auth/register",
            json={
                "username": "user@example.com",
                "email": "user@example.com",
                "password": "short",  # Only 5 characters
            },
        )

        assert response.status_code == 422
        data = await response.get_json()
        assert "error" in data


@pytest.mark.asyncio
class TestEmailValidationErrorMessages:
    """Test that validation errors return helpful messages."""

    async def test_validation_error_includes_details(self, client, app):
        """Test validation errors include detailed information."""
        response = await client.post(
            "/api/v1/auth/login",
            json={
                "username": "notanemail",
                "password": "password123",
            },
        )

        assert response.status_code == 422
        data = await response.get_json()
        assert "error" in data
        assert "details" in data
        assert isinstance(data["details"], list)
        # Should have validation error details
        assert len(data["details"]) > 0

    async def test_multiple_validation_errors(self, client, app):
        """Test multiple validation errors are reported."""
        response = await client.post(
            "/api/v1/auth/register",
            json={
                "username": "notanemail",  # Invalid
                "email": "alsonotanemail",  # Invalid
                "password": "bad",  # Too short
            },
        )

        assert response.status_code == 422
        data = await response.get_json()
        assert "error" in data
        # Should report multiple validation errors
        if "details" in data:
            assert len(data["details"]) >= 2
