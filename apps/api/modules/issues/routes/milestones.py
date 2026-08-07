"""Milestones management API endpoints for Elder using PyDAL with async/await."""

# flake8: noqa: E501


from dataclasses import asdict
from datetime import datetime, timezone

from quart import Blueprint, current_app, jsonify, request

from apps.api.auth.decorators import (
    login_required,
    require_scope,
    resource_role_required,
)
from apps.api.models.dataclasses import (
    MilestoneDTO,
    PaginatedResponse,
    from_pydal_row,
    from_pydal_rows,
)
from apps.api.modules.issues.routes.common import _tenant_id
from apps.api.utils.async_utils import run_in_threadpool
from apps.api.utils.pydal_helpers import PaginationParams

bp = Blueprint("milestones", __name__)


@bp.route("", methods=["GET"])
@login_required
@require_scope("issues:read")
async def list_milestones():
    """
    List milestones with optional filtering.

    Query Parameters:
        - organization_id: Filter by organization
        - project_id: Filter by project
        - status: Filter by status (open/closed)
        - page: Page number (default: 1)
        - per_page: Items per page (default: 50)
        - search: Search in title and description

    Returns:
        200: List of milestones with pagination
        400: Invalid parameters
        403: Tenant not found

    Example:
        GET /api/v1/milestones?organization_id=1&status=open
    """
    db = current_app.db

    tenant_id = _tenant_id()
    if not tenant_id:
        return jsonify({"error": "Tenant not found"}), 403

    # Get pagination params
    pagination = PaginationParams.from_request()

    # Build query
    def get_milestones():
        query = db.milestones.tenant_id == tenant_id

        # Apply filters
        if request.args.get("organization_id"):
            org_id = request.args.get("organization_id", type=int)
            query &= db.milestones.organization_id == org_id

        if request.args.get("project_id"):
            project_id = request.args.get("project_id", type=int)
            query &= db.milestones.project_id == project_id

        if request.args.get("status"):
            query &= db.milestones.status == request.args.get("status")

        if request.args.get("search"):
            search = request.args.get("search")
            search_pattern = f"%{search}%"
            query &= (db.milestones.title.ilike(search_pattern)) | (
                db.milestones.description.ilike(search_pattern)
            )

        # Get count and rows
        total = db(query).count()
        rows = db(query).select(
            orderby=~db.milestones.created_at,
            limitby=(pagination.offset, pagination.offset + pagination.per_page),
        )

        return total, rows

    total, rows = await run_in_threadpool(get_milestones)

    # Calculate total pages
    pages = pagination.calculate_pages(total)

    # Convert to DTOs
    items = from_pydal_rows(rows, MilestoneDTO)

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
async def create_milestone():
    """
    Create a new milestone.

    Requires viewer role on the resource.

    Request Body:
        {
            "title": "Beta Release",
            "description": "Complete beta version with all core features",
            "status": "open",
            "organization_id": 1,
            "project_id": 5,
            "due_date": "2024-06-30"
        }

    Returns:
        201: Milestone created
        400: Invalid request
        403: Insufficient permissions
        404: Organization not found

    Example:
        POST /api/v1/milestones
    """
    db = current_app.db

    tenant_id = _tenant_id()
    if not tenant_id:
        return jsonify({"error": "Tenant not found"}), 403

    data = await request.get_json()
    if not data:
        return jsonify({"error": "Request body must be JSON"}), 400

    # Validate required fields
    if not data.get("title"):
        return jsonify({"error": "title is required"}), 400
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
    # tenant, not merely exist (matches projects.py:create_project).
    if org.tenant_id != tenant_id:
        return jsonify({"error": "Organization not found"}), 404

    def create():
        # Create milestone
        now = datetime.now(timezone.utc)
        milestone_id = db.milestones.insert(
            title=data["title"],
            description=data.get("description"),
            status=data.get("status", "open"),
            organization_id=data["organization_id"],
            project_id=data.get("project_id"),
            due_date=data.get("due_date"),
            tenant_id=tenant_id,
            created_at=now,
            updated_at=now,
        )
        db.commit()

        return db(db.milestones.id == milestone_id).select().first()

    milestone = await run_in_threadpool(create)

    milestone_dto = from_pydal_row(milestone, MilestoneDTO)
    return jsonify(asdict(milestone_dto)), 201


@bp.route("/<int:id>", methods=["GET"])
@login_required
@require_scope("issues:read")
async def get_milestone(id: int):
    """
    Get a single milestone by ID.

    Path Parameters:
        - id: Milestone ID

    Returns:
        200: Milestone details
        403: Tenant not found
        404: Milestone not found

    Example:
        GET /api/v1/milestones/1
    """
    db = current_app.db

    tenant_id = _tenant_id()
    if not tenant_id:
        return jsonify({"error": "Tenant not found"}), 403

    milestone = await run_in_threadpool(
        lambda: db((db.milestones.id == id) & (db.milestones.tenant_id == tenant_id))
        .select()
        .first()
    )

    if not milestone:
        return jsonify({"error": "Milestone not found"}), 404

    milestone_dto = from_pydal_row(milestone, MilestoneDTO)
    return jsonify(asdict(milestone_dto)), 200


