"""AI Resources management API endpoints for Elder."""

# flake8: noqa: E501

import json
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from quart import Blueprint, current_app, request
from quart_schema import validate_response
from sqlalchemy import inspect as sa_inspect
from sqlalchemy import text

from apps.api.auth.decorators import (
    login_required,
    require_scope,
    resource_role_required,
)
from apps.api.models.pydantic.ai import (
    AIResourceDTO,
    AIResourceListResponse,
    CreateAIResourceRequest,
    UpdateAIResourceRequest,
)
from apps.api.utils.api_responses import ApiResponse
from apps.api.utils.async_utils import run_in_threadpool
from apps.api.utils.pydal_helpers import PaginationParams
from apps.api.utils.quart_validation import validated_request
from apps.api.utils.tenant_scoping import get_current_tenant_id, get_tenant_scoped
from apps.api.utils.validation_helpers import validate_organization_and_get_tenant

bp = Blueprint("ai_resources", __name__)

_table_ensured = False

_PG_CREATE_SQL = """
CREATE TABLE IF NOT EXISTS ai_resources (
    id SERIAL PRIMARY KEY,
    tenant_id INTEGER NOT NULL REFERENCES tenants(id),
    village_id VARCHAR(32) UNIQUE,
    name VARCHAR(255) NOT NULL,
    description TEXT,
    organization_id INTEGER NOT NULL REFERENCES organizations(id),
    resource_type VARCHAR(50) NOT NULL,
    provider VARCHAR(255),
    model_name VARCHAR(255),
    endpoint_url VARCHAR(1024),
    status VARCHAR(50) NOT NULL DEFAULT 'pending_review',
    agreement_level VARCHAR(50),
    plan_info VARCHAR(255),
    budget_monthly NUMERIC(12, 2),
    cost_per_10m_tokens NUMERIC(12, 4),
    seats INTEGER,
    parent_system_id INTEGER REFERENCES entities(id),
    owner_id INTEGER REFERENCES identities(id),
    notes TEXT,
    tags JSONB,
    metadata JSONB,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);
"""

_SQLITE_CREATE_SQL = """
CREATE TABLE IF NOT EXISTS ai_resources (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id INTEGER NOT NULL REFERENCES tenants(id),
    village_id VARCHAR(32) UNIQUE,
    name VARCHAR(255) NOT NULL,
    description TEXT,
    organization_id INTEGER NOT NULL REFERENCES organizations(id),
    resource_type VARCHAR(50) NOT NULL,
    provider VARCHAR(255),
    model_name VARCHAR(255),
    endpoint_url VARCHAR(1024),
    status VARCHAR(50) NOT NULL DEFAULT 'pending_review',
    agreement_level VARCHAR(50),
    plan_info VARCHAR(255),
    budget_monthly NUMERIC(12, 2),
    cost_per_10m_tokens NUMERIC(12, 4),
    seats INTEGER,
    parent_system_id INTEGER REFERENCES entities(id),
    owner_id INTEGER REFERENCES identities(id),
    notes TEXT,
    tags TEXT,
    metadata TEXT,
    is_active BOOLEAN NOT NULL DEFAULT 1,
    created_at DATETIME,
    updated_at DATETIME
);
"""


def _ensure_ai_resources_table(db: Any) -> None:
    """Ensure the ai_resources table exists for both Postgres and SQLite."""
    global _table_ensured
    if _table_ensured:
        return
    try:
        engine = db.engine
    except Exception:
        engine = None
    if engine is None:
        _table_ensured = True
        return

    try:
        if sa_inspect(engine).has_table("ai_resources"):
            _table_ensured = True
            return
    except Exception:
        pass

    is_sqlite = getattr(getattr(engine, "dialect", None), "name", "") == "sqlite"
    with engine.begin() as conn:
        if is_sqlite:
            conn.execute(text(_SQLITE_CREATE_SQL))
        else:
            conn.execute(text(_PG_CREATE_SQL))
            conn.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS ix_ai_resources_resource_type "
                    "ON ai_resources (resource_type);"
                )
            )
            conn.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS ix_ai_resources_status "
                    "ON ai_resources (status);"
                )
            )
    _table_ensured = True


def _to_float(val: Any) -> float | None:
    """Convert Numeric/Decimal values to float for JSON serialization."""
    if val is None:
        return None
    if isinstance(val, Decimal):
        return float(val)
    return float(val)


def _parse_json_field(val: Any) -> Any:
    """Parse JSON string fields returned by SQLite if needed."""
    if isinstance(val, str):
        try:
            return json.loads(val)
        except ValueError:
            return None
    return val


