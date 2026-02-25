"""Dependency API endpoints using PyDAL with async/await and Pydantic validation."""

# flake8: noqa: E501


import asyncio
import logging
from dataclasses import asdict
from typing import Optional

from flask import Blueprint, current_app, jsonify, request
from penguin_libs.pydantic import RequestModel, validated_request
from pydantic import Field

from apps.api.auth.decorators import login_required
from apps.api.logging_config import log_error_and_respond
from apps.api.models.dataclasses import (
    DependencyDTO,
    PaginatedResponse,
    from_pydal_row,
    from_pydal_rows,
)
from apps.api.utils.async_utils import run_in_threadpool

logger = logging.getLogger(__name__)

bp = Blueprint("dependencies", __name__)

# Valid resource types for dependencies
VALID_RESOURCE_TYPES = [
    "entity",
    "identity",
    "project",
    "milestone",
    "issue",
    "organization",
]

# Map resource types to their database tables
RESOURCE_TABLE_MAP = {
    "entity": "entities",
    "identity": "identities",
    "project": "projects",
    "milestone": "milestones",
    "issue": "issues",
    "organization": "organizations",
}


# Pydantic request models
class CreateDependencyRequest(RequestModel):
    """Request to create a new dependency."""

    source_type: str = Field(
        ...,
        description="Type of source resource",
    )
    source_id: int = Field(
        ...,
        ge=1,
        description="ID of source resource (must be positive)",
    )
    target_type: str = Field(
        ...,
        description="Type of target resource",
    )
    target_id: int = Field(
        ...,
        ge=1,
        description="ID of target resource (must be positive)",
    )
    dependency_type: str = Field(
        ...,
        description="Type of dependency relationship",
    )
    metadata: Optional[dict] = Field(
        default=None,
        description="Optional custom metadata",
    )


class UpdateDependencyRequest(RequestModel):
    """Request to update an existing dependency."""

    source_type: Optional[str] = Field(
        default=None,
        description="Type of source resource",
    )
    source_id: Optional[int] = Field(
        default=None,
        ge=1,
        description="ID of source resource (must be positive)",
    )
    target_type: Optional[str] = Field(
        default=None,
        description="Type of target resource",
    )
    target_id: Optional[int] = Field(
        default=None,
        ge=1,
        description="ID of target resource (must be positive)",
    )
    dependency_type: Optional[str] = Field(
        default=None,
        description="Type of dependency relationship",
    )
    metadata: Optional[dict] = Field(
        default=None,
        description="Custom metadata",
    )


class BulkCreateDependenciesRequest(RequestModel):
    """Request to create multiple dependencies."""

    dependencies: list[CreateDependencyRequest] = Field(
        ...,
        min_items=1,
        max_items=100,
        description="Array of dependencies to create",
    )


class BulkDeleteDependenciesRequest(RequestModel):
    """Request to delete multiple dependencies."""

    ids: list[int] = Field(
        ...,
        min_items=1,
        max_items=100,
        description="Array of dependency IDs to delete",
    )


def get_resource(db, resource_type: str, resource_id: int):
    """Get a resource by type and ID."""
    table_name = RESOURCE_TABLE_MAP.get(resource_type)
    if not table_name:
        return None
    table = getattr(db, table_name, None)
    if not table:
        return None
    return table[resource_id]


