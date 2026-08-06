"""Shared tenant-scoping helpers for the issues module route handlers.

Every issue sub-resource handler (comments, labels, entity links,
project/milestone links) must resolve its parent issue scoped to the
caller's tenant before touching the sub-resource — otherwise any tenant can
read or mutate another tenant's issue data by guessing its numeric id. This
module centralizes that logic so `issues.py`, `comments.py`, and `labels.py`
share one implementation instead of copy-pasting it.
"""

from typing import Optional

from quart import g


def _tenant_id() -> Optional[int]:
    """Tenant id from validated JWT claims (populated by before_request)."""
    claims = getattr(g, "claims", {}) or {}
    raw = claims.get("tenant", "")
    if not raw:
        return None
    try:
        return int(raw)
    except (ValueError, TypeError):
        return None


def get_tenant_scoped_issue(db, issue_id: int, tenant_id: int):
    """Resolve an issue by id, scoped to tenant_id.

    Returns the row, or None if the issue does not exist or belongs to a
    different tenant. Must be called with a `tenant_id` already resolved via
    `_tenant_id()` in the request coroutine — Quart's `g` does not propagate
    into thread-pool workers, so `tenant_id` cannot be re-derived inside a
    `run_in_threadpool` callable.
    """
    return (
        db((db.issues.id == issue_id) & (db.issues.tenant_id == tenant_id))
        .select()
        .first()
    )
