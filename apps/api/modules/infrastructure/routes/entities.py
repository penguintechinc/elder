"""Entity API endpoints using PyDAL with async/await and shared helpers."""

# flake8: noqa: E501

import asyncio
from dataclasses import asdict
from datetime import UTC, datetime, timezone

from quart import Blueprint, current_app, jsonify, request

from apps.api.auth.decorators import login_required, require_scope
from apps.api.models.dataclasses import (
    EntityDTO,
    PaginatedResponse,
    from_pydal_row,
    from_pydal_rows,
)
from apps.api.models.pydantic.entity import CreateEntityRequest, UpdateEntityRequest
from apps.api.utils.api_responses import ApiResponse
from apps.api.utils.async_utils import run_in_threadpool
from apps.api.utils.pydal_helpers import PaginationParams
from apps.api.utils.quart_validation import validated_request
from apps.api.utils.tenant_scoping import (
    get_current_tenant_id,
    get_tenant_org_ids,
    get_tenant_scoped,
)
from apps.api.utils.validation_helpers import (
    validate_organization_and_get_tenant,
)
from apps.api.utils.village_id import mint_village_id

bp = Blueprint("entities", __name__)


def _caller_tenant() -> int | None:
    """Return the caller's tenant id from verified claims, or None.

    Routes map None to a 403: a token without a tenant never sees any
    tenant's entities.
    """
    tid = get_current_tenant_id()
    return tid if tid and tid > 0 else None


async def _check_refs_in_tenant(
    db, tenant_id: int, organization_id: int | None, parent_id: int | None
):
    """Verify caller-supplied organization/parent ids belong to ``tenant_id``.

    Returns None when every supplied id is owned by the caller's tenant,
    otherwise a 404 response (never 403, so other tenants' ids cannot be
    told apart from ids that do not exist).
    """
    if organization_id is not None:
        org = await run_in_threadpool(
            lambda: get_tenant_scoped(db, db.organizations, organization_id, tenant_id)
        )
        if not org:
            return ApiResponse.not_found("Organization", organization_id)
    if parent_id is not None:
        parent = await run_in_threadpool(
            lambda: get_tenant_scoped(
                db, db.entities, parent_id, tenant_id, org_fk="organization_id"
            )
        )
        if not parent:
            return ApiResponse.not_found("Entity", parent_id)
    return None


@bp.route("", methods=["GET"])
@login_required
@require_scope("infrastructure:read")
async def list_entities():
    """
    List all entities with pagination and filtering.

    Query Parameters:
        - page: Page number (default: 1)
        - per_page: Items per page (default: 50, max: 1000)
        - entity_type: Filter by entity type
        - sub_type: Filter by entity sub-type (e.g. k8s_node, k8s_deployment)
        - parent_id: Filter by parent entity ID (e.g. all resources under a cluster)
        - organization_id: Filter by organization ID
        - name: Filter by name (partial match)
        - is_active: Filter by active status

    Returns:
        200: List of entities with pagination metadata
        403: Token carries no tenant
    """
    db = current_app.db_read
    tenant_id = _caller_tenant()
    if tenant_id is None:
        return ApiResponse.forbidden("Tenant context required")

    # Get pagination params using helper
    pagination = PaginationParams.from_request()

    # Only entities owned (via their organization) by the caller's tenant.
    org_ids = await run_in_threadpool(lambda: get_tenant_org_ids(db, tenant_id))
    if not org_ids:
        empty = PaginatedResponse(
            items=[],
            total=0,
            page=pagination.page,
            per_page=pagination.per_page,
            pages=0,
        )
        return jsonify(asdict(empty)), 200
    query = db.entities.organization_id.belongs(org_ids)

    # Apply filters
    if request.args.get("entity_type"):
        entity_type = request.args.get("entity_type")
        query &= db.entities.type == entity_type

    if request.args.get("sub_type"):
        sub_type = request.args.get("sub_type")
        query &= db.entities.sub_type == sub_type

    if request.args.get("parent_id"):
        parent_id = request.args.get("parent_id", type=int)
        query &= db.entities.parent_id == parent_id

    if request.args.get("organization_id"):
        organization_id = request.args.get("organization_id", type=int)
        query &= db.entities.organization_id == organization_id

    if request.args.get("name"):
        name = request.args.get("name")
        query &= db.entities.name.ilike(f"%{name}%")

    if request.args.get("external_id"):
        query &= db.entities.external_id == request.args.get("external_id")

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
@require_scope("infrastructure:write")
@validated_request(body_model=CreateEntityRequest)
async def create_entity(body: CreateEntityRequest):
    """
    Create a new entity.

    Request Body:
        JSON object with entity fields

    Returns:
        201: Created entity
        400: Validation error
        403: Token carries no tenant
        404: Organization or parent entity not found in the caller's tenant
    """
    db = current_app.db
    caller_tenant = _caller_tenant()
    if caller_tenant is None:
        return ApiResponse.forbidden("Tenant context required")

    # Organization and parent must both belong to the caller's tenant.
    denied = await _check_refs_in_tenant(
        db, caller_tenant, body.organization_id, body.parent_id
    )
    if denied:
        return denied

    # Get organization to derive tenant_id using helper
    org, tenant_id, error = await validate_organization_and_get_tenant(
        body.organization_id
    )
    if error:
        return error

    # Create entity in database
    redis_client = getattr(current_app, "redis_client", None)

    def create_in_db():
        now = datetime.now(UTC)
        entity_id = db.entities.insert(
            name=body.name,
            type=body.entity_type,
            organization_id=body.organization_id,
            parent_id=body.parent_id,
            sub_type=body.sub_type,
            external_id=body.external_id,
            tags=body.tags or [],
            metadata=body.attributes,
            status="active",
            is_managed=False,
            village_id=mint_village_id(tenant_id, redis_client),
            created_at=now,
            updated_at=now,
        )
        db.commit()
        # tenant_id resolved from the validated organization above (gh-237)
        return get_tenant_scoped(
            db, db.entities, entity_id, tenant_id, org_fk="organization_id"
        )

    row = await run_in_threadpool(create_in_db)

    # Convert to DTO
    entity_dto = from_pydal_row(row, EntityDTO)

    return ApiResponse.created(asdict(entity_dto))


