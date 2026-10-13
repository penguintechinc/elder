"""@enforce_limit guard: hard-block (402) or observe-only (WARN log) scale limits.

Enforcement is gated behind the `elder.license-enforcement` PostHog flag so
this framework can ship counting-and-logging in production before any
create path is ever actually blocked -- see
docs/superpowers/plans/2026-08-20-license-enforcement-phase2.md.
"""

from __future__ import annotations

import os
from collections.abc import Awaitable, Callable
from functools import wraps
from typing import Any, ParamSpec, TypeVar

import structlog
from quart import current_app, g

from apps.api.common.flags.posthog_client import flag_enabled
from apps.api.common.licensing.counters import (
    count_global_admins,
    count_nodes,
    count_objects,
    count_teams,
    count_tenant_admins,
    count_tenants,
)
from apps.api.common.licensing.limits import resolve_limits
from apps.api.utils.api_responses import ApiResponse
from apps.api.utils.async_utils import run_in_threadpool

logger = structlog.get_logger()

FLAG_KEY = "elder.license-enforcement"

# kind -> LimitSet field name that bounds it (see limits.py's Global
# Constraints table).
_LIMIT_FIELD: dict[str, str] = {
    "tenant": "max_tenants",
    "global_admin": "max_global_admins",
    "tenant_admin": "max_tenant_admins",
    "team": "max_teams",
    "object": "max_objects",
    "node": "max_nodes_per_type",
}

P = ParamSpec("P")
T = TypeVar("T")


def _count_for_kind(kind: str, tenant_id: int | None) -> int:
    """Dispatch to the Task 3 counter matching `kind`.

    Synchronous by design (mirrors the rest of apps/api/common/licensing) --
    call via `run_in_threadpool` from async code, never directly in a
    coroutine. Reads `current_app.db` / `current_app.extensions`, so it must
    run with an active Quart app context.

    Args:
        kind: One of _LIMIT_FIELD's keys.
        tenant_id: Tenant to scope the count to (ignored for "tenant" and
            "node", which are global/per-service-type rather than
            per-tenant -- see counters.py).

    Returns:
        int: The current count for this kind.
    """
    db = current_app.db
    if kind == "tenant":
        return count_tenants(db)
    if kind == "global_admin":
        return count_global_admins(db, tenant_id)
    if kind == "tenant_admin":
        return count_tenant_admins(db, tenant_id)
    if kind == "team":
        return count_teams(db, tenant_id)
    if kind == "object":
        redis_client = current_app.extensions.get("module_redis")
        return count_objects(db, tenant_id, redis_client)
    if kind == "node":
        service_type = os.getenv("ELDER_SERVICE_TYPE", "main")
        return count_nodes(db, service_type)
    raise ValueError(f"unknown license limit kind: {kind!r}")


async def check_limit(kind: str, tenant_id: int | None) -> tuple[Any, int] | None:
    """Check `kind` against its tier limit; hard-block or observe-only per the flag.

    Args:
        kind: One of "tenant", "global_admin", "tenant_admin", "team",
            "object", "node".
        tenant_id: Tenant to scope the count to (also used as the PostHog
            flag's distinct_id; falls back to "global" when None -- e.g. the
            "tenant" and "node" kinds, which aren't scoped to a caller's own
            tenant).

    Returns:
        None if under the limit, if the limit is unlimited (None/∞), or if
        the `elder.license-enforcement` flag is OFF for this tenant
        (observe-only -- logs `license_limit_would_block` at WARN and always
        allows). Returns a `(response, 402)` tuple -- ready to return
        directly from a Quart route -- only when at/over the limit AND the
        flag is ON.

    Raises:
        ValueError: If `kind` is not a recognized limit kind.
    """
    if kind not in _LIMIT_FIELD:
        raise ValueError(f"unknown license limit kind: {kind!r}")

    license_client = current_app.extensions.get("license_client")
    tier, limits = resolve_limits(license_client)
    limit = getattr(limits, _LIMIT_FIELD[kind])

    if limit is None:
        return None  # unlimited (∞) -- never blocks, no need to even count

    count = await run_in_threadpool(_count_for_kind, kind, tenant_id)
    if count < limit:
        return None  # under the limit

    distinct_id = str(tenant_id) if tenant_id is not None else "global"
    enforced = flag_enabled(FLAG_KEY, distinct_id, default=False)

    if not enforced:
        logger.warning(
            "license_limit_would_block",
            kind=kind,
            tenant_id=tenant_id,
            count=count,
            limit=limit,
            tier=tier,
        )
        return None

    logger.warning(
        "license_limit_blocked",
        kind=kind,
        tenant_id=tenant_id,
        count=count,
        limit=limit,
        tier=tier,
    )
    return ApiResponse.error("limit_reached", 402, limit=kind, tier=tier, upgrade=True)


def enforce_limit(
    kind: str,
) -> Callable[[Callable[P, Awaitable[T]]], Callable[P, Awaitable[T | tuple[Any, int]]]]:
    """Route decorator: runs check_limit(kind, tenant) before the handler.

    Derives tenant_id from `g.claims["tenant"]` (populated by
    `populate_claims` in apps/api/main.py). Short-circuits with the 402
    tuple from check_limit when over limit and the flag is ON; otherwise
    calls through to the wrapped handler unchanged.

    Args:
        kind: One of check_limit's supported kinds.

    Returns:
        A decorator for async Quart route handlers.
    """

    def decorator(
        func: Callable[P, Awaitable[T]],
    ) -> Callable[P, Awaitable[T | tuple[Any, int]]]:
        @wraps(func)
        async def wrapper(*args: P.args, **kwargs: P.kwargs) -> T | tuple[Any, int]:
            claims = getattr(g, "claims", {}) or {}
            tenant_str = claims.get("tenant", "")
            try:
                tenant_id = int(tenant_str) if tenant_str else None
            except (TypeError, ValueError):
                tenant_id = None

            blocked = await check_limit(kind, tenant_id)
            if blocked is not None:
                return blocked
            return await func(*args, **kwargs)

        return wrapper

    return decorator
