"""Fail-closed guard for gRPC data RPCs under row-level security.

The Elder gRPC servicer authenticates nothing on its data RPCs and its tokens
carry no ``tenant`` claim, so no tenant is ever bound for them. Once PostgreSQL
RLS is genuinely enforced those RPCs would silently return zero rows / reject
writes. This guard turns that into an explicit ``UNAUTHENTICATED`` error with a
log line and an OTel counter. It deliberately invents no authentication: while
RLS is not enforced (today's deployments) it is a pass-through.
"""

from collections.abc import Callable
from functools import wraps
from typing import Any

import grpc
import structlog

from shared.database import rls

logger = structlog.get_logger(__name__)

# Data RPCs that read/write RLS-protected tables (organizations, dependencies,
# and the entity/graph RPCs that resolve tenants through organizations).
RLS_GUARDED_RPCS: tuple[str, ...] = (
    "ListOrganizations",
    "GetOrganization",
    "CreateOrganization",
    "UpdateOrganization",
    "DeleteOrganization",
    "GetOrganizationChildren",
    "GetOrganizationHierarchy",
    "ListEntities",
    "GetEntity",
    "CreateEntity",
    "UpdateEntity",
    "DeleteEntity",
    "GetEntityDependencies",
    "BatchCreateEntities",
    "ListDependencies",
    "GetDependency",
    "CreateDependency",
    "UpdateDependency",
    "DeleteDependency",
    "BulkCreateDependencies",
    "BulkDeleteDependencies",
    "GetDependencyGraph",
    "AnalyzeGraph",
    "FindPath",
    "GetEntityImpact",
)


def requires_tenant_context(func: Callable[..., Any]) -> Callable[..., Any]:
    """Abort an RPC with UNAUTHENTICATED when RLS is enforced but no tenant is bound.

    Logs ``grpc_rpc_without_tenant_context`` and counts it so the gap is visible
    instead of a silent empty result. Pass-through when RLS is not enforced or a
    tenant/system scope is already bound.
    """

    @wraps(func)
    def wrapper(self: Any, request: Any, context: Any) -> Any:
        """Check for a bound tenant before delegating to the wrapped RPC."""
        if (
            rls.enforcement_active()
            and rls.current_tenant_id() is None
            and rls.current_system_scope() is None
        ):
            logger.warning("grpc_rpc_without_tenant_context", rpc=func.__name__)
            rls.count_metric(
                "elder.rls.grpc_rejected",
                "gRPC data RPCs rejected because RLS is enforced and no tenant is bound",
                {"rpc": func.__name__},
            )
            context.abort(
                grpc.StatusCode.UNAUTHENTICATED,
                "tenant context required: gRPC data RPCs are not authenticated "
                "and cannot run under tenant row-level security",
            )
        return func(self, request, context)

    return wrapper


def guard_servicer(servicer_cls: type) -> type:
    """Wrap every RPC named in ``RLS_GUARDED_RPCS`` on ``servicer_cls`` in place.

    Raises AttributeError if a listed RPC does not exist, so a rename cannot
    silently drop a guard.
    """
    for name in RLS_GUARDED_RPCS:
        setattr(
            servicer_cls, name, requires_tenant_context(getattr(servicer_cls, name))
        )
    return servicer_cls