@bp.route("/<int:id>", methods=["PUT"])
@login_required
@require_scope("issues:write")
@resource_role_required("maintainer")
async def update_milestone(id: int):
    """
    Update a milestone.

    Requires maintainer role.

    Path Parameters:
        - id: Milestone ID

    Request Body:
        {
            "title": "Updated Milestone Title",
            "status": "closed"
        }

    Returns:
        200: Milestone updated
        400: Invalid request
        403: Insufficient permissions
        404: Milestone not found

    Example:
        PUT /api/v1/milestones/1
    """
    db = current_app.db

    tenant_id = _tenant_id()
    if not tenant_id:
        return jsonify({"error": "Tenant not found"}), 403

    data = await request.get_json()
    if not data:
        return jsonify({"error": "Request body must be JSON"}), 400

    # If organization is being changed, validate and get tenant
    if "organization_id" in data:

        def get_org():
            return db.organizations[data["organization_id"]]

        org = await run_in_threadpool(get_org)
        if not org:
            return jsonify({"error": "Organization not found"}), 404
        if not org.tenant_id:
            return jsonify({"error": "Organization must have a tenant"}), 400
        # Cross-tenant IDOR guard: re-pointing a milestone at another
        # tenant's org must not silently re-link it cross-tenant.
        if org.tenant_id != tenant_id:
            return jsonify({"error": "Organization not found"}), 404

    def update():
        milestone = (
            db((db.milestones.id == id) & (db.milestones.tenant_id == tenant_id))
            .select()
            .first()
        )
        if not milestone:
            return None

        # Update fields
        update_dict = {}
        if "title" in data:
            update_dict["title"] = data["title"]
        if "description" in data:
            update_dict["description"] = data["description"]
        if "status" in data:
            update_dict["status"] = data["status"]
            # Set closed_at when closing
            if data["status"] == "closed":
                update_dict["closed_at"] = datetime.now(timezone.utc)
        if "project_id" in data:
            update_dict["project_id"] = data["project_id"]
        if "due_date" in data:
            update_dict["due_date"] = data["due_date"]
        if "closed_at" in data:
            update_dict["closed_at"] = data["closed_at"]
        if "organization_id" in data:
            update_dict["organization_id"] = data["organization_id"]

        if update_dict:
            db(
                (db.milestones.id == id) & (db.milestones.tenant_id == tenant_id)
            ).update(**update_dict)
            db.commit()

        return (
            db((db.milestones.id == id) & (db.milestones.tenant_id == tenant_id))
            .select()
            .first()
        )

    milestone = await run_in_threadpool(update)

    if not milestone:
        return jsonify({"error": "Milestone not found"}), 404

    milestone_dto = from_pydal_row(milestone, MilestoneDTO)
    return jsonify(asdict(milestone_dto)), 200


@bp.route("/<int:id>", methods=["DELETE"])
@login_required
@require_scope("issues:write")
@resource_role_required("maintainer")
async def delete_milestone(id: int):
    """
    Delete a milestone.

    Requires maintainer role.

    Path Parameters:
        - id: Milestone ID

    Returns:
        204: Milestone deleted
        403: Insufficient permissions
        404: Milestone not found

    Example:
        DELETE /api/v1/milestones/1
    """
    db = current_app.db

    tenant_id = _tenant_id()
    if not tenant_id:
        return jsonify({"error": "Tenant not found"}), 403

    def delete():
        milestone = (
            db((db.milestones.id == id) & (db.milestones.tenant_id == tenant_id))
            .select()
            .first()
        )
        if not milestone:
            return False

        db((db.milestones.id == id) & (db.milestones.tenant_id == tenant_id)).delete()
        db.commit()
        return True

    success = await run_in_threadpool(delete)

    if not success:
        return jsonify({"error": "Milestone not found"}), 404

    return "", 204


@bp.route("/<int:id>/issues", methods=["GET"])
@login_required
@require_scope("issues:read")
async def get_milestone_issues(id: int):
    """
    Get all issues linked to a milestone.

    Path Parameters:
        - id: Milestone ID

    Returns:
        200: List of issues
        403: Tenant not found
        404: Milestone not found

    Example:
        GET /api/v1/milestones/1/issues
    """
    db = current_app.db

    tenant_id = _tenant_id()
    if not tenant_id:
        return jsonify({"error": "Tenant not found"}), 403

    def get_issues():
        milestone = (
            db((db.milestones.id == id) & (db.milestones.tenant_id == tenant_id))
            .select()
            .first()
        )
        if not milestone:
            return None, []

        # Get issue-milestone links
        links = db(db.issue_milestone_links.milestone_id == id).select()
        issue_ids = [link.issue_id for link in links]

        if not issue_ids:
            return milestone, []

        # Get issues, additionally scoped to the caller's tenant so a
        # milestone can never surface another tenant's issues even if the
        # link table itself were ever populated cross-tenant.
        issues = db(
            (db.issues.id.belongs(issue_ids)) & (db.issues.tenant_id == tenant_id)
        ).select()
        return milestone, issues

    milestone, issues = await run_in_threadpool(get_issues)

    if milestone is None:
        return jsonify({"error": "Milestone not found"}), 404

    from apps.api.models.dataclasses import IssueDTO

    issues_dto = from_pydal_rows(issues, IssueDTO)

    return jsonify({"issues": [asdict(issue) for issue in issues_dto]}), 200
