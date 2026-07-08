"""
Unit tests for API response utilities.

These tests ensure consistent response formatting across all API endpoints.
No external dependencies required - pure unit tests.
"""

import json

import pytest
from flask import Flask

from apps.api.utils.api_responses import ApiResponse


@pytest.fixture
def app():
    """Create Flask app for testing."""
    app = Flask(__name__)
    return app


class TestApiResponse:
    """Test ApiResponse helper methods."""

    def test_error_basic(self, app):
        """Test basic error response."""
        with app.app_context():
            response, status_code = ApiResponse.error("Test error")

            assert status_code == 400
            data = json.loads(response.data)
            assert data["error"] == "Test error"

    def test_error_custom_status(self, app):
        """Test error response with custom status code."""
        with app.app_context():
            response, status_code = ApiResponse.error("Server error", 500)

            assert status_code == 500
            data = json.loads(response.data)
            assert data["error"] == "Server error"

    def test_error_with_kwargs(self, app):
        """Test error response with additional fields."""
        with app.app_context():
            response, status_code = ApiResponse.error(
                "Validation failed", field="email", constraint="format"
            )

            assert status_code == 400
            data = json.loads(response.data)
            assert data["error"] == "Validation failed"
            assert data["field"] == "email"
            assert data["constraint"] == "format"

    def test_validation_error(self, app):
        """Test validation error response."""
        with app.app_context():
            response, status_code = ApiResponse.validation_error("name", "is required")

            assert status_code == 400
            data = json.loads(response.data)
            assert data["error"] == "name is required"
            assert data["field"] == "name"

    def test_not_found_basic(self, app):
        """Test not found response without ID."""
        with app.app_context():
            response, status_code = ApiResponse.not_found("Organization")

            assert status_code == 404
            data = json.loads(response.data)
            assert data["error"] == "Organization not found"

    def test_not_found_with_id(self, app):
        """Test not found response with resource ID."""
        with app.app_context():
            response, status_code = ApiResponse.not_found("Entity", 123)

            assert status_code == 404
            data = json.loads(response.data)
            assert data["error"] == "Entity with id 123 not found"

    def test_forbidden(self, app):
        """Test forbidden response."""
        with app.app_context():
            response, status_code = ApiResponse.forbidden("Access denied")

            assert status_code == 403
            data = json.loads(response.data)
            assert data["error"] == "Access denied"

    def test_unauthorized(self, app):
        """Test unauthorized response."""
        with app.app_context():
            response, status_code = ApiResponse.unauthorized("Invalid token")

            assert status_code == 401
            data = json.loads(response.data)
            assert data["error"] == "Invalid token"

    def test_success(self, app):
        """Test success response."""
        with app.app_context():
            test_data = {"id": 1, "name": "Test"}
            response, status_code = ApiResponse.success(test_data)

            assert status_code == 200
            data = json.loads(response.data)
            assert data["id"] == 1
            assert data["name"] == "Test"

    def test_created(self, app):
        """Test created response."""
        with app.app_context():
            test_data = {"id": 1, "name": "New Resource"}
            response, status_code = ApiResponse.created(test_data)

            assert status_code == 201
            data = json.loads(response.data)
            assert data["id"] == 1
            assert data["name"] == "New Resource"

    def test_no_content(self, app):
        """Test no content response."""
        with app.app_context():
            response, status_code = ApiResponse.no_content()

            assert status_code == 204
            assert response == ""

    def test_bad_request(self, app):
        """Test bad request response."""
        with app.app_context():
            response, status_code = ApiResponse.bad_request("Invalid JSON")

            assert status_code == 400
            data = json.loads(response.data)
            assert data["error"] == "Invalid JSON"

    def test_conflict(self, app):
        """Test conflict response."""
        with app.app_context():
            response, status_code = ApiResponse.conflict("Duplicate name")

            assert status_code == 409
            data = json.loads(response.data)
            assert data["error"] == "Duplicate name"

    def test_internal_error(self, app):
        """Test internal error response."""
        with app.app_context():
            response, status_code = ApiResponse.internal_error("Database error")

            assert status_code == 500
            data = json.loads(response.data)
            assert data["error"] == "Database error"
