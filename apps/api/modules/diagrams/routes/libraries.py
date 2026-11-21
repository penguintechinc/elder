"""Diagram shape libraries endpoints using penguin-dal."""

# flake8: noqa: E501

import logging
from datetime import UTC, datetime, timezone

from quart import Blueprint, current_app, g, jsonify, request

from apps.api.auth.decorators import login_required, require_scope
from apps.api.utils.api_responses import ApiResponse
from apps.api.utils.async_utils import run_in_threadpool
from apps.api.utils.pydal_helpers import PaginationParams

from .diagrams import _get_tenant_id

logger = logging.getLogger(__name__)

bp = Blueprint("diagram_libraries", __name__)


def _can_read_library(db, library_row, tenant_id, identity_id=None) -> bool:
    """Check if current user can read this library.

    Args:
        db: PyDAL database instance
        library_row: Library row from database
        tenant_id: Current tenant ID
        identity_id: Current identity ID (optional)

    Returns:
        True if user can read, False otherwise
    """
    # Cross-tenant isolation: library must belong to this tenant
    if library_row.tenant_id != tenant_id:
        return False

    # Public libraries visible to everyone
    if library_row.is_public:
        return True

    # Unauthenticated users can't read private libraries
    if identity_id is None:
        return False

    # Owner can always read their own library
    if library_row.owner_identity_id == identity_id:
        return True

    return False


