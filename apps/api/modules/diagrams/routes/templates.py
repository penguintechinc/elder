"""Diagram templates endpoints using penguin-dal."""

# flake8: noqa: E501

import logging
from datetime import UTC, datetime, timezone

from quart import Blueprint, current_app, g, jsonify, request

from apps.api.auth.decorators import login_required, require_scope
from apps.api.utils.api_responses import ApiResponse
from apps.api.utils.async_utils import run_in_threadpool
from apps.api.utils.pydal_helpers import PaginationParams
from shared.utils.village_id import generate_village_id

from .diagrams import _get_tenant_id

logger = logging.getLogger(__name__)

bp = Blueprint("diagram_templates", __name__)


def _can_read_template(db, template_row, tenant_id, identity_id=None) -> bool:
    """Check if current user can read this template.

    Args:
        db: PyDAL database instance
        template_row: Template row from database
        tenant_id: Current tenant ID
        identity_id: Current identity ID (optional)

    Returns:
        True if user can read, False otherwise
    """
    # Cross-tenant isolation: template must belong to this tenant
    if template_row.tenant_id != tenant_id:
        return False

    # Public templates visible to everyone
    if template_row.is_public:
        return True

    # Unauthenticated users can't read private templates
    if identity_id is None:
        return False

    # Creator can always read their own template
    if template_row.created_by_identity_id == identity_id:
        return True

    return False


