"""Shared helpers for documents routes."""

from __future__ import annotations

import logging
import re
from typing import Any

# Canonical bleach-based sanitizer shared with the pages module. Re-exported here
# so existing ``from ...documents.common import sanitize_html`` imports keep working.
from apps.api.common.html_sanitize import sanitize_html  # noqa: F401

logger = logging.getLogger(__name__)


def identity_in_tenant(db: Any, identity_id: int | None, tenant_id: int) -> bool:
    """Return True if identity_id is unset or belongs to tenant_id.

    Guards against cross-tenant IDOR whenever an identity_id is accepted from a
    request body (document visibility_users, ...).
    A None id is treated as valid (the reference is simply absent).
    """
    if identity_id is None:
        return True
    return (
        db((db.identities.id == identity_id) & (db.identities.tenant_id == tenant_id))
        .select()
        .first()
        is not None
    )


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
