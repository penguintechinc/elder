"""Pytest configuration and fixtures for Elder tests.

This module provides fixtures for both unit tests and integration tests.
Unit tests should use mocks and not require external services.
Integration tests may use the actual database.

Migration Note:
    Elder uses PyDAL for database operations (not SQLAlchemy).
    All fixtures have been updated for PyDAL compatibility.
"""

import os

import pytest

# Set testing environment before any app imports
os.environ.setdefault("FLASK_ENV", "testing")
os.environ.setdefault("DATABASE_URL", "sqlite://test.db")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only")


@pytest.fixture(scope="session")
def app():
    """
    Create Flask application for testing.

    Returns:
        Flask app configured for testing
    """
    from apps.api.main import create_app
    from apps.api.database import db

    app = create_app("testing")

    with app.app_context():
        # PyDAL auto-creates tables on initialization
        # Tables are already defined in init_db()
        yield app

        # Cleanup: drop test database file if using SQLite
        if "sqlite" in str(app.config.get("DATABASE_URL", "")):
            try:
                db_file = "test.db"
                if os.path.exists(db_file):
                    os.remove(db_file)
                if os.path.exists(f"{db_file}.sqlite"):
                    os.remove(f"{db_file}.sqlite")
            except Exception:
                pass  # Ignore cleanup errors


@pytest.fixture(scope="function")
def client(app):
    """
    Create Flask test client.

    Args:
        app: Flask application fixture

    Returns:
        Flask test client
    """
    return app.test_client()


@pytest.fixture(scope="function")
def db_session(app):
    """
    Provide PyDAL database session for tests with transaction rollback.

    This fixture wraps each test in a transaction that gets rolled back,
    ensuring test isolation.

    Args:
        app: Flask application fixture

    Yields:
        PyDAL db instance
    """
    from apps.api.database import db

    with app.app_context():
        # Start a savepoint for test isolation
        # Note: PyDAL doesn't have nested transactions like SQLAlchemy
        # We'll commit at the end of setup and rollback changes manually
        yield db

        # Rollback any uncommitted changes
        try:
            db.rollback()
        except Exception:
            pass


@pytest.fixture(scope="function")
def auth_headers(client, app):
    """
    Provide authenticated headers for API tests.

    Creates a test user and returns headers with JWT token.

    Args:
        client: Flask test client
        app: Flask application

    Returns:
        dict with Authorization header
    """
    with app.app_context():
        # Login with admin user (created during db init)
        response = client.post(
            "/api/v1/auth/login",
            json={
                "email": os.getenv("ADMIN_EMAIL", "admin@localhost.local"),
                "password": os.getenv("ADMIN_PASSWORD", "admin123"),
            },
        )

        if response.status_code == 200:
            data = response.get_json()
            token = data.get("access_token") or data.get("token")
            return {"Authorization": f"Bearer {token}"}

        # Fallback: return empty headers (tests may skip or use mock)
        return {}


@pytest.fixture
def mock_pydal_db(mocker):
    """
    Create a mock PyDAL database for unit tests.

    Use this fixture for unit tests that shouldn't touch the real database.

    Args:
        mocker: pytest-mock fixture

    Returns:
        MagicMock configured as a PyDAL db
    """
    from unittest.mock import MagicMock

    mock_db = MagicMock()
    mock_db.tables = []
    mock_db.commit = MagicMock()
    mock_db.rollback = MagicMock()

    return mock_db


# Markers for test categories
def pytest_configure(config):
    """Configure custom pytest markers."""
    config.addinivalue_line(
        "markers", "unit: Unit tests (no external dependencies)"
    )
    config.addinivalue_line(
        "markers", "integration: Integration tests (requires database)"
    )
    config.addinivalue_line(
        "markers", "e2e: End-to-end tests (requires full Docker environment)"
    )
    config.addinivalue_line(
        "markers", "slow: Tests that take a long time to run"
    )
