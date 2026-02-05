"""Entity API endpoints using PyDAL with async/await and shared helpers."""

# flake8: noqa: E501


import asyncio
from dataclasses import asdict

from flask import Blueprint, current_app, jsonify, request
from penguin_libs.pydantic.flask_integration import validated_request
from apps.api.models.pydantic.entity import CreateEntityRequest, UpdateEntityRequest

from apps.api.auth.decorators import login_required
from apps.api.models.dataclasses import (
    EntityDTO,
    PaginatedResponse,
    from_pydal_row,
    from_pydal_rows,
)
from apps.api.utils.api_responses import ApiResponse
from apps.api.utils.pydal_helpers import PaginationParams
from apps.api.utils.validation_helpers import (
    validate_organization_and_get_tenant,
    validate_resource_exists,
)
from apps.api.utils.async_utils import run_in_threadpool

bp = Blueprint("entities", __name__)


@bp.route("", methods=["GET"])
@login_required
async def list_entities():
    """
    List all entities with pagination and filtering.

    Query Parameters:
        - page: Page number (default: 1)
        - per_page: Items per page (default: 50, max: 1000)
        - entity_type: Filter by entity type
        - organization_id: Filter by organization ID
        - name: Filter by name (partial match)
        - is_active: Filter by active status

    Returns:
        200: List of entities with pagination metadata
    """
    db = current_app.db

    # Get pagination params using helper
    pagination = PaginationParams.from_request()

    # Build query
    query = db.entities.id > 0

    # Apply filters
    if request.args.get("entity_type"):
        entity_type = request.args.get("entity_type")
        query &= db.entities.entity_type == entity_type

    if request.args.get("organization_id"):
        organization_id = request.args.get("organization_id", type=int)
        query &= db.entities.organization_id == organization_id

    if request.args.get("name"):
        name = request.args.get("name")
        query &= db.entities.name.ilike(f"%{name}%")

    if request.args.get("is_active") is not None:
        is_active = request.args.get("is_active", "true").lower() == "true"
        query &= db.entities.is_active == is_active

    # Use asyncio TaskGroup for concurrent queries (Python 3.12)
    async with asyncio.TaskGroup() as tg:
        count_task = tg.create_task(run_in_threadpool(lambda: db(query).count()))
        rows_task = tg.create_task(
            run_in_threadpool(
                lambda: db(query).select(
                    orderby=db.entities.name,
                    limitby=(
                        pagination.offset,
                        pagination.offset + pagination.per_page,
                    ),
                )
            )
        )

    total = count_task.result()
    rows = rows_task.result()

    # Calculate total pages using helper
    pages = pagination.calculate_pages(total)

    # Convert PyDAL rows to DTOs
    items = from_pydal_rows(rows, EntityDTO)

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
@validated_request(body_model=CreateEntityRequest)
async def create_entity(body: CreateEntityRequest):
    """
    Create a new entity.

    Request Body:
        JSON object with entity fields

    Returns:
        201: Created entity
        400: Validation error
    """
    db = current_app.db

    # Get organization to derive tenant_id using helper
    org, tenant_id, error = await validate_organization_and_get_tenant(
        body.organization_id
    )
    if error:
        return error

    # Create entity in database
    def create_in_db():
        entity_id = db.entities.insert(
            name=body.name,
            description=body.description,
            entity_type=body.entity_type,
            organization_id=body.organization_id,
            tenant_id=tenant_id,
            parent_id=body.parent_id,
            attributes=body.attributes,
            tags=body.tags or [],
            is_active=body.is_active,
        )
        db.commit()
        return db.entities[entity_id]

    row = await run_in_threadpool(create_in_db)

    # Convert to DTO
    entity_dto = from_pydal_row(row, EntityDTO)

    return ApiResponse.created(asdict(entity_dto))


@bp.route("/<int:id>", methods=["GET"])
@login_required
async def get_entity(id: int):
    """
    Get a single entity by ID.

    Path Parameters:
        - id: Entity ID

    Returns:
        200: Entity details
        404: Entity not found
    """
    db = current_app.db

    # Validate resource exists using helper
    row, error = await validate_resource_exists(db.entities, id, "Entity")
    if error:
        return error

    entity_dto = from_pydal_row(row, EntityDTO)
    return ApiResponse.success(asdict(entity_dto))


