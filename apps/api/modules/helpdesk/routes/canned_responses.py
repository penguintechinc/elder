"""Helpdesk canned response CRUD endpoints using penguin-dal."""

# flake8: noqa: E501

import logging
from datetime import datetime, timezone

from quart import Blueprint, current_app, g, request

from apps.api.auth.decorators import login_required
from apps.api.utils.api_responses import ApiResponse
from apps.api.utils.async_utils import run_in_threadpool
from apps.api.utils.pydal_helpers import PaginationParams

logger = logging.getLogger(__name__)

bp = Blueprint("helpdesk_canned_responses", __name__)


def _get_tenant_id() -> int:
    """Extract tenant_id from g.claims (populated by before_request)."""
    claims = getattr(g, "claims", {}) or {}
    tenant_str = claims.get("tenant", "")
    if not tenant_str:
        return None
    try:
        return int(tenant_str)
    except (ValueError, TypeError):
        return None


@bp.route("", methods=["GET"])
@login_required
async def list_canned_responses():
    """
    List canned responses with optional filtering and pagination.

    Query Parameters:
        - page: Page number (default: 1)
        - per_page: Items per page (default: 20, max: 100)
        - category: Filter by category

    Returns:
        200: Paginated list of canned responses
    """
    db = current_app.db
    tenant_id = _get_tenant_id()

    if not tenant_id:
        return ApiResponse.error("Tenant not found", 403)

    # Extract pagination params
    pagination = PaginationParams.from_request()

    # Build query
    query = db.hd_canned_responses.tenant_id == tenant_id

    # Apply filters
    category_filter = request.args.get("category")
    if category_filter:
        query &= db.hd_canned_responses.category == category_filter

    def get_responses():
        total = db(query).count()
        rows = db(query).select(
            orderby=~db.hd_canned_responses.created_at,
            limitby=(pagination.offset, pagination.offset + pagination.per_page),
        )
        return total, rows

    total, rows = await run_in_threadpool(get_responses)

    responses = [
        {
            "id": r.id,
            "title": r.title,
            "category": r.category,
            "body_html": r.body_html,
            "is_shared": r.is_shared,
            "created_by_id": r.created_by_identity_id,
            "created_at": r.created_at.isoformat(),
        }
        for r in rows
    ]

    return (
        {
            "items": responses,
            "pagination": {
                "page": pagination.page,
                "per_page": pagination.per_page,
                "total": total,
                "pages": (total + pagination.per_page - 1) // pagination.per_page,
            },
        },
        200,
    )


@bp.route("", methods=["POST"])
@login_required
async def create_canned_response():
    """
    Create a new canned response.

    Request body:
        {
            "title": "Response Title",
            "body_html": "<p>Response body HTML</p>",
            "category": "billing",
            "is_shared": true
        }

    Returns:
        201: Created canned response
    """
    db = current_app.db
    tenant_id = _get_tenant_id()

    if not tenant_id:
        return ApiResponse.error("Tenant not found", 403)

    claims = getattr(g, "claims", {}) or {}
    current_user_id = claims.get("sub")

    data = await request.get_json() or {}

    # Validate required fields
    title = data.get("title", "").strip()
    body_html = data.get("body_html", "").strip()

    if not title:
        return ApiResponse.validation_error("title", "is required")

    if not body_html:
        return ApiResponse.validation_error("body_html", "is required")

    def create():
        now = datetime.now(timezone.utc)
        response_id = db.hd_canned_responses.insert(
            tenant_id=tenant_id,
            title=title,
            body_html=body_html,
            category=data.get("category"),
            created_by_identity_id=None,  # No identity_id available from JWT sub (would need lookup)
            is_shared=data.get("is_shared", True),
            created_at=now,
            updated_at=now,
        )
        db.commit()
        return db(db.hd_canned_responses.id == response_id).select().first()

    response_row = await run_in_threadpool(create)

    return (
        {
            "id": response_row.id,
            "title": response_row.title,
            "category": response_row.category,
            "body_html": response_row.body_html,
            "is_shared": response_row.is_shared,
            "created_at": response_row.created_at.isoformat(),
        },
        201,
    )


@bp.route("/<int:response_id>", methods=["PUT"])
@login_required
async def update_canned_response(response_id: int):
    """
    Update canned response properties.

    Path parameters:
        response_id: Response ID

    Request body:
        {
            "title": "Updated Title",
            "body_html": "<p>Updated body</p>",
            "category": "billing",
            "is_shared": true
        }

    Returns:
        200: Updated canned response
    """
    db = current_app.db
    tenant_id = _get_tenant_id()

    if not tenant_id:
        return ApiResponse.error("Tenant not found", 403)

    data = await request.get_json() or {}

    def update():
        # Fetch response
        query = (db.hd_canned_responses.id == response_id) & (
            db.hd_canned_responses.tenant_id == tenant_id
        )
        response_row = db(query).select().first()

        if not response_row:
            return None

        # Update fields
        update_data = {}
        if "title" in data:
            title = data["title"].strip()
            if title:
                update_data["title"] = title

        if "body_html" in data:
            body_html = data["body_html"].strip()
            if body_html:
                update_data["body_html"] = body_html

        if "category" in data:
            update_data["category"] = data["category"]

        if "is_shared" in data:
            update_data["is_shared"] = data["is_shared"]

        update_data["updated_at"] = datetime.now(timezone.utc)

        if update_data:
            db(query).update(**update_data)
            db.commit()

        # Return updated row
        return db(query).select().first()

    result = await run_in_threadpool(update)

    if result is None:
        return ApiResponse.not_found("Canned response", response_id)

    return (
        {
            "id": result.id,
            "title": result.title,
            "category": result.category,
            "body_html": result.body_html,
            "is_shared": result.is_shared,
            "created_at": result.created_at.isoformat(),
        },
        200,
    )


@bp.route("/<int:response_id>", methods=["DELETE"])
@login_required
async def delete_canned_response(response_id: int):
    """
    Delete a canned response.

    Path parameters:
        response_id: Response ID

    Returns:
        204: No content
    """
    db = current_app.db
    tenant_id = _get_tenant_id()

    if not tenant_id:
        return ApiResponse.error("Tenant not found", 403)

    def delete():
        # Fetch response
        query = (db.hd_canned_responses.id == response_id) & (
            db.hd_canned_responses.tenant_id == tenant_id
        )
        response_row = db(query).select().first()

        if not response_row:
            return None

        # Delete response
        db(query).delete()
        db.commit()
        return True

    result = await run_in_threadpool(delete)

    if result is None:
        return ApiResponse.not_found("Canned response", response_id)

    return ApiResponse.no_content()
