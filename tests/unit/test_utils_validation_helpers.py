"""
Unit tests for validation helper utilities.

These tests use mocking to avoid external dependencies.
No network calls or real database required.
"""

from unittest.mock import Mock, patch

import pytest

from apps.api.utils.validation_helpers import (
    validate_enum_value,
    validate_json_body,
    validate_organization_and_get_tenant,
    validate_pagination_params,
    validate_required_fields,
    validate_resource_exists,
    validate_tenant_exists,
)


class TestValidationHelpers:
    """Test validation helper functions."""

    def test_validate_required_fields_success(self):
        """Test successful required fields validation."""
        data = {"name": "Test", "type": "server"}
        result = validate_required_fields(data, ["name", "type"])
        assert result is None

    @pytest.mark.asyncio
    async def test_validate_required_fields_missing(self, app):
        """Test required fields validation with missing field."""
        async with app.app_context():
            data = {"name": "Test"}
            response, status_code = validate_required_fields(data, ["name", "type"])
            assert status_code == 400
            assert "type" in (await response.get_json())["error"]

    @pytest.mark.asyncio
    async def test_validate_required_fields_empty_value(self, app):
        """Test required fields validation with empty value."""
        async with app.app_context():
            data = {"name": "", "type": "server"}
            response, status_code = validate_required_fields(data, ["name", "type"])
            assert status_code == 400
            assert "name" in (await response.get_json())["error"]

    def test_validate_json_body_success(self):
        """Test successful JSON body validation."""
        data = {"key": "value"}
        result = validate_json_body(data)
        assert result is None

    @pytest.mark.asyncio
    async def test_validate_json_body_none(self, app):
        """Test JSON body validation with None."""
        async with app.app_context():
            response, status_code = validate_json_body(None)
            assert status_code == 400
            assert "JSON" in (await response.get_json())["error"]

    @pytest.mark.asyncio
    async def test_validate_json_body_empty_dict(self, app):
        """Test JSON body validation with empty dict."""
        async with app.app_context():
            response, status_code = validate_json_body({})
            assert status_code == 400
            assert "JSON" in (await response.get_json())["error"]

    def test_validate_pagination_params_success(self):
        """Test successful pagination params validation."""
        result = validate_pagination_params(1, 50)
        assert result is None

    @pytest.mark.asyncio
    async def test_validate_pagination_params_zero_page(self, app):
        """Test pagination validation with page < 1."""
        async with app.app_context():
            response, status_code = validate_pagination_params(0, 50)
            assert status_code == 400
            assert "Page" in (await response.get_json())["error"]

    @pytest.mark.asyncio
    async def test_validate_pagination_params_zero_per_page(self, app):
        """Test pagination validation with per_page < 1."""
        async with app.app_context():
            response, status_code = validate_pagination_params(1, 0)
            assert status_code == 400
            assert "per_page" in (await response.get_json())["error"]

    @pytest.mark.asyncio
    async def test_validate_pagination_params_exceeds_max(self, app):
        """Test pagination validation with per_page > max."""
        async with app.app_context():
            response, status_code = validate_pagination_params(1, 2000, max_per_page=1000)
            assert status_code == 400
            assert "1000" in (await response.get_json())["error"]

    def test_validate_enum_value_success(self):
        """Test successful enum value validation."""
        result = validate_enum_value("active", ["active", "inactive"], "status")
        assert result is None

    @pytest.mark.asyncio
    async def test_validate_enum_value_invalid(self, app):
        """Test enum value validation with invalid value."""
        async with app.app_context():
            response, status_code = validate_enum_value(
                "invalid", ["active", "inactive"], "status"
            )
            assert status_code == 400
            assert "must be one of" in (await response.get_json())["error"]

    @pytest.mark.asyncio
    async def test_validate_organization_and_get_tenant_success(self, app):
        """Test successful organization and tenant validation."""
        async with app.app_context():
            with patch("apps.api.utils.validation_helpers.current_app") as mock_app, \
                 patch("apps.api.utils.validation_helpers.run_in_threadpool") as mock_threadpool:
                mock_org = Mock()
                mock_org.tenant_id = 1
                mock_threadpool.return_value = mock_org

                org, tenant_id, error = await validate_organization_and_get_tenant(1)

                assert error is None
                assert org == mock_org
                assert tenant_id == 1

    @pytest.mark.asyncio
    async def test_validate_organization_and_get_tenant_not_found(self, app):
        """Test organization validation when not found."""
        async with app.app_context():
            with patch("apps.api.utils.validation_helpers.current_app") as mock_app, \
                 patch("apps.api.utils.validation_helpers.run_in_threadpool") as mock_threadpool:
                mock_threadpool.return_value = None

                org, tenant_id, error = await validate_organization_and_get_tenant(999)

                assert org is None
                assert tenant_id is None
                assert error is not None
                response, status_code = error
                assert status_code == 404

    @pytest.mark.asyncio
    async def test_validate_organization_and_get_tenant_no_tenant(self, app):
        """Test organization validation when no tenant assigned."""
        async with app.app_context():
            with patch("apps.api.utils.validation_helpers.current_app") as mock_app, \
                 patch("apps.api.utils.validation_helpers.run_in_threadpool") as mock_threadpool:
                mock_org = Mock()
                mock_org.tenant_id = None
                mock_threadpool.return_value = mock_org

                org, tenant_id, error = await validate_organization_and_get_tenant(1)

                assert org is None
                assert tenant_id is None
                assert error is not None

    @pytest.mark.asyncio
    async def test_validate_tenant_exists_success(self, app):
        """Test successful tenant validation."""
        async with app.app_context():
            with patch("apps.api.utils.validation_helpers.current_app") as mock_app, \
                 patch("apps.api.utils.validation_helpers.run_in_threadpool") as mock_threadpool:
                mock_tenant = Mock()
                mock_threadpool.return_value = mock_tenant

                tenant, error = await validate_tenant_exists(1)

                assert error is None
                assert tenant == mock_tenant

    @pytest.mark.asyncio
    async def test_validate_tenant_exists_not_found(self, app):
        """Test tenant validation when not found."""
        async with app.app_context():
            with patch("apps.api.utils.validation_helpers.current_app") as mock_app, \
                 patch("apps.api.utils.validation_helpers.run_in_threadpool") as mock_threadpool:
                mock_threadpool.return_value = None

                tenant, error = await validate_tenant_exists(999)

                assert tenant is None
                assert error is not None

    @pytest.mark.asyncio
    async def test_validate_resource_exists_not_found(self, app):
        """Test resource validation when not found."""
        async with app.app_context():
            mock_table = Mock()
            mock_table.__getitem__ = Mock(return_value=None)

            resource, error = await validate_resource_exists(mock_table, 999, "Entity")

            assert resource is None
            assert error is not None
            response, status_code = error
            assert status_code == 404
