"""Organization Units (OUs) API endpoints using PyDAL with async/await."""

# flake8: noqa: E501


import logging
from dataclasses import asdict

from flask import Blueprint, current_app, g, jsonify, request
from penguin_libs.pydantic.flask_integration import validated_request
from apps.api.models.pydantic.organization import (
    CreateOrganizationRequest,
    UpdateOrganizationRequest,
)

from apps.api.auth.decorators import login_required
from apps.api.logging_config import log_error_and_respond
from apps.api.models.dataclasses import (
    OrganizationDTO,
    PaginatedResponse,
    from_pydal_row,
    from_pydal_rows,
)
from apps.api.utils.api_responses import ApiResponse
from apps.api.utils.pydal_helpers import (
    PaginationParams,
    commit_db,
    get_by_id,
    insert_record,
)
from apps.api.utils.async_utils import run_in_threadpool

logger = logging.getLogger(__name__)

bp = Blueprint("organizations", __name__)


@bp.route("", methods=["GET"])
@login_required
async def list_organizations():
    """
    List all Organization Units (OUs) with pagination and filtering.

    Query Parameters:
        - page: Page number (default: 1)
        - per_page: Items per page (default: 50, max: 1000)
        - parent_id: Filter by parent OU ID
        - name: Filter by name (partial match)

    Returns:
        200: List of Organization Units with pagination metadata
    """
    db = current_app.db

    # Extract pagination parameters using helper
    pagination = PaginationParams.from_request()

    # Build query
    query = db.organizations.id > 0

    # Apply filters
    if request.args.get("parent_id"):
        parent_id = request.args.get("parent_id", type=int)
        query &= db.organizations.parent_id == parent_id

    # Support both 'name' and 'search' parameters for name filtering
    search_term = request.args.get("name") or request.args.get("search")
    if search_term:
        # Case-insensitive search using PostgreSQL ILIKE
        query &= db.organizations.name.ilike(f"%{search_term}%")

    # Execute database queries in a single thread pool task
    def get_orgs():
        total = db(query).count()
        rows = db(query).select(
            orderby=db.organizations.name,
            limitby=(pagination.offset, pagination.offset + pagination.per_page),
        )
        return total, rows

    total, rows = await run_in_threadpool(get_orgs)

    # Calculate total pages
    pages = pagination.calculate_pages(total)

    # Convert PyDAL rows to DTOs
    items = from_pydal_rows(rows, OrganizationDTO)

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
@validated_request(body_model=CreateOrganizationRequest)
async def create_organization(body: CreateOrganizationRequest):
    """
    Create a new Organization Unit (OU).

    Request Body:
        JSON object with Organization Unit fields

    Returns:
        201: Created Organization Unit
        400: Validation error
    """
    db = current_app.db

    # Insert organization using helper
    try:
        # Get tenant_id from current user (g.current_user set by @login_required)
        # Default to 1 if not available (for tests without auth)
        tenant_id = (
            getattr(g.current_user, "tenant_id", 1) if hasattr(g, "current_user") else 1
        )

        org_data = body.model_dump(exclude_none=True)
        if "tenant_id" not in org_data:
            org_data["tenant_id"] = tenant_id

        org_id = await insert_record(db.organizations, **org_data)
        if not org_id:
            return log_error_and_respond(
                logger,
                Exception("Failed to insert organization"),
                "Failed to create organization",
                500,
            )

        await commit_db(db)

        # Fetch the created org to ensure it exists and return full data
        org_row = await get_by_id(db.organizations, org_id)
        if not org_row:
            logger.error(
                f"Organization {org_id} was inserted but not found after commit"
            )
            # Still return success with the data we have
            result = {"id": org_id, **org_data}
            return ApiResponse.created(result)

        # Convert to dict and return
        org_dict = await run_in_threadpool(lambda: org_row.as_dict())
        return ApiResponse.created(org_dict)

    except Exception as e:
        await run_in_threadpool(lambda: db.rollback())
        return log_error_and_respond(logger, e, "Failed to process request", 500)


