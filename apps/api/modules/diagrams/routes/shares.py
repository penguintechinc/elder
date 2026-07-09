"""Diagram sharing endpoints using penguin-dal."""

# flake8: noqa: E501

import logging
import secrets
from datetime import datetime, timezone

from quart import Blueprint, current_app, g, jsonify, request

from apps.api.auth.decorators import login_required, require_scope
from apps.api.utils.api_responses import ApiResponse
from apps.api.utils.async_utils import run_in_threadpool
from apps.api.utils.pydal_helpers import PaginationParams

from .diagrams import _can_edit_diagram, _can_read_diagram, _get_tenant_id

logger = logging.getLogger(__name__)

bp = Blueprint("diagram_shares", __name__)


@bp.route("/<int:diagram_id>/shares", methods=["POST"])
@login_required
@require_scope("diagrams:write")
async def create_share(diagram_id):
    """Create a share for a diagram.

    Requires: diagrams:write scope

    Request body:
        {
            "shared_with_identity_id": "optional integer",
            "shared_with_group_id": "optional integer",
            "permission": "viewer|editor (default: viewer)",
            "expires_at": "optional ISO datetime",
            "is_public": false (default)
        }

    Returns:
        201: Created share
        404: Diagram not found
    """
    db = current_app.db
    tenant_id = _get_tenant_id()

    if not tenant_id:
        return ApiResponse.error("Tenant not found", 403)

    data = await request.get_json() or {}

    claims = getattr(g, "claims", {}) or {}
    identity_id = claims.get("identity_id")
    if not identity_id:
        return ApiResponse.error("Identity not found in token", 403)

    # Pre-check: diagram exists and caller can read it
    pre_diagram = await run_in_threadpool(
        lambda: db(
            (db.dg_diagrams.id == diagram_id) & (db.dg_diagrams.tenant_id == tenant_id)
        )
        .select()
        .first()
    )
    if pre_diagram is None:
        return ApiResponse.not_found("Diagram")
    # Granting access is share management — require edit rights (owner/editor),
    # not mere read-ability, so a viewer of a public diagram cannot create
    # shares (which would let them grant others access).
    if not _can_edit_diagram(db, pre_diagram, tenant_id, identity_id):
        return ApiResponse.not_found("Diagram")

    def create():
        now = datetime.now(timezone.utc)
        is_public = data.get("is_public", False)
        share_token = None
        if is_public:
            share_token = secrets.token_urlsafe(48)

        share_id = db.dg_shares.insert(
            tenant_id=tenant_id,
            diagram_id=diagram_id,
            shared_with_identity_id=data.get("shared_with_identity_id"),
            shared_with_group_id=data.get("shared_with_group_id"),
            shared_by_identity_id=identity_id,
            permission=data.get("permission", "viewer"),
            expires_at=data.get("expires_at"),
            share_token=share_token,
            is_public=is_public,
            created_at=now,
            updated_at=now,
        )
        db.commit()

        return db(db.dg_shares.id == share_id).select().first()

    share = await run_in_threadpool(create)

    if not share:
        return ApiResponse.error("Failed to create share", 500)

    return (
        jsonify(
            {
                "id": share.id,
                "diagram_id": share.diagram_id,
                "shared_with_identity_id": share.shared_with_identity_id,
                "shared_with_group_id": share.shared_with_group_id,
                "permission": share.permission,
                "is_public": share.is_public,
                "share_token": share.share_token,
                "expires_at": (
                    share.expires_at.isoformat() if share.expires_at else None
                ),
                "created_at": share.created_at.isoformat(),
            }
        ),
        201,
    )


