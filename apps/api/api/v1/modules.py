"""Module registry endpoint — lists enabled modules and capabilities."""

from __future__ import annotations

import logging
from typing import Any

from quart import Blueprint, current_app, jsonify

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
    enabled_modules = current_app.extensions.get("elder_modules", {})

    modules_list = []
    for manifest in enabled_modules.values():
        # Phase 0: All licensing stubs are true; Phase 2 will add real checks
        # TODO Phase 2: licensed = license_client.has_feature(manifest.license_feature)
        # TODO Phase 2: tenant_enabled = check tenant_modules table
        modules_list.append(
            {
                "name": manifest.name,
                "title": manifest.title,
                "installed": True,
                "licensed": True,  # TODO Phase 2
                "tenant_enabled": True,  # TODO Phase 2
                "effective": True,
                "nav_id": manifest.nav_id,
                "scopes": manifest.scopes,
                "capabilities": {},  # TODO Phase 5: add module-specific capabilities
            }
        )

    return (
        jsonify({"modules": sorted(modules_list, key=lambda m: m["name"])}),
        200,
    )
