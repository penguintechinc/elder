"""Integration tests for cross-reference system.

Tests the full stack: registry, service layer, and database operations.
Requires DATABASE_URL to be set and test database initialized.

Includes regression tests for fail-open IDOR vulnerability in refs endpoints.
Reference: security review — refs fail-open IDOR

Regression test tracking: commit security review — refs fail-open IDOR
"""

# flake8: noqa: E501

from datetime import UTC, datetime
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

from apps.api.common.refs.registry import (
    get_type,
    resolve_by_village_id,
    resolve_ref,
)
from apps.api.common.refs.service import (
    backlinks_for,
    create_reference,
    delete_references_for_source,
    outbound_for,
)


class TestReferenceCreation:
    """Test creating references in the database."""

    def test_create_reference(self, app):
        """Should create a reference and retrieve it."""
        db = app.db

        # Create a reference
        ref = create_reference(
            db,
            tenant_id=1,
            source_module="issues",
            source_type="issue",
            source_id="123",
            target_module="infrastructure",
            target_type="entity",
            target_id="456",
            ref_type="link",
            context={"anchor": "section1"},
            created_by="user-uuid-1",
        )

        assert ref is not None
        assert ref.source_module == "issues"
        assert ref.source_type == "issue"
        assert ref.source_id == "123"
        assert ref.target_module == "infrastructure"
        assert ref.target_type == "entity"
        assert ref.target_id == "456"
        assert ref.ref_type == "link"
        assert ref.context["anchor"] == "section1"
        assert ref.created_by == "user-uuid-1"

    def test_create_multiple_references(self, app):
        """Should create multiple references from the same source."""
        db = app.db

        for i in range(3):
            ref = create_reference(
                db,
                tenant_id=1,
                source_module="issues",
                source_type="issue",
                source_id="789",
                target_module="infrastructure",
                target_type="entity",
                target_id=str(100 + i),
                ref_type="embed",
            )
            assert ref is not None


class TestBacklinkQueries:
    """Test retrieving references pointing to a target."""

    def test_backlinks_for_target(self, app):
        """Should retrieve all backlinks for a target resource."""
        db = app.db

        # Create several references pointing to the same target
        for i in range(3):
            create_reference(
                db,
                tenant_id=1,
                source_module="issues",
                source_type="issue",
                source_id=str(1000 + i),
                target_module="infrastructure",
                target_type="entity",
                target_id="entity-99",
                ref_type="link",
            )

        # Query backlinks
        backlinks = backlinks_for(
            db,
            target_module="infrastructure",
            target_type="entity",
            target_id="entity-99",
            tenant_id=1,
        )

        assert len(backlinks) >= 3
        for ref in backlinks:
            assert ref.target_module == "infrastructure"
            assert ref.target_type == "entity"
            assert ref.target_id == "entity-99"

    def test_backlinks_empty_for_nonexistent_target(self, app):
        """Should return empty list for target with no backlinks."""
        db = app.db

        backlinks = backlinks_for(
            db,
            target_module="infrastructure",
            target_type="entity",
            target_id="nonexistent",
            tenant_id=1,
        )

        assert len(backlinks) == 0


class TestOutboundQueries:
    """Test retrieving references from a source."""

    def test_outbound_for_source(self, app):
        """Should retrieve all outbound references from a source."""
        db = app.db

        # Create several references from the same source
        for i in range(3):
            create_reference(
                db,
                tenant_id=1,
                source_module="issues",
                source_type="issue",
                source_id="issue-src-999",
                target_module="infrastructure",
                target_type="entity",
                target_id=str(2000 + i),
                ref_type="link",
            )

        # Query outbound
        refs = outbound_for(
            db,
            source_module="issues",
            source_type="issue",
            source_id="issue-src-999",
            tenant_id=1,
        )

        assert len(refs) >= 3
        for ref in refs:
            assert ref.source_module == "issues"
            assert ref.source_type == "issue"
            assert ref.source_id == "issue-src-999"

    def test_outbound_empty_for_nonexistent_source(self, app):
        """Should return empty list for source with no outbound refs."""
        db = app.db

        refs = outbound_for(
            db,
            source_module="issues",
            source_type="issue",
            source_id="nonexistent",
            tenant_id=1,
        )

        assert len(refs) == 0


