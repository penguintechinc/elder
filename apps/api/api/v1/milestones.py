"""Milestones management API endpoints for Elder using PyDAL with async/await."""

# flake8: noqa: E501


from dataclasses import asdict
from datetime import datetime, timezone

from flask import Blueprint, current_app, jsonify, request

from apps.api.auth.decorators import login_required, resource_role_required
from apps.api.models.dataclasses import (
    MilestoneDTO,
    PaginatedResponse,
    from_pydal_row,
    from_pydal_rows,
)
from apps.api.utils.async_utils import run_in_threadpool

bp = Blueprint("milestones", __name__)


@bp.route("", methods=["GET"])
@login_required
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

    Example:
        GET /api/v1/milestones?organization_id=1&status=open
    """
    db = current_app.db

    # Get pagination params
    page = request.args.get("page", 1, type=int)
    per_page = min(request.args.get("per_page", 50, type=int), 1000)

    # Build query
    def get_milestones():
        query = db.milestones.id > 0

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

        # Calculate pagination
        offset = (page - 1) * per_page

        # Get count and rows
        total = db(query).count()
        rows = db(query).select(
            orderby=~db.milestones.created_at, limitby=(offset, offset + per_page)
        )

        return total, rows

    total, rows = await run_in_threadpool(get_milestones)

    # Calculate total pages
    pages = (total + per_page - 1) // per_page if total > 0 else 0

    # Convert to DTOs
    items = from_pydal_rows(rows, MilestoneDTO)

    # Create paginated response
    response = PaginatedResponse(
        items=[asdict(item) for item in items],
        total=total,
        page=page,
        per_page=per_page,
        pages=pages,
    )

    return jsonify(asdict(response)), 200


@bp.route("", methods=["POST"])
@login_required
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

    Example:
        POST /api/v1/milestones
    """
    db = current_app.db

    data = request.get_json()
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

    def create():
        # Create milestone
        milestone_id = db.milestones.insert(
            title=data["title"],
            description=data.get("description"),
            status=data.get("status", "open"),
            organization_id=data["organization_id"],
            tenant_id=org.tenant_id,
            project_id=data.get("project_id"),
            due_date=data.get("due_date"),
        )
        db.commit()

        return db.milestones[milestone_id]

    milestone = await run_in_threadpool(create)

    milestone_dto = from_pydal_row(milestone, MilestoneDTO)
    return jsonify(asdict(milestone_dto)), 201


@bp.route("/<int:id>", methods=["GET"])
@login_required
async def get_milestone(id: int):
    """
    Get a single milestone by ID.

    Path Parameters:
        - id: Milestone ID

    Returns:
        200: Milestone details
        404: Milestone not found

    Example:
        GET /api/v1/milestones/1
    """
    db = current_app.db

    milestone = await run_in_threadpool(lambda: db.milestones[id])

    if not milestone:
        return jsonify({"error": "Milestone not found"}), 404

    milestone_dto = from_pydal_row(milestone, MilestoneDTO)
    return jsonify(asdict(milestone_dto)), 200


@bp.route("/<int:id>", methods=["PUT"])
@login_required
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

    data = request.get_json()
    if not data:
        return jsonify({"error": "Request body must be JSON"}), 400

    # If organization is being changed, validate and get tenant
    org_tenant_id = None
    if "organization_id" in data:

        def get_org():
            return db.organizations[data["organization_id"]]

        org = await run_in_threadpool(get_org)
        if not org:
            return jsonify({"error": "Organization not found"}), 404
        if not org.tenant_id:
            return jsonify({"error": "Organization must have a tenant"}), 400
        org_tenant_id = org.tenant_id

    def update():
        milestone = db.milestones[id]
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
            update_dict["tenant_id"] = org_tenant_id

        if update_dict:
            db(db.milestones.id == id).update(**update_dict)
            db.commit()

        return db.milestones[id]

    milestone = await run_in_threadpool(update)

    if not milestone:
        return jsonify({"error": "Milestone not found"}), 404

    milestone_dto = from_pydal_row(milestone, MilestoneDTO)
    return jsonify(asdict(milestone_dto)), 200


@bp.route("/<int:id>", methods=["DELETE"])
@login_required
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

    def delete():
        milestone = db.milestones[id]
        if not milestone:
            return False

        del db.milestones[id]
        db.commit()
        return True

    success = await run_in_threadpool(delete)

    if not success:
        return jsonify({"error": "Milestone not found"}), 404

    return "", 204


@bp.route("/<int:id>/issues", methods=["GET"])
@login_required
async def get_milestone_issues(id: int):
    """
    Get all issues linked to a milestone.

    Path Parameters:
        - id: Milestone ID

    Returns:
        200: List of issues
        404: Milestone not found

    Example:
        GET /api/v1/milestones/1/issues
    """
    db = current_app.db

    def get_issues():
        milestone = db.milestones[id]
        if not milestone:
            return None, []

        # Get issue-milestone links
        links = db(db.issue_milestone_links.milestone_id == id).select()
        issue_ids = [link.issue_id for link in links]

        if not issue_ids:
            return milestone, []

        # Get issues
        issues = db(db.issues.id.belongs(issue_ids)).select()
        return milestone, issues

    milestone, issues = await run_in_threadpool(get_issues)

    if milestone is None:
        return jsonify({"error": "Milestone not found"}), 404

    from apps.api.models.dataclasses import IssueDTO

    issues_dto = from_pydal_rows(issues, IssueDTO)

    return jsonify({"issues": [asdict(issue) for issue in issues_dto]}), 200
