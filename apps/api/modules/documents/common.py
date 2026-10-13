"""Shared helpers for documents routes."""

from __future__ import annotations

import logging
import re
from typing import Any

# Canonical bleach-based sanitizer shared with the pages module. Re-exported here
# so existing ``from ...documents.common import sanitize_html`` imports keep working.
from apps.api.common.html_sanitize import sanitize_html  # noqa: F401

# Tenant-scoped identity validation, re-exported from common.identity
from apps.api.common.identity import identity_in_tenant  # noqa: F401

logger = logging.getLogger(__name__)


def visibility_users_in_tenant(
    db: Any, visibility_users: list[int] | None, tenant_id: int
) -> bool:
    """Validate all user IDs in visibility_users belong to tenant_id.

    Used when accepting visibility_users from request body to prevent cross-tenant IDOR.
    Returns True if the list is None/empty or all users belong to the tenant.
    """
    if not visibility_users:
        return True

    for user_id in visibility_users:
        if not identity_in_tenant(db, user_id, tenant_id):
            return False

    return True


def slugify(title: str) -> str:
    """Convert title to URL-safe slug.

    Args:
        title: Article title

    Returns:
        URL-safe slug
    """
    slug = title.lower()
    slug = re.sub(r"[^\w\s-]", "", slug)
    slug = re.sub(r"[-\s]+", "-", slug)
    return slug.strip("-")
