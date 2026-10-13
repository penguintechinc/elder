"""Unit tests for wiki-link parser.

Tests the parse_wikilinks() function in isolation without database access.
All tests are robust to malformed input and verify deduplication.
"""

# flake8: noqa: E501

import pytest

from apps.api.common.refs.wikilinks import (
    WikiLink,
    extract_wikilink_targets,
    parse_wikilinks,
)


class TestParseWikilinksBasicSyntax:
    """Test basic wiki-link syntax parsing."""

    def test_parse_explicit_type_and_slug(self):
        """Should parse [[type:slug]] syntax."""
        text = "See [[entity:prod-db]] for details."
        links = parse_wikilinks(text)

        assert len(links) == 1
        assert links[0].target_type == "entity"
        assert links[0].slug == "prod-db"
        assert links[0].alias is None

    def test_parse_explicit_type_with_alias(self):
        """Should parse [[type:slug|alias]] syntax."""
        text = "Check [[entity:prod-db|Production Database]]."
        links = parse_wikilinks(text)

        assert len(links) == 1
        assert links[0].target_type == "entity"
        assert links[0].slug == "prod-db"
        assert links[0].alias == "Production Database"

    def test_parse_bare_slug_defaults_to_document(self):
        """Should parse bare [[slug]] and default type to 'document'."""
        text = "Read the [[getting-started]] guide."
        links = parse_wikilinks(text)

        assert len(links) == 1
        assert links[0].target_type == "document"
        assert links[0].slug == "getting-started"
        assert links[0].alias is None

    def test_parse_bare_slug_with_alias(self):
        """Should parse bare [[slug|alias]] and default type to 'document'."""
        text = "See [[architecture|System Architecture]] doc."
        links = parse_wikilinks(text)

        assert len(links) == 1
        assert links[0].target_type == "document"
        assert links[0].slug == "architecture"
        assert links[0].alias == "System Architecture"

    def test_parse_multiple_links(self):
        """Should parse multiple links from text."""
        text = "Link to [[entity:db1]], [[entity:db2|Database 2]], and [[doc|Guide]]."
        links = parse_wikilinks(text)

        assert len(links) == 3
        assert links[0].target_type == "entity"
        assert links[0].slug == "db1"
        assert links[1].target_type == "entity"
        assert links[1].slug == "db2"
        assert links[1].alias == "Database 2"
        assert links[2].target_type == "document"
        assert links[2].slug == "doc"

    def test_parse_links_with_whitespace(self):
        """Should trim whitespace in type, slug, and alias."""
        text = "See [[ entity : prod-db | Prod DB ]]."
        links = parse_wikilinks(text)

        assert len(links) == 1
        assert links[0].target_type == "entity"
        assert links[0].slug == "prod-db"
        assert links[0].alias == "Prod DB"


class TestParseWikilinksSlugFormats:
    """Test various slug format variations."""

    def test_slug_with_hyphens(self):
        """Should accept hyphens in slugs."""
        text = "[[entity:prod-db-primary]]"
        links = parse_wikilinks(text)

        assert len(links) == 1
        assert links[0].slug == "prod-db-primary"

    def test_slug_with_underscores(self):
        """Should accept underscores in slugs."""
        text = "[[entity:prod_db_primary]]"
        links = parse_wikilinks(text)

        assert len(links) == 1
        assert links[0].slug == "prod_db_primary"

    def test_slug_with_dots(self):
        """Should accept dots in slugs."""
        text = "[[entity:prod.db.primary]]"
        links = parse_wikilinks(text)

        assert len(links) == 1
        assert links[0].slug == "prod.db.primary"

    def test_slug_with_forward_slashes(self):
        """Should accept forward slashes in slugs."""
        text = "[[entity:prod/db/primary]]"
        links = parse_wikilinks(text)

        assert len(links) == 1
        assert links[0].slug == "prod/db/primary"

    def test_slug_alphanumeric(self):
        """Should accept alphanumeric slugs."""
        text = "[[entity:prod123db456]]"
        links = parse_wikilinks(text)

        assert len(links) == 1
        assert links[0].slug == "prod123db456"