@bp.route("", methods=["GET"])
@login_required
async def list_dependencies():
    """
    List all dependencies with pagination and filtering.

    Query Parameters:
        - page: Page number (default: 1)
        - per_page: Items per page (default: 50, max: 1000)
        - source_type: Filter by source type (entity, identity, project, etc.)
        - source_id: Filter by source ID
        - target_type: Filter by target type
        - target_id: Filter by target ID
        - dependency_type: Filter by dependency type

    Returns:
        200: List of dependencies with pagination metadata
    """
    db = current_app.db

    # Get pagination params
    page = request.args.get("page", 1, type=int)
    per_page = min(request.args.get("per_page", 50, type=int), 1000)

    # Build query
    query = db.dependencies.id > 0

    # Apply filters
    if request.args.get("source_type"):
        query &= db.dependencies.source_type == request.args.get("source_type")

    if request.args.get("source_id"):
        source_id = request.args.get("source_id", type=int)
        query &= db.dependencies.source_id == source_id

    if request.args.get("target_type"):
        query &= db.dependencies.target_type == request.args.get("target_type")

    if request.args.get("target_id"):
        target_id = request.args.get("target_id", type=int)
        query &= db.dependencies.target_id == target_id

    if request.args.get("dependency_type"):
        dep_type = request.args.get("dependency_type")
        query &= db.dependencies.dependency_type == dep_type

    # Calculate pagination
    offset = (page - 1) * per_page

    # Use asyncio TaskGroup for concurrent queries (Python 3.12)
    async with asyncio.TaskGroup() as tg:
        count_task = tg.create_task(run_in_threadpool(lambda: db(query).count()))
        rows_task = tg.create_task(
            run_in_threadpool(
                lambda: db(query).select(
                    orderby=~db.dependencies.created_at,
                    limitby=(offset, offset + per_page),
                )
            )
        )

    total = count_task.result()
    rows = rows_task.result()

    # Calculate total pages
    pages = (total + per_page - 1) // per_page if total > 0 else 0

    # Convert PyDAL rows to DTOs
    items = from_pydal_rows(rows, DependencyDTO)

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
@validated_request(body_model=CreateDependencyRequest)
async def create_dependency(body: CreateDependencyRequest):
    """
    Create a new dependency relationship between any resource types.

    Request Body:
        {
            "source_type": "entity",
            "source_id": 1,
            "target_type": "identity",
            "target_id": 5,
            "dependency_type": "manages",
            "metadata": {}
        }

    Returns:
        201: Created dependency
        400: Validation error or invalid resource IDs
        409: Dependency already exists
    """
    db = current_app.db

    source_type = body.source_type
    source_id = body.source_id
    target_type = body.target_type
    target_id = body.target_id

    # Validate resource types
    if source_type not in VALID_RESOURCE_TYPES:
        return (
            jsonify(
                {
                    "error": f"Invalid source_type. Must be one of: {VALID_RESOURCE_TYPES}"
                }
            ),
            400,
        )
    if target_type not in VALID_RESOURCE_TYPES:
        return (
            jsonify(
                {
                    "error": f"Invalid target_type. Must be one of: {VALID_RESOURCE_TYPES}"
                }
            ),
            400,
        )

    # Prevent self-dependencies (same type and ID)
    if source_type == target_type and source_id == target_id:
        return (
            jsonify({"error": "Cannot create dependency from resource to itself"}),
            400,
        )

    # Check if resources exist and dependency doesn't already exist
    def validate_and_create():
        # Check if source resource exists
        source = get_resource(db, source_type, source_id)
        if not source:
            return {"error": f"Source {source_type} {source_id} not found"}, 404, None

        # Check if target resource exists
        target = get_resource(db, target_type, target_id)
        if not target:
            return {"error": f"Target {target_type} {target_id} not found"}, 404, None

        # Check if dependency already exists
        existing = (
            db(
                (db.dependencies.source_type == source_type)
                & (db.dependencies.source_id == source_id)
                & (db.dependencies.target_type == target_type)
                & (db.dependencies.target_id == target_id)
                & (db.dependencies.dependency_type == body.dependency_type)
            )
            .select()
            .first()
        )

        if existing:
            return (
                {"error": "Dependency already exists", "dependency_id": existing.id},
                409,
                None,
            )

        # Get tenant from source resource
        tenant_id = getattr(source, "tenant_id", None)
        if not tenant_id:
            return {"error": f"Source {source_type} must have a tenant"}, 400, None

        # Create dependency
        dep_id = db.dependencies.insert(
            tenant_id=tenant_id,
            source_type=source_type,
            source_id=source_id,
            target_type=target_type,
            target_id=target_id,
            dependency_type=body.dependency_type,
            metadata=body.metadata,
        )
        db.commit()
        return None, None, db.dependencies[dep_id]

    error, status, row = await run_in_threadpool(validate_and_create)

    if error:
        return jsonify(error), status

    dependency_dto = from_pydal_row(row, DependencyDTO)
    return jsonify(asdict(dependency_dto)), 201


