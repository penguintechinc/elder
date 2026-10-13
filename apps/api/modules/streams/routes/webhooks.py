"""Streams webhooks and node metadata endpoints using penguin-dal."""

# flake8: noqa: E501

import logging
import secrets
from datetime import UTC, datetime, timezone

from quart import Blueprint, current_app, g, jsonify, request

from apps.api.auth.decorators import login_required, require_scope
from apps.api.utils.api_responses import ApiResponse
from apps.api.utils.async_utils import run_in_threadpool

from .streams import (
    _can_edit_stream,
    _can_read_stream,
    _get_identity_id,
    _get_tenant_id,
)

logger = logging.getLogger(__name__)

bp = Blueprint("streams_webhooks", __name__)


# ============================================================================
# Node Metadata
# ============================================================================


@bp.route("/<int:stream_id>/nodes/<node_id>/metadata", methods=["GET"])
@login_required
@require_scope("streams:read")
async def get_node_metadata(stream_id, node_id):
    """Get metadata for a specific node.

    Args:
        stream_id: Stream identifier
        node_id: Node identifier within the stream

    Returns:
        200: Node metadata
        404: Stream not found
    """
    db = current_app.db
    tenant_id = _get_tenant_id()
    identity_id = _get_identity_id()

    if not tenant_id:
        return ApiResponse.error("Tenant not found", 403)

    def get():
        stream = (
            db(
                (db.stream_playbooks.id == stream_id)
                & (db.stream_playbooks.tenant_id == tenant_id)
            )
            .select()
            .first()
        )
        if not stream:
            return None, None

        # Check access
        if not _can_read_stream(db, stream, tenant_id, identity_id):
            return None, None

        metadata = (
            db(
                (db.stream_node_metadata.playbook_id == stream_id)
                & (db.stream_node_metadata.node_id == node_id)
                & (db.stream_node_metadata.tenant_id == tenant_id)
            )
            .select()
            .first()
        )

        return stream, metadata

    stream, metadata = await run_in_threadpool(get)

    if not stream:
        return ApiResponse.not_found("Stream")

    if not metadata:
        return ApiResponse.success(
            data={
                "node_id": node_id,
                "comments": None,
                "metadata": {},
            }
        )

    return ApiResponse.success(
        data={
            "node_id": node_id,
            "comments": metadata.comments,
            "metadata": metadata.metadata_json or {},
            "updated_at": (
                metadata.updated_at.isoformat() if metadata.updated_at else None
            ),
        }
    )


@bp.route("/<int:stream_id>/nodes/<node_id>/metadata", methods=["PUT"])
@login_required
@require_scope("streams:write")
async def update_node_metadata(stream_id, node_id):
    """Update metadata for a specific node.

    Args:
        stream_id: Stream identifier
        node_id: Node identifier within the stream

    Request body:
        {
            "comments": "optional string",
            "metadata": "optional object"
        }

    Returns:
        200: Updated node metadata
        404: Stream not found
        403: Access denied
    """
    db = current_app.db
    tenant_id = _get_tenant_id()
    identity_id = _get_identity_id()

    if not tenant_id or not identity_id:
        return ApiResponse.error("Tenant or identity not found", 403)

    data = await request.get_json() or {}

    def update():
        stream = (
            db(
                (db.stream_playbooks.id == stream_id)
                & (db.stream_playbooks.tenant_id == tenant_id)
            )
            .select()
            .first()
        )
        if not stream:
            return None, 404

        # Check edit access
        if not _can_edit_stream(db, stream, tenant_id, identity_id):
            return None, 403

        now = datetime.now(UTC)

        # Check if metadata exists
        existing = (
            db(
                (db.stream_node_metadata.playbook_id == stream_id)
                & (db.stream_node_metadata.node_id == node_id)
                & (db.stream_node_metadata.tenant_id == tenant_id)
            )
            .select()
            .first()
        )

        if existing:
            db(db.stream_node_metadata.id == existing.id).update(
                comments=data.get("comments"),
                metadata_json=data.get("metadata") or {},
                updated_by_identity_id=identity_id,
                updated_at=now,
            )
        else:
            db.stream_node_metadata.insert(
                tenant_id=tenant_id,
                playbook_id=stream_id,
                node_id=node_id,
                comments=data.get("comments"),
                metadata_json=data.get("metadata") or {},
                updated_by_identity_id=identity_id,
                created_at=now,
                updated_at=now,
            )

        db.commit()

        return {
            "node_id": node_id,
            "comments": data.get("comments"),
            "metadata": data.get("metadata") or {},
            "updated_at": now.isoformat(),
        }, 200

    result, status_code = await run_in_threadpool(update)

    if status_code == 404:
        return ApiResponse.not_found("Stream")
    elif status_code == 403:
        return ApiResponse.error("Access denied", 403)

    return ApiResponse.success(data=result)


# ============================================================================
# Webhooks
# ============================================================================


