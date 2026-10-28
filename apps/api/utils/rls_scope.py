"""API helpers for row-level-security scoping (cross-tenant admin access).

Routes that legitimately act on a tenant other than the caller's (a global
admin, or a ``system``-tenant operator) must say so explicitly; PostgreSQL then
sees an audited SUPER_ADMIN system scope instead of a silent bypass.
"""

from collections.abc import AsyncIterator
from contextlib import (
    AbstractAsyncContextManager,
    AbstractContextManager,
    asynccontextmanager,
    nullcontext,
)

from shared.database.rls import SystemScopeReason, system_scope, system_scope_async


@asynccontextmanager
async def _no_scope() -> AsyncIterator[None]:
    """Async no-op scope used when the caller acts on its own tenant."""
    yield None


def cross_tenant_scope(
    caller_tenant_id: int | None,
    target_tenant_id: int,
    actor: str,
    purpose: str,
) -> AbstractContextManager[object]:
    """Return a SUPER_ADMIN system scope when acting on another tenant.

    Same-tenant access needs no elevation and returns a no-op context manager;
    the caller's own tenant binding (set from the validated JWT) already covers
    it. ``actor`` must be a non-PII reference to the acting identity. Use this
    form from sync handlers (they already run off the event loop).
    """
    if caller_tenant_id is not None and caller_tenant_id == target_tenant_id:
        return nullcontext()
    return system_scope(SystemScopeReason.SUPER_ADMIN, purpose, actor=actor)


def cross_tenant_scope_async(
    caller_tenant_id: int | None,
    target_tenant_id: int,
    actor: str,
    purpose: str,
) -> AbstractAsyncContextManager[object]:
    """Async twin of :func:`cross_tenant_scope` for ``async def`` routes.

    The durable audit write for the SUPER_ADMIN scope runs in a worker thread so
    it never blocks the event loop; use as ``async with``.
    """
    if caller_tenant_id is not None and caller_tenant_id == target_tenant_id:
        return _no_scope()
    return system_scope_async(SystemScopeReason.SUPER_ADMIN, purpose, actor=actor)