@bp.route("", methods=["GET"])
@login_required
@require_scope("diagrams:read")
async def list_libraries():
    """List shape libraries (tenant-scoped, paginated).

    Public libraries and owner's private libraries are visible.

    Query parameters:
        - page: Page number (default: 1)
        - per_page: Items per page (default: 20, max: 100)

    Returns:
        200: Paginated list of libraries
    """
    db = current_app.db
    tenant_id = _get_tenant_id()

    if not tenant_id:
        return ApiResponse.error("Tenant not found", 403)

    claims = getattr(g, "claims", {}) or {}
    identity_id = claims.get("identity_id")

    pagination = PaginationParams.from_request()

    def list_libs():
        query = db.dg_shape_libraries.tenant_id == tenant_id
        total = db(query).count()
        rows = db(query).select(
            orderby=~db.dg_shape_libraries.created_at,
            limitby=(pagination.offset, pagination.offset + pagination.per_page),
        )
        return total, rows

    total, rows = await run_in_threadpool(list_libs)

    # Post-filter by visibility
    libraries = []
    for r in rows:
        if _can_read_library(db, r, tenant_id, identity_id):
            libraries.append(
                {
                    "id": r.id,
                    "name": r.name,
                    "description": r.description,
                    "is_public": r.is_public,
                    "owner_identity_id": r.owner_identity_id,
                    "created_at": r.created_at.isoformat(),
                    "updated_at": r.updated_at.isoformat(),
                }
            )

    return (
        jsonify(
            {
                "items": libraries,
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
async def create_library():
    """Create a new shape library.

    Requires: diagrams:write scope

    Request body:
        {
            "name": "string (required)",
            "description": "string (optional)",
            "is_public": bool (default: false)
        }

    Returns:
        201: Created library
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
    name = data.get("name", "").strip()
    if not name:
        return ApiResponse.validation_error("name", "is required")

    def create_lib():
        now = datetime.now(UTC)

        library_id = db.dg_shape_libraries.insert(
            tenant_id=tenant_id,
            name=name,
            description=data.get("description"),
            is_public=data.get("is_public", False),
            owner_identity_id=identity_id,
            created_at=now,
            updated_at=now,
        )
        db.commit()

        return db(db.dg_shape_libraries.id == library_id).select().first()

    result = await run_in_threadpool(create_lib)

    if not result:
        return ApiResponse.error("Failed to create library", 500)

    return (
        jsonify(
            {
                "id": result.id,
                "name": result.name,
                "description": result.description,
                "is_public": result.is_public,
                "owner_identity_id": result.owner_identity_id,
                "created_at": result.created_at.isoformat(),
                "updated_at": result.updated_at.isoformat(),
            }
        ),
        201,
    )


@bp.route("/<int:library_id>", methods=["GET"])
@login_required
@require_scope("diagrams:read")
async def get_library(library_id: int):
    """Get a specific library by ID with all its shapes.

    Returns 404 if library is private and not owned by caller.

    Requires: diagrams:read scope

    Returns:
        200: Library details with nested shapes array
    """
    db = current_app.db
    tenant_id = _get_tenant_id()

    if not tenant_id:
        return ApiResponse.error("Tenant not found", 403)

    claims = getattr(g, "claims", {}) or {}
    identity_id = claims.get("identity_id")

    def get_lib():
        library_row = db(db.dg_shape_libraries.id == library_id).select().first()
        if not library_row:
            return None, None, "not_found"

        if not _can_read_library(db, library_row, tenant_id, identity_id):
            return None, None, "forbidden"

        # Fetch shapes for this library
        shape_rows = db(
            (db.dg_library_shapes.library_id == library_id)
            & (db.dg_library_shapes.tenant_id == tenant_id)
        ).select(orderby=db.dg_library_shapes.created_at)

        return library_row, shape_rows, "ok"

    library_row, shape_rows, status = await run_in_threadpool(get_lib)

    if status == "not_found":
        return ApiResponse.error("Library not found", 404)
    if status == "forbidden":
        return ApiResponse.error("Forbidden", 403)

    shapes = [
        {
            "id": s.id,
            "name": s.name,
            "shape_type": s.shape_type,
            "default_width": s.default_width,
            "default_height": s.default_height,
            "svg_content": s.svg_content,
            "shape_meta": s.shape_meta,
            "created_at": s.created_at.isoformat(),
        }
        for s in shape_rows
    ]

    return (
        jsonify(
            {
                "id": library_row.id,
                "name": library_row.name,
                "description": library_row.description,
                "is_public": library_row.is_public,
                "owner_identity_id": library_row.owner_identity_id,
                "created_at": library_row.created_at.isoformat(),
                "updated_at": library_row.updated_at.isoformat(),
                "shapes": shapes,
            }
        ),
        200,
    )


@bp.route("/<int:library_id>", methods=["PATCH"])
@login_required
@require_scope("diagrams:write")
async def update_library(library_id: int):
    """Update library (owner only).

    Requires: diagrams:write scope
    Authorization: owner only

    Request body:
        {
            "name": "string (optional)",
            "description": "string (optional)",
            "is_public": bool (optional)
        }

    Returns:
        200: Updated library
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

    def update_lib():
        library_row = db(db.dg_shape_libraries.id == library_id).select().first()
        if not library_row:
            return None, "not_found"

        # Authorization: owner only
        if library_row.owner_identity_id != identity_id:
            return None, "forbidden"

        # Update fields
        now = datetime.now(UTC)
        update_dict = {"updated_at": now}

        if "name" in data:
            name = data.get("name", "").strip()
            if name:
                update_dict["name"] = name

        if "description" in data:
            update_dict["description"] = data.get("description")

        if "is_public" in data:
            update_dict["is_public"] = bool(data.get("is_public"))

        db(db.dg_shape_libraries.id == library_id).update(**update_dict)
        db.commit()

        return db(db.dg_shape_libraries.id == library_id).select().first(), "ok"

    library_row, status = await run_in_threadpool(update_lib)

    if status == "not_found":
        return ApiResponse.error("Library not found", 404)
    if status == "forbidden":
        return ApiResponse.error("Forbidden", 403)

    return (
        jsonify(
            {
                "id": library_row.id,
                "name": library_row.name,
                "description": library_row.description,
                "is_public": library_row.is_public,
                "owner_identity_id": library_row.owner_identity_id,
                "created_at": library_row.created_at.isoformat(),
                "updated_at": library_row.updated_at.isoformat(),
            }
        ),
        200,
    )


@bp.route("/<int:library_id>", methods=["DELETE"])
@login_required
@require_scope("diagrams:write")
async def delete_library(library_id: int):
    """Delete library (owner only).

    Cascades: dg_library_shapes CASCADE, shapes are deleted.

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

    def delete_lib():
        library_row = db(db.dg_shape_libraries.id == library_id).select().first()
        if not library_row:
            return None, "not_found"

        # Authorization: owner only
        if library_row.owner_identity_id != identity_id:
            return None, "forbidden"

        # Delete library (cascades shapes)
        db(db.dg_shape_libraries.id == library_id).delete()
        db.commit()

        return True, "ok"

    result, status = await run_in_threadpool(delete_lib)

    if status == "not_found":
        return ApiResponse.error("Library not found", 404)
    if status == "forbidden":
        return ApiResponse.error("Forbidden", 403)

    return "", 204


@bp.route("/<int:library_id>/shapes", methods=["POST"])
@login_required
@require_scope("diagrams:write")
async def add_shape(library_id: int):
    """Add a shape to a library (owner only).

    Requires: diagrams:write scope
    Authorization: library owner only

    Request body:
        {
            "name": "string (required)",
            "shape_type": "string (optional)",
            "default_width": int (optional),
            "default_height": int (optional),
            "svg_content": "string (optional, SVG markup)",
            "shape_meta": "JSON object (optional)"
        }

    Returns:
        201: Created shape
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
    name = data.get("name", "").strip()
    if not name:
        return ApiResponse.validation_error("name", "is required")

    def add_shape_tx():
        library_row = db(db.dg_shape_libraries.id == library_id).select().first()
        if not library_row:
            return None, "not_found"

        # Authorization: owner only
        if library_row.owner_identity_id != identity_id:
            return None, "forbidden"

        # Add shape to library
        now = datetime.now(UTC)
        shape_id = db.dg_library_shapes.insert(
            library_id=library_id,
            tenant_id=tenant_id,
            name=name,
            shape_type=data.get("shape_type"),
            default_width=data.get("default_width"),
            default_height=data.get("default_height"),
            svg_content=data.get("svg_content"),
            shape_meta=data.get("shape_meta"),
            created_at=now,
            updated_at=now,
        )
        db.commit()

        return db(db.dg_library_shapes.id == shape_id).select().first(), "ok"

    shape_row, status = await run_in_threadpool(add_shape_tx)

    if status == "not_found":
        return ApiResponse.error("Library not found", 404)
    if status == "forbidden":
        return ApiResponse.error("Forbidden", 403)

    return (
        jsonify(
            {
                "id": shape_row.id,
                "library_id": shape_row.library_id,
                "name": shape_row.name,
                "shape_type": shape_row.shape_type,
                "default_width": shape_row.default_width,
                "default_height": shape_row.default_height,
                "svg_content": shape_row.svg_content,
                "shape_meta": shape_row.shape_meta,
                "created_at": shape_row.created_at.isoformat(),
            }
        ),
        201,
    )


@bp.route("/<int:library_id>/shapes/<int:shape_id>", methods=["DELETE"])
@login_required
@require_scope("diagrams:write")
async def remove_shape(library_id: int, shape_id: int):
    """Remove a shape from a library (owner only).

    Requires: diagrams:write scope
    Authorization: library owner only

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

    def remove_shape_tx():
        library_row = db(db.dg_shape_libraries.id == library_id).select().first()
        if not library_row:
            return None, "library_not_found"

        # Authorization: owner only
        if library_row.owner_identity_id != identity_id:
            return None, "forbidden"

        # Verify shape belongs to this library
        shape_row = (
            db(
                (db.dg_library_shapes.id == shape_id)
                & (db.dg_library_shapes.library_id == library_id)
                & (db.dg_library_shapes.tenant_id == tenant_id)
            )
            .select()
            .first()
        )
        if not shape_row:
            return None, "shape_not_found"

        # Delete shape
        db(db.dg_library_shapes.id == shape_id).delete()
        db.commit()

        return True, "ok"

    result, status = await run_in_threadpool(remove_shape_tx)

    if status == "library_not_found":
        return ApiResponse.error("Library not found", 404)
    if status == "shape_not_found":
        return ApiResponse.error("Shape not found", 404)
    if status == "forbidden":
        return ApiResponse.error("Forbidden", 403)

    return "", 204
