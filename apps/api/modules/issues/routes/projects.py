"""Projects management API endpoints for Elder using PyDAL with async/await."""

# flake8: noqa: E501

from dataclasses import asdict
from datetime import UTC, datetime, timezone

from quart import Blueprint, current_app, jsonify, request

from apps.api.auth.decorators import (
    login_required,
    require_scope,
    resource_role_required,
)
from apps.api.models.dataclasses import (
    PaginatedResponse,
    ProjectDTO,
    from_pydal_row,
    from_pydal_rows,
)
from apps.api.modules.issues.routes.common import _tenant_id
from apps.api.utils.async_utils import run_in_threadpool
from apps.api.utils.pydal_helpers import PaginationParams

bp = Blueprint("projects", __name__)


@bp.route("", methods=["GET"])
@login_required
@require_scope("issues:read")
async def list_projects():
    """
    List projects with optional filtering.

    Query Parameters:
        - organization_id: Filter by organization
        - status: Filter by status (active/completed/archived)
        - page: Page number (default: 1)
        - per_page: Items per page (default: 50)
        - search: Search in name and description

    Returns:
        200: List of projects with pagination
        400: Invalid parameters

    Example:
        GET /api/v1/projects?organization_id=1&status=active
    """
    db = current_app.db

    tenant_id = _tenant_id()
    if not tenant_id:
        return jsonify({"error": "Tenant not found"}), 403

    # Get pagination params
    pagination = PaginationParams.from_request()

    # Build query
    def get_projects():
        query = db.projects.tenant_id == tenant_id

        # Apply filters
        if request.args.get("organization_id"):
            org_id = request.args.get("organization_id", type=int)
            query &= db.projects.organization_id == org_id

        if request.args.get("status"):
            query &= db.projects.status == request.args.get("status")

        if request.args.get("search"):
            search = request.args.get("search")
            search_pattern = f"%{search}%"
            query &= (db.projects.name.ilike(search_pattern)) | (
                db.projects.description.ilike(search_pattern)
            )

        # Get count and rows
        total = db(query).count()
        rows = db(query).select(
            orderby=~db.projects.created_at,
            limitby=(pagination.offset, pagination.offset + pagination.per_page),
        )

        return total, rows

    total, rows = await run_in_threadpool(get_projects)

    # Calculate total pages
    pages = pagination.calculate_pages(total)

    # Convert to DTOs
    items = from_pydal_rows(rows, ProjectDTO)

    # Create paginated response
    response = PaginatedResponse(
        items=[asdict(item) for item in items],
        total=total,
        page=pagination.page,
        per_page=pagination.per_page,
        pages=pages,
    )

    return jsonify(asdict(response)), 200


@bp.route("", methods=["POST"])
@login_required
@require_scope("issues:write")
async def create_project():
    """
    Create a new project.

    Requires viewer role on the resource.

    Request Body:
        {
            "name": "Q1 2024 Infrastructure Upgrade",
            "description": "Upgrade all servers to latest LTS versions",
            "status": "active",
            "organization_id": 1,
            "start_date": "2024-01-01",
            "end_date": "2024-03-31"
        }

    Returns:
        201: Project created
        400: Invalid request
        403: Insufficient permissions

    Example:
        POST /api/v1/projects
    """
    db = current_app.db

    tenant_id = _tenant_id()
    if not tenant_id:
        return jsonify({"error": "Tenant not found"}), 403

    data = await request.get_json()
    if not data:
        return jsonify({"error": "Request body must be JSON"}), 400

    # Validate required fields
    if not data.get("name"):
        return jsonify({"error": "name is required"}), 400
    if not data.get("organization_id"):
        return jsonify({"error": "organization_id is required"}), 400

    # Get organization to derive tenant_id
    def get_org():
        return db.organizations[data["organization_id"]]

    org = await run_in_threadpool(get_org)
    if not org:
        return jsonify({"error": "Organization not found"}), 404
    if not org.tenant_id:
        return jsonify({"error": "Organization must have a tenant"}), 400
    # Cross-tenant IDOR guard: the org must belong to the caller's own
    # tenant, not merely exist (matches issues.py:create_issue).
    if org.tenant_id != tenant_id:
        return jsonify({"error": "Organization not found"}), 404

    def create():
        # Create project
        now = datetime.now(UTC)
        project_id = db.projects.insert(
            name=data["name"],
            description=data.get("description"),
            status=data.get("status", "active"),
            organization_id=data["organization_id"],
            tenant_id=tenant_id,
            created_at=now,
            updated_at=now,
        )
        db.commit()

        return db(db.projects.id == project_id).select().first()

    project = await run_in_threadpool(create)

    project_dto = from_pydal_row(project, ProjectDTO)
    return jsonify(asdict(project_dto)), 201


