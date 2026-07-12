"""Tenant module management API endpoints (admin).

Provides REST endpoints for managing per-tenant module enablement state,
guarded by admin scope and tenant isolation.
"""

# flake8: noqa: E501


from typing import Optional

import redis
import structlog
from penguin_libs.pydantic import RequestModel
from pydantic import Field, ValidationError
from quart import Blueprint, current_app, g, jsonify, request

from apps.api.api.v1.refs import _get_tenant_id
from apps.api.auth.decorators import login_required, require_scope
from apps.api.common.modules.tenant_toggle import (
    get_tenant_modules,
    is_module_enabled,
    set_module_enabled,
)
from apps.api.utils.api_responses import ApiResponse
from apps.api.utils.async_utils import run_in_threadpool

bp = Blueprint("tenant_modules", __name__)
logger = structlog.get_logger(__name__)


# Pydantic Models for request validation
class SetModuleRequest(RequestModel):
    """Validation model for setting module enablement."""

    module_name: str = Field(..., min_length=1, max_length=64)
    enabled: bool
    settings: Optional[dict] = Field(default=None)


class ModuleToggleResponse(RequestModel):
    """Response model for a module toggle state."""

    module_name: str
    enabled: bool
    settings: Optional[dict] = None


@bp.route("/tenants/<int:tenant_id>/modules", methods=["GET"])
@login_required
@require_scope("admin:write")
async def list_tenant_modules(tenant_id: int):
    """List all module enablement states for a specific tenant.

    Requires admin:write scope and tenant isolation (caller must be in their own tenant
    unless they're a super-admin).

    Returns:
        List of {module_name, enabled, settings?} for each module registered in the system.
    """
    # Check tenant isolation
    caller_tenant_id = _get_tenant_id()
    if caller_tenant_id is None:
        return ApiResponse.unauthorized("Invalid or missing tenant claim")

    # Non-superusers can only manage their own tenant
    if not getattr(g.current_user, "is_superuser", False):
        if caller_tenant_id != tenant_id:
            return ApiResponse.forbidden("Cannot manage modules for other tenants")

    try:
        db = current_app.db
        redis_client = redis.from_url(
            current_app.config.get("REDIS_URL", "redis://localhost:6379/0")
        )

        # Get module registry from app extensions
        module_manifests = current_app.extensions.get("elder_modules", {})

        # Build map of all known modules with their defaults
        all_modules_with_defaults = {
            name: manifest.default_enabled
            for name, manifest in module_manifests.items()
        }

        # Get effective map for this tenant
        effective_map = await run_in_threadpool(
            get_tenant_modules, db, redis_client, tenant_id, all_modules_with_defaults
        )

        # Build response: get actual settings from DB for modules that have overrides
        response = []
        rows = await run_in_threadpool(
            lambda: db(db.tenant_modules.tenant_id == tenant_id).select()
        )
        module_settings = {row.module_name: row.settings for row in rows}

        for module_name, enabled in effective_map.items():
            module_response = {
                "module_name": module_name,
                "enabled": enabled,
            }
            if module_name in module_settings and module_settings[module_name]:
                module_response["settings"] = module_settings[module_name]
            response.append(module_response)

        logger.info(
            "list_tenant_modules", tenant_id=tenant_id, module_count=len(response)
        )

        return jsonify({"status": "success", "data": response}), 200

    except Exception as e:
        logger.error("list_tenant_modules_failed", error=str(e))
        return (
            jsonify({"error": "Failed to list tenant modules", "details": str(e)}),
            500,
        )


@bp.route("/tenants/<int:tenant_id>/modules", methods=["PUT"])
@login_required
@require_scope("admin:write")
async def set_tenant_module(tenant_id: int):
    """Set module enablement state for a tenant.

    Request body:
        {
            "module_name": "string (required)",
            "enabled": "boolean (required)",
            "settings": "object (optional)"
        }

    Requires admin:write scope and tenant isolation.

    Returns:
        Updated module state: {module_name, enabled, settings?}
    """
    # Check tenant isolation
    caller_tenant_id = _get_tenant_id()
    if caller_tenant_id is None:
        return ApiResponse.unauthorized("Invalid or missing tenant claim")

    # Non-superusers can only manage their own tenant
    if not getattr(g.current_user, "is_superuser", False):
        if caller_tenant_id != tenant_id:
            return ApiResponse.forbidden("Cannot manage modules for other tenants")

    try:
        # Validate request body
        try:
            req_data = await request.json
            req = SetModuleRequest(**req_data)
        except ValidationError as e:
            return (
                jsonify({"error": "Invalid request", "validation_errors": e.errors()}),
                400,
            )
        except Exception as e:
            return jsonify({"error": "Invalid JSON", "details": str(e)}), 400

        # Only accept toggles for modules that actually exist in the registry.
        # Rejecting unknown names before any store/log prevents storing bogus
        # rows and avoids logging arbitrary user-controlled strings.
        known_modules = current_app.extensions.get("elder_modules", {})
        if req.module_name not in known_modules:
            return jsonify({"error": "unknown_module"}), 400

        db = current_app.db
        redis_client = redis.from_url(
            current_app.config.get("REDIS_URL", "redis://localhost:6379/0")
        )

        # Set the module state
        await run_in_threadpool(
            set_module_enabled,
            db,
            redis_client,
            tenant_id,
            req.module_name,
            req.enabled,
            req.settings,
        )

        logger.info(
            "set_tenant_module",
            tenant_id=tenant_id,
            module_name=req.module_name,
            enabled=req.enabled,
        )

        return (
            jsonify(
                {
                    "status": "success",
                    "data": {
                        "module_name": req.module_name,
                        "enabled": req.enabled,
                        "settings": req.settings,
                    },
                }
            ),
            200,
        )

    except Exception as e:
        logger.error("set_tenant_module_failed", error=str(e))
        return (
            jsonify({"error": "Failed to set tenant module", "details": str(e)}),
            500,
        )
