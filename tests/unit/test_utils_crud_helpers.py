"""
Unit tests for CRUD helper utilities.

These tests use extensive mocking to avoid external dependencies.
No network calls or real database required.
"""

import json
from unittest.mock import Mock, patch

import pytest
from flask import Flask

from apps.api.utils.crud_helpers import CrudHelper


@pytest.fixture
def app():
    """Create Flask app for testing."""
    app = Flask(__name__)
    app.config["TESTING"] = True
    return app


@pytest.fixture
def mock_table():
    """Mock PyDAL table."""
    table = Mock()
    table.id = Mock()
    table.created_at = Mock()
    return table


@pytest.fixture
def mock_db(mock_table):
    """Mock database with table."""
    db = Mock()
    db.entities = mock_table
    return db


class TestCrudHelperList:
    """Test CrudHelper.list_resources method."""

    @pytest.mark.asyncio
    @patch("apps.api.utils.crud_helpers.current_app")
    @patch("apps.api.utils.crud_helpers.PaginationParams")
    @patch("apps.api.utils.crud_helpers.run_in_threadpool")
    async def test_list_resources_basic(
        self, mock_threadpool, mock_pagination_class, mock_app, app, mock_table, mock_db
    ):
        """Test basic list resources."""
        with app.app_context():
            # Setup mocks
            mock_app.db = mock_db
            mock_pagination = Mock()
            mock_pagination.page = 1
            mock_pagination.per_page = 50
            mock_pagination.offset = 0
            mock_pagination.calculate_pages = Mock(return_value=1)
            mock_pagination_class.from_request = Mock(return_value=mock_pagination)

            # Mock query results
            mock_row1 = Mock()
            mock_row1.as_dict = Mock(return_value={"id": 1, "name": "Test1"})
            mock_row2 = Mock()
            mock_row2.as_dict = Mock(return_value={"id": 2, "name": "Test2"})
            mock_rows = [mock_row1, mock_row2]

            mock_threadpool.return_value = (mock_rows, 2)

            # Execute
            response, status_code = await CrudHelper.list_resources(
                mock_table, resource_type="Entity"
            )

            # Verify
            assert status_code == 200
            data = json.loads(response.data)
            assert data["total"] == 2
            assert len(data["items"]) == 2
            assert data["page"] == 1
            assert data["per_page"] == 50
            assert data["pages"] == 1

    @pytest.mark.asyncio
    @patch("apps.api.utils.crud_helpers.current_app")
    @patch("apps.api.utils.crud_helpers.PaginationParams")
    @patch("apps.api.utils.crud_helpers.run_in_threadpool")
    async def test_list_resources_with_filter(
        self, mock_threadpool, mock_pagination_class, mock_app, app, mock_table, mock_db
    ):
        """Test list resources with custom filter."""
        with app.app_context():
            mock_app.db = mock_db
            mock_pagination = Mock()
            mock_pagination.page = 1
            mock_pagination.per_page = 50
            mock_pagination.offset = 0
            mock_pagination.calculate_pages = Mock(return_value=0)
            mock_pagination_class.from_request = Mock(return_value=mock_pagination)

            mock_threadpool.return_value = ([], 0)

            # Custom filter function
            def filter_fn(query):
                return query

            response, status_code = await CrudHelper.list_resources(
                mock_table, resource_type="Entity", filter_fn=filter_fn
            )

            assert status_code == 200
            data = json.loads(response.data)
            assert data["total"] == 0
            assert len(data["items"]) == 0


class TestCrudHelperCreate:
    """Test CrudHelper.create_resource method."""

    @pytest.mark.asyncio
    @patch("apps.api.utils.crud_helpers.current_app")
    @patch("apps.api.utils.crud_helpers.request")
    @patch("apps.api.utils.crud_helpers.run_in_threadpool")
    @patch("apps.api.utils.crud_helpers.get_by_id")
    async def test_create_resource_success(
        self,
        mock_get_by_id,
        mock_threadpool,
        mock_request,
        mock_app,
        app,
        mock_table,
        mock_db,
    ):
        """Test successful resource creation."""
        with app.app_context():
            mock_app.db = mock_db
            mock_request.get_json = Mock(
                return_value={"name": "Test", "type": "server"}
            )

            # Mock insert
            mock_threadpool.return_value = 1

            # Mock get created record
            mock_record = Mock()
            mock_record.as_dict = Mock(
                return_value={"id": 1, "name": "Test", "type": "server"}
            )
            mock_get_by_id.return_value = mock_record

            response, status_code = await CrudHelper.create_resource(
                mock_table, resource_type="Entity"
            )

            assert status_code == 201
            data = json.loads(response.data)
            assert data["id"] == 1
            assert data["name"] == "Test"

    @pytest.mark.asyncio
    @patch("apps.api.utils.crud_helpers.request")
    async def test_create_resource_no_json(self, mock_request, app, mock_table):
        """Test create resource with no JSON body."""
        with app.app_context():
            mock_request.get_json = Mock(return_value=None)

            response, status_code = await CrudHelper.create_resource(
                mock_table, resource_type="Entity"
            )

            assert status_code == 400
            data = json.loads(response.data)
            assert "JSON" in data["error"]

    @pytest.mark.asyncio
    @patch("apps.api.utils.crud_helpers.request")
    async def test_create_resource_missing_required_field(
        self, mock_request, app, mock_table
    ):
        """Test create resource with missing required field."""
        with app.app_context():
            mock_request.get_json = Mock(return_value={"name": "Test"})

            response, status_code = await CrudHelper.create_resource(
                mock_table, resource_type="Entity", required_fields=["name", "type"]
            )

            assert status_code == 400
            data = json.loads(response.data)
            assert "type" in data["error"]


