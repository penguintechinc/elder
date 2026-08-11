"""Pytest configuration and fixtures for Elder tests.

This module provides fixtures for both unit tests and integration tests.
Unit tests should use mocks and not require external services.
Integration tests use the actual Postgres database via test containers.

Configuration:
- Unit tests (tests/unit/) must NOT require DATABASE_URL to be set
  (use mocks instead)
- Integration tests (tests/integration/) require DATABASE_URL pointing to
  a test Postgres instance (set via env or fixture)
"""

import importlib
import os
from datetime import UTC

import pytest
import pytest_asyncio


def pytest_configure(config):
    """Configure custom pytest markers and ensure sane defaults."""
    config.addinivalue_line(
        "markers", "unit: Unit tests (no external dependencies, mocks only)"
    )
    config.addinivalue_line(
        "markers", "integration: Integration tests (requires test database)"
    )
    config.addinivalue_line(
        "markers", "e2e: End-to-end tests (requires full Docker environment)"
    )
    config.addinivalue_line("markers", "slow: Tests that take a long time to run")

    # Set testing environment before any app imports
    os.environ.setdefault("FLASK_ENV", "testing")
    os.environ.setdefault("QUART_ENV", "testing")
    # Enable all modules for testing (including default_enabled=False modules like helpdesk)
    os.environ.setdefault("ELDER_MODULES_ENABLED", "all")
    # Explicitly enable helpdesk (default_enabled=False, requires per-module override)
    os.environ.setdefault("ELDER_MODULE_HELPDESK", "true")


@pytest.fixture(scope="session")
def test_database_url():
    """Provide test database URL from environment.

    Expects DATABASE_URL to be set to a test Postgres instance.
    Integration tests require this; unit tests should not use it.

    Returns:
        Database URL string, or None if not set.
    """
    return os.getenv("DATABASE_URL")


@pytest.fixture(scope="session")
def redis_url():
    """Provide test Redis URL from environment.

    Returns:
        Redis URL string (may be None if not configured).
    """
    return os.getenv("REDIS_URL", "redis://localhost:56379/0")


@pytest.fixture(scope="session", autouse=True)
def init_test_database(test_database_url):
    """Initialize test database schema at session start.

    This fixture imports all registry models (CORE_MODELS + MODULES models_import)
    and runs SQLAlchemy create_all() to build the 83-table schema (references added).

    Runs once per test session before any integration tests.
    Only initializes if DATABASE_URL is set (integration tests).
    """
    import logging

    logger = logging.getLogger(__name__)

    # Skip database initialization if DATABASE_URL not set (unit tests only)
    if not test_database_url:
        logger.debug(
            "DATABASE_URL not set; skipping database initialization (unit tests only)"
        )
        return

    try:
        # Import registry models
        from sqlalchemy import create_engine

        from apps.api.models.base import Base
        from apps.api.modules import CORE_MODELS, MODULES

        # rsplit is robust to credential-less URLs (e.g. sqlite:///path) that
        # have no '@' — never IndexErrors, just logs the whole URL in that case.
        logger.info(
            f"Initializing test database: {test_database_url.rsplit('@', 1)[-1]}"
        )

        # Collect all model modules to import (registry-driven)
        model_modules_to_import = set(CORE_MODELS)
        for module_manifest in MODULES:
            for model_module in module_manifest.models_import:
                model_modules_to_import.add(model_module)

        # Import all models so they register with Base.metadata
        for model_module_path in sorted(model_modules_to_import):
            try:
                importlib.import_module(model_module_path)
            except ImportError as e:
                logger.warning(
                    f"Failed to import model module {model_module_path}: {e}"
                )

        # Create engine and run create_all()
        engine = create_engine(test_database_url)
        Base.metadata.create_all(engine)
        engine.dispose()

        # Sanity-check that all core + module models loaded. This is a floor,
        # not an exact count, so it survives additive schema growth per phase
        # (helpdesk +12 = 96, documents +4 = 100, pages +2 = 102, references/issue-links reconciliation +2 = 104, ...);
        # it still catches catastrophic under-loading (models failing to import).
        table_count = len(Base.metadata.tables)
        logger.info(f"Test database initialized: {table_count} tables")
        assert table_count >= 102, (
            f"Expected >= 102 tables (core + modules); got {table_count} "
            "— a model module likely failed to load"
        )

    except Exception as e:
        logger.error(f"Failed to initialize test database: {e}")
        raise


