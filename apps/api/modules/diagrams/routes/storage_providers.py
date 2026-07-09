"""Diagram storage provider configuration endpoints using penguin-dal.

Storage providers manage external storage connections (S3, MinIO, GCS, OneDrive, GoogleDrive).
"""

# flake8: noqa: E501

import logging
import re
from datetime import datetime, timezone

from quart import Blueprint, current_app, g, jsonify, request

from apps.api.auth.decorators import login_required, require_scope
from apps.api.utils.api_responses import ApiResponse
from apps.api.utils.async_utils import run_in_threadpool
from apps.api.utils.pydal_helpers import PaginationParams

from .diagrams import _get_tenant_id

logger = logging.getLogger(__name__)

storage_bp = Blueprint("diagram_storage", __name__)

# Valid provider types
VALID_PROVIDER_TYPES = {"minio", "s3", "gcs", "onedrive", "googledrive"}

# Required config keys per provider type
PROVIDER_CONFIG_SCHEMA = {
    "s3": ["endpoint", "bucket", "access_key", "secret_key"],
    "minio": ["endpoint", "bucket", "access_key", "secret_key"],
    "gcs": ["bucket", "credentials"],
    "onedrive": ["client_id", "client_secret"],
    "googledrive": ["client_id", "client_secret"],
}


def _redact_secrets(config_dict):
    """Redact secret values in a config dictionary (recursive).

    Redacts any key matching secret-like patterns, and recursively redacts
    nested dicts and lists. Special handling for GCS: entire 'credentials'
    field is treated as opaque.

    Args:
        config_dict: Configuration dictionary to redact

    Returns:
        New dictionary with secrets redacted
    """
    if not isinstance(config_dict, dict):
        return config_dict

    redacted = {}
    secret_pattern = re.compile(
        r"(password|secret|key|token|credential|creds|cert|"
        r"private|passphrase|auth|sas|dsn|conn_str|connection)",
        re.IGNORECASE,
    )

    for k, v in config_dict.items():
        if secret_pattern.search(k):
            # Entire field is secret
            redacted[k] = "***"
        elif isinstance(v, dict):
            # Recurse into nested dicts
            redacted[k] = _redact_secrets(v)
        elif isinstance(v, list):
            # Recurse into list items (if they're dicts)
            redacted[k] = [
                _redact_secrets(item) if isinstance(item, dict) else item for item in v
            ]
        else:
            redacted[k] = v

    return redacted


def _format_provider_response(provider_row, redact=True):
    """Format a storage provider row as JSON response.

    Args:
        provider_row: Database row
        redact: Whether to redact secrets (default: True)

    Returns:
        Dictionary suitable for JSON response
    """
    config = provider_row.config_json or {}
    if redact:
        config = _redact_secrets(config)

    storage_config = provider_row.storage_config or {}
    if redact:
        storage_config = _redact_secrets(storage_config)

    return {
        "id": provider_row.id,
        "name": provider_row.name,
        "provider_type": provider_row.provider_type,
        "config_json": config,
        "storage_config": storage_config,
        "owner_identity_id": provider_row.owner_identity_id,
        "is_active": provider_row.is_active,
        "is_system_default": provider_row.is_system_default,
        "created_at": provider_row.created_at.isoformat(),
        "updated_at": provider_row.updated_at.isoformat(),
    }


@storage_bp.route("", methods=["GET"])
@login_required
@require_scope("diagrams:read")
async def list_providers():
    """List storage providers (tenant-scoped, paginated).

    Returns owner's providers + system default providers.
    Secrets are redacted in responses.

    Query parameters:
        - page: Page number (default: 1)
        - per_page: Items per page (default: 20, max: 100)

    Returns:
        200: Paginated list of storage providers
    """
    db = current_app.db
    tenant_id = _get_tenant_id()

    if not tenant_id:
        return ApiResponse.error("Tenant not found", 403)

    claims = getattr(g, "claims", {}) or {}
    identity_id = claims.get("identity_id")

    pagination = PaginationParams.from_request()

    def list_providers_db():
        # Query: (owner_identity_id == current user) OR (is_system_default == true)
        # Scoped to current tenant
        query = (db.dg_storage_providers.tenant_id == tenant_id) & (
            (db.dg_storage_providers.owner_identity_id == identity_id)
            | (db.dg_storage_providers.is_system_default == True)
        )
        total = db(query).count()
        rows = db(query).select(
            orderby=~db.dg_storage_providers.created_at,
            limitby=(pagination.offset, pagination.offset + pagination.per_page),
        )
        return total, rows

    total, rows = await run_in_threadpool(list_providers_db)

    providers = [_format_provider_response(r, redact=True) for r in rows]

    return (
        jsonify(
            {
                "items": providers,
                "pagination": {
                    "page": pagination.page,
                    "per_page": pagination.per_page,
                    "total": total,
                    "pages": (total + pagination.per_page - 1) // pagination.per_page,
                },
            }
        ),
        200,
    )