def _row_to_dto(row: Any) -> AIResourceDTO:
    """Map an AIResource DB row to AIResourceDTO, handling extra_metadata/metadata."""
    raw_meta = getattr(row, "extra_metadata", None)
    if raw_meta is None and isinstance(row, dict):
        raw_meta = row.get("metadata")
    metadata = _parse_json_field(raw_meta)
    tags = _parse_json_field(getattr(row, "tags", None))

    return AIResourceDTO(
        id=row.id,
        tenant_id=row.tenant_id,
        name=row.name,
        description=getattr(row, "description", None),
        organization_id=row.organization_id,
        resource_type=row.resource_type,
        provider=getattr(row, "provider", None),
        model_name=getattr(row, "model_name", None),
        endpoint_url=getattr(row, "endpoint_url", None),
        status=row.status,
        agreement_level=getattr(row, "agreement_level", None),
        plan_info=getattr(row, "plan_info", None),
        budget_monthly=_to_float(getattr(row, "budget_monthly", None)),
        cost_per_10m_tokens=_to_float(getattr(row, "cost_per_10m_tokens", None)),
        seats=getattr(row, "seats", None),
        parent_system_id=getattr(row, "parent_system_id", None),
        owner_id=getattr(row, "owner_id", None),
        notes=getattr(row, "notes", None),
        tags=tags,
        metadata=metadata,
        is_active=bool(getattr(row, "is_active", True)),
        created_at=getattr(row, "created_at", None),
        updated_at=getattr(row, "updated_at", None),
        village_id=getattr(row, "village_id", None),
    )


@bp.route("", methods=["GET"])
@login_required
@require_scope("ai:read")
@validate_response(AIResourceListResponse)
async def list_ai_resources():
    """List AI resources with optional filtering and pagination."""
    db = current_app.db
    tenant_id = get_current_tenant_id()
    if not tenant_id:
        return ApiResponse.forbidden("Tenant context required")

    pagination = PaginationParams.from_request()
    org_id = request.args.get("organization_id", type=int)
    resource_type = request.args.get("resource_type")
    status = request.args.get("status")
    agreement_level = request.args.get("agreement_level")
    search = request.args.get("search")

    def get_resources():
        _ensure_ai_resources_table(db)
        query = db.ai_resources.tenant_id == tenant_id

        if org_id is not None:
            query &= db.ai_resources.organization_id == org_id
        if resource_type:
            query &= db.ai_resources.resource_type == resource_type
        if status:
            query &= db.ai_resources.status == status
        if agreement_level:
            query &= db.ai_resources.agreement_level == agreement_level
        if search:
            pattern = f"%{search}%"
            query &= (
                (db.ai_resources.name.ilike(pattern))
                | (db.ai_resources.provider.ilike(pattern))
                | (db.ai_resources.model_name.ilike(pattern))
                | (db.ai_resources.description.ilike(pattern))
            )

        total = db(query).count()
        rows = db(query).select(
            orderby=~db.ai_resources.created_at,
            limitby=(pagination.offset, pagination.offset + pagination.per_page),
        )
        return total, rows

    total, rows = await run_in_threadpool(get_resources)
    pages = pagination.calculate_pages(total)

    return AIResourceListResponse(
        items=[_row_to_dto(row) for row in rows],
        total=total,
        page=pagination.page,
        per_page=pagination.per_page,
        pages=pages,
    ), 200


@bp.route("", methods=["POST"])
@login_required
@require_scope("ai:write")
@resource_role_required("maintainer")
@validated_request(body_model=CreateAIResourceRequest)
@validate_response(AIResourceDTO, status_code=201)
async def create_ai_resource(body: CreateAIResourceRequest):
    """Create a new AI resource."""
    db = current_app.db
    caller_tenant_id = get_current_tenant_id()

    _org, tenant_id, error = await validate_organization_and_get_tenant(
        body.organization_id
    )
    if error:
        return error

    if caller_tenant_id and tenant_id != caller_tenant_id:
        return ApiResponse.not_found("Organization", body.organization_id)

    if body.owner_id is not None:
        owner = await run_in_threadpool(
            lambda: get_tenant_scoped(db, db.identities, body.owner_id, tenant_id)
        )
        if not owner:
            return ApiResponse.not_found("Owner identity", body.owner_id)

    if body.parent_system_id is not None:
        parent = await run_in_threadpool(
            lambda: get_tenant_scoped(
                db,
                db.entities,
                body.parent_system_id,
                tenant_id,
                org_fk="organization_id",
            )
        )
        if not parent:
            return ApiResponse.not_found("Parent system", body.parent_system_id)

    def create():
        _ensure_ai_resources_table(db)
        now = datetime.now(UTC)
        resource_id = db.ai_resources.insert(
            name=body.name,
            description=body.description,
            organization_id=body.organization_id,
            tenant_id=tenant_id,
            resource_type=body.resource_type,
            provider=body.provider,
            model_name=body.model_name,
            endpoint_url=body.endpoint_url,
            status=body.status,
            agreement_level=body.agreement_level,
            plan_info=body.plan_info,
            budget_monthly=body.budget_monthly,
            cost_per_10m_tokens=body.cost_per_10m_tokens,
            seats=body.seats,
            parent_system_id=body.parent_system_id,
            owner_id=body.owner_id,
            notes=body.notes,
            tags=body.tags,
            extra_metadata=body.metadata,
            is_active=body.is_active,
            created_at=now,
            updated_at=now,
        )
        db.commit()
        return get_tenant_scoped(db, db.ai_resources, resource_id, tenant_id)

    resource = await run_in_threadpool(create)
    return _row_to_dto(resource), 201