@bp.route("", methods=["GET"])
@login_required
@require_scope("diagrams:read")
async def list_templates():
    """List diagram templates (tenant-scoped, paginated).

    Public templates and creator's private templates are visible.

    Query parameters:
        - page: Page number (default: 1)
        - per_page: Items per page (default: 20, max: 100)

    Returns:
        200: Paginated list of templates
    """
    db = current_app.db
    tenant_id = _get_tenant_id()

    if not tenant_id:
        return ApiResponse.error("Tenant not found", 403)

    claims = getattr(g, "claims", {}) or {}
    identity_id = claims.get("identity_id")

    pagination = PaginationParams.from_request()

    def list_tmpl():
        # Query tenant-scoped templates
        query = db.dg_templates.tenant_id == tenant_id
        total = db(query).count()
        rows = db(query).select(
            orderby=~db.dg_templates.created_at,
            limitby=(pagination.offset, pagination.offset + pagination.per_page),
        )
        return total, rows

    total, rows = await run_in_threadpool(list_tmpl)

    # Post-filter by visibility
    templates = []
    for r in rows:
        if _can_read_template(db, r, tenant_id, identity_id):
            templates.append(
                {
                    "id": r.id,
                    "village_id": r.village_id,
                    "name": r.name,
                    "category": r.category,
                    "thumbnail_url": r.thumbnail_url,
                    "is_public": r.is_public,
                    "created_by_identity_id": r.created_by_identity_id,
                    "created_at": r.created_at.isoformat(),
                    "updated_at": r.updated_at.isoformat(),
                }
            )

    return (
        jsonify(
            {
                "items": templates,
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


@bp.route("", methods=["POST"])
@login_required
@require_scope("diagrams:write")
async def create_template():
    """Create a new diagram template.

    Requires: diagrams:write scope

    Request body:
        {
            "name": "string (required)",
            "content": "JSON object (required)",
            "category": "string (default: 'custom')",
            "thumbnail_url": "string (optional)",
            "is_public": bool (default: false)
        }

    Returns:
        201: Created template
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

    content = data.get("content")
    if content is None:
        return ApiResponse.validation_error("content", "is required")

    # Mint village_id in request context
    village_id = generate_village_id(tenant_id, current_app.redis_client)

    def create_tmpl():
        now = datetime.now(UTC)

        template_id = db.dg_templates.insert(
            tenant_id=tenant_id,
            village_id=village_id,
            name=name,
            content=content,
            category=data.get("category", "custom"),
            thumbnail_url=data.get("thumbnail_url"),
            is_public=data.get("is_public", False),
            created_by_identity_id=identity_id,
            created_at=now,
            updated_at=now,
        )
        db.commit()

        return db(db.dg_templates.id == template_id).select().first()

    result = await run_in_threadpool(create_tmpl)

    if not result:
        return ApiResponse.error("Failed to create template", 500)

    return (
        jsonify(
            {
                "id": result.id,
                "village_id": result.village_id,
                "name": result.name,
                "category": result.category,
                "thumbnail_url": result.thumbnail_url,
                "is_public": result.is_public,
                "created_by_identity_id": result.created_by_identity_id,
                "created_at": result.created_at.isoformat(),
                "updated_at": result.updated_at.isoformat(),
            }
        ),
        201,
    )


@bp.route("/<int:template_id>", methods=["GET"])
@login_required
@require_scope("diagrams:read")
async def get_template(template_id: int):
    """Get a specific template by ID.

    Returns 404 if template is private and not owned by caller.

    Requires: diagrams:read scope

    Returns:
        200: Template details including content
    """
    db = current_app.db
    tenant_id = _get_tenant_id()

    if not tenant_id:
        return ApiResponse.error("Tenant not found", 403)

    claims = getattr(g, "claims", {}) or {}
    identity_id = claims.get("identity_id")

    def get_tmpl():
        template_row = db(db.dg_templates.id == template_id).select().first()
        if not template_row:
            return None, "not_found"

        if not _can_read_template(db, template_row, tenant_id, identity_id):
            return None, "forbidden"

        return template_row, "ok"

    template_row, status = await run_in_threadpool(get_tmpl)

    if status == "not_found":
        return ApiResponse.error("Template not found", 404)
    if status == "forbidden":
        return ApiResponse.error("Forbidden", 403)

    return (
        jsonify(
            {
                "id": template_row.id,
                "village_id": template_row.village_id,
                "name": template_row.name,
                "content": template_row.content,
                "category": template_row.category,
                "thumbnail_url": template_row.thumbnail_url,
                "is_public": template_row.is_public,
                "created_by_identity_id": template_row.created_by_identity_id,
                "created_at": template_row.created_at.isoformat(),
                "updated_at": template_row.updated_at.isoformat(),
            }
        ),
        200,
    )


@bp.route("/<int:template_id>", methods=["PATCH"])
@login_required
@require_scope("diagrams:write")
async def update_template(template_id: int):
    """Update template (creator only).

    Requires: diagrams:write scope
    Authorization: creator only

    Request body:
        {
            "name": "string (optional)",
            "content": "JSON object (optional)",
            "category": "string (optional)",
            "thumbnail_url": "string (optional)",
            "is_public": bool (optional)
        }

    Returns:
        200: Updated template
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

    def update_tmpl():
        template_row = db(db.dg_templates.id == template_id).select().first()
        if not template_row:
            return None, "not_found"

        # Authorization: creator only
        if template_row.created_by_identity_id != identity_id:
            return None, "forbidden"

        # Update fields
        now = datetime.now(UTC)
        update_dict = {"updated_at": now}

        if "name" in data:
            name = data.get("name", "").strip()
            if name:
                update_dict["name"] = name

        if "content" in data:
            update_dict["content"] = data.get("content")

        if "category" in data:
            update_dict["category"] = data.get("category", "custom")

        if "thumbnail_url" in data:
            update_dict["thumbnail_url"] = data.get("thumbnail_url")

        if "is_public" in data:
            update_dict["is_public"] = bool(data.get("is_public"))

        db(db.dg_templates.id == template_id).update(**update_dict)
        db.commit()

        return db(db.dg_templates.id == template_id).select().first(), "ok"

    template_row, status = await run_in_threadpool(update_tmpl)

    if status == "not_found":
        return ApiResponse.error("Template not found", 404)
    if status == "forbidden":
        return ApiResponse.error("Forbidden", 403)

    return (
        jsonify(
            {
                "id": template_row.id,
                "village_id": template_row.village_id,
                "name": template_row.name,
                "content": template_row.content,
                "category": template_row.category,
                "thumbnail_url": template_row.thumbnail_url,
                "is_public": template_row.is_public,
                "created_by_identity_id": template_row.created_by_identity_id,
                "created_at": template_row.created_at.isoformat(),
                "updated_at": template_row.updated_at.isoformat(),
            }
        ),
        200,
    )


@bp.route("/<int:template_id>", methods=["DELETE"])
@login_required
@require_scope("diagrams:write")
async def delete_template(template_id: int):
    """Delete template (creator only).

    Requires: diagrams:write scope
    Authorization: creator only

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

    def delete_tmpl():
        template_row = db(db.dg_templates.id == template_id).select().first()
        if not template_row:
            return None, "not_found"

        # Authorization: creator only
        if template_row.created_by_identity_id != identity_id:
            return None, "forbidden"

        # Delete template
        db(db.dg_templates.id == template_id).delete()
        db.commit()

        return True, "ok"

    result, status = await run_in_threadpool(delete_tmpl)

    if status == "not_found":
        return ApiResponse.error("Template not found", 404)
    if status == "forbidden":
        return ApiResponse.error("Forbidden", 403)

    return "", 204
