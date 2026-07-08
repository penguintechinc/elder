"""
Unit tests for PyDAL helper utilities.

These tests use mocking to avoid external dependencies.
No network calls or real database required.
"""

from unittest.mock import Mock, patch

import pytest
from flask import Flask

from apps.api.utils.pydal_helpers import (
    PaginationParams,
    commit_db,
    delete_record,
    get_by_id,
    insert_record,
    paginated_query,
    query_count,
    query_delete,
    query_select,
    query_update,
    update_record,
)


@pytest.fixture
def app():
    """Create Flask app for testing."""
    app = Flask(__name__)
    app.config["TESTING"] = True
    return app


@pytest.fixture
def mock_request():
    """Mock Flask request for pagination testing."""
    with patch("apps.api.utils.pydal_helpers.request") as mock_req:
        mock_req.args = Mock()
        yield mock_req


class TestPyDALHelpers:
    """Test PyDAL helper functions."""

    @pytest.mark.asyncio
    @patch("apps.api.utils.pydal_helpers.run_in_threadpool")
    async def test_get_by_id_success(self, mock_threadpool):
        """Test successful get by ID."""
        mock_record = Mock()
        mock_record.id = 1
        mock_record.name = "Test"
        mock_table = Mock()
        mock_table.__getitem__ = Mock(return_value=mock_record)
        mock_threadpool.return_value = mock_record

        result = await get_by_id(mock_table, 1)

        assert result == mock_record
        mock_threadpool.assert_called_once()

    @pytest.mark.asyncio
    @patch("apps.api.utils.pydal_helpers.run_in_threadpool")
    async def test_get_by_id_not_found(self, mock_threadpool):
        """Test get by ID when record not found."""
        mock_table = Mock()
        mock_table.__getitem__ = Mock(return_value=None)
        mock_threadpool.return_value = None

        result = await get_by_id(mock_table, 999)

        assert result is None

    @pytest.mark.asyncio
    @patch("apps.api.utils.pydal_helpers.run_in_threadpool")
    async def test_query_count(self, mock_threadpool):
        """Test query count."""
        mock_query = Mock()
        mock_query.count = Mock(return_value=42)
        mock_threadpool.return_value = 42

        result = await query_count(mock_query)

        assert result == 42
        mock_threadpool.assert_called_once()

    @pytest.mark.asyncio
    @patch("apps.api.utils.pydal_helpers.run_in_threadpool")
    async def test_query_select_basic(self, mock_threadpool):
        """Test basic query select."""
        mock_rows = [Mock(id=1), Mock(id=2)]
        mock_query = Mock()
        mock_query.select = Mock(return_value=mock_rows)
        mock_threadpool.return_value = mock_rows

        result = await query_select(mock_query)

        assert len(result) == 2
        mock_threadpool.assert_called_once()

    @pytest.mark.asyncio
    @patch("apps.api.utils.pydal_helpers.run_in_threadpool")
    async def test_query_select_with_orderby_and_limit(self, mock_threadpool):
        """Test query select with orderby and limitby."""
        mock_rows = [Mock(id=1)]
        mock_query = Mock()
        mock_query.select = Mock(return_value=mock_rows)
        mock_threadpool.return_value = mock_rows

        result = await query_select(mock_query, orderby="~id", limitby=(0, 10))

        assert len(result) == 1
        mock_threadpool.assert_called_once()

    @pytest.mark.asyncio
    @patch("apps.api.utils.pydal_helpers.run_in_threadpool")
    async def test_insert_record(self, mock_threadpool):
        """Test insert record."""
        mock_table = Mock()
        mock_table.insert = Mock(return_value=1)
        mock_threadpool.return_value = 1

        result = await insert_record(mock_table, name="Test", status="active")

        assert result == 1
        mock_threadpool.assert_called_once()

    @pytest.mark.asyncio
    @patch("apps.api.utils.pydal_helpers.run_in_threadpool")
    async def test_update_record_success(self, mock_threadpool):
        """Test successful update record."""
        mock_record = Mock()
        mock_table = Mock()
        mock_table.__getitem__ = Mock(return_value=mock_record)
        mock_table.__setitem__ = Mock()
        mock_threadpool.return_value = True

        result = await update_record(mock_table, 1, name="Updated")

        assert result is True
        mock_threadpool.assert_called_once()

    @pytest.mark.asyncio
    @patch("apps.api.utils.pydal_helpers.run_in_threadpool")
    async def test_update_record_not_found(self, mock_threadpool):
        """Test update record when not found."""
        mock_table = Mock()
        mock_table.__getitem__ = Mock(return_value=None)
        mock_threadpool.return_value = False

        result = await update_record(mock_table, 999, name="Updated")

        assert result is False

    @pytest.mark.asyncio
    @patch("apps.api.utils.pydal_helpers.run_in_threadpool")
    async def test_delete_record_success(self, mock_threadpool):
        """Test successful delete record."""
        mock_record = Mock()
        mock_table = Mock()
        mock_table.__getitem__ = Mock(return_value=mock_record)
        mock_table.__delitem__ = Mock()
        mock_threadpool.return_value = True

        result = await delete_record(mock_table, 1)

        assert result is True
        mock_threadpool.assert_called_once()

    @pytest.mark.asyncio
    @patch("apps.api.utils.pydal_helpers.run_in_threadpool")
    async def test_delete_record_not_found(self, mock_threadpool):
        """Test delete record when not found."""
        mock_table = Mock()
        mock_table.__getitem__ = Mock(return_value=None)
        mock_threadpool.return_value = False

        result = await delete_record(mock_table, 999)

        assert result is False

    @pytest.mark.asyncio
    @patch("apps.api.utils.pydal_helpers.run_in_threadpool")
    async def test_query_update(self, mock_threadpool):
        """Test query update."""
        mock_query = Mock()
        mock_query.update = Mock(return_value=3)
        mock_threadpool.return_value = 3

        result = await query_update(mock_query, status="archived")

        assert result == 3
        mock_threadpool.assert_called_once()

    @pytest.mark.asyncio
    @patch("apps.api.utils.pydal_helpers.run_in_threadpool")
    async def test_query_delete(self, mock_threadpool):
        """Test query delete."""
        mock_query = Mock()
        mock_query.delete = Mock(return_value=2)
        mock_threadpool.return_value = 2

        result = await query_delete(mock_query)

        assert result == 2
        mock_threadpool.assert_called_once()

    @pytest.mark.asyncio
    @patch("apps.api.utils.pydal_helpers.run_in_threadpool")
    async def test_commit_db(self, mock_threadpool):
        """Test database commit."""
        mock_db = Mock()
        mock_db.commit = Mock()
        mock_threadpool.return_value = None

        await commit_db(mock_db)

        mock_threadpool.assert_called_once()


