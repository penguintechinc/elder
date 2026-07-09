"""Diagram comments endpoints using penguin-dal."""

# flake8: noqa: E501

import logging
from datetime import datetime, timezone

from quart import Blueprint, current_app, g, jsonify, request

from apps.api.auth.decorators import login_required, require_scope
from apps.api.utils.api_responses import ApiResponse
from apps.api.utils.async_utils import run_in_threadpool
from apps.api.utils.pydal_helpers import PaginationParams

from .diagrams import _can_read_diagram, _get_tenant_id

logger = logging.getLogger(__name__)

bp = Blueprint("diagram_comments", __name__)


@bp.route("/<int:diagram_id>/comments", methods=["POST"])
@login_required
@require_scope("diagrams:read")
async def create_comment(diagram_id: int):
    """Create a comment on a diagram.

    Requires: diagrams:read scope (commenting is a read-tier collaborative action)

    Request body:
        {
            "text_content": "string (required)",
            "x_position": int (optional),
            "y_position": int (optional)
        }

    Returns:
        201: Created comment with replies (empty list)
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

    # Validate required field
    text_content = data.get("text_content", "").strip()
    if not text_content:
        return ApiResponse.validation_error("text_content", "is required")

    def create_comment_tx():
        # Fetch diagram and verify read access
        diagram_row = db(db.dg_diagrams.id == diagram_id).select().first()
        if not diagram_row:
            return None, "diagram_not_found"

        if not _can_read_diagram(db, diagram_row, tenant_id, identity_id):
            return None, "forbidden"

        # Create comment
        now = datetime.now(timezone.utc)
        comment_id = db.dg_comments.insert(
            diagram_id=diagram_id,
            tenant_id=tenant_id,
            author_identity_id=identity_id,
            text_content=text_content,
            x_position=data.get("x_position"),
            y_position=data.get("y_position"),
            is_resolved=False,
            created_at=now,
            updated_at=now,
        )
        db.commit()

        # Fetch created comment
        return db(db.dg_comments.id == comment_id).select().first(), "ok"

    comment_row, status = await run_in_threadpool(create_comment_tx)

    if status == "diagram_not_found":
        return ApiResponse.error("Diagram not found", 404)
    if status == "forbidden":
        return ApiResponse.error("Forbidden", 403)

    return (
        jsonify(
            {
                "id": comment_row.id,
                "diagram_id": comment_row.diagram_id,
                "author_identity_id": comment_row.author_identity_id,
                "text_content": comment_row.text_content,
                "x_position": comment_row.x_position,
                "y_position": comment_row.y_position,
                "is_resolved": comment_row.is_resolved,
                "created_at": comment_row.created_at.isoformat(),
                "updated_at": comment_row.updated_at.isoformat(),
                "replies": [],
            }
        ),
        201,
    )


@bp.route("/<int:diagram_id>/comments", methods=["GET"])
@login_required
@require_scope("diagrams:read")
async def list_comments(diagram_id: int):
    """List comments on a diagram (paginated) with nested replies.

    Query parameters:
        - page: Page number (default: 1)
        - per_page: Items per page (default: 20, max: 100)

    Returns:
        200: Paginated list of comments with replies
    """
    db = current_app.db
    tenant_id = _get_tenant_id()

    if not tenant_id:
        return ApiResponse.error("Tenant not found", 403)

    claims = getattr(g, "claims", {}) or {}
    identity_id = claims.get("identity_id")

    pagination = PaginationParams.from_request()

    def list_cmts():
        # Verify diagram exists and user can read it
        diagram_row = db(db.dg_diagrams.id == diagram_id).select().first()
        if not diagram_row:
            return None, "diagram_not_found"

        if not _can_read_diagram(db, diagram_row, tenant_id, identity_id):
            return None, "forbidden"

        # Count and fetch comments
        query = (db.dg_comments.diagram_id == diagram_id) & (
            db.dg_comments.tenant_id == tenant_id
        )
        total = db(query).count()
        rows = db(query).select(
            orderby=~db.dg_comments.created_at,
            limitby=(pagination.offset, pagination.offset + pagination.per_page),
        )

        return (total, rows), "ok"

    (total, rows), status = await run_in_threadpool(list_cmts)

    if status == "diagram_not_found":
        return ApiResponse.error("Diagram not found", 404)
    if status == "forbidden":
        return ApiResponse.error("Forbidden", 403)

    def fetch_replies(comment_id):
        reply_rows = db(
            (db.dg_comment_replies.comment_id == comment_id)
            & (db.dg_comment_replies.tenant_id == tenant_id)
        ).select(orderby=db.dg_comment_replies.created_at)
        return [
            {
                "id": r.id,
                "comment_id": r.comment_id,
                "author_identity_id": r.author_identity_id,
                "text_content": r.text_content,
                "created_at": r.created_at.isoformat(),
                "updated_at": r.updated_at.isoformat(),
            }
            for r in reply_rows
        ]

    comments_data = []
    for r in rows:
        replies = await run_in_threadpool(fetch_replies, r.id)
        comments_data.append(
            {
                "id": r.id,
                "diagram_id": r.diagram_id,
                "author_identity_id": r.author_identity_id,
                "text_content": r.text_content,
                "x_position": r.x_position,
                "y_position": r.y_position,
                "is_resolved": r.is_resolved,
                "created_at": r.created_at.isoformat(),
                "updated_at": r.updated_at.isoformat(),
                "replies": replies,
            }
        )

    return (
        jsonify(
            {
                "items": comments_data,
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


@bp.route("/<int:diagram_id>/comments/<int:comment_id>/replies", methods=["POST"])
@login_required
@require_scope("diagrams:read")
async def add_reply(diagram_id: int, comment_id: int):
    """Add a reply to a comment.

    Requires: diagrams:read scope

    Request body:
        {
            "text_content": "string (required)"
        }

    Returns:
        201: Created reply
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

    # Validate required field
    text_content = data.get("text_content", "").strip()
    if not text_content:
        return ApiResponse.validation_error("text_content", "is required")

    def add_reply_tx():
        # Verify diagram exists and user can read it
        diagram_row = db(db.dg_diagrams.id == diagram_id).select().first()
        if not diagram_row:
            return None, "diagram_not_found"

        if not _can_read_diagram(db, diagram_row, tenant_id, identity_id):
            return None, "forbidden"

        # Verify comment exists and belongs to this diagram
        comment_row = (
            db(
                (db.dg_comments.id == comment_id)
                & (db.dg_comments.diagram_id == diagram_id)
                & (db.dg_comments.tenant_id == tenant_id)
            )
            .select()
            .first()
        )
        if not comment_row:
            return None, "comment_not_found"

        # Create reply
        now = datetime.now(timezone.utc)
        reply_id = db.dg_comment_replies.insert(
            comment_id=comment_id,
            tenant_id=tenant_id,
            author_identity_id=identity_id,
            text_content=text_content,
            created_at=now,
            updated_at=now,
        )
        db.commit()

        # Fetch created reply
        return db(db.dg_comment_replies.id == reply_id).select().first(), "ok"

    reply_row, status = await run_in_threadpool(add_reply_tx)

    if status == "diagram_not_found":
        return ApiResponse.error("Diagram not found", 404)
    if status == "forbidden":
        return ApiResponse.error("Forbidden", 403)
    if status == "comment_not_found":
        return ApiResponse.error("Comment not found", 404)

    return (
        jsonify(
            {
                "id": reply_row.id,
                "comment_id": reply_row.comment_id,
                "author_identity_id": reply_row.author_identity_id,
                "text_content": reply_row.text_content,
                "created_at": reply_row.created_at.isoformat(),
                "updated_at": reply_row.updated_at.isoformat(),
            }
        ),
        201,
    )


