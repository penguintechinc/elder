"""Diagrams CRUD and version management endpoints using penguin-dal."""

# flake8: noqa: E501

import logging
from datetime import UTC, datetime, timezone

from quart import Blueprint, current_app, g, jsonify, request

from apps.api.auth.decorators import login_required, require_scope
from apps.api.common.licensing.enforce import check_limit
from apps.api.logging_config import log_error_and_respond
from apps.api.utils.api_responses import ApiResponse
from apps.api.utils.async_utils import run_in_threadpool
from apps.api.utils.pydal_helpers import PaginationParams
from shared.utils.village_id import generate_village_id

logger = logging.getLogger(__name__)

bp = Blueprint("diagrams", __name__)


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


def _can_read_diagram(db, diagram_row, tenant_id, identity_id=None) -> bool:
    """Check if current user can read this diagram based on visibility.

    Args:
        db: PyDAL database instance
        diagram_row: Diagram row from database
        tenant_id: Current tenant ID
        identity_id: Current identity ID (optional, from token)

    Returns:
        True if user can read, False otherwise
    """
    # Cross-tenant isolation: diagram must belong to this tenant
    if diagram_row.tenant_id != tenant_id:
        return False

    # Public diagrams visible to everyone
    if diagram_row.is_public:
        return True

    # Unauthenticated users can't read anything else
    if identity_id is None:
        return False

    # Owner can always read their own diagram
    if diagram_row.owner_identity_id == identity_id:
        return True

    # Check shared access: query dg_shares table
    share_row = (
        db(
            (db.dg_shares.diagram_id == diagram_row.id)
            & (db.dg_shares.shared_with_identity_id == identity_id)
        )
        .select()
        .first()
    )
    if share_row:
        return True

    return False


def _can_edit_diagram(db, diagram_row, tenant_id, identity_id=None) -> bool:
    """Write authorization for a diagram — STRICTER than read.

    Only the owner, or a user holding an ``editor``-permission share, may
    mutate a diagram. Public visibility and ``viewer`` shares grant read only;
    gating writes on read-ability would let any reader of a public diagram (or
    a viewer-shared user) edit or delete it.

    Args:
        db: PyDAL database instance
        diagram_row: Diagram row from database
        tenant_id: Current tenant ID
        identity_id: Current identity ID (from token)

    Returns:
        True if the caller may modify this diagram, False otherwise.
    """
    # Cross-tenant isolation.
    if diagram_row.tenant_id != tenant_id:
        return False

    if identity_id is None:
        return False

    # Owner always has write access.
    if diagram_row.owner_identity_id == identity_id:
        return True

    # Editor-permission share grants write; viewer shares and is_public do not.
    share_row = (
        db(
            (db.dg_shares.diagram_id == diagram_row.id)
            & (db.dg_shares.shared_with_identity_id == identity_id)
            & (db.dg_shares.permission == "editor")
        )
        .select()
        .first()
    )
    return bool(share_row)