@bp.route("/<int:id>", methods=["GET"])
@login_required
async def get_organization(id: int):
    """
    Get a single Organization Unit (OU) by ID.

    Path Parameters:
        - id: Organization Unit ID

    Returns:
        200: Organization Unit details
        404: Organization Unit not found
    """
    db = current_app.db

    # Get organization using helper
    try:
        # Log request details for debugging
        tenant_id = (
            getattr(g.current_user, "tenant_id", None)
            if hasattr(g, "current_user")
            else None
        )
        user_id = (
            getattr(g.current_user, "id", None) if hasattr(g, "current_user") else None
        )
        logger.error(
            f"DEBUG GET /organizations/{id}: user_id={user_id}, tenant_id={tenant_id}"
        )

        org_row = await get_by_id(db.organizations, id)
        logger.error(f"DEBUG: org_row = {org_row}")
        if not org_row:
            logger.error(f"Organization {id} not found in database")
            return ApiResponse.not_found("Organization Unit")

        org_dto = from_pydal_row(org_row, OrganizationDTO)
        return ApiResponse.success(asdict(org_dto))
    except Exception as e:
        logger.error(f"Error fetching organization {id}: {e}")
        return ApiResponse.not_found("Organization Unit")


@bp.route("/<int:id>", methods=["PATCH", "PUT"])
@validated_request(body_model=UpdateOrganizationRequest)
async def update_organization(id: int, body: UpdateOrganizationRequest):
    """
    Update an Organization Unit (OU).

    Path Parameters:
        - id: Organization Unit ID

    Request Body:
        JSON object with fields to update

    Returns:
        200: Updated Organization Unit
        404: Organization Unit not found
    """
    db = current_app.db

    # Verify organization exists using helper
    org_row = await get_by_id(db.organizations, id)
    if not org_row:
        return ApiResponse.not_found("Organization Unit")

    # Get update fields from validated body
    update_fields = body.model_dump(exclude_none=True)

    if not update_fields:
        return ApiResponse.bad_request("No fields to update")

    # Update organization
    try:
        await run_in_threadpool(
            lambda: db(db.organizations.id == id).update(**update_fields)
        )
        await commit_db(db)

        # Fetch updated org using helper
        org_row = await get_by_id(db.organizations, id)
        org_dto = from_pydal_row(org_row, OrganizationDTO)
        return ApiResponse.success(asdict(org_dto))

    except Exception as e:
        await run_in_threadpool(lambda: db.rollback())
        return log_error_and_respond(logger, e, "Failed to process request", 500)


@bp.route("/<int:id>", methods=["DELETE"])
async def delete_organization(id: int):
    """
    Delete an Organization Unit (OU).

    Path Parameters:
        - id: Organization Unit ID

    Returns:
        204: Organization Unit deleted
        404: Organization Unit not found
        400: Cannot delete OU with child OUs
    """
    db = current_app.db

    # Verify organization exists using helper
    org_row = await get_by_id(db.organizations, id)
    if not org_row:
        return ApiResponse.not_found("Organization Unit")

    # Check if OU has children
    children_count = await run_in_threadpool(
        lambda: db(db.organizations.parent_id == id).count()
    )

    if children_count > 0:
        return ApiResponse.bad_request("Cannot delete Organization Unit with child OUs")

    # Delete organization
    try:
        await run_in_threadpool(lambda: db.organizations.__delitem__(id))
        await commit_db(db)
        return ApiResponse.no_content()

    except Exception as e:
        await run_in_threadpool(lambda: db.rollback())
        return log_error_and_respond(logger, e, "Failed to process request", 500)


