"""Unit tests for per-tenant/global license limit counters (apps/api/common/licensing/counters.py)."""

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from quart import current_app

from apps.api.common.licensing.counters import (
    count_global_admins,
    count_objects,
    count_teams,
    count_tenant_admins,
    count_tenants,
    count_users,
)


def _make_tenant(db, prefix: str = "lic-test") -> int:
    """Create a fresh, uniquely-slugged (never the default/system) tenant."""
    now = datetime.now(UTC)
    return db.tenants.insert(
        name=f"License Test {prefix}",
        slug=f"{prefix}-{uuid4().hex[:8]}",
        is_active=True,
        created_at=now,
        updated_at=now,
    )


def _make_identity(db, tenant_id: int, **overrides) -> int:
    now = datetime.now(UTC)
    fields = {
        "tenant_id": tenant_id,
        "username": f"user-{uuid4().hex[:8]}",
        "identity_type": "human",
        "auth_provider": "local",
        "is_active": True,
        "is_superuser": False,
        "mfa_enabled": False,
        "must_change_password": False,
        "portal_role": "observer",
        "created_at": now,
        "updated_at": now,
    }
    fields.update(overrides)
    return db.identities.insert(**fields)


def _make_portal_user(db, tenant_id: int, **overrides) -> int:
    now = datetime.now(UTC)
    fields = {
        "tenant_id": tenant_id,
        "email": f"portal-{uuid4().hex[:8]}@example.com",
        "is_active": True,
        "email_verified": True,
        "created_at": now,
        "updated_at": now,
    }
    fields.update(overrides)
    return db.portal_users.insert(**fields)


@pytest.mark.asyncio
class TestCountUsers:
    """count_users sums identities + portal_users for a tenant."""

    async def test_sums_identities_and_portal_users(self, app):
        async with app.app_context():
            db = current_app.db
            tenant_id = _make_tenant(db)
            _make_identity(db, tenant_id)
            _make_portal_user(db, tenant_id)
            db.commit()

            assert count_users(db, tenant_id) == 2

    async def test_scoped_to_tenant(self, app):
        async with app.app_context():
            db = current_app.db
            tenant_a = _make_tenant(db)
            tenant_b = _make_tenant(db)
            _make_identity(db, tenant_a)
            _make_identity(db, tenant_b)
            db.commit()

            assert count_users(db, tenant_a) == 1


@pytest.mark.asyncio
class TestCountGlobalAdmins:
    """count_global_admins: Identity.is_superuser OR PortalUser.global_role admin/superadmin."""

    async def test_superuser_identity_counts(self, app):
        async with app.app_context():
            db = current_app.db
            tenant_id = _make_tenant(db)
            _make_identity(db, tenant_id, is_superuser=True)
            db.commit()

            assert count_global_admins(db, tenant_id) == 1

    async def test_non_admin_identity_does_not_count(self, app):
        async with app.app_context():
            db = current_app.db
            tenant_id = _make_tenant(db)
            _make_identity(db, tenant_id)
            db.commit()

            assert count_global_admins(db, tenant_id) == 0

    async def test_portal_user_global_role_admin_counts(self, app):
        async with app.app_context():
            db = current_app.db
            tenant_id = _make_tenant(db)
            _make_portal_user(db, tenant_id, global_role="admin")
            _make_portal_user(db, tenant_id, global_role="superadmin")
            _make_portal_user(db, tenant_id, global_role=None)
            db.commit()

            assert count_global_admins(db, tenant_id) == 2

    async def test_identity_portal_role_admin_on_default_tenant_counts_as_global(
        self, app
    ):
        """On the deployment's default/system tenant, portal_role=='admin' is
        a *global* admin (the single-tenant case -- Free/Professional).
        Asserted as a delta since the default tenant is shared across the
        whole test session and may already carry admin rows from other
        tests/fixtures.
        """
        async with app.app_context():
            db = current_app.db
            default_tenant = (
                db(db.tenants.slug == "system").select().first()
                or db(db.tenants.slug == "default").select().first()
            )
            assert default_tenant, "default tenant bootstrap must have run"
            tenant_id = default_tenant.id

            before = count_global_admins(db, tenant_id)
            _make_identity(db, tenant_id, portal_role="admin")
            db.commit()

            assert count_global_admins(db, tenant_id) == before + 1