@bp.route("/<int:id>", methods=["GET"])
@login_required
async def get_dependency(id: int):
    """
    Get a single dependency by ID.

    Path Parameters:
        - id: Dependency ID

    Returns:
        200: Dependency details
        404: Dependency not found
    """
    db = current_app.db

    row = await run_in_threadpool(lambda: db.dependencies[id])

    if not row:
        return jsonify({"error": "Dependency not found"}), 404

    dependency_dto = from_pydal_row(row, DependencyDTO)
    return jsonify(asdict(dependency_dto)), 200


@bp.route("/<int:id>", methods=["PATCH", "PUT"])
@login_required
@validated_request(body_model=UpdateDependencyRequest)
async def update_dependency(id: int, body: UpdateDependencyRequest):
    """
    Update a dependency (edit relationship type, metadata, or endpoints).

    Path Parameters:
        - id: Dependency ID

    Request Body:
        JSON object with fields to update

    Returns:
        200: Updated dependency
        400: Validation error
        404: Dependency not found
    """
    db = current_app.db

    # Check if dependency exists
    existing = await run_in_threadpool(lambda: db.dependencies[id])
    if not existing:
        return jsonify({"error": "Dependency not found"}), 404

    # Validate resource types if being updated
    if body.source_type and body.source_type not in VALID_RESOURCE_TYPES:
        return (
            jsonify(
                {
                    "error": f"Invalid source_type. Must be one of: {VALID_RESOURCE_TYPES}"
                }
            ),
            400,
        )
    if body.target_type and body.target_type not in VALID_RESOURCE_TYPES:
        return (
            jsonify(
                {
                    "error": f"Invalid target_type. Must be one of: {VALID_RESOURCE_TYPES}"
                }
            ),
            400,
        )

    # Update dependency
    def update_in_db():
        update_fields = {}
        if body.dependency_type is not None:
            update_fields["dependency_type"] = body.dependency_type
        if body.metadata is not None:
            update_fields["metadata"] = body.metadata
        if body.source_type is not None:
            update_fields["source_type"] = body.source_type
        if body.source_id is not None:
            update_fields["source_id"] = body.source_id
        if body.target_type is not None:
            update_fields["target_type"] = body.target_type
        if body.target_id is not None:
            update_fields["target_id"] = body.target_id

        db(db.dependencies.id == id).update(**update_fields)
        db.commit()
        return db.dependencies[id]

    row = await run_in_threadpool(update_in_db)

    dependency_dto = from_pydal_row(row, DependencyDTO)
    return jsonify(asdict(dependency_dto)), 200


@bp.route("/<int:id>", methods=["DELETE"])
@login_required
async def delete_dependency(id: int):
    """
    Delete a dependency relationship.

    Path Parameters:
        - id: Dependency ID

    Returns:
        204: Dependency deleted
        404: Dependency not found
    """
    db = current_app.db

    # Check if dependency exists
    existing = await run_in_threadpool(lambda: db.dependencies[id])
    if not existing:
        return jsonify({"error": "Dependency not found"}), 404

    # Delete dependency
    await run_in_threadpool(
        lambda: (db(db.dependencies.id == id).delete(), db.commit())
    )

    return "", 204