@bp.route("", methods=["GET"])
@login_required
@require_scope("diagrams:read")
async def list_diagrams():
    """List diagrams with optional filtering and pagination.

    Query Parameters:
        - page: Page number (default: 1)
        - per_page: Items per page (default: 20, max: 100)
        - status: Filter by status (draft, active, archived)
        - q: Full-text search query on title
        - is_template: Filter templates (true/false)

    Returns:
        200: Paginated list of diagrams
    """
    db = current_app.db
    tenant_id = _get_tenant_id()

    if not tenant_id:
        return ApiResponse.error("Tenant not found", 403)

    # Extract pagination params and filters BEFORE threadpool
    pagination = PaginationParams.from_request()
    status_filter = request.args.get("status")
    q = request.args.get("q", "").strip()
    is_template = request.args.get("is_template")

    claims = getattr(g, "claims", {}) or {}
    identity_id = claims.get("identity_id")

    def list_dgms():
        # Build base query scoped to tenant
        query = db.dg_diagrams.tenant_id == tenant_id

        # Status filter
        if status_filter:
            query &= db.dg_diagrams.status == status_filter

        # Template filter
        if is_template is not None:
            is_template_bool = is_template.lower() == "true"
            query &= db.dg_diagrams.is_template == is_template_bool

        # Full-text search on title
        if q:
            query &= db.dg_diagrams.title.like(f"%{q}%")

        total = db(query).count()
        rows = db(query).select(
            orderby=~db.dg_diagrams.created_at,
            limitby=(pagination.offset, pagination.offset + pagination.per_page),
        )

        return total, rows

    total, rows = await run_in_threadpool(list_dgms)

    # Post-filter by visibility
    diagrams = []
    for r in rows:
        if _can_read_diagram(db, r, tenant_id, identity_id):
            diagrams.append(
                {
                    "id": r.id,
                    "village_id": r.village_id,
                    "title": r.title,
                    "description": r.description,
                    "status": r.status,
                    "is_public": r.is_public,
                    "is_template": r.is_template,
                    "tags": r.tags or [],
                    "thumbnail_url": r.thumbnail_url,
                    "owner_identity_id": r.owner_identity_id,
                    "created_at": r.created_at.isoformat(),
                    "updated_at": r.updated_at.isoformat(),
                }
            )

    return (
        jsonify(
            {
                "items": diagrams,
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
async def create_diagram():
    """Create a new diagram.

    Requires: diagrams:write scope

    Request body:
        {
            "title": "string (required)",
            "description": "optional description",
            "tags": ["array of tags (optional)"],
            "status": "draft|active|archived (default: draft)",
            "is_public": false (default),
            "is_template": false (default),
            "content": {nodes, edges dict (optional)}
        }

    Returns:
        201: Created diagram
    """
    db = current_app.db
    tenant_id = _get_tenant_id()

    if not tenant_id:
        return ApiResponse.error("Tenant not found", 403)

    blocked = await check_limit("object", tenant_id)
    if blocked is not None:
        return blocked

    data = await request.get_json() or {}

    # Validate required fields
    title = data.get("title", "").strip()
    if not title:
        return ApiResponse.validation_error("title", "is required")

    # Get current identity from token
    claims = getattr(g, "claims", {}) or {}
    identity_id = claims.get("identity_id")
    if not identity_id:
        return ApiResponse.error("Identity not found in token", 403)

    # Mint the village_id in request context (generate_village_id needs current_app.redis_client)
    village_id = generate_village_id(tenant_id, current_app.redis_client)

    def create():
        now = datetime.now(UTC)

        # Insert diagram
        diagram_id = db.dg_diagrams.insert(
            tenant_id=tenant_id,
            village_id=village_id,
            title=title,
            description=data.get("description"),
            owner_identity_id=identity_id,
            created_by_identity_id=identity_id,
            updated_by_identity_id=identity_id,
            is_public=data.get("is_public", False),
            is_template=data.get("is_template", False),
            status=data.get("status", "draft"),
            tags=data.get("tags", []),
            thumbnail_url=None,
            created_at=now,
            updated_at=now,
        )
        db.commit()

        # If initial content provided, create version_number=1
        content = data.get("content")
        if content:
            db.dg_diagram_versions.insert(
                diagram_id=diagram_id,
                tenant_id=tenant_id,
                version_number=1,
                created_by_identity_id=identity_id,
                content_json=content,
                change_summary="Initial version",
                created_at=now,
            )
            db.commit()

        # Fetch and return created diagram
        return db(db.dg_diagrams.id == diagram_id).select().first()

    result = await run_in_threadpool(create)

    if not result:
        return ApiResponse.error("Failed to create diagram", 500)

    diagram = result

    return (
        jsonify(
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
                "created_at": diagram.created_at.isoformat(),
                "updated_at": diagram.updated_at.isoformat(),
            }
        ),
        201,
    )


@bp.route("/<int:diagram_id>", methods=["GET"])
@login_required
@require_scope("diagrams:read")
async def get_diagram(diagram_id):
    """Get a diagram by ID with latest version content.

    Returns:
        200: Diagram details with latest version content_json
        404: Diagram not found
    """
    db = current_app.db
    tenant_id = _get_tenant_id()

    if not tenant_id:
        return ApiResponse.error("Tenant not found", 403)

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
            return (None, None)

        # Fetch latest version
        latest_version = (
            db(db.dg_diagram_versions.diagram_id == diagram_id)
            .select(orderby=~db.dg_diagram_versions.version_number, limitby=(0, 1))
            .first()
        )

        return (diagram, latest_version)

    diagram, latest_version = await run_in_threadpool(fetch)

    # Return 404 for both "does not exist" and "not visible to caller"
    if not diagram or not _can_read_diagram(db, diagram, tenant_id, identity_id):
        return ApiResponse.not_found("Diagram")

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
            "owner_identity_id": diagram.owner_identity_id,
            "content": content_json or {"nodes": [], "edges": []},
            "created_at": diagram.created_at.isoformat(),
            "updated_at": diagram.updated_at.isoformat(),
        }
    )


