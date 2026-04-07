"""Organization API endpoints."""

# flake8: noqa: E501


from datetime import datetime, timezone

from flask import Blueprint, current_app, jsonify, request
from marshmallow import ValidationError

from apps.api.auth.decorators import login_required
from apps.api.schemas.organization import (
    OrganizationCreateSchema,
    OrganizationUpdateSchema,
)
from apps.api.utils.async_utils import run_in_threadpool
from shared.api_utils import (
    handle_validation_error,
    make_error_response,
    validate_request,
)

bp = Blueprint("organizations", __name__)


@bp.route("", methods=["GET"])
def list_organizations():
    """
    List all organizations with pagination and filtering.

    Query Parameters:
        - page: Page number (default: 1)
        - per_page: Items per page (default: 50, max: 1000)
        - parent_id: Filter by parent organization ID
        - name: Filter by name (partial match)
        - search: Search by name (partial match, alias for name)

    Returns:
        200: List of organizations with pagination metadata
    """
    db = current_app.db
    # Get pagination params
    page = request.args.get("page", 1, type=int)
    per_page = min(request.args.get("per_page", 50, type=int), 1000)

    # Build PyDAL query
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

    # Get total count
    total = db(query).count()

    # Calculate pagination
    offset = (page - 1) * per_page
    pages = (total + per_page - 1) // per_page

    # Execute query with pagination and ordering
    rows = db(query).select(
        orderby=db.organizations.name, limitby=(offset, offset + per_page)
    )

    # Convert to dict list
    items = [row.as_dict() for row in rows]

    # Return paginated response
    result = {
        "items": items,
        "total": total,
        "page": page,
        "per_page": per_page,
        "pages": pages,
    }

    return jsonify(result), 200


@bp.route("", methods=["POST"])
@login_required
async def create_organization():
    """
    Create a new organization.

    Request Body:
        JSON object with organization fields (see OrganizationCreateSchema)

    Returns:
        201: Created organization
        400: Validation error
    """
    db = current_app.db
    try:
        data = validate_request(OrganizationCreateSchema)
    except ValidationError as e:
        return handle_validation_error(e)

    def inner():
        try:
            now = datetime.now(timezone.utc)
            data["created_at"] = now
            data["updated_at"] = now
            org_id = db.organizations.insert(**data)
            db.commit()

            # Fetch the created organization
            org = db.organizations[org_id]

            # Return as dict
            return org.as_dict(), None, None
        except Exception as e:
            db.rollback()
            return None, f"Database error: {str(e)}", 500

    result, error, status = await run_in_threadpool(inner)
    if error:
        return make_error_response(error, status)
    return jsonify(result), 201


@bp.route("/<int:id>", methods=["GET"])
def get_organization(id: int):
    """
    Get a single organization by ID.

    Path Parameters:
        - id: Organization ID

    Returns:
        200: Organization details
        404: Organization not found
    """
    db = current_app.db
    org = db.organizations[id]
    if not org:
        return make_error_response("Organization not found", 404)

    return jsonify(org.as_dict()), 200