@bp.route("/<int:id>", methods=["GET"])
@login_required
@require_scope("issues:read")
async def get_project(id: int):
    """
    Get a single project by ID.

    Path Parameters:
        - id: Project ID

    Returns:
        200: Project details
        404: Project not found

    Example:
        GET /api/v1/projects/1
    """
    db = current_app.db

    tenant_id = _tenant_id()
    if not tenant_id:
        return jsonify({"error": "Tenant not found"}), 403

    project = await run_in_threadpool(
        lambda: (
            db((db.projects.id == id) & (db.projects.tenant_id == tenant_id))
            .select()
            .first()
        )
    )

    if not project:
        return jsonify({"error": "Project not found"}), 404

    project_dto = from_pydal_row(project, ProjectDTO)
    return jsonify(asdict(project_dto)), 200


@bp.route("/<int:id>", methods=["PUT"])
@login_required
@require_scope("issues:write")
@resource_role_required("maintainer")
async def update_project(id: int):
    """
    Update a project.

    Requires maintainer role.

    Path Parameters:
        - id: Project ID

    Request Body:
        {
            "name": "Updated Project Name",
            "status": "completed"
        }

    Returns:
        200: Project updated
        400: Invalid request
        403: Insufficient permissions
        404: Project not found

    Example:
        PUT /api/v1/projects/1
    """
    db = current_app.db

    tenant_id = _tenant_id()
    if not tenant_id:
        return jsonify({"error": "Tenant not found"}), 403

    data = await request.get_json()
    if not data:
        return jsonify({"error": "Request body must be JSON"}), 400

    # Validate organization if being changed
    if "organization_id" in data:

        def get_org():
            return db.organizations[data["organization_id"]]

        org = await run_in_threadpool(get_org)
        if not org:
            return jsonify({"error": "Organization not found"}), 404
        # Cross-tenant IDOR guard: re-pointing a project at another
        # tenant's org must not silently re-link it cross-tenant.
        if org.tenant_id != tenant_id:
            return jsonify({"error": "Organization not found"}), 404

    def update():
        project = (
            db((db.projects.id == id) & (db.projects.tenant_id == tenant_id))
            .select()
            .first()
        )
        if not project:
            return None

        # Update fields (only those that exist in DB schema)
        update_dict = {}
        if "name" in data:
            update_dict["name"] = data["name"]
        if "description" in data:
            update_dict["description"] = data["description"]
        if "status" in data:
            update_dict["status"] = data["status"]
        if "organization_id" in data:
            update_dict["organization_id"] = data["organization_id"]

        if update_dict:
            db((db.projects.id == id) & (db.projects.tenant_id == tenant_id)).update(
                **update_dict
            )
            db.commit()

        return (
            db((db.projects.id == id) & (db.projects.tenant_id == tenant_id))
            .select()
            .first()
        )

    project = await run_in_threadpool(update)

    if not project:
        return jsonify({"error": "Project not found"}), 404

    project_dto = from_pydal_row(project, ProjectDTO)
    return jsonify(asdict(project_dto)), 200


@bp.route("/<int:id>", methods=["DELETE"])
@login_required
@require_scope("issues:write")
@resource_role_required("maintainer")
async def delete_project(id: int):
    """
    Delete a project.

    Requires maintainer role.

    Path Parameters:
        - id: Project ID

    Returns:
        204: Project deleted
        403: Insufficient permissions
        404: Project not found

    Example:
        DELETE /api/v1/projects/1
    """
    db = current_app.db

    tenant_id = _tenant_id()
    if not tenant_id:
        return jsonify({"error": "Tenant not found"}), 403

    def delete():
        project = (
            db((db.projects.id == id) & (db.projects.tenant_id == tenant_id))
            .select()
            .first()
        )
        if not project:
            return False

        db((db.projects.id == id) & (db.projects.tenant_id == tenant_id)).delete()
        db.commit()
        return True

    success = await run_in_threadpool(delete)

    if not success:
        return jsonify({"error": "Project not found"}), 404

    return "", 204