@pytest.mark.asyncio
class TestCountTenantAdmins:
    """count_tenant_admins: PortalUser.tenant_role=='admin' + Identity.portal_role=='admin' on a non-default tenant."""

    async def test_portal_user_tenant_role_admin_counts(self, app):
        async with app.app_context():
            db = current_app.db
            tenant_id = _make_tenant(db)
            _make_portal_user(db, tenant_id, tenant_role="admin")
            _make_portal_user(db, tenant_id, tenant_role="member")
            db.commit()

            assert count_tenant_admins(db, tenant_id) == 1

    async def test_identity_portal_role_admin_on_non_default_tenant_counts(self, app):
        async with app.app_context():
            db = current_app.db
            # _make_tenant always mints a fresh, non-default tenant.
            tenant_id = _make_tenant(db)
            _make_identity(db, tenant_id, portal_role="admin")
            db.commit()

            assert count_tenant_admins(db, tenant_id) == 1

    async def test_superuser_identity_is_not_a_tenant_admin(self, app):
        """A superuser is a global admin (see TestCountGlobalAdmins), never a
        tenant admin, even if its portal_role also happens to be 'admin'.
        """
        async with app.app_context():
            db = current_app.db
            tenant_id = _make_tenant(db)
            _make_identity(db, tenant_id, is_superuser=True, portal_role="admin")
            db.commit()

            assert count_tenant_admins(db, tenant_id) == 0
            assert count_global_admins(db, tenant_id) == 1


@pytest.mark.asyncio
class TestCountTeams:
    """count_teams counts organizations of type='team' for a tenant."""

    async def test_counts_only_team_type(self, app):
        async with app.app_context():
            db = current_app.db
            tenant_id = _make_tenant(db)
            now = datetime.now(UTC)
            db.organizations.insert(
                name=f"Team A {uuid4().hex[:8]}",
                tenant_id=tenant_id,
                type="team",
                created_at=now,
                updated_at=now,
            )
            db.organizations.insert(
                name=f"Team B {uuid4().hex[:8]}",
                tenant_id=tenant_id,
                type="team",
                created_at=now,
                updated_at=now,
            )
            db.organizations.insert(
                name=f"Not a team {uuid4().hex[:8]}",
                tenant_id=tenant_id,
                type="department",
                created_at=now,
                updated_at=now,
            )
            db.commit()

            assert count_teams(db, tenant_id) == 2


@pytest.mark.asyncio
class TestCountTenants:
    """count_tenants counts all tenants globally (not tenant-scoped)."""

    async def test_counts_new_tenants(self, app):
        async with app.app_context():
            db = current_app.db
            before = count_tenants(db)
            _make_tenant(db)
            _make_tenant(db)
            db.commit()

            assert count_tenants(db) == before + 2


@pytest.mark.asyncio
class TestCountObjects:
    """count_objects sums tenant-scoped rows across the village_id object tables."""

    async def test_sums_across_tables(self, app):
        async with app.app_context():
            db = current_app.db
            tenant_id = _make_tenant(db)
            now = datetime.now(UTC)

            org_id = db.organizations.insert(
                name=f"Org {uuid4().hex[:8]}",
                tenant_id=tenant_id,
                created_at=now,
                updated_at=now,
            )
            db.issues.insert(
                title="Issue 1",
                description="Test",
                status="OPEN",
                priority="MEDIUM",
                issue_type="OTHER",
                reporter_id=None,
                assignee_id=None,
                resource_type="organization",
                resource_id=org_id,
                is_incident=0,
                tenant_id=tenant_id,
                created_at=now,
                updated_at=now,
            )
            db.commit()

            # 1 organization + 1 issue == 2, on a brand-new tenant with no
            # other objects.
            assert count_objects(db, tenant_id, None) == 2

    async def test_does_not_count_other_tenants(self, app):
        async with app.app_context():
            db = current_app.db
            tenant_a = _make_tenant(db)
            tenant_b = _make_tenant(db)
            now = datetime.now(UTC)
            db.organizations.insert(
                name=f"Org {uuid4().hex[:8]}",
                tenant_id=tenant_b,
                created_at=now,
                updated_at=now,
            )
            db.commit()

            assert count_objects(db, tenant_a, None) == 0

    async def test_caches_result_in_redis(self, app):
        class _FakeRedisCache:
            """Minimal in-memory get/setex double."""

            def __init__(self):
                self.store: dict[str, bytes] = {}

            def get(self, key):
                return self.store.get(key)

            def setex(self, key, ttl, value):
                self.store[key] = str(value).encode()

        async with app.app_context():
            db = current_app.db
            tenant_id = _make_tenant(db)
            now = datetime.now(UTC)
            db.organizations.insert(
                name=f"Org {uuid4().hex[:8]}",
                tenant_id=tenant_id,
                created_at=now,
                updated_at=now,
            )
            db.commit()

            redis_client = _FakeRedisCache()
            first = count_objects(db, tenant_id, redis_client)
            assert first == 1
            assert f"elder:objcount:{tenant_id:08x}" in redis_client.store

            # A second object is added, but the cached value must be served
            # unchanged until TTL expiry -- proves the cache is actually read.
            db.organizations.insert(
                name=f"Org 2 {uuid4().hex[:8]}",
                tenant_id=tenant_id,
                created_at=now,
                updated_at=now,
            )
            db.commit()

            second = count_objects(db, tenant_id, redis_client)
            assert second == first