@bp.route("/<int:diagram_id>/shares", methods=["GET"])
@login_required
@require_scope("diagrams:read")
async def list_shares(diagram_id):
    """List shares for a diagram.

    Requires: diagrams:read scope

    Returns:
        200: List of shares
        404: Diagram not found
    """
    db = current_app.db
    tenant_id = _get_tenant_id()

    if not tenant_id:
        return ApiResponse.error("Tenant not found", 403)

    pagination = PaginationParams.from_request()

    claims = getattr(g, "claims", {}) or {}
    identity_id = claims.get("identity_id")

    def fetch():
        diagram = (
            db(
                (db.dg_diagrams.id == diagram_id)
                & (db.dg_diagrams.tenant_id == tenant_id)
            )
            .select()
            .first()
        )

        if not diagram:
            return (None, 0, [])

        total = db(db.dg_shares.diagram_id == diagram_id).count()
        rows = db(db.dg_shares.diagram_id == diagram_id).select(
            orderby=~db.dg_shares.created_at,
            limitby=(pagination.offset, pagination.offset + pagination.per_page),
        )

        return (diagram, total, rows)

    diagram, total, rows = await run_in_threadpool(fetch)

    if diagram is None:
        return ApiResponse.not_found("Diagram")

    if not _can_read_diagram(db, diagram, tenant_id, identity_id):
        return ApiResponse.not_found("Diagram")

    shares = [
        {
            "id": r.id,
            "diagram_id": r.diagram_id,
            "shared_with_identity_id": r.shared_with_identity_id,
            "shared_with_group_id": r.shared_with_group_id,
            "permission": r.permission,
            "is_public": r.is_public,
            "share_token": r.share_token,
            "expires_at": r.expires_at.isoformat() if r.expires_at else None,
            "created_at": r.created_at.isoformat(),
        }
        for r in rows
    ]

    return (
        jsonify(
            {
                "items": shares,
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


@bp.route("/<int:diagram_id>/shares/<int:share_id>", methods=["DELETE"])
@login_required
@require_scope("diagrams:write")
async def revoke_share(diagram_id, share_id):
    """Revoke a share.

    Requires: diagrams:write scope

    Returns:
        204: Share revoked
        404: Share not found
    """
    db = current_app.db
    tenant_id = _get_tenant_id()

    if not tenant_id:
        return ApiResponse.error("Tenant not found", 403)

    claims = getattr(g, "claims", {}) or {}
    identity_id = claims.get("identity_id")

    def delete():
        share = (
            db(
                (db.dg_shares.id == share_id)
                & (db.dg_shares.diagram_id == diagram_id)
                & (db.dg_shares.tenant_id == tenant_id)
            )
            .select()
            .first()
        )

        if not share:
            return False

        # Revoking access is share management — require edit rights.
        diagram = (
            db(
                (db.dg_diagrams.id == diagram_id)
                & (db.dg_diagrams.tenant_id == tenant_id)
            )
            .select()
            .first()
        )

        if not diagram or not _can_edit_diagram(db, diagram, tenant_id, identity_id):
            return False

        db(db.dg_shares.id == share_id).delete()
        db.commit()
        return True

    success = await run_in_threadpool(delete)

    if not success:
        return ApiResponse.not_found("Share")

    return "", 204


@bp.route("/shared/<share_token>", methods=["GET"])
async def get_shared_diagram(share_token):
    """Get a diagram via public share token (no auth required).

    Returns:
        200: Diagram details with latest version content
        404: Share token not found or expired
    """
    db = current_app.db

    # Extract request context data BEFORE threadpool
    access_ip = request.remote_addr or ""
    user_agent = request.headers.get("User-Agent", "")

    def fetch():
        now = datetime.now(timezone.utc)

        # Find the share by token
        share = (
            db(
                (db.dg_shares.share_token == share_token)
                & (db.dg_shares.is_public == True)
            )
            .select()
            .first()
        )

        if not share:
            return (None, None, None)

        # Check expiration
        if share.expires_at and share.expires_at < now:
            return (None, None, None)

        # Fetch diagram
        diagram = db(db.dg_diagrams.id == share.diagram_id).select().first()

        if not diagram:
            return (None, None, None)

        # Fetch latest version
        latest_version = (
            db(db.dg_diagram_versions.diagram_id == diagram.id)
            .select(orderby=~db.dg_diagram_versions.version_number, limitby=(0, 1))
            .first()
        )

        return (share, diagram, latest_version)

    share, diagram, latest_version = await run_in_threadpool(fetch)

    if share is None or diagram is None:
        return ApiResponse.not_found("Diagram")

    # Record analytics
    def record_analytics():
        now = datetime.now(timezone.utc)

        db.dg_share_analytics.insert(
            tenant_id=diagram.tenant_id,
            share_type="diagram",
            share_id=diagram.id,
            share_token=share_token,
            accessed_by_identity_id=None,  # Public access, no auth
            access_ip=access_ip,
            user_agent=user_agent,
            accessed_at=now,
            created_at=now,
            updated_at=now,
        )
        db.commit()

    await run_in_threadpool(record_analytics)

    content_json = None
    if latest_version:
        content_json = latest_version.content_json

    return jsonify(
        {
            "id": diagram.id,
            "village_id": diagram.village_id,
            "title": diagram.title,
            "description": diagram.description,
            "status": diagram.status,
            "is_public": diagram.is_public,
            "is_template": diagram.is_template,
            "tags": diagram.tags or [],
            "thumbnail_url": diagram.thumbnail_url,
            "content": content_json or {"nodes": [], "edges": []},
            "created_at": diagram.created_at.isoformat(),
            "updated_at": diagram.updated_at.isoformat(),
        }
    )
