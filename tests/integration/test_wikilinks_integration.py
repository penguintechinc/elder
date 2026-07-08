"""Integration tests for wiki-link parsing and rebuild-on-save mechanism.

Tests rebuild_references_from_text() with real Postgres database.
Requires DATABASE_URL to be set and test database initialized.
"""

# flake8: noqa: E501

import pytest

from apps.api.common.refs.service import outbound_for
from apps.api.common.refs.wikilinks import rebuild_references_from_text


class TestRebuildReferencesFromText:
    """Test rebuild_references_from_text() function."""

    def test_rebuild_creates_references_from_wikilinks(self, app):
        """Should parse wiki-links and create references."""
        db = app.db

        text = "See [[entity:db1]] and [[issue:123]]."
        count = rebuild_references_from_text(
            db,
            tenant_id=1,
            source_module="issues",
            source_type="issue",
            source_id="issue-wiki-001",
            text=text,
        )

        assert count == 2

        # Verify references were created
        refs = outbound_for(
            db,
            source_module="issues",
            source_type="issue",
            source_id="issue-wiki-001",
            tenant_id=1,
        )
        assert len(refs) >= 2

    def test_rebuild_with_bare_links_defaults_to_document(self, app):
        """Should default bare links to type='document'."""
        db = app.db

        text = "Read [[my-guide]] for details."
        count = rebuild_references_from_text(
            db,
            tenant_id=1,
            source_module="issues",
            source_type="issue",
            source_id="issue-bare-links",
            text=text,
        )

        assert count == 1

        refs = outbound_for(
            db,
            source_module="issues",
            source_type="issue",
            source_id="issue-bare-links",
            tenant_id=1,
        )
        assert len(refs) >= 1

        # Verify the bare link was created as document type
        ref = next(
            (
                r
                for r in refs
                if r.target_type == "document" and r.target_id == "my-guide"
            ),
            None,
        )
        assert ref is not None
        assert (
            ref.target_module == "document"
        )  # Should use type as module when not in registry

    def test_rebuild_stores_alias_in_context(self, app):
        """Should store alias in reference context."""
        db = app.db

        text = "[[entity:prod-db|Production Database]]"
        count = rebuild_references_from_text(
            db,
            tenant_id=1,
            source_module="issues",
            source_type="issue",
            source_id="issue-with-alias",
            text=text,
        )

        assert count == 1

        refs = outbound_for(
            db,
            source_module="issues",
            source_type="issue",
            source_id="issue-with-alias",
            tenant_id=1,
        )
        assert len(refs) >= 1

        ref = next(
            (r for r in refs if r.target_type == "entity" and r.target_id == "prod-db"),
            None,
        )
        assert ref is not None
        assert ref.context is not None
        assert ref.context.get("alias") == "Production Database"

    def test_rebuild_deduplicates_identical_links(self, app):
        """Should deduplicate identical wiki-links before creating references."""
        db = app.db

        # Text with duplicate link
        text = "See [[entity:db1]] and [[entity:db1]] again."
        count = rebuild_references_from_text(
            db,
            tenant_id=1,
            source_module="issues",
            source_type="issue",
            source_id="issue-dedup",
            text=text,
        )

        # Should create only 1 reference despite 2 identical links in text
        assert count == 1

        refs = outbound_for(
            db,
            source_module="issues",
            source_type="issue",
            source_id="issue-dedup",
            tenant_id=1,
        )
        # Should have exactly 1 ref to this target
        entity_refs = [
            r for r in refs if r.target_type == "entity" and r.target_id == "db1"
        ]
        assert len(entity_refs) == 1

    def test_rebuild_replaces_existing_references(self, app):
        """Should delete old references and replace with new ones."""
        db = app.db

        source_id = "issue-replace"

        # First rebuild: create refs to db1 and db2
        text1 = "[[entity:db1]] and [[entity:db2]]"
        count1 = rebuild_references_from_text(
            db,
            tenant_id=1,
            source_module="issues",
            source_type="issue",
            source_id=source_id,
            text=text1,
        )
        assert count1 == 2

        refs1 = outbound_for(
            db,
            source_module="issues",
            source_type="issue",
            source_id=source_id,
            tenant_id=1,
        )
        assert len(refs1) >= 2

        # Second rebuild: create refs to db3 only (db1 and db2 should be deleted)
        text2 = "[[entity:db3]]"
        count2 = rebuild_references_from_text(
            db,
            tenant_id=1,
            source_module="issues",
            source_type="issue",
            source_id=source_id,
            text=text2,
        )
        assert count2 == 1

        refs2 = outbound_for(
            db,
            source_module="issues",
            source_type="issue",
            source_id=source_id,
            tenant_id=1,
        )
        # Should only have the new reference
        assert len(refs2) == 1
        assert refs2[0].target_id == "db3"

    def test_rebuild_empty_text_clears_references(self, app):
        """Should delete all references when text is empty."""
        db = app.db

        source_id = "issue-clear"

        # First: create refs
        text1 = "[[entity:db1]]"
        rebuild_references_from_text(
            db,
            tenant_id=1,
            source_module="issues",
            source_type="issue",
            source_id=source_id,
            text=text1,
        )

        # Verify refs exist
        refs1 = outbound_for(
            db,
            source_module="issues",
            source_type="issue",
            source_id=source_id,
            tenant_id=1,
        )
        assert len(refs1) >= 1

        # Second: rebuild with empty text
        count2 = rebuild_references_from_text(
            db,
            tenant_id=1,
            source_module="issues",
            source_type="issue",
            source_id=source_id,
            text="",
        )
        assert count2 == 0

        # Verify refs are gone
        refs2 = outbound_for(
            db,
            source_module="issues",
            source_type="issue",
            source_id=source_id,
            tenant_id=1,
        )
        assert len(refs2) == 0

    def test_rebuild_handles_unknown_target_types(self, app):
        """Should store references even for types not in registry (soft integrity)."""
        db = app.db

        # Use a type that's NOT in the registry
        text = "[[custom_type:custom_id]]"
        count = rebuild_references_from_text(
            db,
            tenant_id=1,
            source_module="issues",
            source_type="issue",
            source_id="issue-unknown-type",
            text=text,
        )

        # Should still create the reference
        assert count == 1

        refs = outbound_for(
            db,
            source_module="issues",
            source_type="issue",
            source_id="issue-unknown-type",
            tenant_id=1,
        )
        assert len(refs) >= 1

        ref = next(
            (r for r in refs if r.target_type == "custom_type"),
            None,
        )
        assert ref is not None
        # target_module should be set to the type name (soft integrity)
        assert ref.target_module == "custom_type"

    def test_rebuild_tenant_scoped(self, app):
        """Should create references scoped to the specified tenant."""
        db = app.db

        text = "[[entity:db1]]"
        count = rebuild_references_from_text(
            db,
            tenant_id=1,
            source_module="issues",
            source_type="issue",
            source_id="issue-tenant-1",
            text=text,
        )
        assert count == 1

        # Query as tenant 1 — should find it
        refs_t1 = outbound_for(
            db,
            source_module="issues",
            source_type="issue",
            source_id="issue-tenant-1",
            tenant_id=1,
        )
        assert len(refs_t1) >= 1
        assert refs_t1[0].tenant_id == 1

        # Query as tenant 2 — should NOT find it
        refs_t2 = outbound_for(
            db,
            source_module="issues",
            source_type="issue",
            source_id="issue-tenant-1",
            tenant_id=2,
        )
        assert len(refs_t2) == 0

    def test_rebuild_malformed_input_skipped_gracefully(self, app):
        """Should skip malformed wiki-links and continue."""
        db = app.db

        # Text with mix of valid and malformed links
        text = """
        Valid: [[entity:db1]]
        Malformed: [[]]
        Also valid: [[document:guide]]
        Bad type: [[entity/bad:db2]]
        """
        count = rebuild_references_from_text(
            db,
            tenant_id=1,
            source_module="issues",
            source_type="issue",
            source_id="issue-malformed",
            text=text,
        )

        # Should create 2 valid references (db1 and guide), skip malformed ones
        assert count == 2

        refs = outbound_for(
            db,
            source_module="issues",
            source_type="issue",
            source_id="issue-malformed",
            tenant_id=1,
        )
        assert len(refs) == 2

    def test_rebuild_different_source_types_independent(self, app):
        """Should keep references independent across different source types."""
        db = app.db

        text = "[[entity:db1]]"

        # Create refs from an issue
        count1 = rebuild_references_from_text(
            db,
            tenant_id=1,
            source_module="issues",
            source_type="issue",
            source_id="issue-1",
            text=text,
        )
        assert count1 == 1

        # Create refs from a document (different source type)
        count2 = rebuild_references_from_text(
            db,
            tenant_id=1,
            source_module="documents",
            source_type="document",
            source_id="doc-1",
            text=text,
        )
        assert count2 == 1

        # Query each source separately — should have exactly 1 ref each
        refs_issue = outbound_for(
            db,
            source_module="issues",
            source_type="issue",
            source_id="issue-1",
            tenant_id=1,
        )
        assert len(refs_issue) == 1

        refs_doc = outbound_for(
            db,
            source_module="documents",
            source_type="document",
            source_id="doc-1",
            tenant_id=1,
        )
        assert len(refs_doc) == 1

    def test_rebuild_complex_real_world_scenario(self, app):
        """Should handle real-world wiki content with multiple link types."""
        db = app.db

        text = """
        # Release Notes

        This release includes updates to:
        - [[entity:api-gateway|API Gateway]] (see [[issue:1001]])
        - [[entity:database|Database]] layer
        - Documentation: [[deployment-guide|How to Deploy]]

        Related issues: [[issue:999]] and [[issue:1002]].
        Previous releases: [[release-notes-v1|Release v1]].
        """

        count = rebuild_references_from_text(
            db,
            tenant_id=1,
            source_module="issues",
            source_type="release",
            source_id="release-v2",
            text=text,
        )

        # Count: api-gateway, database, issue:1001, deployment-guide, issue:999, issue:1002, release-notes-v1 = 7
        assert count == 7

        refs = outbound_for(
            db,
            source_module="issues",
            source_type="release",
            source_id="release-v2",
            tenant_id=1,
        )
        assert len(refs) == 7

        # Verify various reference types
        entity_refs = [r for r in refs if r.target_type == "entity"]
        issue_refs = [r for r in refs if r.target_type == "issue"]
        doc_refs = [r for r in refs if r.target_type == "document"]

        assert len(entity_refs) == 2
        assert len(issue_refs) == 3
        assert len(doc_refs) == 2

        # Verify aliases are stored
        api_gw_ref = next(
            (r for r in entity_refs if r.target_id == "api-gateway"), None
        )
        assert api_gw_ref is not None
        assert api_gw_ref.context is not None
        assert api_gw_ref.context.get("alias") == "API Gateway"

    def test_rebuild_returns_zero_for_no_links(self, app):
        """Should return 0 when text has no wiki-links."""
        db = app.db

        text = "Just regular text with no wiki-links at all."
        count = rebuild_references_from_text(
            db,
            tenant_id=1,
            source_module="issues",
            source_type="issue",
            source_id="issue-no-links",
            text=text,
        )

        assert count == 0

        refs = outbound_for(
            db,
            source_module="issues",
            source_type="issue",
            source_id="issue-no-links",
            tenant_id=1,
        )
        assert len(refs) == 0
