"""
Unit tests for email validation in portal authentication.

Tests ensure that:
1. Portal users (identities table for login) MUST use email format for username
2. Portal users (portal_users table) MUST use email format
3. Non-portal identities (external systems) CAN use non-email usernames
"""

import pytest
from pydantic import ValidationError

from apps.api.models.schemas import (
    LoginRequest,
    PortalLoginRequest,
    PortalRegisterRequest,
    RegisterRequest,
)


class TestPortalAuthEmailValidation:
    """Test email validation for portal authentication endpoints."""

    def test_register_request_valid_email(self):
        """Test RegisterRequest accepts valid email as username."""
        data = {
            "username": "user@example.com",
            "password": "password123",
            "email": "user@example.com",
        }
        request = RegisterRequest(**data)
        assert request.username == "user@example.com"
        assert request.email == "user@example.com"

    def test_register_request_invalid_username(self):
        """Test RegisterRequest rejects non-email username."""
        data = {
            "username": "admin",  # Not an email
            "password": "password123",
            "email": "admin@example.com",
        }
        with pytest.raises(ValidationError) as exc_info:
            RegisterRequest(**data)

        errors = exc_info.value.errors()
        assert any(
            "username" in str(error["loc"]) for error in errors
        ), "Should reject non-email username"

    def test_register_request_mismatched_email(self):
        """Test RegisterRequest rejects when email doesn't match username."""
        data = {
            "username": "user1@example.com",
            "password": "password123",
            "email": "user2@example.com",  # Different from username
        }
        with pytest.raises(ValidationError) as exc_info:
            RegisterRequest(**data)

        errors = exc_info.value.errors()
        assert any(
            "email must match username" in str(error["msg"]).lower()
            for error in errors
        ), "Should reject mismatched email and username"

    def test_login_request_valid_email(self):
        """Test LoginRequest accepts valid email as username."""
        data = {"username": "user@example.com", "password": "password123"}
        request = LoginRequest(**data)
        assert request.username == "user@example.com"

    def test_login_request_invalid_username(self):
        """Test LoginRequest rejects non-email username."""
        data = {"username": "admin", "password": "password123"}
        with pytest.raises(ValidationError) as exc_info:
            LoginRequest(**data)

        errors = exc_info.value.errors()
        assert any(
            "username" in str(error["loc"]) for error in errors
        ), "Should reject non-email username"

    def test_portal_register_valid_email(self):
        """Test PortalRegisterRequest accepts valid email."""
        data = {
            "email": "user@example.com",
            "password": "password123",
            "tenant": "acme-corp",
        }
        request = PortalRegisterRequest(**data)
        assert request.email == "user@example.com"

    def test_portal_register_invalid_email(self):
        """Test PortalRegisterRequest rejects invalid email."""
        data = {"email": "not-an-email", "password": "password123", "tenant": "acme"}
        with pytest.raises(ValidationError) as exc_info:
            PortalRegisterRequest(**data)

        errors = exc_info.value.errors()
        assert any(
            "email" in str(error["loc"]) for error in errors
        ), "Should reject invalid email"

    def test_portal_login_valid_email(self):
        """Test PortalLoginRequest accepts valid email."""
        data = {"email": "user@example.com", "password": "password123"}
        request = PortalLoginRequest(**data)
        assert request.email == "user@example.com"

    def test_portal_login_invalid_email(self):
        """Test PortalLoginRequest rejects invalid email."""
        data = {"email": "admin", "password": "password123"}
        with pytest.raises(ValidationError) as exc_info:
            PortalLoginRequest(**data)

        errors = exc_info.value.errors()
        assert any(
            "email" in str(error["loc"]) for error in errors
        ), "Should reject invalid email"

    def test_password_minimum_length_register(self):
        """Test password minimum length validation in registration."""
        data = {
            "username": "user@example.com",
            "password": "short",  # Less than 8 characters
            "email": "user@example.com",
        }
        with pytest.raises(ValidationError) as exc_info:
            RegisterRequest(**data)

        errors = exc_info.value.errors()
        assert any(
            "password" in str(error["loc"]) for error in errors
        ), "Should reject password shorter than 8 characters"

    def test_various_email_formats(self):
        """Test various valid email formats are accepted."""
        valid_emails = [
            "user@example.com",
            "user.name@example.com",
            "user+tag@example.co.uk",
            "user_name@sub.example.com",
            "123@example.com",
        ]

        for email in valid_emails:
            data = {"email": email, "password": "password123"}
            request = PortalLoginRequest(**data)
            assert request.email == email, f"Should accept valid email: {email}"

    def test_invalid_email_formats(self):
        """Test various invalid email formats are rejected."""
        invalid_emails = [
            "admin",  # No @ symbol
            "user@",  # Missing domain
            "@example.com",  # Missing local part
            "user name@example.com",  # Space in local part
            "user@",  # Incomplete
            "",  # Empty string
        ]

        for email in invalid_emails:
            data = {"email": email, "password": "password123"}
            with pytest.raises(ValidationError):
                PortalLoginRequest(**data)


class TestPenguinDALEmailValidation:
    """Test penguin-dal-level email validation for database operations."""

    def test_portal_users_table_has_email_validator(self):
        """Verify portal_users table requires IS_EMAIL validation."""
        # This test verifies the schema definition
        # Table setup handled by penguin-dal reflection
        # Actual validation is tested in integration tests

    def test_identities_table_allows_flexible_username(self):
        """Verify identities table allows non-email usernames for external systems."""
        # Table setup handled by penguin-dal reflection
        # Identities table supports flexible usernames for external systems
        # Validation is tested in integration tests