@bp.route("/<int:id>", methods=["PATCH", "PUT"])
def update_organization(id: int):
    """
    Update an organization.

    Path Parameters:
        - id: Organization ID

    Request Body:
        JSON object with fields to update (see OrganizationUpdateSchema)

    Returns:
        200: Updated organization
        400: Validation error
        404: Organization not found
    """
    db = current_app.db
    org = db.organizations[id]
    if not org:
        return make_error_response("Organization not found", 404)

    try:
        data = validate_request(OrganizationUpdateSchema)
    except ValidationError as e:
        return handle_validation_error(e)

    # Update organization using PyDAL
    try:
        # Check if tenant_id is changing
        old_tenant_id = org.tenant_id
        new_tenant_id = data.get("tenant_id")
        tenant_changed = new_tenant_id is not None and new_tenant_id != old_tenant_id

        # Update organization
        db(db.organizations.id == id).update(**data)

        # If tenant changed, cascade to associated resources
        if tenant_changed:
            # Update all entities belonging to this organization
            db(db.entities.organization_id == id).update(tenant_id=new_tenant_id)

            # Update all identities linked to this organization
            db(db.identities.organization_id == id).update(tenant_id=new_tenant_id)

            # Update child organizations recursively
            def update_children_tenant(parent_id, tenant_id):
                children = db(db.organizations.parent_id == parent_id).select()
                for child in children:
                    db(db.organizations.id == child.id).update(tenant_id=tenant_id)
                    # Update entities and identities of child org
                    db(db.entities.organization_id == child.id).update(
                        tenant_id=tenant_id
                    )
                    db(db.identities.organization_id == child.id).update(
                        tenant_id=tenant_id
                    )
                    # Recurse to grandchildren
                    update_children_tenant(child.id, tenant_id)

            update_children_tenant(id, new_tenant_id)

        db.commit()

        # Fetch updated organization
        org = db.organizations[id]
        return jsonify(org.as_dict()), 200
    except Exception as e:
        db.rollback()
        return make_error_response(f"Database error: {str(e)}", 500)


@bp.route("/<int:id>", methods=["DELETE"])
def delete_organization(id: int):
    """
    Delete an organization.

    Path Parameters:
        - id: Organization ID

    Returns:
        204: Organization deleted
        404: Organization not found
        400: Cannot delete organization with children
    """
    db = current_app.db
    org = db.organizations[id]
    if not org:
        return make_error_response("Organization not found", 404)

    # Check if organization has children
    children_count = db(db.organizations.parent_id == id).count()
    if children_count > 0:
        return make_error_response(
            "Cannot delete organization with child organizations",
            400,
        )

    try:
        del db.organizations[id]
        db.commit()
    except Exception as e:
        db.rollback()
        return make_error_response(f"Database error: {str(e)}", 500)

    return "", 204


@bp.route("/<int:id>/children", methods=["GET"])
def get_organization_children(id: int):
    """
    Get all child organizations.

    Path Parameters:
        - id: Organization ID

    Query Parameters:
        - recursive: Include all descendants (default: false)

    Returns:
        200: List of child organizations
        404: Organization not found
    """
    db = current_app.db
    org = db.organizations[id]
    if not org:
        return make_error_response("Organization not found", 404)

    # Get children
    recursive = request.args.get("recursive", "false").lower() == "true"

    if recursive:
        # Recursively get all descendants
        def get_descendants(parent_id):
            children = db(db.organizations.parent_id == parent_id).select()
            result = []
            for child in children:
                result.append(child.as_dict())
                result.extend(get_descendants(child.id))
            return result

        children = get_descendants(id)
    else:
        # Just direct children
        children = [
            row.as_dict() for row in db(db.organizations.parent_id == id).select()
        ]

    return jsonify(children), 200


@bp.route("/<int:id>/hierarchy", methods=["GET"])
def get_organization_hierarchy(id: int):
    """
    Get organization hierarchy path from root to this organization.

    Path Parameters:
        - id: Organization ID

    Returns:
        200: List of organizations in hierarchy path
        404: Organization not found
    """
    db = current_app.db
    org = db.organizations[id]
    if not org:
        return make_error_response("Organization not found", 404)

    # Build hierarchy path from root to current org
    path = [org.as_dict()]
    current = org
    depth = 0

    while current.parent_id:
        parent = db.organizations[current.parent_id]
        if not parent:
            break
        path.insert(0, parent.as_dict())
        current = parent
        depth += 1

    # Build hierarchy string
    hierarchy_string = " > ".join([o["name"] for o in path])

    return (
        jsonify(
            {
                "path": path,
                "depth": depth,
                "hierarchy_string": hierarchy_string,
            }
        ),
        200,
    )


