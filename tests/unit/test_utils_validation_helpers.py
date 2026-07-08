"""
Unit tests for validation helper utilities.

These tests use mocking to avoid external dependencies.
No network calls or real database required.
"""

from unittest.mock import Mock, patch

import pytest
from flask import Flask

from apps.api.utils.validation_helpers import (
    validate_enum_value,
    validate_json_body,
    validate_organization_and_get_tenant,
    validate_pagination_params,
    validate_required_fields,
    validate_resource_exists,
    validate_tenant_exists,
)


@pytest.fixture
def app():
    """Create Flask app for testing."""
    app = Flask(__name__)
    return app


class TestValidationHelpers:
    """Test validation helper functions."""

    def test_validate_required_fields_success(self):
        """Test successful required fields validation."""
        data = {"name": "Test", "type": "server"}
        result = validate_required_fields(data, ["name", "type"])
        assert result is None

    def test_validate_required_fields_missing(self):
        """Test required fields validation with missing field."""
        data = {"name": "Test"}
        response, status_code = validate_required_fields(data, ["name", "type"])
        assert status_code == 400
        assert "type" in response.get_json()["error"]

    def test_validate_required_fields_empty_value(self):
        """Test required fields validation with empty value."""
        data = {"name": "", "type": "server"}
        response, status_code = validate_required_fields(data, ["name", "type"])
        assert status_code == 400
        assert "name" in response.get_json()["error"]

    def test_validate_json_body_success(self):
        """Test successful JSON body validation."""
        data = {"key": "value"}
        result = validate_json_body(data)
        assert result is None

    def test_validate_json_body_none(self):
        """Test JSON body validation with None."""
        response, status_code = validate_json_body(None)
        assert status_code == 400
        assert "JSON" in response.get_json()["error"]

    def test_validate_json_body_empty_dict(self):
        """Test JSON body validation with empty dict."""
        response, status_code = validate_json_body({})
        assert status_code == 400
        assert "JSON" in response.get_json()["error"]

    def test_validate_pagination_params_success(self):
        """Test successful pagination params validation."""
        result = validate_pagination_params(1, 50)
        assert result is None

    def test_validate_pagination_params_zero_page(self):
        """Test pagination validation with page < 1."""
        response, status_code = validate_pagination_params(0, 50)
        assert status_code == 400
        assert "Page" in response.get_json()["error"]

    def test_validate_pagination_params_zero_per_page(self):
        """Test pagination validation with per_page < 1."""
        response, status_code = validate_pagination_params(1, 0)
        assert status_code == 400
        assert "per_page" in response.get_json()["error"]

    def test_validate_pagination_params_exceeds_max(self):
        """Test pagination validation with per_page > max."""
        response, status_code = validate_pagination_params(1, 2000, max_per_page=1000)
        assert status_code == 400
        assert "1000" in response.get_json()["error"]

    def test_validate_enum_value_success(self):
        """Test successful enum value validation."""
        result = validate_enum_value("active", ["active", "inactive"], "status")
        assert result is None

    def test_validate_enum_value_invalid(self):
        """Test enum validation with invalid value."""
        response, status_code = validate_enum_value(
            "invalid", ["active", "inactive"], "status"
        )
        assert status_code == 400
        error_msg = response.get_json()["error"]
        assert "status" in error_msg
        assert "active" in error_msg
        assert "inactive" in error_msg

    @pytest.mark.asyncio
    @patch("apps.api.utils.validation_helpers.current_app")
    @patch("apps.api.utils.validation_helpers.run_in_threadpool")
    async def test_validate_organization_and_get_tenant_success(
        self, mock_threadpool, mock_app, app
    ):
        """Test successful organization and tenant validation."""
        with app.app_context():
            # Mock organization with tenant
            mock_org = Mock()
            mock_org.tenant_id = 1
            mock_threadpool.return_value = mock_org

            org, tenant_id, error = await validate_organization_and_get_tenant(1)

            assert org == mock_org
            assert tenant_id == 1
            assert error is None

    @pytest.mark.asyncio
    @patch("apps.api.utils.validation_helpers.current_app")
    @patch("apps.api.utils.validation_helpers.run_in_threadpool")
    async def test_validate_organization_and_get_tenant_not_found(
        self, mock_threadpool, mock_app, app
    ):
        """Test organization validation when org not found."""
        with app.app_context():
            mock_threadpool.return_value = None

            org, tenant_id, error = await validate_organization_and_get_tenant(999)

            assert org is None
            assert tenant_id is None
            assert error is not None
            response, status_code = error
            assert status_code == 404

    @pytest.mark.asyncio
    @patch("apps.api.utils.validation_helpers.current_app")
    @patch("apps.api.utils.validation_helpers.run_in_threadpool")
    async def test_validate_organization_and_get_tenant_no_tenant(
        self, mock_threadpool, mock_app, app
    ):
        """Test organization validation when org has no tenant."""
        with app.app_context():
            # Mock organization without tenant
            mock_org = Mock()
            mock_org.tenant_id = None
            mock_threadpool.return_value = mock_org

            org, tenant_id, error = await validate_organization_and_get_tenant(1)

            assert org is None
            assert tenant_id is None
            assert error is not None
            response, status_code = error
            assert status_code == 400
            assert "tenant" in response.get_json()["error"]

    @pytest.mark.asyncio
    @patch("apps.api.utils.validation_helpers.current_app")
    @patch("apps.api.utils.validation_helpers.run_in_threadpool")
    async def test_validate_tenant_exists_success(self, mock_threadpool, mock_app, app):
        """Test successful tenant validation."""
        with app.app_context():
            mock_tenant = Mock()
            mock_tenant.id = 1
            mock_threadpool.return_value = mock_tenant

            tenant, error = await validate_tenant_exists(1)

            assert tenant == mock_tenant
            assert error is None

    @pytest.mark.asyncio
    @patch("apps.api.utils.validation_helpers.current_app")
    @patch("apps.api.utils.validation_helpers.run_in_threadpool")
    async def test_validate_tenant_exists_not_found(
        self, mock_threadpool, mock_app, app
    ):
        """Test tenant validation when not found."""
        with app.app_context():
            mock_threadpool.return_value = None

            tenant, error = await validate_tenant_exists(999)

            assert tenant is None
            assert error is not None
            response, status_code = error
            assert status_code == 404

    @pytest.mark.asyncio
    @patch("apps.api.utils.validation_helpers.run_in_threadpool")
    async def test_validate_resource_exists_success(self, mock_threadpool, app):
        """Test successful resource existence validation."""
        with app.app_context():
            mock_resource = Mock()
            mock_resource.id = 1
            mock_threadpool.return_value = mock_resource

            mock_table = Mock()
            resource, error = await validate_resource_exists(mock_table, 1, "Entity")

            assert resource == mock_resource
            assert error is None

    @pytest.mark.asyncio
    @patch("apps.api.utils.validation_helpers.run_in_threadpool")
    async def test_validate_resource_exists_not_found(self, mock_threadpool, app):
        """Test resource existence validation when not found."""
        with app.app_context():
            mock_threadpool.return_value = None

            mock_table = Mock()
            resource, error = await validate_resource_exists(mock_table, 999, "Entity")

            assert resource is None
            assert error is not None
            response, status_code = error
            assert status_code == 404
            assert "Entity" in response.get_json()["error"]