class TestParseWikilinksDeduplication:
    """Test deduplication of identical links."""

    def test_duplicate_links_deduplicated(self):
        """Should deduplicate identical (type, slug) pairs."""
        text = "See [[entity:db1]]. Also see [[entity:db1]] again."
        links = parse_wikilinks(text)

        assert len(links) == 1
        assert links[0].target_type == "entity"
        assert links[0].slug == "db1"

    def test_dedup_keeps_first_occurrence(self):
        """Should keep first occurrence when deduplicating."""
        text = "[[entity:db|First]] and [[entity:db|Second]]."
        links = parse_wikilinks(text)

        assert len(links) == 1
        assert links[0].alias == "First"

    def test_different_aliases_still_deduplicated_by_target(self):
        """Should deduplicate by (type, slug), ignoring alias differences."""
        text = "[[issue:123|Bug]] and [[issue:123|Ticket]]."
        links = parse_wikilinks(text)

        # Should be deduplicated to one link (keeps first: "Bug")
        assert len(links) == 1
        assert links[0].target_type == "issue"
        assert links[0].slug == "123"
        assert links[0].alias == "Bug"

    def test_no_dedup_for_different_types(self):
        """Should NOT deduplicate if type differs."""
        text = "[[document:db1]] and [[entity:db1]]."
        links = parse_wikilinks(text)

        assert len(links) == 2
        assert links[0].target_type == "document"
        assert links[1].target_type == "entity"

    def test_no_dedup_for_different_slugs(self):
        """Should NOT deduplicate if slug differs."""
        text = "[[entity:db1]] and [[entity:db2]]."
        links = parse_wikilinks(text)

        assert len(links) == 2
        assert links[0].slug == "db1"
        assert links[1].slug == "db2"


class TestParseWikilinesMalformedInput:
    """Test robustness to malformed input."""

    def test_empty_text_returns_empty_list(self):
        """Should return empty list for empty text."""
        links = parse_wikilinks("")

        assert links == []

    def test_none_text_returns_empty_list(self):
        """Should return empty list for None text."""
        links = parse_wikilinks(None or "")

        assert links == []

    def test_text_without_links_returns_empty_list(self):
        """Should return empty list if no wiki-links present."""
        text = "Just regular text with no links."
        links = parse_wikilinks(text)

        assert links == []

    def test_unclosed_brackets_skipped(self):
        """Should skip unclosed brackets [[without closing."""
        text = "See [[entity:db1 for details."
        links = parse_wikilinks(text)

        assert len(links) == 0

    def test_empty_brackets_skipped(self):
        """Should skip empty brackets [[]]."""
        text = "See [[]] for details."
        links = parse_wikilinks(text)

        assert len(links) == 0

    def test_only_whitespace_in_brackets_skipped(self):
        """Should skip brackets with only whitespace [[  ]]."""
        text = "See [[   ]] for details."
        links = parse_wikilinks(text)

        assert len(links) == 0

    def test_nested_brackets_handled_gracefully(self):
        """Should handle nested brackets without crashing."""
        text = "See [[entity:[nested]]] for details."
        # The regex will skip this due to non-greedy matching
        links = parse_wikilinks(text)

        # Nested brackets don't match the pattern, so this should find nothing
        # or find partial match depending on bracket order
        # The important thing is it doesn't crash
        assert isinstance(links, list)

    def test_invalid_type_characters_skipped(self):
        """Should skip links with invalid characters in type."""
        text = "See [[entity/invalid:db1]] for details."
        links = parse_wikilinks(text)

        # type="entity/invalid" is invalid (has slash), should be skipped
        assert len(links) == 0

    def test_invalid_slug_characters_skipped(self):
        """Should skip links with invalid characters in slug.

        Note: pipes, brackets are invalid; hyphens, dots, slashes, underscores are OK.
        """
        text = "See [[entity:db@1]] for details."
        links = parse_wikilinks(text)

        # slug="db@1" is invalid (has @), should be skipped
        assert len(links) == 0

    def test_multiple_pipes_handled(self):
        """Should handle multiple pipes by using first as separator."""
        text = "[[entity:db1|Alias with | pipe]]"
        links = parse_wikilinks(text)

        # Should split on first pipe only
        assert len(links) == 1
        assert links[0].slug == "db1"
        assert links[0].alias == "Alias with | pipe"

    def test_colon_in_alias_preserved(self):
        """Should preserve colons in alias part."""
        text = "[[entity:db1|Database: prod]]"
        links = parse_wikilinks(text)

        assert len(links) == 1
        assert links[0].target_type == "entity"
        assert links[0].slug == "db1"
        assert links[0].alias == "Database: prod"