@bp.route("/<int:id>/graph", methods=["GET"])
def get_organization_graph(id: int):
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
    org = db.organizations[id]
    if not org:
        return make_error_response("Organization not found", 404)

    # Get depth parameter
    depth = min(request.args.get("depth", 3, type=int), 10)

    # Build graph data
    nodes = []
    edges = []
    visited_orgs = set()
    visited_entities = set()

    # Helper function to add organization node (PyDAL row)
    def add_org_node(org_row):
        if org_row.id in visited_orgs:
            return
        visited_orgs.add(org_row.id)
        nodes.append(
            {
                "id": f"org-{org_row.id}",
                "label": org_row.name,
                "type": "organization",
                "metadata": {
                    "id": org_row.id,
                    "description": org_row.description,
                    "parent_id": org_row.parent_id,
                },
            }
        )

    # Helper function to add entity node (PyDAL row)
    def add_entity_node(entity_row):
        if entity_row.id in visited_entities:
            return
        visited_entities.add(entity_row.id)
        nodes.append(
            {
                "id": f"entity-{entity_row.id}",
                "label": entity_row.name,
                "type": entity_row.entity_type or "default",
                "metadata": {
                    "id": entity_row.id,
                    "entity_type": entity_row.entity_type,
                    "organization_id": entity_row.organization_id,
                },
            }
        )

    # Helper to recursively get children
    def get_all_descendants(parent_id, current_depth=0):
        if current_depth >= depth * 10:  # Limit depth
            return []
        children = db(db.organizations.parent_id == parent_id).select()
        result = list(children)
        for child in children:
            result.extend(get_all_descendants(child.id, current_depth + 1))
        return result

    # Add current organization
    add_org_node(org)

    # Get all child organizations recursively
    all_children = get_all_descendants(org.id)
    for child in all_children[: depth * 10]:  # Limit total orgs
        add_org_node(child)
        # Add edge from parent to child
        if child.parent_id:
            edges.append(
                {
                    "from": f"org-{child.parent_id}",
                    "to": f"org-{child.id}",
                    "label": "parent",
                }
            )

    # Get parent hierarchy up to depth
    current = org
    for _ in range(depth):
        if current.parent_id:
            parent = db.organizations[current.parent_id]
            if not parent:
                break
            add_org_node(parent)
            edges.append(
                {
                    "from": f"org-{parent.id}",
                    "to": f"org-{current.id}",
                    "label": "parent",
                }
            )
            current = parent
        else:
            break

    # Get entities for all visited organizations
    org_ids = list(visited_orgs)
    if org_ids:
        try:
            entities = db(db.entities.organization_id.belongs(org_ids)).select(
                limitby=(0, 100)
            )

            for entity in entities:
                add_entity_node(entity)
                # Add edge from organization to entity
                edges.append(
                    {
                        "from": f"org-{entity.organization_id}",
                        "to": f"entity-{entity.id}",
                        "label": "contains",
                    }
                )
        except Exception as e:
            # Log error but continue - entities are optional
            current_app.logger.warning(f"Error fetching entities: {str(e)}")

    # Get dependencies between entities (if dependencies table exists)
    entity_ids = list(visited_entities)
    if entity_ids and hasattr(db, "dependencies"):
        try:
            # Dependencies table uses source_type/source_id, not source_entity_id
            dependencies = db(
                (db.dependencies.source_type == "entity")
                & (db.dependencies.source_id.belongs(entity_ids))
                & (db.dependencies.target_type == "entity")
                & (db.dependencies.target_id.belongs(entity_ids))
            ).select()

            for dep in dependencies:
                edges.append(
                    {
                        "from": f"entity-{dep.source_id}",
                        "to": f"entity-{dep.target_id}",
                        "label": dep.dependency_type or "depends",
                    }
                )
        except Exception as e:
            # Log error but continue - dependencies are optional
            current_app.logger.warning(f"Error fetching dependencies: {str(e)}")

    return (
        jsonify(
            {
                "nodes": nodes,
                "edges": edges,
                "center_node": f"org-{org.id}",
                "depth": depth,
            }
        ),
        200,
    )
