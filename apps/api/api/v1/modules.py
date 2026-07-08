"""Module registry endpoint — lists enabled modules and capabilities."""

from __future__ import annotations

import logging
from typing import Any

from quart import Blueprint, current_app, g, jsonify

from apps.api.auth.decorators import login_required

logger = logging.getLogger(__name__)

bp = Blueprint("modules", __name__)


@bp.route("/modules", methods=["GET"])
@login_required
async def list_modules() -> tuple[dict[str, Any], int]:
    """Get list of available modules with status, licensing, and capabilities.

    Returns:
        200: {
            "modules": [
                {
                    "name": "infrastructure",
                    "title": "Infrastructure CMDB",
                    "installed": true,
                    "licensed": true,
                    "tenant_enabled": true,
                    "effective": true,
                    "nav_id": "nav_infrastructure",
                    "scopes": ["infrastructure:read", "infrastructure:write", ...],
                    "capabilities": {}
                },
                ...
            ]
        }
    """
    from apps.api.common.modules.licensing import module_licensed
    from apps.api.common.modules.tenant_toggle import is_module_enabled
    import redis

    enabled_modules = current_app.extensions.get("elder_modules", {})

    # Get caller's tenant (from g.claims set by populate_claims)
    claims = getattr(g, "claims", {}) or {}
    tenant_str = claims.get("tenant", "")
    tenant_id = None
    if tenant_str:
        try:
            tenant_id = int(tenant_str)
        except (ValueError, TypeError):
            pass

    modules_list = []
    for manifest in enabled_modules.values():
        # Layer 2: Check if module is licensed
        licensed = module_licensed(current_app, manifest)

        # Layer 3: Check if module is enabled for this tenant
        tenant_enabled = True  # Default: enabled if no tenant context
        if tenant_id:
            try:
                redis_client = redis.from_url(
                    current_app.config.get("REDIS_URL", "redis://localhost:6379/0")
                )
                db = current_app.db
                tenant_enabled = is_module_enabled(
                    db, redis_client, tenant_id, manifest.name, manifest.default_enabled
                )
            except Exception as e:
                logger.warning(
                    "tenant_toggle_check_failed_in_list",
                    module=manifest.name,
                    tenant_id=tenant_id,
                    error=str(e),
                    fallback="default",
                )
                tenant_enabled = manifest.default_enabled

        # Effective: module is enabled if both licensed AND tenant_enabled
        effective = licensed and tenant_enabled

        modules_list.append(
            {
                "name": manifest.name,
                "title": manifest.title,
                "installed": True,
                "licensed": licensed,
                "tenant_enabled": tenant_enabled,
                "effective": effective,
                "nav_id": manifest.nav_id,
                "scopes": manifest.scopes,
                "capabilities": {},  # TODO Phase 5: add module-specific capabilities
            }
        )

    return (
        jsonify({"modules": sorted(modules_list, key=lambda m: m["name"])}),
        200,
    )