class TestPaginationParams:
    """Test PaginationParams class."""

    def test_pagination_params_init(self):
        """Test PaginationParams initialization."""
        pagination = PaginationParams(page=2, per_page=25, offset=25)

        assert pagination.page == 2
        assert pagination.per_page == 25
        assert pagination.offset == 25

    def test_from_request_defaults(self, mock_request):
        """Test from_request with default values."""
        mock_request.args.get = Mock(side_effect=[None, None])

        pagination = PaginationParams.from_request()

        assert pagination.page == 1
        assert pagination.per_page == 50
        assert pagination.offset == 0

    def test_from_request_custom_values(self, mock_request):
        """Test from_request with custom values."""

        def mock_get(key, default, type=None):
            if key == "page":
                return 3
            elif key == "per_page":
                return 100
            return default

        mock_request.args.get = mock_get

        pagination = PaginationParams.from_request()

        assert pagination.page == 3
        assert pagination.per_page == 100
        assert pagination.offset == 200  # (3-1) * 100

    def test_from_request_max_per_page(self, mock_request):
        """Test from_request enforces max per_page."""

        def mock_get(key, default, type=None):
            if key == "page":
                return 1
            elif key == "per_page":
                return 2000  # Exceeds max
            return default

        mock_request.args.get = mock_get

        pagination = PaginationParams.from_request(max_per_page=1000)

        assert pagination.per_page == 1000  # Capped at max

    def test_calculate_pages_empty(self):
        """Test calculate_pages with 0 total."""
        pagination = PaginationParams(page=1, per_page=50, offset=0)

        pages = pagination.calculate_pages(0)

        assert pages == 0

    def test_calculate_pages_exact(self):
        """Test calculate_pages with exact multiple."""
        pagination = PaginationParams(page=1, per_page=50, offset=0)

        pages = pagination.calculate_pages(100)

        assert pages == 2

    def test_calculate_pages_partial(self):
        """Test calculate_pages with partial page."""
        pagination = PaginationParams(page=1, per_page=50, offset=0)

        pages = pagination.calculate_pages(125)

        assert pages == 3  # 50, 50, 25

    @pytest.mark.asyncio
    @patch("apps.api.utils.pydal_helpers.run_in_threadpool")
    async def test_paginated_query(self, mock_threadpool):
        """Test paginated_query function."""
        mock_rows = [Mock(id=1), Mock(id=2)]

        # Mock count and select calls
        call_count = [0]

        def mock_executor(func):
            call_count[0] += 1
            if call_count[0] == 1:  # First call is count
                return 42
            else:  # Second call is select
                return mock_rows

        mock_threadpool.side_effect = mock_executor

        mock_query = Mock()
        pagination = PaginationParams(page=1, per_page=10, offset=0)

        rows, total = await paginated_query(mock_query, pagination)

        assert len(rows) == 2
        assert total == 42
        assert mock_threadpool.call_count == 2