@bp.route("/<int:diagram_id>", methods=["PATCH"])
@login_required
@require_scope("diagrams:write")
async def update_diagram(diagram_id):
    """Update diagram metadata (not content).

    Requires: diagrams:write scope

    Request body can include:
        {
            "title": "new title",
            "description": "new description",
            "status": "draft|active|archived",
            "tags": ["new tags"],
            "is_public": true/false,
            "is_template": true/false,
            "thumbnail_url": "url"
        }

    Returns:
        200: Updated diagram
        404: Diagram not found
    """
    db = current_app.db
    tenant_id = _get_tenant_id()

    if not tenant_id:
        return ApiResponse.error("Tenant not found", 403)

    data = await request.get_json() or {}

    claims = getattr(g, "claims", {}) or {}
    identity_id = claims.get("identity_id")

    def update():
        diagram = (
            db(
                (db.dg_diagrams.id == diagram_id)
                & (db.dg_diagrams.tenant_id == tenant_id)
            )
            .select()
            .first()
        )

        if not diagram:
            return None

        # Write authorization: only owner or an editor-permission share may modify.
        if not _can_edit_diagram(db, diagram, tenant_id, identity_id):
            return None

        now = datetime.now(UTC)
        updates = {"updated_at": now, "updated_by_identity_id": identity_id}

        if "title" in data:
            updates["title"] = data["title"]
        if "description" in data:
            updates["description"] = data["description"]
        if "status" in data:
            updates["status"] = data["status"]
        if "tags" in data:
            updates["tags"] = data["tags"]
        if "is_public" in data:
            updates["is_public"] = data["is_public"]
        if "is_template" in data:
            updates["is_template"] = data["is_template"]
        if "thumbnail_url" in data:
            updates["thumbnail_url"] = data["thumbnail_url"]

        db(db.dg_diagrams.id == diagram_id).update(**updates)
        db.commit()

        return db(db.dg_diagrams.id == diagram_id).select().first()

    diagram = await run_in_threadpool(update)

    if not diagram:
        return ApiResponse.not_found("Diagram")

    return jsonify(
        {
            "id": diagram.id,
            "title": diagram.title,
            "description": diagram.description,
            "status": diagram.status,
            "is_public": diagram.is_public,
            "is_template": diagram.is_template,
            "tags": diagram.tags or [],
            "updated_at": diagram.updated_at.isoformat(),
        }
    )


@bp.route("/<int:diagram_id>", methods=["DELETE"])
@login_required
@require_scope("diagrams:write")
async def delete_diagram(diagram_id):
    """Delete a diagram and cascade delete its versions.

    Requires: diagrams:write scope

    Returns:
        204: Diagram deleted
        404: Diagram not found
    """
    db = current_app.db
    tenant_id = _get_tenant_id()

    if not tenant_id:
        return ApiResponse.error("Tenant not found", 403)

    claims = getattr(g, "claims", {}) or {}
    identity_id = claims.get("identity_id")

    def delete():
        diagram = (
            db(
                (db.dg_diagrams.id == diagram_id)
                & (db.dg_diagrams.tenant_id == tenant_id)
            )
            .select()
            .first()
        )

        if not diagram:
            return False

        # Write authorization: only owner or an editor-permission share may delete.
        if not _can_edit_diagram(db, diagram, tenant_id, identity_id):
            return False

        # Cascade delete versions (dg_diagram_versions has FK with CASCADE)
        # Cascade delete shapes/connectors if they exist
        db(db.dg_diagram_versions.diagram_id == diagram_id).delete()
        db(db.dg_shapes.diagram_id == diagram_id).delete()
        db(db.dg_connectors.diagram_id == diagram_id).delete()

        # Delete diagram
        db(db.dg_diagrams.id == diagram_id).delete()
        db.commit()
        return True

    deleted = await run_in_threadpool(delete)

    if not deleted:
        return ApiResponse.not_found("Diagram")

    return "", 204