@bp.route("/<int:id>", methods=["GET"])
@login_required
@require_scope("infrastructure:read")
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
    tenant_id = _caller_tenant()
    if tenant_id is None:
        return ApiResponse.forbidden("Tenant context required")

    # gh-237: scope by tenant (via organizations.tenant_id) — entities have
    # no tenant_id column of their own, only organization_id
    row = await run_in_threadpool(
        lambda: get_tenant_scoped(
            db, db.entities, id, tenant_id, org_fk="organization_id"
        )
    )
    if not row:
        return ApiResponse.not_found("Entity", id)

    entity_dto = from_pydal_row(row, EntityDTO)
    return ApiResponse.success(asdict(entity_dto))


@bp.route("/<int:id>", methods=["PATCH", "PUT"])
@login_required
@require_scope("infrastructure:write")
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
    tenant_id = _caller_tenant()
    if tenant_id is None:
        return ApiResponse.forbidden("Tenant context required")

    # Check if entity exists and belongs to caller's tenant (gh-237)
    existing = await run_in_threadpool(
        lambda: get_tenant_scoped(
            db, db.entities, id, tenant_id, org_fk="organization_id"
        )
    )
    if not existing:
        return ApiResponse.not_found("Entity", id)

    # A move to another organization or parent must stay inside the tenant.
    denied = await _check_refs_in_tenant(
        db, tenant_id, body.organization_id, body.parent_id
    )
    if denied:
        return denied

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
        if body.entity_type is not None:
            update_fields["type"] = body.entity_type
        if body.organization_id is not None:
            update_fields["organization_id"] = body.organization_id
        if body.parent_id is not None:
            update_fields["parent_id"] = body.parent_id
        if body.sub_type is not None:
            update_fields["sub_type"] = body.sub_type
        if body.external_id is not None:
            update_fields["external_id"] = body.external_id
        if body.attributes is not None:
            update_fields["metadata"] = body.attributes
        if body.tags is not None:
            update_fields["tags"] = body.tags

        update_fields["updated_at"] = datetime.now(UTC)

        db(db.entities.id == id).update(**update_fields)
        db.commit()
        return db.entities[id]  # tenant-scope-exempt: tenant verified above

    row = await run_in_threadpool(update_in_db)

    entity_dto = from_pydal_row(row, EntityDTO)
    return ApiResponse.success(asdict(entity_dto))


@bp.route("/<int:id>", methods=["DELETE"])
@login_required
@require_scope("infrastructure:write")
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
    tenant_id = _caller_tenant()
    if tenant_id is None:
        return ApiResponse.forbidden("Tenant context required")

    # Check if entity exists and belongs to caller's tenant (gh-237)
    existing = await run_in_threadpool(
        lambda: get_tenant_scoped(
            db, db.entities, id, tenant_id, org_fk="organization_id"
        )
    )
    if not existing:
        return ApiResponse.not_found("Entity", id)

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
        del db.entities[id]  # tenant-scope-exempt: tenant verified above
        db.commit()
        return None, True

    result, success = await run_in_threadpool(check_and_delete)

    if not success:
        return result

    return ApiResponse.no_content()


@bp.route("/<int:id>/dependencies", methods=["GET"])
@login_required
@require_scope("infrastructure:read")
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
    tenant_id = _caller_tenant()
    if tenant_id is None:
        return ApiResponse.forbidden("Tenant context required")

    # Check if entity exists and belongs to caller's tenant (gh-237)
    entity = await run_in_threadpool(
        lambda: get_tenant_scoped(
            db, db.entities, id, tenant_id, org_fk="organization_id"
        )
    )
    if not entity:
        return ApiResponse.not_found("Entity", id)

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
                & (db.dependencies.tenant_id == tenant_id)
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
                & (db.dependencies.tenant_id == tenant_id)
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
@login_required
@require_scope("infrastructure:write")
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
    tenant_id = _caller_tenant()
    if tenant_id is None:
        return ApiResponse.forbidden("Tenant context required")

    # Check if entity exists and belongs to caller's tenant (gh-237)
    existing = await run_in_threadpool(
        lambda: get_tenant_scoped(
            db, db.entities, id, tenant_id, org_fk="organization_id"
        )
    )
    if not existing:
        return ApiResponse.not_found("Entity", id)

    data = await request.get_json()
    if not isinstance(data, dict):
        return ApiResponse.bad_request("Attributes must be a JSON object")

    # Update attributes (stored in the `metadata` column; "attributes" is
    # the public API name for it, matching CreateEntityRequest/UpdateEntityRequest)
    def update_attributes():
        current_attrs = existing.metadata or {}
        current_attrs.update(data)

        db(db.entities.id == id).update(metadata=current_attrs)
        db.commit()
        return db.entities[id]  # tenant-scope-exempt: tenant verified above

    row = await run_in_threadpool(update_attributes)

    entity_dto = from_pydal_row(row, EntityDTO)
    return ApiResponse.success(asdict(entity_dto))
