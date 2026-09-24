"""Tenant-scoped resource lookup helpers.

Centralizes the pattern used to prevent cross-tenant IDOR (gh-237): resolving
a row by primary key must always be filtered by the caller's JWT tenant
claim, never a bare ``table[id]`` bracket lookup — that pattern lets any
authenticated caller read another tenant's row by guessing its numeric id.

Modules should call ``get_tenant_scoped()`` instead of hand-rolling the
tenant filter, and the ``scripts/check_tenant_scoping.py`` regression gate
(wired into ``make lint``) fails CI if a new unscoped ``db.<table>[<id>]``
lookup is introduced in a module route file. This module only covers new
call sites going forward — the existing ~339-site sweep is tracked
separately (gh-237) and is out of scope here.
"""

# flake8: noqa: E501

from typing import Any

from quart import g


def get_current_tenant_id() -> int | None:
    """Resolve the caller's tenant id from validated JWT claims.

    Reads ``g.claims["tenant"]``, populated by the ``populate_claims``
    before_request hook in ``apps/api/main.py`` from the verified JWT
    payload — never trust a tenant id supplied via request body/query/path.
    Returns None if no tenant claim is present; callers MUST treat that as
    "no access" (never as "skip the filter").
    """
    claims = getattr(g, "claims", {}) or {}
    raw = claims.get("tenant", "")
    if not raw:
        return None
    try:
        return int(raw)
    except (ValueError, TypeError):
        return None


def get_tenant_scoped(
    db: Any,
    table: Any,
    record_id: int,
    tenant_id: int | None,
    *,
    org_fk: str | None = None,
    parent_table: Any | None = None,
) -> Any | None:
    """Resolve a row by primary key, scoped to the caller's tenant.

    Two scoping modes:

    - **Direct** (default): ``table`` carries its own ``tenant_id`` column —
      filters ``table.id == record_id AND table.tenant_id == tenant_id``.
    - **Joined**: pass ``org_fk`` (e.g. ``"organization_id"``) for tables
      that scope tenancy indirectly through a parent table's
      ``tenant_id`` (e.g. ``entities``, which has no ``tenant_id`` column
      of its own) — resolves the row, then verifies its referenced parent
      belongs to ``tenant_id`` before returning it. ``parent_table``
      defaults to ``db.organizations`` (the original gh-237 shape); pass an
      explicit table (e.g. ``db.on_call_rotations``) for tables scoped
      through a different tenant-owned parent (e.g.
      ``on_call_rotation_participants.rotation_id`` -> ``on_call_rotations.tenant_id``).

    Returns ``None`` (never raises) when ``tenant_id`` or ``record_id`` is
    falsy, the row doesn't exist, or it belongs to a different tenant.
    Callers MUST map ``None`` to a 404, never a 403 — a 403 would let a
    caller distinguish "exists in another tenant" from "doesn't exist",
    i.e. enumerate other tenants' ids.

    Must be called with ``tenant_id`` already resolved via
    ``get_current_tenant_id()`` in the request coroutine and passed in —
    Quart's ``g`` does not propagate into thread-pool workers, so
    ``tenant_id`` cannot be re-derived inside a ``run_in_threadpool``
    callable.
    """
    if not tenant_id or not record_id:
        return None

    if org_fk is None:
        query = (table.id == record_id) & (table.tenant_id == tenant_id)
        return db(query).select().first()

    row = table[record_id]
    if not row:
        return None

    parent_id = getattr(row, org_fk, None)
    if not parent_id:
        return None

    parent = parent_table if parent_table is not None else db.organizations
    owning_parent = (
        db((parent.id == parent_id) & (parent.tenant_id == tenant_id)).select().first()
    )
    if not owning_parent:
        return None

    return row