@bp.route("/<int:id>/graph", methods=["GET"])
@login_required
async def get_organization_graph(id: int):
    """
    Get relationship graph for an organization and its nearby entities.

    Path Parameters:
        - id: Organization ID

    Query Parameters:
        - depth: How many hops away to include (default: 3, max: 10)

    Returns:
        200: Graph data with nodes and edges
        404: Organization not found
    """
    db = current_app.db

    # Check if organization exists using helper
    try:
        org_row = await get_by_id(db.organizations, id)
        if org_row is None:
            return ApiResponse.not_found("Organization Unit")
    except Exception:
        return ApiResponse.not_found("Organization Unit")

    # Get depth parameter
    depth = min(request.args.get("depth", 3, type=int), 10)

    # Build graph data
    nodes = []
    edges = []
    visited_orgs = set()
    visited_entities = set()

    # Helper to add organization node
    def add_org_node(org_id):
        if org_id in visited_orgs:
            return
        org = db.organizations[org_id]
        if not org:
            return
        visited_orgs.add(org_id)
        nodes.append(
            {
                "id": f"org-{org_id}",
                "label": org.name,
                "type": "organization",
                "metadata": {
                    "id": org_id,
                    "description": org.description,
                    "parent_id": org.parent_id,
                },
            }
        )
        return org

    # Helper to add entity node
    def add_entity_node(entity_id):
        if entity_id in visited_entities:
            return
        entity = db.entities[entity_id]
        if not entity:
            return
        visited_entities.add(entity_id)
        nodes.append(
            {
                "id": f"entity-{entity_id}",
                "label": entity.name,
                "type": entity.entity_type or "default",
                "metadata": {
                    "id": entity_id,
                    "entity_type": entity.entity_type,
                    "organization_id": entity.organization_id,
                },
            }
        )

    # Add current organization
    add_org_node(id)

    # Get all child organizations recursively (limit to depth * 10)
    def get_children_recursive(parent_id, current_depth=0):
        if current_depth >= depth:
            return []
        children = db(db.organizations.parent_id == parent_id).select()
        all_children = list(children)
        for child in children:
            all_children.extend(get_children_recursive(child.id, current_depth + 1))
        return all_children[: depth * 10]

    all_children = await run_in_threadpool(lambda: get_children_recursive(id))
    for child in all_children:
        add_org_node(child.id)
        if child.parent_id:
            edges.append(
                {
                    "from": f"org-{child.parent_id}",
                    "to": f"org-{child.id}",
                    "label": "parent",
                }
            )

    # Get parent hierarchy up to depth
    current_org = org_row
    for _ in range(depth):
        if current_org and current_org.parent_id:
            parent = db.organizations[current_org.parent_id]
            if parent:
                add_org_node(parent.id)
                edges.append(
                    {
                        "from": f"org-{parent.id}",
                        "to": f"org-{current_org.id}",
                        "label": "parent",
                    }
                )
                current_org = parent
            else:
                break
        else:
            break

    # Get entities for all visited organizations
    org_ids = list(visited_orgs)
    entities = await run_in_threadpool(
        lambda: db(db.entities.organization_id.belongs(org_ids)).select(
            limitby=(0, 100)
        )
    )

    for entity in entities:
        add_entity_node(entity.id)
        edges.append(
            {
                "from": f"org-{entity.organization_id}",
                "to": f"entity-{entity.id}",
                "label": "contains",
            }
        )

    # Get dependencies between entities
    entity_ids = list(visited_entities)
    # Dependencies table uses source_type/source_id, not source_entity_id
    dependencies = await run_in_threadpool(
        lambda: db(
            (db.dependencies.source_type == "entity")
            & (db.dependencies.source_id.belongs(entity_ids))
            & (db.dependencies.target_type == "entity")
            & (db.dependencies.target_id.belongs(entity_ids))
        ).select()
    )

    for dep in dependencies:
        edges.append(
            {
                "from": f"entity-{dep.source_id}",
                "to": f"entity-{dep.target_id}",
                "label": dep.dependency_type or "depends",
            }
        )

    return (
        jsonify(
            {
                "nodes": nodes,
                "edges": edges,
                "center_node": f"org-{id}",
                "depth": depth,
            }
        ),
        200,
    )