@storage_bp.route("", methods=["POST"])
@login_required
@require_scope("diagrams:write")
async def create_provider():
    """Create a new storage provider.

    Requires: diagrams:write scope
    is_system_default: admin/tenant_admin only (silently forced to False for others)

    Request body:
        {
            "name": "string (required)",
            "provider_type": "minio|s3|gcs|onedrive|googledrive (required)",
            "config_json": "object (required) - connection config",
            "storage_config": "object (optional) - storage-specific settings",
            "is_active": bool (default: true),
            "is_system_default": bool (default: false, admin only)
        }

    Returns:
        201: Created provider
    """
    db = current_app.db
    tenant_id = _get_tenant_id()

    if not tenant_id:
        return ApiResponse.error("Tenant not found", 403)

    claims = getattr(g, "claims", {}) or {}
    identity_id = claims.get("identity_id")
    if not identity_id:
        return ApiResponse.error("Identity not found in token", 403)

    data = await request.get_json() or {}

    # Validate required fields
    name = data.get("name", "").strip()
    if not name:
        return ApiResponse.validation_error("name", "is required")

    provider_type = data.get("provider_type", "").lower()
    if provider_type not in VALID_PROVIDER_TYPES:
        return ApiResponse.validation_error(
            "provider_type",
            f"must be one of {', '.join(sorted(VALID_PROVIDER_TYPES))}",
        )

    config_json = data.get("config_json")
    if config_json is None or not isinstance(config_json, dict):
        return ApiResponse.validation_error("config_json", "must be a non-empty object")

    storage_config = data.get("storage_config") or {}
    is_active = data.get("is_active", True)

    # is_system_default: only admin/tenant_admin can set it; silently force to False otherwise
    caller_roles = claims.get("roles") or []
    if "admin" in caller_roles or "tenant_admin" in caller_roles:
        is_system_default = data.get("is_system_default", False)
    else:
        is_system_default = False

    def create_prov():
        now = datetime.now(timezone.utc)
        provider_id = db.dg_storage_providers.insert(
            tenant_id=tenant_id,
            name=name,
            provider_type=provider_type,
            config_json=config_json,
            storage_config=storage_config,
            owner_identity_id=identity_id,
            is_active=is_active,
            is_system_default=is_system_default,
            created_at=now,
            updated_at=now,
        )
        db.commit()
        return db(db.dg_storage_providers.id == provider_id).select().first()

    result = await run_in_threadpool(create_prov)

    if not result:
        return ApiResponse.error("Failed to create provider", 500)

    return jsonify(_format_provider_response(result, redact=True)), 201


@storage_bp.route("/<int:provider_id>", methods=["GET"])
@login_required
@require_scope("diagrams:read")
async def get_provider(provider_id: int):
    """Get a specific storage provider.

    Returns 404 if provider is not owned by caller and not system default.

    Requires: diagrams:read scope

    Returns:
        200: Provider details
    """
    db = current_app.db
    tenant_id = _get_tenant_id()

    if not tenant_id:
        return ApiResponse.error("Tenant not found", 403)

    claims = getattr(g, "claims", {}) or {}
    identity_id = claims.get("identity_id")

    def get_prov():
        provider_row = db(db.dg_storage_providers.id == provider_id).select().first()
        if not provider_row:
            return None, "not_found"

        # Cross-tenant isolation
        if provider_row.tenant_id != tenant_id:
            return None, "forbidden"

        # Owner or system default
        if (
            provider_row.owner_identity_id == identity_id
            or provider_row.is_system_default
        ):
            return provider_row, "ok"

        return None, "forbidden"

    provider_row, status = await run_in_threadpool(get_prov)

    if status == "not_found":
        return ApiResponse.error("Provider not found", 404)
    if status == "forbidden":
        return ApiResponse.error("Forbidden", 403)

    return jsonify(_format_provider_response(provider_row, redact=True)), 200