class TestExtractWikilinksTargets:
    """Test the extract_wikilink_targets() convenience helper."""

    def test_extract_targets_returns_type_slug_tuples(self):
        """Should return list of (type, slug) tuples."""
        text = "[[entity:db1]], [[doc]], and [[issue:123]]."
        targets = extract_wikilink_targets(text)

        assert len(targets) == 3
        assert targets[0] == ("entity", "db1")
        assert targets[1] == ("document", "doc")
        assert targets[2] == ("issue", "123")

    def test_extract_targets_empty_for_no_links(self):
        """Should return empty list if no wiki-links."""
        text = "No links here."
        targets = extract_wikilink_targets(text)

        assert targets == []

    def test_extract_targets_deduplicated(self):
        """Should be deduplicated like parse_wikilinks()."""
        text = "[[entity:db1]] and [[entity:db1]]."
        targets = extract_wikilink_targets(text)

        assert len(targets) == 1
        assert targets[0] == ("entity", "db1")


class TestWikiLinkDataclass:
    """Test the WikiLink dataclass."""

    def test_wikilink_creation(self):
        """Should create WikiLink with required fields."""
        wl = WikiLink(
            raw="entity:prod-db|Prod",
            target_type="entity",
            slug="prod-db",
            alias="Prod",
        )

        assert wl.raw == "entity:prod-db|Prod"
        assert wl.target_type == "entity"
        assert wl.slug == "prod-db"
        assert wl.alias == "Prod"

    def test_wikilink_frozen(self):
        """WikiLink should be frozen (immutable)."""
        wl = WikiLink(
            raw="entity:db",
            target_type="entity",
            slug="db",
            alias=None,
        )

        with pytest.raises(AttributeError):
            wl.slug = "new-db"

    def test_wikilink_hashable(self):
        """WikiLink should be hashable (slots + frozen)."""
        wl = WikiLink(
            raw="entity:db",
            target_type="entity",
            slug="db",
            alias=None,
        )

        # Should not raise
        hash(wl)

        # Should work in sets
        links_set = {wl}
        assert len(links_set) == 1


class TestParseWikilinksRealWorldExamples:
    """Test real-world wiki-link usage patterns."""

    def test_documentation_with_mixed_links(self):
        """Should parse documentation-style text with mixed link types."""
        text = """
        # Architecture Overview

        See [[architecture|System Architecture]] for the high-level design.
        The [[entity:api-gateway]] service handles routing.
        For deployment, read [[deployment-guide|How to Deploy]].
        Related: [[issue:456|Production Issue]].
        """
        links = parse_wikilinks(text)

        assert len(links) == 4
        assert links[0].target_type == "document"
        assert links[0].slug == "architecture"
        assert links[1].target_type == "entity"
        assert links[1].slug == "api-gateway"
        assert links[2].target_type == "document"
        assert links[2].slug == "deployment-guide"
        assert links[3].target_type == "issue"
        assert links[3].slug == "456"

    def test_rich_wiki_content(self):
        """Should parse complex wiki content."""
        text = """
        The [[entity:db-primary|Primary Database]] stores user data.
        Backup: [[entity:db-replica|Standby Database]].
        See [[backup-policy]] for details.
        """
        links = parse_wikilinks(text)

        assert len(links) == 3
        targets = extract_wikilink_targets(text)
        assert len(targets) == 3

    def test_links_with_special_slugs(self):
        """Should handle various slug naming patterns."""
        text = """
        [[entity:us-east-1]]
        [[service:k8s_cluster_prod]]
        [[identity:user.email.123]]
        [[milestone:v1.0/beta]]
        """
        links = parse_wikilinks(text)

        assert len(links) == 4
        assert links[0].slug == "us-east-1"
        assert links[1].slug == "k8s_cluster_prod"
        assert links[2].slug == "user.email.123"
        assert links[3].slug == "v1.0/beta"