@pytest.fixture(scope="session")
def app(test_database_url):
    """Create Quart application for testing.

    Only fully initializes if DATABASE_URL is set (integration tests).
    For unit tests without DATABASE_URL, provides a minimal app.

    Returns:
        Configured Quart app (native ASGI).
    """
    from apps.api.main import create_app

    if test_database_url:
        os.environ["DATABASE_URL"] = test_database_url

    try:
        app = create_app("testing")
        app.config["TESTING"] = True
    except Exception as e:
        # If DATABASE_URL not set, create minimal app for mocked tests
        if not test_database_url:
            from quart import Quart

            app = Quart(__name__)
            app.config["TESTING"] = True
        else:
            raise

    yield app


@pytest.fixture(scope="function")
def client(app):
    """Create Quart test client for API testing.

    Args:
        app: Quart application fixture

    Returns:
        Quart test client
    """
    return app.test_client()


@pytest_asyncio.fixture(scope="function")
async def async_client(app):
    """Create async Quart test client for async API testing.

    Args:
        app: Quart application fixture

    Returns:
        Async Quart test client
    """
    async with app.test_client() as client:
        yield client


@pytest.fixture(scope="function")
def mock_pydal_db(mocker):
    """Create a mock PyDAL database for unit tests.

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


@pytest.fixture(scope="function")
def auth_headers(client, app):
    """Provide authenticated headers for API tests.

    Creates a test user and returns headers with JWT token.
    For unit tests: return empty dict (fixture is optional).

    Args:
        client: Quart test client
        app: Quart application

    Returns:
        dict with Authorization header (empty if auth unavailable)
    """
    # Placeholder: real implementation would create test user and get token
    # For now, return empty headers (tests should mock auth)
    return {}


@pytest.fixture(scope="function")
def generate_token(app):
    """Generate JWT token with tenant claim for security tests.

    Args:
        app: Quart application
        tenant_id: Tenant ID to include in token (required)

    Returns:
        Function that takes (tenant_id, scopes=[]) and returns JWT token string
    """
    from datetime import datetime, timedelta, timezone

    import jwt

    def _generate_token(tenant_id: int, scopes: list = None):
        """Generate a JWT token with the given tenant_id and scopes."""
        if scopes is None:
            scopes = []

        secret = app.config.get("JWT_SECRET_KEY") or app.config.get("SECRET_KEY")
        algorithm = app.config.get("JWT_ALGORITHM", "HS256")

        now = datetime.now(UTC)
        payload = {
            "sub": "test-user-123",
            "tenant": str(tenant_id),  # Important: tenant claim as string
            "iat": now,
            "exp": now + timedelta(hours=1),
            "scope": scopes,
            "roles": ["test"],
        }

        token = jwt.encode(payload, secret, algorithm=algorithm)
        return token

    return _generate_token


@pytest.fixture(scope="function", autouse=True)
def enable_helpdesk_module(app):
    """Enable helpdesk module for tenant 1 for all tests.

    This fixture runs for every test function and ensures that the helpdesk
    module is enabled for tenant 1 in the test database via direct SQL.
    """
    if app.config.get("TESTING"):
        try:
            from quart import current_app as ctx_app

            # Enable helpdesk for tenant 1 in the database
            async def enable():
                async with app.app_context():
                    db = ctx_app.db
                    # Insert or update tenant_modules to enable helpdesk for tenant 1
                    try:
                        db.tenant_modules.insert(
                            tenant_id=1, module_name="helpdesk", is_enabled=True
                        )
                    except:
                        # If row already exists, update it
                        db(
                            (db.tenant_modules.tenant_id == 1)
                            & (db.tenant_modules.module_name == "helpdesk")
                        ).update(is_enabled=True)
                    db.commit()

            # Run the async function synchronously
            import asyncio

            try:
                loop = asyncio.get_event_loop()
                if loop.is_closed():
                    raise RuntimeError("Loop is closed")
            except RuntimeError:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)

            loop.run_until_complete(enable())
        except Exception as e:
            # Silently fail—module may already be enabled or DB unavailable
            pass

    yield