@bp.route("/<int:id>", methods=["GET"])
@login_required
@require_scope("ai:read")
@validate_response(AIResourceDTO)
async def get_ai_resource(id: int):
    """Get a single AI resource by ID."""
    db = current_app.db
    tenant_id = get_current_tenant_id()

    def fetch():
        _ensure_ai_resources_table(db)
        return get_tenant_scoped(db, db.ai_resources, id, tenant_id)

    resource = await run_in_threadpool(fetch)
    if not resource:
        return ApiResponse.not_found("AI resource", id)

    return _row_to_dto(resource), 200


@bp.route("/<int:id>", methods=["PUT"])
@login_required
@require_scope("ai:write")
@resource_role_required("maintainer")
@validated_request(body_model=UpdateAIResourceRequest)
@validate_response(AIResourceDTO)
async def update_ai_resource(id: int, body: UpdateAIResourceRequest):
    """Update an existing AI resource."""
    db = current_app.db
    tenant_id = get_current_tenant_id()

    target_tenant_id = tenant_id
    if body.organization_id is not None:
        _org, org_tenant_id, error = await validate_organization_and_get_tenant(
            body.organization_id
        )
        if error:
            return error
        if tenant_id and org_tenant_id != tenant_id:
            return ApiResponse.not_found("Organization", body.organization_id)
        target_tenant_id = org_tenant_id

    if body.owner_id is not None:
        owner = await run_in_threadpool(
            lambda: get_tenant_scoped(
                db, db.identities, body.owner_id, target_tenant_id
            )
        )
        if not owner:
            return ApiResponse.not_found("Owner identity", body.owner_id)

    if body.parent_system_id is not None:
        parent = await run_in_threadpool(
            lambda: get_tenant_scoped(
                db,
                db.entities,
                body.parent_system_id,
                target_tenant_id,
                org_fk="organization_id",
            )
        )
        if not parent:
            return ApiResponse.not_found("Parent system", body.parent_system_id)

    def update():
        _ensure_ai_resources_table(db)
        resource = get_tenant_scoped(db, db.ai_resources, id, tenant_id)
        if not resource:
            return None

        updateable_fields = [
            "name",
            "description",
            "resource_type",
            "provider",
            "model_name",
            "endpoint_url",
            "status",
            "agreement_level",
            "plan_info",
            "budget_monthly",
            "cost_per_10m_tokens",
            "seats",
            "parent_system_id",
            "owner_id",
            "notes",
            "tags",
            "is_active",
        ]

        data_dict = body.model_dump(exclude_unset=True)
        update_dict: dict[str, Any] = {}
        for field in updateable_fields:
            if field in data_dict:
                update_dict[field] = getattr(body, field)

        if "metadata" in data_dict:
            update_dict["extra_metadata"] = body.metadata

        if body.organization_id is not None:
            update_dict["organization_id"] = body.organization_id
            update_dict["tenant_id"] = target_tenant_id

        if update_dict:
            update_dict["updated_at"] = datetime.now(UTC)
            db(
                (db.ai_resources.id == id) & (db.ai_resources.tenant_id == tenant_id)
            ).update(**update_dict)
            db.commit()

        return get_tenant_scoped(db, db.ai_resources, id, target_tenant_id)

    resource = await run_in_threadpool(update)
    if not resource:
        return ApiResponse.not_found("AI resource", id)

    return _row_to_dto(resource), 200


@bp.route("/<int:id>", methods=["DELETE"])
@login_required
@require_scope("ai:write")
@resource_role_required("maintainer")
async def delete_ai_resource(id: int):
    """Delete an AI resource."""
    db = current_app.db
    tenant_id = get_current_tenant_id()

    def fetch_and_delete():
        _ensure_ai_resources_table(db)
        resource = get_tenant_scoped(db, db.ai_resources, id, tenant_id)
        if not resource:
            return False
        db(
            (db.ai_resources.id == id) & (db.ai_resources.tenant_id == tenant_id)
        ).delete()
        db.commit()
        return True

    deleted = await run_in_threadpool(fetch_and_delete)
    if not deleted:
        return ApiResponse.not_found("AI resource", id)

    return ApiResponse.no_content()