@bp.route("/<int:id>", methods=["PATCH", "PUT"])
@validated_request(body_model=UpdateEntityRequest)
async def update_entity(id: int, body: UpdateEntityRequest):
    """
    Update an entity (full edit support).

    Path Parameters:
        - id: Entity ID

    Request Body:
        JSON object with fields to update

    Returns:
        200: Updated entity
        400: Validation error
        404: Entity not found
    """
    db = current_app.db

    # Check if entity exists
    existing, error = await validate_resource_exists(db.entities, id, "Entity")
    if error:
        return error

    # If organization is being changed, validate and get tenant
    org_tenant_id = None
    if body.organization_id is not None:
        org, org_tenant_id, error = await validate_organization_and_get_tenant(
            body.organization_id
        )
        if error:
            return error

    # Update entity
    def update_in_db():
        update_fields = {}
        if body.name is not None:
            update_fields["name"] = body.name
        if body.description is not None:
            update_fields["description"] = body.description
        if body.entity_type is not None:
            update_fields["entity_type"] = body.entity_type
        if body.organization_id is not None:
            update_fields["organization_id"] = body.organization_id
            update_fields["tenant_id"] = org_tenant_id
        if body.parent_id is not None:
            update_fields["parent_id"] = body.parent_id
        if body.attributes is not None:
            update_fields["attributes"] = body.attributes
        if body.tags is not None:
            update_fields["tags"] = body.tags
        if body.is_active is not None:
            update_fields["is_active"] = body.is_active

        db(db.entities.id == id).update(**update_fields)
        db.commit()
        return db.entities[id]

    row = await run_in_threadpool(update_in_db)

    entity_dto = from_pydal_row(row, EntityDTO)
    return ApiResponse.success(asdict(entity_dto))


@bp.route("/<int:id>", methods=["DELETE"])
async def delete_entity(id: int):
    """
    Delete an entity.

    Path Parameters:
        - id: Entity ID

    Returns:
        204: Entity deleted
        404: Entity not found
        400: Cannot delete entity with dependencies
    """
    db = current_app.db

    # Check if entity exists
    existing, error = await validate_resource_exists(db.entities, id, "Entity")
    if error:
        return error

    # Check for dependencies
    def check_and_delete():
        # Check outgoing dependencies (this entity depends on others)
        outgoing_count = db(
            (db.dependencies.source_type == "entity")
            & (db.dependencies.source_id == id)
        ).count()
        # Check incoming dependencies (others depend on this entity)
        incoming_count = db(
            (db.dependencies.target_type == "entity")
            & (db.dependencies.target_id == id)
        ).count()

        total_deps = outgoing_count + incoming_count
        if total_deps > 0:
            return (
                ApiResponse.bad_request(
                    f"Cannot delete entity with {total_deps} dependencies. Remove dependencies first."
                ),
                False,
            )

        # Delete entity
        del db.entities[id]
        db.commit()
        return None, True

    result, success = await run_in_threadpool(check_and_delete)

    if not success:
        return result

    return ApiResponse.no_content()


@bp.route("/<int:id>/dependencies", methods=["GET"])
@login_required
async def get_entity_dependencies(id: int):
    """
    Get all dependencies for an entity.

    Path Parameters:
        - id: Entity ID

    Query Parameters:
        - direction: 'outgoing' (depends on), 'incoming' (depended by), or 'all' (default)

    Returns:
        200: Dependencies information
        404: Entity not found
    """
    db = current_app.db

    # Check if entity exists
    entity, error = await validate_resource_exists(db.entities, id, "Entity")
    if error:
        return error

    direction = request.args.get("direction", "all")

    def get_dependencies():
        result = {
            "entity_id": entity.id,
            "entity_name": entity.name,
        }

        if direction in ("outgoing", "all"):
            # Entities this entity depends on
            outgoing = db(
                (db.dependencies.source_type == "entity")
                & (db.dependencies.source_id == id)
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
            # Entities that depend on this entity
            incoming = db(
                (db.dependencies.target_type == "entity")
                & (db.dependencies.target_id == id)
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

    result = await run_in_threadpool(get_dependencies)
    return ApiResponse.success(result)


@bp.route("/<int:id>/attributes", methods=["PATCH"])
async def update_entity_attributes(id: int):
    """
    Update entity attributes (JSON field for type-specific fields).

    Path Parameters:
        - id: Entity ID

    Request Body:
        JSON object with attribute fields to update

    Returns:
        200: Updated entity
        404: Entity not found
    """
    db = current_app.db

    # Check if entity exists
    existing, error = await validate_resource_exists(db.entities, id, "Entity")
    if error:
        return error

    data = request.get_json()
    if not isinstance(data, dict):
        return ApiResponse.bad_request("Attributes must be a JSON object")

    # Update attributes
    def update_attributes():
        current_attrs = existing.attributes or {}
        current_attrs.update(data)

        db(db.entities.id == id).update(attributes=current_attrs)
        db.commit()
        return db.entities[id]

    row = await run_in_threadpool(update_attributes)

    entity_dto = from_pydal_row(row, EntityDTO)
    return ApiResponse.success(asdict(entity_dto))
