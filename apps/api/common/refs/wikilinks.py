"""Wiki-link parser and rebuild-on-save mechanism for cross-reference management.

Parses wiki-link syntax from text and rebuilds references on save.
Supports multiple wiki-link formats and handles unresolvable types gracefully.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


@dataclass(slots=True, frozen=True)
class WikiLink:
    """Parsed wiki-link structure.

    Attributes:
        raw: Original raw text of the wiki-link
        target_type: Resource type being referenced
        slug: Resource slug/identifier
        alias: Optional display alias (if specified with pipe syntax)
    """

    raw: str
    target_type: str
    slug: str
    alias: str | None = None


def parse_wikilinks(text: str) -> list[WikiLink]:
    """Parse wiki-links from text with robust error handling.

    Supports these formats:
    - [[type:slug]] — explicit type with colon
    - [[type:slug|alias]] — explicit type with alias
    - [[slug]] — bare slug (defaults to type="document")
    - [[slug|alias]] — bare slug with alias

    Robust to malformed input:
    - Unclosed brackets are silently skipped
    - Empty brackets are silently skipped
    - Nested brackets within links are silently skipped
    - Invalid characters in type/slug are silently skipped

    Deduplicates identical (target_type, slug) pairs, keeping the first occurrence.

    Args:
        text: Text to parse for wiki-links

    Returns:
        List of WikiLink objects in order of appearance (deduplicated)

    Raises:
        Never — all malformed input is silently skipped
    """
    if not text:
        return []

    # Regex pattern to match [[...]] blocks
    # Non-greedy match to avoid consuming nested brackets
    pattern = r"\[\[([^\[\]]+?)\]\]"
    matches = re.findall(pattern, text)

    links: list[WikiLink] = []
    seen: set[tuple[str, str]] = set()  # (target_type, slug) dedup set

    for raw_content in matches:
        raw_content = raw_content.strip()

        if not raw_content:
            # Empty brackets [[]]
            continue

        try:
            # Split by pipe to separate alias
            parts = raw_content.split("|", 1)
            main_part = parts[0].strip()
            alias = parts[1].strip() if len(parts) > 1 else None

            if not main_part:
                # Empty main part
                continue

            # Split by colon to check for explicit type
            if ":" in main_part:
                type_and_slug = main_part.split(":", 1)
                target_type = type_and_slug[0].strip()
                slug = type_and_slug[1].strip()
            else:
                # Bare slug defaults to type="document"
                target_type = "document"
                slug = main_part

            if not target_type or not slug:
                # Empty type or slug after split
                continue

            # Validate that type and slug contain only safe characters
            # Allow alphanumeric, hyphens, underscores, and dots in type/slug
            if not re.match(r"^[a-zA-Z0-9_-]+$", target_type):
                logger.debug(f"Skipping wiki-link with invalid type: {target_type}")
                continue

            if not re.match(r"^[a-zA-Z0-9_\-\./:]+$", slug):
                logger.debug(f"Skipping wiki-link with invalid slug: {slug}")
                continue

            # Deduplicate by (target_type, slug)
            dedup_key = (target_type, slug)
            if dedup_key in seen:
                continue

            seen.add(dedup_key)

            wl = WikiLink(
                raw=raw_content,
                target_type=target_type,
                slug=slug,
                alias=alias,
            )
            links.append(wl)

        except Exception as e:
            # Catch any unexpected error and skip this link
            logger.debug(f"Failed to parse wiki-link content '{raw_content}': {e}")
            continue

    return links


def extract_wikilink_targets(text: str) -> list[tuple[str, str]]:
    """Extract (target_type, slug) pairs from wiki-links in text.

    Convenience helper for callers needing just the target information.

    Args:
        text: Text to extract targets from

    Returns:
        List of (target_type, slug) tuples in order of appearance
    """
    links = parse_wikilinks(text)
    return [(link.target_type, link.slug) for link in links]


def rebuild_references_from_text(
    db: Any,
    tenant_id: int,
    source_module: str,
    source_type: str,
    source_id: str,
    text: str,
) -> int:
    """Parse wiki-links from text and rebuild references for source.

    Implements rebuild-on-save: deletes all existing outbound references from
    the source, parses the new text for wiki-links, and creates new references
    for each link. Stores references even if the target type is not yet in the
    registry (soft integrity).

    Non-blocking per-link failures: if a single link fails to create (e.g., due
    to a database error), that failure is logged and skipped. The rebuild
    continues for remaining links.

    Deduplicates based on parse_wikilinks() output (prevents duplicate inserts).

    Args:
        db: PyDAL database instance
        tenant_id: Tenant ID for scoping (required)
        source_module: Source resource module
        source_type: Source resource type
        source_id: Source resource ID
        text: Text to parse for wiki-links

    Returns:
        Count of references successfully written
    """
    from apps.api.common.refs.registry import get_type
    from apps.api.common.refs.service import (
        create_reference,
        delete_references_for_source,
    )

    try:
        # Delete all existing outbound references for this source (rebuild-on-save)
        deleted_count = delete_references_for_source(
            db,
            source_module=source_module,
            source_type=source_type,
            source_id=source_id,
        )
        logger.debug(
            f"Deleted {deleted_count} existing references for "
            f"{source_module}:{source_type}:{source_id}"
        )
    except Exception as e:
        logger.error(
            f"Failed to delete references for {source_module}:{source_type}:{source_id}: {e}"
        )
        raise

    # Parse wiki-links from text (deduplicated)
    links = parse_wikilinks(text)

    created_count = 0

    # Create a reference for each parsed link
    for link in links:
        try:
            # Look up the target type in registry to get module
            resolvable = get_type(link.target_type)
            if resolvable:
                target_module = resolvable.module
            else:
                # Type not in registry yet — use target_type as module (soft integrity)
                target_module = link.target_type
                logger.debug(
                    f"Target type '{link.target_type}' not in registry; "
                    f"using as module for reference"
                )

            # Build context with alias if present
            context = None
            if link.alias:
                context = {"alias": link.alias}

            # Create the reference
            ref = create_reference(
                db,
                tenant_id=tenant_id,
                source_module=source_module,
                source_type=source_type,
                source_id=source_id,
                target_module=target_module,
                target_type=link.target_type,
                target_id=link.slug,
                ref_type="link",
                context=context,
            )

            if ref:
                created_count += 1
                logger.debug(
                    f"Created reference: {source_module}:{source_type}:{source_id} "
                    f"→ {target_module}:{link.target_type}:{link.slug}"
                )
            else:
                logger.warning(
                    f"Failed to create reference (insert returned None): "
                    f"{source_module}:{source_type}:{source_id} "
                    f"→ {target_module}:{link.target_type}:{link.slug}"
                )

        except Exception as e:
            # Non-blocking failure: log and continue with next link
            logger.error(
                f"Failed to create reference for wiki-link "
                f"[[{link.target_type}:{link.slug}]] "
                f"from {source_module}:{source_type}:{source_id}: {e}"
            )
            continue

    logger.info(
        f"Rebuilt references for {source_module}:{source_type}:{source_id}: "
        f"created {created_count} references from {len(links)} parsed links"
    )

    return created_count
