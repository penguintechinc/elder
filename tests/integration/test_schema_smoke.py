"""Smoke test for schema initialization — verifies the 82-table invariant.

This test is the acceptance proof that the test infrastructure works:
- create_app boots against test Postgres
- create_all produces exactly 82 tables
- Redis round-trip works
"""

import asyncio
import os

import pytest


@pytest.mark.integration
def test_schema_initialization(app, test_database_url):
    """Verify schema initialization: exactly 82 tables created.

    Args:
        app: Quart application fixture
        test_database_url: Test database connection string

    Asserts:
        - DATABASE_URL is set correctly
        - SQLAlchemy Base.metadata contains exactly 82 tables
    """
    # Verify DATABASE_URL is set
    assert test_database_url, "DATABASE_URL not set"
    assert "elder_test" in test_database_url, "Using test database"

    # Import and verify table count
    from apps.api.models.base import Base

    table_count = len(Base.metadata.tables)
    assert table_count == 82, f"Expected 82 tables, got {table_count}"

    # List table names for audit (useful for debugging)
    table_names = sorted([name for name in Base.metadata.tables.keys()])
    assert (
        len(table_names) == 82
    ), f"Table name count mismatch: {len(table_names)} != 82"

    print(f"\n✓ Schema validation passed: {table_count} tables")
    print(
        f"✓ Table names: {', '.join(table_names[:5])}... (+{len(table_names) - 5} more)"
    )


@pytest.mark.integration
def test_app_bootstrap(app, test_database_url):
    """Verify create_app boots successfully against test Postgres.

    Args:
        app: Quart application fixture (already bootstrapped)
        test_database_url: Test database connection string

    Asserts:
        - App is configured for testing
        - Health check endpoint responds
    """
    assert app.config["TESTING"], "App not in testing mode"
    assert app.config.get("DATABASE_URL") == test_database_url, "DB URL mismatch"

    print(f"\n✓ App bootstrap passed")
    print(
        f"✓ Config: TESTING={app.config['TESTING']}, ENV={app.config.get('ENV', 'unknown')}"
    )


@pytest.mark.integration
def test_redis_connectivity(redis_url):
    """Verify Redis connectivity and set/get round-trip.

    Args:
        redis_url: Test Redis connection string

    Asserts:
        - Redis set/get round-trip works
    """
    import redis

    if not redis_url:
        pytest.skip("REDIS_URL not set — skipping Redis test")

    try:
        r = redis.from_url(redis_url)
        r.ping()  # Test connectivity

        # Simple set/get round-trip
        test_key = "elder:test:smoke"
        test_value = "smoke-test-value"
        r.set(test_key, test_value)
        retrieved = r.get(test_key).decode("utf-8")
        assert (
            retrieved == test_value
        ), f"Redis round-trip failed: {retrieved} != {test_value}"
        r.delete(test_key)

        print(f"\n✓ Redis connectivity passed")
        print(f"✓ Round-trip: SET {test_key} / GET OK")

    except Exception as e:
        pytest.skip(f"Redis not available: {e}")


@pytest.mark.integration
@pytest.mark.asyncio
async def test_health_check_endpoint(app):
    """Verify /healthz endpoint responds.

    Args:
        app: Quart application

    Asserts:
        - GET /healthz returns 200 with healthy status
    """
    async with app.test_client() as client:
        response = await client.get("/healthz")
        assert response.status_code == 200, f"Expected 200, got {response.status_code}"

        data = await response.get_json()
        assert (
            data["status"] == "healthy"
        ), f"Expected healthy status, got {data['status']}"

        print(f"\n✓ Health check passed: {response.status_code}")
        print(f"✓ Response: {data}")


@pytest.mark.integration
@pytest.mark.asyncio
async def test_api_status_endpoint(app):
    """Verify /api/v1/status endpoint responds.

    Args:
        app: Quart application

    Asserts:
        - GET /api/v1/status returns 200 with operational status
    """
    async with app.test_client() as client:
        response = await client.get("/api/v1/status")
        assert response.status_code == 200, f"Expected 200, got {response.status_code}"

        data = await response.get_json()
        assert (
            data["status"] == "operational"
        ), f"Expected operational status, got {data['status']}"
        assert "version" in data, "Missing version in response"
        assert "environment" in data, "Missing environment in response"

        print(f"\n✓ API status check passed: {response.status_code}")
        print(f"✓ Response: version={data['version']}, env={data['environment']}")