@bp.route("/<int:diagram_id>/versions", methods=["POST"])
@login_required
@require_scope("diagrams:write")
async def save_version(diagram_id):
    """Save a new version of diagram content (nodes + edges).

    Requires: diagrams:write scope

    Request body:
        {
            "content": {nodes, edges dict (required)},
            "change_summary": "Description of changes (optional)"
        }

    Returns:
        201: Created version
        404: Diagram not found
    """
    db = current_app.db
    tenant_id = _get_tenant_id()

    if not tenant_id:
        return ApiResponse.error("Tenant not found", 403)

    data = await request.get_json() or {}

    # Validate required content
    content = data.get("content")
    if content is None:
        return ApiResponse.validation_error("content", "is required")

    claims = getattr(g, "claims", {}) or {}
    identity_id = claims.get("identity_id")
    if not identity_id:
        return ApiResponse.error("Identity not found in token", 403)

    # Pre-check authorization before threadpool
    pre_diagram = await run_in_threadpool(
        lambda: (
            db(
                (db.dg_diagrams.id == diagram_id)
                & (db.dg_diagrams.tenant_id == tenant_id)
            )
            .select()
            .first()
        )
    )
    if pre_diagram is None:
        return ApiResponse.not_found("Diagram")
    if not _can_edit_diagram(db, pre_diagram, tenant_id, identity_id):
        return ApiResponse.not_found("Diagram")

    def save():
        # Fetch diagram
        diagram = (
            db(
                (db.dg_diagrams.id == diagram_id)
                & (db.dg_diagrams.tenant_id == tenant_id)
            )
            .select()
            .first()
        )

        if not diagram:
            return None

        now = datetime.now(UTC)

        # Get next version number
        max_version = (
            db(db.dg_diagram_versions.diagram_id == diagram_id)
            .select(orderby=~db.dg_diagram_versions.version_number, limitby=(0, 1))
            .first()
        )
        next_version_number = (max_version.version_number + 1) if max_version else 1

        # Insert new version
        version_id = db.dg_diagram_versions.insert(
            diagram_id=diagram_id,
            tenant_id=tenant_id,
            version_number=next_version_number,
            created_by_identity_id=identity_id,
            content_json=content,
            change_summary=data.get("change_summary"),
            created_at=now,
        )
        db.commit()

        # Update diagram's updated_at and updated_by
        db(db.dg_diagrams.id == diagram_id).update(
            updated_at=now, updated_by_identity_id=identity_id
        )
        db.commit()

        return db(db.dg_diagram_versions.id == version_id).select().first()

    version = await run_in_threadpool(save)

    if not version:
        return ApiResponse.error("Failed to save version", 500)

    return (
        jsonify(
            {
                "id": version.id,
                "diagram_id": diagram_id,
                "version_number": version.version_number,
                "content": version.content_json,
                "change_summary": version.change_summary,
                "created_by_identity_id": version.created_by_identity_id,
                "created_at": version.created_at.isoformat(),
            }
        ),
        201,
    )