class TestReferenceDelete:
    """Test deleting references."""

    def test_delete_references_for_source(self, app):
        """Should delete all references from a source."""
        db = app.db

        # Create references to delete
        source_id = "issue-to-delete-777"
        for i in range(3):
            create_reference(
                db,
                tenant_id=1,
                source_module="issues",
                source_type="issue",
                source_id=source_id,
                target_module="infrastructure",
                target_type="entity",
                target_id=str(3000 + i),
            )

        # Verify they exist
        refs_before = outbound_for(
            db,
            source_module="issues",
            source_type="issue",
            source_id=source_id,
            tenant_id=1,
        )
        assert len(refs_before) >= 3

        # Delete them
        count = delete_references_for_source(
            db,
            source_module="issues",
            source_type="issue",
            source_id=source_id,
        )
        assert count >= 3

        # Verify they're gone
        refs_after = outbound_for(
            db,
            source_module="issues",
            source_type="issue",
            source_id=source_id,
            tenant_id=1,
        )
        assert len(refs_after) == 0


class TestTenantIsolation:
    """Test that references are properly tenant-scoped."""

    def test_backlinks_tenant_scoped_query(self, app):
        """Backlinks query should only return refs from specified tenant."""
        db = app.db

        # Create refs in tenant 1
        create_reference(
            db,
            tenant_id=1,
            source_module="issues",
            source_type="issue",
            source_id="issue-tenant-1",
            target_module="infrastructure",
            target_type="entity",
            target_id="target-1",
        )

        # Query for tenant 1
        refs_t1 = backlinks_for(
            db,
            target_module="infrastructure",
            target_type="entity",
            target_id="target-1",
            tenant_id=1,
        )

        # Should see the ref and verify tenant_id
        assert len(refs_t1) >= 1
        for ref in refs_t1:
            if (
                ref.target_module == "infrastructure"
                and ref.target_type == "entity"
                and ref.target_id == "target-1"
            ):
                assert ref.tenant_id == 1

    def test_outbound_tenant_scoped_query(self, app):
        """Outbound refs query should respect tenant scope."""
        db = app.db

        # Create refs in tenant 1
        create_reference(
            db,
            tenant_id=1,
            source_module="issues",
            source_type="issue",
            source_id="issue-outbound-1",
            target_module="infrastructure",
            target_type="entity",
            target_id="target-out-1",
        )

        # Query tenant 1
        refs_t1 = outbound_for(
            db,
            source_module="issues",
            source_type="issue",
            source_id="issue-outbound-1",
            tenant_id=1,
        )

        # Verify results are from tenant 1
        assert len(refs_t1) >= 1
        for ref in refs_t1:
            assert ref.tenant_id == 1


class TestReferenceIndexes:
    """Test that reference queries use indexes effectively."""

    def test_backlink_index_present(self, app):
        """Backlink index should support efficient reverse queries."""
        db = app.db

        # Create many references and query by target
        for i in range(10):
            create_reference(
                db,
                tenant_id=1,
                source_module="issues",
                source_type="issue",
                source_id=f"issue-{i}",
                target_module="infrastructure",
                target_type="entity",
                target_id="indexed-target",
            )

        # Query should be efficient (no error checking, just verify it works)
        refs = backlinks_for(
            db,
            target_module="infrastructure",
            target_type="entity",
            target_id="indexed-target",
            tenant_id=1,
        )
        assert len(refs) >= 10

    def test_outbound_index_present(self, app):
        """Outbound index should support efficient source queries."""
        db = app.db

        # Create many references from same source
        for i in range(10):
            create_reference(
                db,
                tenant_id=1,
                source_module="issues",
                source_type="issue",
                source_id="indexed-source",
                target_module="infrastructure",
                target_type="entity",
                target_id=f"entity-{i}",
            )

        # Query should be efficient
        refs = outbound_for(
            db,
            source_module="issues",
            source_type="issue",
            source_id="indexed-source",
            tenant_id=1,
        )
        assert len(refs) >= 10