@storage_bp.route("/<int:provider_id>", methods=["PATCH"])
@login_required
@require_scope("diagrams:write")
async def update_provider(provider_id: int):
    """Update storage provider (owner only).

    Requires: diagrams:write scope
    Authorization: owner only
    is_system_default: admin/tenant_admin only (silently forced to False for others)

    Request body:
        {
            "name": "string (optional)",
            "config_json": "object (optional)",
            "storage_config": "object (optional)",
            "is_active": bool (optional),
            "is_system_default": bool (optional, admin only)
        }

    Returns:
        200: Updated provider
    """
    db = current_app.db
    tenant_id = _get_tenant_id()

    if not tenant_id:
        return ApiResponse.error("Tenant not found", 403)

    claims = getattr(g, "claims", {}) or {}
    identity_id = claims.get("identity_id")
    if not identity_id:
        return ApiResponse.error("Identity not found in token", 403)

    caller_roles = claims.get("roles") or []
    is_admin = "admin" in caller_roles or "tenant_admin" in caller_roles

    data = await request.get_json() or {}

    def update_prov():
        provider_row = db(db.dg_storage_providers.id == provider_id).select().first()
        if not provider_row:
            return None, "not_found"

        # Cross-tenant isolation
        if provider_row.tenant_id != tenant_id:
            return None, "forbidden"

        # Authorization: owner only
        if provider_row.owner_identity_id != identity_id:
            return None, "forbidden"

        # Update fields
        now = datetime.now(timezone.utc)
        update_dict = {"updated_at": now}

        if "name" in data:
            name = data.get("name", "").strip()
            if name:
                update_dict["name"] = name

        if "config_json" in data:
            update_dict["config_json"] = data.get("config_json")

        if "storage_config" in data:
            update_dict["storage_config"] = data.get("storage_config")

        if "is_active" in data:
            update_dict["is_active"] = bool(data.get("is_active"))

        if "is_system_default" in data:
            # Only admin/tenant_admin can set is_system_default; silently ignore for others
            if is_admin:
                update_dict["is_system_default"] = bool(data.get("is_system_default"))

        db(db.dg_storage_providers.id == provider_id).update(**update_dict)
        db.commit()

        return db(db.dg_storage_providers.id == provider_id).select().first(), "ok"

    provider_row, status = await run_in_threadpool(update_prov)

    if status == "not_found":
        return ApiResponse.error("Provider not found", 404)
    if status == "forbidden":
        return ApiResponse.error("Forbidden", 403)

    return jsonify(_format_provider_response(provider_row, redact=True)), 200


@storage_bp.route("/<int:provider_id>", methods=["DELETE"])
@login_required
@require_scope("diagrams:write")
async def delete_provider(provider_id: int):
    """Delete storage provider (owner only).

    Requires: diagrams:write scope
    Authorization: owner only

    Returns:
        204: No content
    """
    db = current_app.db
    tenant_id = _get_tenant_id()

    if not tenant_id:
        return ApiResponse.error("Tenant not found", 403)

    claims = getattr(g, "claims", {}) or {}
    identity_id = claims.get("identity_id")
    if not identity_id:
        return ApiResponse.error("Identity not found in token", 403)

    def delete_prov():
        provider_row = db(db.dg_storage_providers.id == provider_id).select().first()
        if not provider_row:
            return "not_found"

        # Cross-tenant isolation
        if provider_row.tenant_id != tenant_id:
            return "forbidden"

        # Authorization: owner only
        if provider_row.owner_identity_id != identity_id:
            return "forbidden"

        # Delete provider
        db(db.dg_storage_providers.id == provider_id).delete()
        db.commit()

        return "ok"

    status = await run_in_threadpool(delete_prov)

    if status == "not_found":
        return ApiResponse.error("Provider not found", 404)
    if status == "forbidden":
        return ApiResponse.error("Forbidden", 403)

    return "", 204


@storage_bp.route("/<int:provider_id>/test", methods=["POST"])
@login_required
@require_scope("diagrams:write")
async def test_provider(provider_id: int):
    """Test storage provider configuration (owner only).

    Performs lightweight validation only — checks for required config keys
    per provider type. Does NOT attempt network/SDK calls.

    Requires: diagrams:write scope
    Authorization: owner only

    Returns:
        200: {"ok": bool, "missing": [list of missing keys]}
    """
    db = current_app.db
    tenant_id = _get_tenant_id()

    if not tenant_id:
        return ApiResponse.error("Tenant not found", 403)

    claims = getattr(g, "claims", {}) or {}
    identity_id = claims.get("identity_id")
    if not identity_id:
        return ApiResponse.error("Identity not found in token", 403)

    def test_prov():
        provider_row = db(db.dg_storage_providers.id == provider_id).select().first()
        if not provider_row:
            return None, "not_found"

        # Cross-tenant isolation
        if provider_row.tenant_id != tenant_id:
            return None, "forbidden"

        # Authorization: owner only
        if provider_row.owner_identity_id != identity_id:
            return None, "forbidden"

        # Validate config
        provider_type = provider_row.provider_type
        config = provider_row.config_json or {}

        required_keys = PROVIDER_CONFIG_SCHEMA.get(provider_type, [])
        missing = [k for k in required_keys if k not in config]

        return {"ok": len(missing) == 0, "missing": missing}, "ok"

    result, status = await run_in_threadpool(test_prov)

    if status == "not_found":
        return ApiResponse.error("Provider not found", 404)
    if status == "forbidden":
        return ApiResponse.error("Forbidden", 403)

    return jsonify(result), 200