@bp.route("/<int:stream_id>/webhooks", methods=["GET"])
@login_required
@require_scope("streams:read")
async def list_webhooks(stream_id):
    """List webhooks for a stream.

    Args:
        stream_id: Stream identifier

    Returns:
        200: List of webhooks
        404: Stream not found
        403: Access denied
    """
    db = current_app.db
    tenant_id = _get_tenant_id()
    identity_id = _get_identity_id()

    if not tenant_id:
        return ApiResponse.error("Tenant not found", 403)

    def list_wh():
        stream = (
            db(
                (db.stream_playbooks.id == stream_id)
                & (db.stream_playbooks.tenant_id == tenant_id)
            )
            .select()
            .first()
        )
        if not stream:
            return None, None

        # Check access
        if not _can_read_stream(db, stream, tenant_id, identity_id):
            return None, None

        # The webhook token is an inbound-trigger credential: anyone holding it
        # can POST /api/v1/hooks/<token> to launch executions. Only callers with
        # EDIT rights (owner/editor) may see the raw token + URL; read-only
        # viewers see that a webhook exists but not the secret (prevents a
        # read→execute privilege escalation).
        can_edit = _can_edit_stream(db, stream, tenant_id, identity_id)

        webhooks = db(
            (db.stream_webhooks.playbook_id == stream_id)
            & (db.stream_webhooks.tenant_id == tenant_id)
        ).select()

        result = [
            {
                "id": w.id,
                "name": w.name,
                "token": w.token if can_edit else None,
                "url": (f"/api/v1/hooks/{w.token}" if can_edit else None),
                "allowed_methods": w.allowed_methods or ["POST"],
                "validate_signature": w.validate_signature,
                "is_enabled": w.is_enabled,
                "created_at": (w.created_at.isoformat() if w.created_at else None),
            }
            for w in webhooks
        ]

        return stream, result

    stream, result = await run_in_threadpool(list_wh)

    if not stream:
        return ApiResponse.not_found("Stream")

    return ApiResponse.success(data=result)


@bp.route("/<int:stream_id>/webhooks", methods=["POST"])
@login_required
@require_scope("streams:write")
async def create_webhook(stream_id):
    """Create a webhook for a stream.

    Args:
        stream_id: Stream identifier

    Request body:
        {
            "name": "optional string",
            "allowed_methods": "optional array",
            "validate_signature": "optional boolean",
            "signature_secret": "optional string"
        }

    Returns:
        201: Created webhook
        404: Stream not found
        403: Access denied
    """
    db = current_app.db
    tenant_id = _get_tenant_id()
    identity_id = _get_identity_id()

    if not tenant_id or not identity_id:
        return ApiResponse.error("Tenant or identity not found", 403)

    data = await request.get_json() or {}

    def create():
        stream = (
            db(
                (db.stream_playbooks.id == stream_id)
                & (db.stream_playbooks.tenant_id == tenant_id)
            )
            .select()
            .first()
        )
        if not stream:
            return None, 404

        # Check edit access
        if not _can_edit_stream(db, stream, tenant_id, identity_id):
            return None, 403

        # Generate secure token
        token = secrets.token_urlsafe(32)
        now = datetime.now(UTC)

        webhook_id = db.stream_webhooks.insert(
            tenant_id=tenant_id,
            playbook_id=stream_id,
            name=data.get("name") or f"Webhook for {stream.name}",
            token=token,
            allowed_methods=data.get("allowed_methods") or ["POST"],
            validate_signature=data.get("validate_signature", False),
            signature_secret=data.get("signature_secret"),
            is_enabled=True,
            is_active=True,
            created_at=now,
            updated_at=now,
        )
        db.commit()

        return {
            "id": webhook_id,
            "name": data.get("name"),
            "token": token,
            "url": f"/api/v1/hooks/{token}",
            "allowed_methods": data.get("allowed_methods") or ["POST"],
            "validate_signature": data.get("validate_signature", False),
            "is_enabled": True,
        }, 201

    result, status_code = await run_in_threadpool(create)

    if status_code == 404:
        return ApiResponse.not_found("Stream")
    elif status_code == 403:
        return ApiResponse.error("Access denied", 403)

    return ApiResponse.success(data=result, status_code=status_code)


@bp.route("/<int:stream_id>/webhooks/<int:webhook_id>", methods=["DELETE"])
@login_required
@require_scope("streams:write")
async def delete_webhook(stream_id, webhook_id):
    """Delete a webhook.

    Args:
        stream_id: Stream identifier
        webhook_id: Webhook identifier

    Returns:
        200: Success response
        404: Stream or webhook not found
        403: Access denied
    """
    db = current_app.db
    tenant_id = _get_tenant_id()
    identity_id = _get_identity_id()

    if not tenant_id or not identity_id:
        return ApiResponse.error("Tenant or identity not found", 403)

    def delete():
        stream = (
            db(
                (db.stream_playbooks.id == stream_id)
                & (db.stream_playbooks.tenant_id == tenant_id)
            )
            .select()
            .first()
        )
        if not stream:
            return 404

        # Check edit access
        if not _can_edit_stream(db, stream, tenant_id, identity_id):
            return 403

        webhook = (
            db(
                (db.stream_webhooks.id == webhook_id)
                & (db.stream_webhooks.playbook_id == stream_id)
                & (db.stream_webhooks.tenant_id == tenant_id)
            )
            .select()
            .first()
        )

        if not webhook:
            return 404

        db(db.stream_webhooks.id == webhook_id).delete()
        db.commit()
        return 200

    status_code = await run_in_threadpool(delete)

    if status_code == 404:
        return ApiResponse.not_found("Stream or webhook")
    elif status_code == 403:
        return ApiResponse.error("Access denied", 403)

    return ApiResponse.success({"message": "Webhook deleted successfully"})
