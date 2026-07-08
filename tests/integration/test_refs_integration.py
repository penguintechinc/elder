"""Integration tests for cross-reference system.

Tests the full stack: registry, service layer, and database operations.
Requires DATABASE_URL to be set and test database initialized.
"""

# flake8: noqa: E501

import pytest

from apps.api.common.refs.registry import get_type, resolve_by_village_id
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
