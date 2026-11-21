"""Village ID lookup endpoint for resolving village_id to resource type and location.

Refactored to use the registry-driven resolver from apps.api.common.refs.registry.
Exposed at BOTH ``GET /api/v1/id/<village_id>`` (used by the WebUI SPA) and
``GET /id/<village_id>`` (backward compatibility).
"""

# flake8: noqa: E501

from quart import Blueprint, current_app, g, jsonify

from apps.api.auth.decorators import login_required
from apps.api.common.refs.registry import (
    format_resolvable_url,
    get_type,
    resolve_by_village_id,
)
from apps.api.utils.async_utils import run_in_threadpool
from apps.api.utils.tenant_scoping import get_current_tenant_id

bp = Blueprint("lookup_village_id", __name__)
legacy_bp = Blueprint("lookup_village_id_legacy", __name__)

# Additional scope aliases accepted per resolved module alongside
# ``{module}:read``, ``infrastructure:read``, ``*:read``, and superuser.
_MODULE_READ_SCOPE_ALIASES: dict[str, tuple[str, ...]] = {
    "identity": ("identities:read", "iam:read", "secrets:read"),
    "tenant": ("tenants:read", "admin:read"),
    "backup": ("backups:read", "webhooks_alerting:read"),
    "sbom": ("services_oncall:read",),
    "webhooks_alerting": ("webhooks:read",),
}


def _caller_can_read_module(module: str) -> bool:
    """Return True if the authenticated caller holds read access for ``module``."""
    user = getattr(g, "current_user", None)
    if user is not None and getattr(user, "is_superuser", False):
        return True

    claims = getattr(g, "claims", {}) or {}
    granted = set(claims.get("scope", []))
    allowed = {
        "*:read",
        "*:*",
        "*:admin",
        "infrastructure:read",
        f"{module}:read",
        f"{module}:write",
        f"{module}:admin",
        *_MODULE_READ_SCOPE_ALIASES.get(module, ()),
    }
    return bool(granted & allowed)


async def _handle_lookup_village_id(village_id: str):
    """Shared handler for ``GET /api/v1/id/<village_id>`` and ``GET /id/<village_id>``.

    regression: gh-237 -- this route previously had no auth at all and
    called the registry resolver WITHOUT a tenant id, which (prior to the
    registry fail-closed fix) fell through to an unscoped query and let any
    unauthenticated caller enumerate another tenant's resources by
    village_id. Requires authentication, resolves strictly within the caller's
    JWT tenant (returning 404 for unknown or cross-tenant IDs), and verifies
    read scope for the resolved resource's module (returning 403 if absent).
    """
    db = current_app.db
    tenant_id = get_current_tenant_id()

    def search():
        """Search registry for the village_id, scoped to the caller's tenant."""
        result = resolve_by_village_id(db, village_id, tenant_id=tenant_id)
        if not result:
            return None

        resolvable = get_type(result["type"])
        if not resolvable:
            return None

        url = format_resolvable_url(db, resolvable, result["id"], tenant_id=tenant_id)
        return {
            "resource_type": result["type"],
            "resource_id": result["id"],
            "title": result.get("title"),
            "module": result.get("module", resolvable.module),
            "redirect_url": url,
        }

    result = await run_in_threadpool(search)

    if not result:
        return (
            jsonify({"error": f"Resource with village_id '{village_id}' not found"}),
            404,
        )

    module = result["module"]
    if not _caller_can_read_module(module):
        return (
            jsonify({"error": f"Missing required scope: '{module}:read'"}),
            403,
        )

    return (
        jsonify(
            {
                "village_id": village_id,
                "resource_type": result["resource_type"],
                "resource_id": result["resource_id"],
                "title": result["title"],
                "module": module,
                "redirect_url": result["redirect_url"],
                "short_link": f"/id/{village_id}",
            }
        ),
        200,
    )


@bp.route("/id/<village_id>", methods=["GET"])
@login_required
async def lookup_village_id(village_id: str):
    """Resolve a village_id at ``GET /api/v1/id/<village_id>``."""
    return await _handle_lookup_village_id(village_id)


@legacy_bp.route("/id/<village_id>", methods=["GET"])
@login_required
async def lookup_village_id_legacy(village_id: str):
    """Resolve a village_id at ``GET /id/<village_id>`` (backward compatibility)."""
    return await _handle_lookup_village_id(village_id)