@bp.route("/bulk", methods=["POST"])
@login_required
async def create_bulk_dependencies():
    """
    Create multiple dependencies at once.

    Request Body:
        {
            "dependencies": [
                {
                    "source_type": "entity",
                    "source_id": 1,
                    "target_type": "identity",
                    "target_id": 5,
                    "dependency_type": "manages"
                }
            ]
        }

    Returns:
        201: Created dependencies
        400: Validation error
    """
    db = current_app.db

    try:
        data = request.get_json()
        if not data:
            return jsonify({"error": "Request body must be JSON"}), 400

        # Validate using Pydantic model
        request_data = {"dependencies": data.get("dependencies", [])}
        validated = BulkCreateDependenciesRequest(**request_data)

        # Validate and create dependencies
        def create_all():
            created_ids = []
            for i, dep_req in enumerate(validated.dependencies):
                # Get source resource for tenant
                source = get_resource(db, dep_req.source_type, dep_req.source_id)
                if not source:
                    raise ValueError(
                        f"Source {dep_req.source_type} {dep_req.source_id} not found at index {i}"
                    )

                tenant_id = getattr(source, "tenant_id", None)
                if not tenant_id:
                    raise ValueError(f"Source must have tenant at index {i}")

                # Create dependency
                dep_id = db.dependencies.insert(
                    tenant_id=tenant_id,
                    source_type=dep_req.source_type,
                    source_id=dep_req.source_id,
                    target_type=dep_req.target_type,
                    target_id=dep_req.target_id,
                    dependency_type=dep_req.dependency_type,
                    metadata=dep_req.metadata,
                )
                created_ids.append(dep_id)

            db.commit()

            # Fetch created dependencies
            return db(db.dependencies.id.belongs(created_ids)).select()

        rows = await run_in_threadpool(create_all)
        dependencies = from_pydal_rows(rows, DependencyDTO)
        return jsonify([asdict(d) for d in dependencies]), 201
    except ValueError as e:
        return log_error_and_respond(logger, e, "Failed to process request", 400)
    except Exception as e:
        return log_error_and_respond(logger, e, "Failed to process request", 500)


@bp.route("/bulk", methods=["DELETE"])
@login_required
@validated_request(body_model=BulkDeleteDependenciesRequest)
async def delete_bulk_dependencies(body: BulkDeleteDependenciesRequest):
    """
    Delete multiple dependencies at once.

    Request Body:
        {
            "ids": [1, 2, 3]
        }

    Returns:
        200: Number of deleted dependencies
        400: Validation error
    """
    db = current_app.db

    # Delete dependencies
    def delete_all():
        deleted = db(db.dependencies.id.belongs(body.ids)).delete()
        db.commit()
        return deleted

    deleted = await run_in_threadpool(delete_all)

    return jsonify({"deleted": deleted, "requested": len(body.ids)}), 200


@bp.route("/resource/<resource_type>/<int:resource_id>", methods=["GET"])
@login_required
async def get_resource_dependencies(resource_type: str, resource_id: int):
    """
    Get all dependencies for a specific resource.

    Path Parameters:
        - resource_type: Type of resource (entity, identity, project, etc.)
        - resource_id: Resource ID

    Query Parameters:
        - direction: 'outgoing', 'incoming', or 'all' (default)

    Returns:
        200: Dependencies for the resource
        400: Invalid resource type
        404: Resource not found
    """
    db = current_app.db

    if resource_type not in VALID_RESOURCE_TYPES:
        return (
            jsonify(
                {
                    "error": f"Invalid resource_type. Must be one of: {VALID_RESOURCE_TYPES}"
                }
            ),
            400,
        )

    # Check if resource exists
    resource = await run_in_threadpool(
        lambda: get_resource(db, resource_type, resource_id)
    )
    if not resource:
        return jsonify({"error": f"{resource_type.title()} not found"}), 404

    direction = request.args.get("direction", "all")

    def get_deps():
        result = {
            "resource_type": resource_type,
            "resource_id": resource_id,
            "resource_name": getattr(
                resource, "name", getattr(resource, "username", str(resource_id))
            ),
        }

        if direction in ("outgoing", "all"):
            # This resource depends on others
            outgoing = db(
                (db.dependencies.source_type == resource_type)
                & (db.dependencies.source_id == resource_id)
            ).select()
            result["depends_on"] = [
                {
                    "id": dep.id,
                    "target_type": dep.target_type,
                    "target_id": dep.target_id,
                    "dependency_type": dep.dependency_type,
                    "metadata": dep.metadata,
                }
                for dep in outgoing
            ]

        if direction in ("incoming", "all"):
            # Others depend on this resource
            incoming = db(
                (db.dependencies.target_type == resource_type)
                & (db.dependencies.target_id == resource_id)
            ).select()
            result["depended_by"] = [
                {
                    "id": dep.id,
                    "source_type": dep.source_type,
                    "source_id": dep.source_id,
                    "dependency_type": dep.dependency_type,
                    "metadata": dep.metadata,
                }
                for dep in incoming
            ]

        return result

    result = await run_in_threadpool(get_deps)
    return jsonify(result), 200
