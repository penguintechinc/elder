"""API helpers for row-level-security scoping (cross-tenant admin access).

Routes that legitimately act on a tenant other than the caller's (a global
admin, or a ``system``-tenant operator) must say so explicitly; PostgreSQL then
sees an audit-logged system scope instead of a silent bypass.
"""

from contextlib import AbstractContextManager, nullcontext

from shared.database.rls import SystemScopeReason, system_scope


def cross_tenant_scope(
    caller_tenant_id: int | None,
    target_tenant_id: int,
    actor: str,
    purpose: str,
) -> AbstractContextManager[object]:
    """Return a SUPER_ADMIN system scope when acting on another tenant.

    Same-tenant access needs no elevation and returns a no-op context manager;
    the caller's own tenant binding (set from the validated JWT) already
    covers it. ``actor`` must be a non-PII reference to the acting identity.
    """
    if caller_tenant_id is not None and caller_tenant_id == target_tenant_id:
        return nullcontext()
    return system_scope(SystemScopeReason.SUPER_ADMIN, purpose, actor=actor)