@bp.route("/<int:diagram_id>/comments/<int:comment_id>", methods=["PATCH"])
@login_required
@require_scope("diagrams:read")
async def update_comment(diagram_id: int, comment_id: int):
    """Update comment (author or diagram editor only).

    Requires: diagrams:read scope
    Authorization: comment author or diagram editor

    Request body:
        {
            "text_content": "updated text (optional)",
            "is_resolved": bool (optional)
        }

    Returns:
        200: Updated comment
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

    def update_comment_tx():
        from .diagrams import _can_edit_diagram

        # Verify diagram exists
        diagram_row = db(db.dg_diagrams.id == diagram_id).select().first()
        if not diagram_row:
            return None, "diagram_not_found"

        # Verify comment exists and belongs to this diagram
        comment_row = (
            db(
                (db.dg_comments.id == comment_id)
                & (db.dg_comments.diagram_id == diagram_id)
                & (db.dg_comments.tenant_id == tenant_id)
            )
            .select()
            .first()
        )
        if not comment_row:
            return None, "comment_not_found"

        # Authorization: comment author OR diagram editor
        is_author = comment_row.author_identity_id == identity_id
        is_editor = _can_edit_diagram(db, diagram_row, tenant_id, identity_id)

        if not (is_author or is_editor):
            return None, "forbidden"

        # Update fields
        now = datetime.now(timezone.utc)
        update_dict = {"updated_at": now}

        if "text_content" in data:
            text = data.get("text_content", "").strip()
            if text:
                update_dict["text_content"] = text

        if "is_resolved" in data:
            update_dict["is_resolved"] = bool(data.get("is_resolved"))

        db(db.dg_comments.id == comment_id).update(**update_dict)
        db.commit()

        # Fetch updated comment
        return db(db.dg_comments.id == comment_id).select().first(), "ok"

    comment_row, status = await run_in_threadpool(update_comment_tx)

    if status == "diagram_not_found":
        return ApiResponse.error("Diagram not found", 404)
    if status == "comment_not_found":
        return ApiResponse.error("Comment not found", 404)
    if status == "forbidden":
        return ApiResponse.error("Forbidden", 403)

    return (
        jsonify(
            {
                "id": comment_row.id,
                "diagram_id": comment_row.diagram_id,
                "author_identity_id": comment_row.author_identity_id,
                "text_content": comment_row.text_content,
                "x_position": comment_row.x_position,
                "y_position": comment_row.y_position,
                "is_resolved": comment_row.is_resolved,
                "created_at": comment_row.created_at.isoformat(),
                "updated_at": comment_row.updated_at.isoformat(),
            }
        ),
        200,
    )


@bp.route("/<int:diagram_id>/comments/<int:comment_id>", methods=["DELETE"])
@login_required
@require_scope("diagrams:read")
async def delete_comment(diagram_id: int, comment_id: int):
    """Delete comment (author or diagram editor only).

    Cascades: dg_comment_replies CASCADE, replies are deleted.

    Requires: diagrams:read scope
    Authorization: comment author or diagram editor

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

    def delete_comment_tx():
        from .diagrams import _can_edit_diagram

        # Verify diagram exists
        diagram_row = db(db.dg_diagrams.id == diagram_id).select().first()
        if not diagram_row:
            return None, "diagram_not_found"

        # Verify comment exists and belongs to this diagram
        comment_row = (
            db(
                (db.dg_comments.id == comment_id)
                & (db.dg_comments.diagram_id == diagram_id)
                & (db.dg_comments.tenant_id == tenant_id)
            )
            .select()
            .first()
        )
        if not comment_row:
            return None, "comment_not_found"

        # Authorization: comment author OR diagram editor
        is_author = comment_row.author_identity_id == identity_id
        is_editor = _can_edit_diagram(db, diagram_row, tenant_id, identity_id)

        if not (is_author or is_editor):
            return None, "forbidden"

        # Delete comment (cascades replies)
        db(db.dg_comments.id == comment_id).delete()
        db.commit()

        return True, "ok"

    result, status = await run_in_threadpool(delete_comment_tx)

    if status == "diagram_not_found":
        return ApiResponse.error("Diagram not found", 404)
    if status == "comment_not_found":
        return ApiResponse.error("Comment not found", 404)
    if status == "forbidden":
        return ApiResponse.error("Forbidden", 403)

    return "", 204
