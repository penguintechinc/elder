"""Flows credentials API endpoints — Secure Git provider token management.

SECURITY GATE: access_token is stored in plaintext (field encryption not implemented).
- LIST/GET/DELETE: normal operations, token never returned
- CREATE/UPDATE-token: return 501 gate (pending compliance module #28)
"""

import logging
from datetime import datetime, timezone

from quart import Blueprint, current_app, request

from apps.api.auth.decorators import login_required, require_scope
from apps.api.utils.api_responses import ApiResponse
from apps.api.utils.async_utils import run_in_threadpool

logger = logging.getLogger(__name__)

bp = Blueprint("flows_credentials", __name__)


def _get_tenant_id() -> int:
    """Extract tenant_id from g.claims."""
    from quart import g

    claims = getattr(g, "claims", {}) or {}
    tenant_str = claims.get("tenant", "")
    if not tenant_str:
        return None
    try:
        return int(tenant_str)
    except (ValueError, TypeError):
        return None


def _serialize_credential(cred):
    """Serialize credential without exposing access_token."""
    return {
        "id": cred.id,
        "credential_id": cred.credential_id,
        "name": cred.name,
        "description": cred.description or "",
        "provider": cred.provider,
        "token_type": cred.token_type or "personal",
        "scopes": cred.scopes or [],
        "expires_at": cred.expires_at.isoformat() if cred.expires_at else None,
        "is_active": cred.is_active or True,
        "last_used_at": cred.last_used_at.isoformat() if cred.last_used_at else None,
        "created_at": cred.created_at.isoformat() if cred.created_at else None,
        "updated_at": cred.updated_at.isoformat() if cred.updated_at else None,
    }


@bp.route("", methods=["GET"])
@login_required
@require_scope("flows:read")
async def list_credentials():
    """List all credentials for current tenant."""
    tenant_id = _get_tenant_id()

    if not tenant_id:
        return ApiResponse.error("Tenant not found", 403)

    # Capture db in request context BEFORE threadpool
    db = current_app.db

    # Extract filters BEFORE threadpool
    provider = request.args.get("provider")
    is_active = request.args.get("is_active")

    def list_creds():
        # Build query (tenant-scoped)
        query = db.iceflows_credentials.tenant_id == tenant_id

        if provider:
            query &= db.iceflows_credentials.provider == provider
        if is_active is not None:
            query &= db.iceflows_credentials.is_active == (is_active.lower() == "true")

        # Execute query
        credentials = db(query).select(orderby=~db.iceflows_credentials.created_at)

        result = [_serialize_credential(cred) for cred in credentials]

        return {
            "data": result,
            "total": len(result),
        }

    data = await run_in_threadpool(list_creds)
    return ApiResponse.success(data)


@bp.route("", methods=["POST"])
@login_required
@require_scope("flows:write")
async def create_credential():
    """Create a new credential.

    SECURITY GATE (#28): access_token storage requires field encryption (not yet implemented).
    Returns 501 Unavailable.
    """
    # SECURITY GATE (#28): field encryption not yet implemented
    return ApiResponse.error(
        "Credential storage requires field encryption (pending compliance module #171)",
        501,
    )


@bp.route("/<credential_id>", methods=["GET"])
@login_required
@require_scope("flows:read")
async def get_credential(credential_id: str):
    """Get credential details (token never returned)."""
    tenant_id = _get_tenant_id()

    if not tenant_id:
        return ApiResponse.error("Tenant not found", 403)

    # Capture db in request context BEFORE threadpool
    db = current_app.db

    def get():
        cred = (
            db(
                (db.iceflows_credentials.credential_id == credential_id)
                & (db.iceflows_credentials.tenant_id == tenant_id)
            )
            .select()
            .first()
        )

        if not cred:
            return None, 404

        return _serialize_credential(cred), 200

    result = await run_in_threadpool(get)

    if result[1] == 404:
        return ApiResponse.error("Not found", 404)

    return result


@bp.route("/<credential_id>", methods=["PUT"])
@login_required
@require_scope("flows:write")
async def update_credential(credential_id: str):
    """Update credential configuration.

    SECURITY GATE (#28): Updating access_token requires field encryption.
    Returns 501 Unavailable.
    """
    data = await request.get_json()
    if not data:
        data = {}

    # Check if trying to update access_token
    if "access_token" in data:
        # SECURITY GATE (#28): field encryption not yet implemented
        return ApiResponse.error(
            "Credential storage requires field encryption (pending compliance module #171)",
            501,
        )

    tenant_id = _get_tenant_id()

    if not tenant_id:
        return ApiResponse.error("Tenant not found", 403)

    # Capture db in request context BEFORE threadpool
    db = current_app.db

    def update():
        cred = (
            db(
                (db.iceflows_credentials.credential_id == credential_id)
                & (db.iceflows_credentials.tenant_id == tenant_id)
            )
            .select()
            .first()
        )

        if not cred:
            return None, 404

        # Build update dict (never update access_token)
        update_data = {"updated_at": datetime.now(timezone.utc)}

        if "name" in data:
            update_data["name"] = data["name"]
        if "description" in data:
            update_data["description"] = data["description"]
        if "token_type" in data:
            update_data["token_type"] = data["token_type"]
        if "scopes" in data:
            update_data["scopes"] = data["scopes"]
        if "expires_at" in data:
            if data["expires_at"]:
                try:
                    update_data["expires_at"] = datetime.fromisoformat(
                        data["expires_at"].replace("Z", "+00:00")
                    )
                except ValueError:
                    return {"error": "Invalid expires_at format"}, 400
            else:
                update_data["expires_at"] = None
        if "is_active" in data:
            update_data["is_active"] = data["is_active"]

        db(db.iceflows_credentials.id == cred.id).update(**update_data)
        db.commit()

        # Fetch updated credential
        updated_cred = db(db.iceflows_credentials.id == cred.id).select().first()

        return _serialize_credential(updated_cred), 200

    result = await run_in_threadpool(update)

    if result[1] == 404:
        return ApiResponse.error("Not found", 404)
    if isinstance(result[0], dict) and "error" in result[0]:
        return ApiResponse.error(result[0]["error"], result[1])

    return result


@bp.route("/<credential_id>", methods=["DELETE"])
@login_required
@require_scope("flows:write")
async def delete_credential(credential_id: str):
    """Delete credential."""
    tenant_id = _get_tenant_id()

    if not tenant_id:
        return ApiResponse.error("Tenant not found", 403)

    # Capture db in request context BEFORE threadpool
    db = current_app.db

    def delete():
        cred = (
            db(
                (db.iceflows_credentials.credential_id == credential_id)
                & (db.iceflows_credentials.tenant_id == tenant_id)
            )
            .select()
            .first()
        )

        if not cred:
            return None, 404

        # Check if credential is in use
        flows_using = db(
            (db.iceflows.credential_id == cred.id)
            & (db.iceflows.tenant_id == tenant_id)
        ).count()

        if flows_using > 0:
            return {
                "error": f"Credential is in use by {flows_using} pipeline(s). Remove from pipelines first."
            }, 409

        # Delete credential
        db(db.iceflows_credentials.id == cred.id).delete()
        db.commit()

        return {"message": "Credential deleted successfully"}, 204

    result = await run_in_threadpool(delete)

    if result[1] == 404:
        return ApiResponse.error("Not found", 404)
    if isinstance(result[0], dict) and "error" in result[0]:
        return ApiResponse.error(result[0]["error"], result[1])

    return result[0], result[1]
