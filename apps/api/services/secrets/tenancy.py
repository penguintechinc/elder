"""Tenant scoping helpers shared by the secrets, keys and certificates layers.

Secret providers, secrets, built-in secrets, key providers and crypto keys
carry no ``tenant_id`` column of their own; they are owned by an organization
whose ``tenant_id`` is the boundary. These helpers resolve that boundary from
a tenant id that the caller MUST take from the verified JWT claim — never from
the request body, query string or path.
"""

from typing import Any


class TenantScopeError(PermissionError):
    """Raised when a tenant-scoped operation is attempted without a tenant.

    Callers map this to HTTP 403. It exists so the service layer fails closed
    instead of silently running an unscoped query.
    """


class TenantResourceNotFound(ValueError):
    """A provider, secret or organization is missing or owned by another tenant.

    Both cases raise the same error so callers can map it to a 404 without
    revealing whether the id exists in a different tenant.
    """


def require_tenant(tenant_id: int | None) -> int:
    """Return ``tenant_id`` or raise ``TenantScopeError`` when it is missing.

    Zero, ``None`` and empty values are all treated as "no tenant".
    """
    if not tenant_id:
        raise TenantScopeError("tenant context required")
    return int(tenant_id)


def tenant_org_ids(db: Any, tenant_id: int | None) -> list[int]:
    """List the ids of every organization owned by ``tenant_id``.

    Raises ``TenantScopeError`` when no tenant is given (fail closed).
    """
    tid = require_tenant(tenant_id)
    rows = db(db.organizations.tenant_id == tid).select(db.organizations.id)
    return [int(r.id) for r in rows]


def org_in_tenant(db: Any, organization_id: Any, tenant_id: int | None) -> bool:
    """Return True when ``organization_id`` belongs to ``tenant_id``.

    Non-integer or missing organization ids return False; a missing tenant
    raises ``TenantScopeError``.
    """
    tid = require_tenant(tenant_id)
    if isinstance(organization_id, bool):
        return False
    try:
        oid = int(organization_id)
    except (TypeError, ValueError):
        return False
    if oid <= 0:
        return False
    row = (
        db((db.organizations.id == oid) & (db.organizations.tenant_id == tid))
        .select(db.organizations.id)
        .first()
    )
    return row is not None