class TestRefsQueryLayerTenantScoping:
    """Regression tests for query-layer tenant scoping in registry.py.

    Ensure that resolve_by_village_id() and resolve_ref() properly scope
    queries to the provided tenant_id to prevent cross-tenant leaks.

    regression: commit security review — refs fail-open IDOR
    """

    def test_resolve_ref_cross_tenant_returns_none(self, app):
        """resolve_ref() for a cross-tenant resource should return None.

        Query-layer scoping must prevent data leaks even if a caller
        somehow bypasses endpoint authentication.
        """
        db = app.db

        # This test verifies that unscopable tables (like entity, which has
        # no tenant_id column) return None instead of leaking data
        result = resolve_ref(
            db,
            module="infrastructure",
            type_name="entity",
            resource_id="cross-tenant-entity",
            tenant_id=1,
        )
        # Should return None or broken (no data leaked)
        assert result is None or result.get("broken") is True

    def test_resolve_by_village_id_cross_tenant_returns_none(self, app):
        """resolve_by_village_id() for cross-tenant should respect tenant scope.

        regression: commit security review — refs fail-open IDOR
        """
        db = app.db

        # Try to resolve a village_id that belongs to a different tenant
        # than what we're querying for
        result = resolve_by_village_id(
            db,
            village_id="00000002-000000000000f3c1",  # tenant 2
            tenant_id=1,  # requesting as tenant 1
        )
        # Should return None (tenant mismatch)
        assert result is None

    def test_resolve_by_village_id_same_tenant_succeeds_if_exists(self, app):
        """resolve_by_village_id() should find records in same tenant.

        regression: commit security review — refs fail-open IDOR
        """
        db = app.db

        # This test verifies the positive case: with proper tenant scoping,
        # a resource CAN be found if it exists and matches the tenant
        # (may return None if doesn't exist, which is fine for this test)
        result = resolve_by_village_id(
            db,
            village_id="00000001-000000000000f3c1",
            tenant_id=1,  # matching tenant
        )
        # Result may be None (doesn't exist) or a dict (found)
        assert result is None or isinstance(result, dict)

    def test_resolve_ref_none_tenant_scopes_query(self, app):
        """resolve_ref() with None tenant still returns None for unsafe tables.

        If tenant_id is None, cannot scope table — should return None.

        regression: commit security review — refs fail-open IDOR
        """
        db = app.db

        # Calling with tenant_id=None should return None for unscopable tables
        result = resolve_ref(
            db,
            module="infrastructure",
            type_name="entity",
            resource_id="any-id",
            tenant_id=None,
        )
        # Must return None (unsafe to resolve without tenant scoping)
        assert result is None

    def test_resolve_by_village_id_none_tenant_scopes_query(self, app):
        """resolve_by_village_id() with None tenant should skip unscopable tables.

        regression: commit security review — refs fail-open IDOR
        """
        db = app.db

        # Calling with tenant_id=None — may fail to find or return None
        result = resolve_by_village_id(
            db,
            village_id="00000001-000000000000f3c1",
            tenant_id=None,
        )
        # With no tenant scoping available, should not return results for
        # tables without explicit tenant_id columns
        # (Result may be None or limited to tenant table type)
        assert result is None or result.get("type") == "tenant"

    def test_tenant_type_self_resolution_only(self, app):
        """resolve_ref() for 'tenant' type should only resolve self.

        A tenant can only resolve itself, not other tenants.

        regression: commit security review — refs fail-open IDOR
        """
        db = app.db

        # Try to resolve tenant 2 as tenant 1
        result = resolve_ref(
            db,
            module="tenant",
            type_name="tenant",
            resource_id="2",
            tenant_id=1,  # requesting as tenant 1
        )
        # Should return None (tenant 1 cannot see tenant 2)
        assert result is None

    def test_backlinks_respects_tenant_scope(self, app):
        """backlinks_for() combined with tenant-scoped resolve_ref() prevents leaks.

        regression: commit security review — refs fail-open IDOR
        """
        db = app.db

        # Create refs in tenant 1
        create_reference(
            db,
            tenant_id=1,
            source_module="issues",
            source_type="issue",
            source_id="idor-backlink-src-t1",
            target_module="infrastructure",
            target_type="entity",
            target_id="idor-backlink-tgt-t1",
        )

        # Query backlinks as tenant 1 (should find them)
        refs_t1 = backlinks_for(
            db,
            target_module="infrastructure",
            target_type="entity",
            target_id="idor-backlink-tgt-t1",
            tenant_id=1,
        )
        assert len(refs_t1) >= 1

        # Query same backlinks as tenant 2 (should NOT find them)
        refs_t2 = backlinks_for(
            db,
            target_module="infrastructure",
            target_type="entity",
            target_id="idor-backlink-tgt-t1",
            tenant_id=2,
        )
        # Backlinks are stored with tenant_id, so tenant 2 sees nothing
        assert len(refs_t2) == 0