class TestCrudHelperGet:
    """Test CrudHelper.get_resource method."""

    @pytest.mark.asyncio
    @patch("apps.api.utils.crud_helpers.validate_resource_exists")
    async def test_get_resource_success(self, mock_validate, app, mock_table):
        """Test successful get resource."""
        with app.app_context():
            mock_record = Mock()
            mock_record.as_dict = Mock(return_value={"id": 1, "name": "Test"})
            mock_validate.return_value = (mock_record, None)

            response, status_code = await CrudHelper.get_resource(
                mock_table, 1, resource_type="Entity"
            )

            assert status_code == 200
            data = json.loads(response.data)
            assert data["id"] == 1
            assert data["name"] == "Test"

    @pytest.mark.asyncio
    @patch("apps.api.utils.crud_helpers.validate_resource_exists")
    async def test_get_resource_not_found(self, mock_validate, app, mock_table):
        """Test get resource when not found."""
        with app.app_context():
            from apps.api.utils.api_responses import ApiResponse

            error_response = ApiResponse.not_found("Entity", 999)
            mock_validate.return_value = (None, error_response)

            response, status_code = await CrudHelper.get_resource(
                mock_table, 999, resource_type="Entity"
            )

            assert status_code == 404


class TestCrudHelperUpdate:
    """Test CrudHelper.update_resource method."""

    @pytest.mark.asyncio
    @patch("apps.api.utils.crud_helpers.current_app")
    @patch("apps.api.utils.crud_helpers.request")
    @patch("apps.api.utils.crud_helpers.validate_resource_exists")
    @patch("apps.api.utils.crud_helpers.run_in_threadpool")
    async def test_update_resource_success(
        self,
        mock_threadpool,
        mock_validate,
        mock_request,
        mock_app,
        app,
        mock_table,
        mock_db,
    ):
        """Test successful resource update."""
        with app.app_context():
            mock_app.db = mock_db
            mock_request.get_json = Mock(return_value={"name": "Updated"})

            # Mock existing resource
            mock_record = Mock()
            mock_validate.return_value = (mock_record, None)

            # Mock updated resource
            mock_updated = Mock()
            mock_updated.as_dict = Mock(return_value={"id": 1, "name": "Updated"})
            mock_threadpool.return_value = mock_updated

            response, status_code = await CrudHelper.update_resource(
                mock_table, 1, resource_type="Entity"
            )

            assert status_code == 200
            data = json.loads(response.data)
            assert data["name"] == "Updated"

    @pytest.mark.asyncio
    @patch("apps.api.utils.crud_helpers.request")
    @patch("apps.api.utils.crud_helpers.validate_resource_exists")
    async def test_update_resource_not_found(
        self, mock_validate, mock_request, app, mock_table
    ):
        """Test update resource when not found."""
        with app.app_context():
            mock_request.get_json = Mock(return_value={"name": "Updated"})

            from apps.api.utils.api_responses import ApiResponse

            error_response = ApiResponse.not_found("Entity", 999)
            mock_validate.return_value = (None, error_response)

            response, status_code = await CrudHelper.update_resource(
                mock_table, 999, resource_type="Entity"
            )

            assert status_code == 404


class TestCrudHelperDelete:
    """Test CrudHelper.delete_resource method."""

    @pytest.mark.asyncio
    @patch("apps.api.utils.crud_helpers.current_app")
    @patch("apps.api.utils.crud_helpers.validate_resource_exists")
    @patch("apps.api.utils.crud_helpers.run_in_threadpool")
    async def test_delete_resource_success(
        self, mock_threadpool, mock_validate, mock_app, app, mock_table, mock_db
    ):
        """Test successful resource deletion."""
        with app.app_context():
            mock_app.db = mock_db

            mock_record = Mock()
            mock_validate.return_value = (mock_record, None)

            mock_threadpool.return_value = None

            response, status_code = await CrudHelper.delete_resource(
                mock_table, 1, resource_type="Entity"
            )

            assert status_code == 204
            assert response == ""

    @pytest.mark.asyncio
    @patch("apps.api.utils.crud_helpers.validate_resource_exists")
    async def test_delete_resource_not_found(self, mock_validate, app, mock_table):
        """Test delete resource when not found."""
        with app.app_context():
            from apps.api.utils.api_responses import ApiResponse

            error_response = ApiResponse.not_found("Entity", 999)
            mock_validate.return_value = (None, error_response)

            response, status_code = await CrudHelper.delete_resource(
                mock_table, 999, resource_type="Entity"
            )

            assert status_code == 404