@bp.route("/<int:diagram_id>/versions", methods=["GET"])
@login_required
@require_scope("diagrams:read")
async def list_versions(diagram_id):
    """List all versions of a diagram.

    Requires: diagrams:read scope

    Returns:
        200: Paginated list of versions
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
        # Verify diagram exists and belongs to tenant
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

        # Fetch versions
        total = db(db.dg_diagram_versions.diagram_id == diagram_id).count()
        rows = db(db.dg_diagram_versions.diagram_id == diagram_id).select(
            orderby=~db.dg_diagram_versions.version_number,
            limitby=(pagination.offset, pagination.offset + pagination.per_page),
        )

        return (diagram, total, rows)

    diagram, total, rows = await run_in_threadpool(fetch)

    if diagram is None:
        return ApiResponse.not_found("Diagram")

    if not _can_read_diagram(db, diagram, tenant_id, identity_id):
        return ApiResponse.not_found("Diagram")

    versions = [
        {
            "id": r.id,
            "version_number": r.version_number,
            "change_summary": r.change_summary,
            "created_by_identity_id": r.created_by_identity_id,
            "created_at": r.created_at.isoformat(),
        }
        for r in rows
    ]

    return (
        jsonify(
            {
                "diagram_id": diagram_id,
                "versions": versions,
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


@bp.route("/<int:diagram_id>/versions/<int:version_number>", methods=["GET"])
@login_required
@require_scope("diagrams:read")
async def get_version(diagram_id, version_number):
    """Get a specific version of a diagram.

    Requires: diagrams:read scope

    Returns:
        200: Version details with content_json
        404: Diagram or version not found
    """
    db = current_app.db
    tenant_id = _get_tenant_id()

    if not tenant_id:
        return ApiResponse.error("Tenant not found", 403)

    claims = getattr(g, "claims", {}) or {}
    identity_id = claims.get("identity_id")

    def fetch():
        # Verify diagram exists and belongs to tenant
        diagram = (
            db(
                (db.dg_diagrams.id == diagram_id)
                & (db.dg_diagrams.tenant_id == tenant_id)
            )
            .select()
            .first()
        )
        if not diagram:
            return (None, None)

        # Fetch specific version
        version = (
            db(
                (db.dg_diagram_versions.diagram_id == diagram_id)
                & (db.dg_diagram_versions.version_number == version_number)
            )
            .select()
            .first()
        )

        return (diagram, version)

    diagram, version = await run_in_threadpool(fetch)

    if diagram is None:
        return ApiResponse.not_found("Diagram")

    if not _can_read_diagram(db, diagram, tenant_id, identity_id):
        return ApiResponse.not_found("Diagram")

    if version is None:
        return ApiResponse.not_found("Version")

    return jsonify(
        {
            "id": version.id,
            "diagram_id": diagram_id,
            "version_number": version.version_number,
            "content": version.content_json,
            "change_summary": version.change_summary,
            "created_by_identity_id": version.created_by_identity_id,
            "created_at": version.created_at.isoformat(),
        }
    )


@bp.route("/<int:diagram_id>/versions/<int:version_number>/restore", methods=["POST"])
@login_required
@require_scope("diagrams:write")
async def restore_version(diagram_id, version_number):
    """Restore a diagram from a specific version.

    Creates a new version from the snapshot. The old version snapshot is never modified.

    Requires: diagrams:write scope

    Returns:
        200: Restored diagram with new version number
        404: Diagram or version not found
    """
    db = current_app.db
    tenant_id = _get_tenant_id()

    if not tenant_id:
        return ApiResponse.error("Tenant not found", 403)

    claims = getattr(g, "claims", {}) or {}
    identity_id = claims.get("identity_id")
    if not identity_id:
        return ApiResponse.error("Identity not found in token", 403)

    # Pre-check authorization before threadpool
    pre_diagram = await run_in_threadpool(
        lambda: (
            db(
                (db.dg_diagrams.id == diagram_id)
                & (db.dg_diagrams.tenant_id == tenant_id)
            )
            .select()
            .first()
        )
    )
    if pre_diagram is None:
        return ApiResponse.not_found("Diagram")
    if not _can_edit_diagram(db, pre_diagram, tenant_id, identity_id):
        return ApiResponse.not_found("Diagram")

    def restore():
        # Verify diagram exists and belongs to tenant
        diagram = (
            db(
                (db.dg_diagrams.id == diagram_id)
                & (db.dg_diagrams.tenant_id == tenant_id)
            )
            .select()
            .first()
        )
        if not diagram:
            return (None, None, None)

        # Fetch source version
        source_version = (
            db(
                (db.dg_diagram_versions.diagram_id == diagram_id)
                & (db.dg_diagram_versions.version_number == version_number)
            )
            .select()
            .first()
        )
        if not source_version:
            return (None, None, None)

        now = datetime.now(UTC)

        # Create a new version from source content (restore creates new version)
        max_version = (
            db(db.dg_diagram_versions.diagram_id == diagram_id)
            .select(orderby=~db.dg_diagram_versions.version_number, limitby=(0, 1))
            .first()
        )
        new_version_number = (max_version.version_number + 1) if max_version else 1

        db.dg_diagram_versions.insert(
            diagram_id=diagram_id,
            tenant_id=tenant_id,
            version_number=new_version_number,
            created_by_identity_id=identity_id,
            content_json=source_version.content_json,
            change_summary=f"Restored from version {version_number}",
            created_at=now,
        )

        # Update diagram's updated_at
        db(db.dg_diagrams.id == diagram_id).update(
            updated_at=now, updated_by_identity_id=identity_id
        )
        db.commit()

        # Fetch updated diagram
        updated_diagram = db(db.dg_diagrams.id == diagram_id).select().first()

        return (updated_diagram, new_version_number, source_version)

    diagram, new_version_num, source_version = await run_in_threadpool(restore)

    if diagram is None:
        return ApiResponse.not_found("Diagram")

    if source_version is None:
        return ApiResponse.not_found("Version")

    return jsonify(
        {
            "id": diagram.id,
            "title": diagram.title,
            "status": diagram.status,
            "restored_from_version": version_number,
            "new_version_number": new_version_num,
            "updated_at": diagram.updated_at.isoformat(),
        }
    )