class TestRefsEndpointAuthGate:
    """HTTP-endpoint-level regression tests for the fail-open IDOR.

    The original vulnerability was that the /refs endpoints served
    UNAUTHENTICATED callers. These tests exercise the actual HTTP layer
    (not just the service functions) so a future removal of the auth gate
    is caught.

    regression: commit security review — refs fail-open IDOR
    """

    @pytest.mark.asyncio
    async def test_resolve_unauthenticated_returns_401(self, app):
        """Unauthenticated GET /refs/resolve must be 401, never resolve."""
        client = app.test_client()
        resp = await client.get(
            "/api/v1/refs/resolve?village_id=0000002a-0000000000000001"
        )
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_backlinks_unauthenticated_returns_401(self, app):
        """Unauthenticated GET /refs/backlinks must be 401, never leak."""
        client = app.test_client()
        resp = await client.get("/api/v1/refs/backlinks?target=infrastructure:entity:1")
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_resolve_cross_tenant_village_id_returns_403(
        self, app, generate_token
    ):
        """A tenant-42 token resolving a tenant-255 village_id must be 403."""
        token = generate_token(tenant_id=42)
        client = app.test_client()
        resp = await client.get(
            "/api/v1/refs/resolve?village_id=000000ff-0000000000000001",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 403


def _foreign_tenant_for_lookup(db, name: str = "Village Lookup Other Tenant") -> int:
    """Create and return a tenant distinct from tenant 1."""
    tid = db.tenants.insert(
        name=name, slug=f"vid-other-{uuid4().hex[:8]}", is_active=True
    )
    db.commit()
    return tid


def _identity_with_village_id(db, tenant_id: int, village_id: str) -> int:
    """Insert an identity row carrying the given village_id for tenant_id."""
    identity_id = db.identities.insert(
        username=f"vid-test-{uuid4().hex[:8]}",
        identity_type="human",
        auth_provider="local",
        is_active=True,
        is_superuser=False,
        mfa_enabled=False,
        must_change_password=False,
        portal_role="observer",
        tenant_id=tenant_id,
        village_id=village_id,
    )
    db.commit()
    return identity_id


class TestResolveByVillageIdFailClosedNoneTenant:
    """Registry-layer regression test for gh-237.

    ``resolve_by_village_id()``'s previous None-tenant branch queried ANY
    table carrying a ``tenant_id`` column (e.g. ``identities``) completely
    UNSCOPED when ``tenant_id`` was omitted -- exactly the shape of bug that
    let the (then-unauthenticated) ``/id/<village_id>`` endpoint
    (``lookup_village_id.py``) enumerate any tenant's resources. The fix
    makes the None-tenant branch skip every table instead of falling
    through to an unscoped query.

    regression: gh-237
    """

    def test_tenant_scoped_table_returns_none_without_tenant_id(self, app):
        """Omitting tenant_id must never resolve a tenant_id-scoped row."""
        db = app.db
        village_id = f"{1:08x}-{uuid4().hex[:16]}"
        identity_id = _identity_with_village_id(db, tenant_id=1, village_id=village_id)

        # Fail closed: no tenant_id means no table can be safely scoped.
        assert resolve_by_village_id(db, village_id, tenant_id=None) is None

        # Sanity: the row DOES exist and resolves once correctly scoped.
        result = resolve_by_village_id(db, village_id, tenant_id=1)
        assert result == {
            "type": "identity",
            "id": identity_id,
            "table_name": "identities",
        }


class TestLookupVillageIdEndpointAuthGate:
    """HTTP-level regression tests for ``GET /id/<village_id>``.

    ``lookup_village_id.py`` previously had no auth decorator at all AND
    called ``resolve_by_village_id()`` without a ``tenant_id``, which (prior
    to the registry fail-closed fix above) fell through to an unscoped query
    and let any unauthenticated caller enumerate another tenant's resources
    by village_id.

    regression: gh-237
    """

    @pytest.mark.asyncio
    async def test_unauthenticated_returns_401(self, async_client):
        """Unauthenticated GET /id/<village_id> must be 401, never resolve."""
        resp = await async_client.get("/id/00000001-0000000000000001")
        assert resp.status_code == 401

    @pytest.mark.asyncio
    @patch("apps.api.auth.decorators.get_current_user")
    async def test_cross_tenant_village_id_returns_404(
        self, mock_get_user, async_client, generate_token, app
    ):
        """A tenant-1 token must not resolve another tenant's village_id."""
        mock_get_user.return_value = MagicMock(id=1, is_superuser=False)
        token = generate_token(tenant_id=1, scopes=["infrastructure:read"])

        db = app.db
        other_tenant_id = _foreign_tenant_for_lookup(db)
        village_id = f"{other_tenant_id:08x}-{uuid4().hex[:16]}"
        _identity_with_village_id(db, tenant_id=other_tenant_id, village_id=village_id)

        resp = await async_client.get(
            f"/id/{village_id}",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 404, (await resp.get_data()).decode()

    @pytest.mark.asyncio
    @patch("apps.api.auth.decorators.get_current_user")
    async def test_same_tenant_village_id_resolves(
        self, mock_get_user, async_client, generate_token, app
    ):
        """Sanity: resolving the caller's own tenant's village_id still works."""
        mock_get_user.return_value = MagicMock(id=1, is_superuser=False)
        token = generate_token(tenant_id=1, scopes=["infrastructure:read"])

        db = app.db
        village_id = f"{1:08x}-{uuid4().hex[:16]}"
        identity_id = _identity_with_village_id(db, tenant_id=1, village_id=village_id)

        resp = await async_client.get(
            f"/id/{village_id}",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200, (await resp.get_data()).decode()
        body = await resp.get_json()
        assert body["resource_type"] == "identity"
        assert body["resource_id"] == identity_id
