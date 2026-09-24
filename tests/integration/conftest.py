"""Shared fixtures for tests/integration/.

Several integration tests (test_refs_integration.py, test_wikilinks_integration.py)
hardcode ``tenant_id=1`` when exercising the refs/wikilinks service layer directly
(bypassing the API's tenant-scoping middleware). Unlike tests/unit/, which shares
one long-lived session-scoped DB across many test files (so some earlier unit test
usually seeds tenant 1 first), tests/integration/ is invoked as its own pytest run
against a freshly created_all() schema with zero seed data -- so without this
fixture, every insert against tenant_id=1 fails FK validation
(psycopg2.errors.ForeignKeyViolation on tenants_tenant_id_fkey). See gh-276.
"""

import os

import pytest


@pytest.fixture(scope="session", autouse=True)
def _seed_default_tenant(app):
    """Ensure a tenant row with id=1 exists before any integration test runs.

    Explicit id=1 relies on this being the first row ever inserted into
    ``tenants`` in a fresh test database -- true as long as this fixture (session
    scope, autouse) is the first thing pytest sets up. Idempotent: skips the
    insert if id=1 is already present (e.g. a partially-seeded DB).
    """
    if not os.getenv("DATABASE_URL"):
        # Unit-style minimal app with no DB configured; nothing to seed.
        yield
        return

    import asyncio

    async def _seed() -> None:
        async with app.app_context():
            db = app.db
            existing = db(db.tenants.id == 1).select().first()
            if existing is not None:
                return
            tenant_id = db.tenants.insert(
                id=1,
                name="Integration Test Tenant",
                slug="integration-test-tenant",
                is_active=True,
            )
            db.commit()
            assert tenant_id == 1, (
                f"Expected the first tenants row to get id=1, got {tenant_id} -- "
                "some other fixture/test inserted into tenants first."
            )

    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(_seed())
    finally:
        loop.close()

    yield
