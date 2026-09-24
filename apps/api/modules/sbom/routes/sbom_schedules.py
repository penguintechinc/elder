"""SBOM scan schedules management API endpoints for Elder using PyDAL with async/await and shared helpers."""

# flake8: noqa: E501

import datetime
from dataclasses import asdict
from datetime import timezone

from croniter import croniter
from quart import Blueprint, current_app, jsonify, request

from apps.api.auth.decorators import (
    login_required,
    require_scope,
    resource_role_required,
)
from apps.api.models.dataclasses import (
    PaginatedResponse,
    SBOMScanScheduleDTO,
    from_pydal_row,
    from_pydal_rows,
)
from apps.api.utils.api_responses import ApiResponse
from apps.api.utils.async_utils import run_in_threadpool
from apps.api.utils.pydal_helpers import PaginationParams
from apps.api.utils.tenant_scoping import get_current_tenant_id, get_tenant_scoped
from apps.api.utils.validation_helpers import (
    validate_json_body,
    validate_required_fields,
)

bp = Blueprint("sbom_schedules", __name__)


@bp.route("", methods=["GET"])
@login_required
@require_scope("sbom:read")
async def list_schedules():
    """
    List SBOM scan schedules with optional filtering.

    Query Parameters:
        - parent_type: Filter by parent type (service, software)
        - parent_id: Filter by parent ID
        - is_active: Filter by active status (true/false)
        - page: Page number (default: 1)
        - per_page: Items per page (default: 50)

    Returns:
        200: List of schedules with pagination
        400: Invalid parameters

    Example:
        GET /api/v1/sbom/schedules?parent_type=service&is_active=true
    """
    db = current_app.db

    # Get pagination params using helper
    pagination = PaginationParams.from_request()

    # Build query
    def get_schedules():
        query = db.sbom_scan_schedules.id > 0

        # Apply filters
        if request.args.get("parent_type"):
            query &= db.sbom_scan_schedules.parent_type == request.args.get(
                "parent_type"
            )

        if request.args.get("parent_id"):
            parent_id = request.args.get("parent_id", type=int)
            query &= db.sbom_scan_schedules.parent_id == parent_id

        if request.args.get("is_active"):
            is_active = request.args.get("is_active").lower() == "true"
            query &= db.sbom_scan_schedules.is_active == is_active

        # Get count and rows
        total = db(query).count()
        rows = db(query).select(
            orderby=~db.sbom_scan_schedules.created_at,
            limitby=(pagination.offset, pagination.offset + pagination.per_page),
        )

        return total, rows

    total, rows = await run_in_threadpool(get_schedules)

    # Calculate total pages using helper
    pages = pagination.calculate_pages(total)

    # Convert to DTOs
    items = from_pydal_rows(rows, SBOMScanScheduleDTO)

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
@require_scope("sbom:write")
async def create_schedule():
    """
    Create a new SBOM scan schedule.

    Requires viewer role on the resource.

    Request Body:
        - parent_type: Parent type (service, software) - required
        - parent_id: Parent ID - required
        - schedule_cron: Cron expression (e.g., "0 0 * * *" for daily at midnight) - required
        - is_active: Active status (default: true)
        - credential_type: Credential type (builtin_secrets, vault, etc.) - optional
        - credential_id: Credential ID reference - optional
        - credential_mapping: JSON mapping of credential fields - optional

    Returns:
        201: Schedule created
        400: Invalid request (including invalid cron expression)
        403: Insufficient permissions

    Example:
        POST /api/v1/sbom/schedules
        {
            "parent_type": "service",
            "parent_id": 1,
            "schedule_cron": "0 0 * * *"
        }
    """
    db = current_app.db
    tenant_id = get_current_tenant_id()

    # Validate JSON body
    data = await request.get_json()
    if error := validate_json_body(data):
        return error

    # Validate required fields
    if error := validate_required_fields(
        data, ["parent_type", "parent_id", "schedule_cron"]
    ):
        return error

    parent_type = data["parent_type"]
    parent_id = data["parent_id"]
    schedule_cron = data["schedule_cron"]

    # Validate parent exists and belongs to caller's tenant (gh-237)
    def validate_parent():
        if parent_type == "service":
            return get_tenant_scoped(db, db.services, parent_id, tenant_id)
        elif parent_type == "software":
            return get_tenant_scoped(db, db.software, parent_id, tenant_id)
        return None

    parent = await run_in_threadpool(validate_parent)
    if not parent:
        return ApiResponse.not_found(parent_type, parent_id)

    # Validate cron expression
    try:
        now = datetime.datetime.now(datetime.UTC)
        cron = croniter(schedule_cron, now)
        next_run_at = cron.get_next(datetime.datetime)
    except Exception as e:
        return ApiResponse.error(f"Invalid cron expression: {str(e)}", 400)

    def create():
        now = datetime.datetime.now(datetime.UTC)
        # Create schedule record
        insert_data = {
            "tenant_id": tenant_id,
            "parent_type": parent_type,
            "parent_id": parent_id,
            "schedule_cron": schedule_cron,
            "is_active": data.get("is_active", True),
            "next_run_at": next_run_at,
            "created_at": now,
            "updated_at": now,
        }

        # Add credential fields if provided
        if "credential_type" in data:
            insert_data["credential_type"] = data["credential_type"]
        if "credential_id" in data:
            insert_data["credential_id"] = data["credential_id"]
        if "credential_mapping" in data:
            insert_data["credential_mapping"] = data["credential_mapping"]

        schedule_id = db.sbom_scan_schedules.insert(**insert_data)
        db.commit()

        return get_tenant_scoped(db, db.sbom_scan_schedules, schedule_id, tenant_id)

    schedule = await run_in_threadpool(create)

    schedule_dto = from_pydal_row(schedule, SBOMScanScheduleDTO)
    return ApiResponse.created(asdict(schedule_dto))


@bp.route("/<int:id>", methods=["GET"])
@login_required
@require_scope("sbom:read")
async def get_schedule(id: int):
    """
    Get a single SBOM scan schedule by ID.

    Path Parameters:
        - id: Schedule ID

    Returns:
        200: Schedule details
        404: Schedule not found

    Example:
        GET /api/v1/sbom/schedules/1
    """
    db = current_app.db
    tenant_id = get_current_tenant_id()

    # Validate resource exists and belongs to caller's tenant (gh-237)
    schedule = await run_in_threadpool(
        lambda: get_tenant_scoped(db, db.sbom_scan_schedules, id, tenant_id)
    )
    if not schedule:
        return ApiResponse.not_found("SBOM Scan Schedule", id)

    schedule_dto = from_pydal_row(schedule, SBOMScanScheduleDTO)
    return ApiResponse.success(asdict(schedule_dto))


@bp.route("/<int:id>", methods=["PUT"])
@login_required
@require_scope("sbom:write")
@resource_role_required("maintainer")
async def update_schedule(id: int):
    """
    Update an SBOM scan schedule.

    Requires maintainer role.

    Path Parameters:
        - id: Schedule ID

    Request Body:
        - schedule_cron: Cron expression (optional)
        - is_active: Active status (optional)
        - last_run_at: Last run timestamp (optional, ISO format)
        - next_run_at: Next run timestamp (optional, ISO format)
        - credential_type: Credential type (builtin_secrets, vault, etc.) - optional
        - credential_id: Credential ID reference - optional
        - credential_mapping: JSON mapping of credential fields - optional

    Returns:
        200: Schedule updated
        400: Invalid request
        403: Insufficient permissions
        404: Schedule not found

    Example:
        PUT /api/v1/sbom/schedules/1
        {
            "is_active": false
        }
    """
    db = current_app.db
    tenant_id = get_current_tenant_id()

    # Validate JSON body
    data = await request.get_json()
    if error := validate_json_body(data):
        return error

    # Validate schedule exists and belongs to caller's tenant (gh-237)
    schedule = await run_in_threadpool(
        lambda: get_tenant_scoped(db, db.sbom_scan_schedules, id, tenant_id)
    )
    if not schedule:
        return ApiResponse.not_found("SBOM Scan Schedule", id)

    # Validate cron expression if being updated
    if "schedule_cron" in data:
        try:
            now = datetime.datetime.now(datetime.UTC)
            cron = croniter(data["schedule_cron"], now)
            # Calculate new next_run_at if cron is changed
            data["next_run_at"] = cron.get_next(datetime.datetime)
        except Exception as e:
            return ApiResponse.error(f"Invalid cron expression: {str(e)}", 400)

    def update():
        # Update fields
        update_dict = {}
        updateable_fields = [
            "schedule_cron",
            "is_active",
            "credential_type",
            "credential_id",
            "credential_mapping",
        ]

        for field in updateable_fields:
            if field in data:
                update_dict[field] = data[field]

        # Handle datetime fields
        if "last_run_at" in data:
            if isinstance(data["last_run_at"], str):
                update_dict["last_run_at"] = datetime.datetime.fromisoformat(
                    data["last_run_at"].replace("Z", "+00:00")
                )
            else:
                update_dict["last_run_at"] = data["last_run_at"]

        if "next_run_at" in data:
            if isinstance(data["next_run_at"], str):
                update_dict["next_run_at"] = datetime.datetime.fromisoformat(
                    data["next_run_at"].replace("Z", "+00:00")
                )
            elif isinstance(data["next_run_at"], datetime.datetime):
                update_dict["next_run_at"] = data["next_run_at"]

        if update_dict:
            db(db.sbom_scan_schedules.id == id).update(**update_dict)
            db.commit()

        return db.sbom_scan_schedules[id]  # tenant-scope-exempt: verified above

    updated_schedule = await run_in_threadpool(update)

    schedule_dto = from_pydal_row(updated_schedule, SBOMScanScheduleDTO)
    return ApiResponse.success(asdict(schedule_dto))


@bp.route("/<int:id>", methods=["DELETE"])
@login_required
@require_scope("sbom:write")
@resource_role_required("maintainer")
async def delete_schedule(id: int):
    """
    Delete an SBOM scan schedule.

    Requires maintainer role.

    Path Parameters:
        - id: Schedule ID

    Returns:
        204: Schedule deleted
        403: Insufficient permissions
        404: Schedule not found

    Example:
        DELETE /api/v1/sbom/schedules/1
    """
    db = current_app.db
    tenant_id = get_current_tenant_id()

    # Validate resource exists and belongs to caller's tenant (gh-237)
    schedule = await run_in_threadpool(
        lambda: get_tenant_scoped(db, db.sbom_scan_schedules, id, tenant_id)
    )
    if not schedule:
        return ApiResponse.not_found("SBOM Scan Schedule", id)

    def delete():
        del db.sbom_scan_schedules[id]  # tenant-scope-exempt: verified above
        db.commit()

    await run_in_threadpool(delete)

    return ApiResponse.no_content()


@bp.route("/due", methods=["GET"])
@login_required
@require_scope("sbom:read")
async def get_due_schedules():
    """
    Get due SBOM scan schedules (for scanner worker).

    This endpoint is used by the scanner worker to fetch all active schedules
    that are due to run (where next_run_at <= now). Results are not paginated
    since the worker needs all due schedules to process them efficiently.

    Returns:
        200: List of due schedules (ordered by next_run_at ascending, oldest first)

    Example:
        GET /api/v1/sbom/schedules/due
    """
    db = current_app.db
    tenant_id = get_current_tenant_id()

    def get_schedules():
        # gh-237: scope to caller's tenant -- this endpoint previously
        # returned every tenant's due schedules (including credential_id/
        # credential_mapping fields) to any caller with sbom:read.
        #
        # Pre-existing bug fixed in passing: `is True` is a Python identity
        # check (always False for a PyDAL Field), not a query condition --
        # `False & Query(...)` then raises TypeError ("unsupported operand
        # type(s) for &: 'bool' and 'Query'"), meaning this endpoint 500'd
        # unconditionally before this fix.
        now = datetime.datetime.now(datetime.UTC)
        query = (
            (db.sbom_scan_schedules.is_active == True)  # noqa: E712
            & (db.sbom_scan_schedules.next_run_at <= now)
            & (db.sbom_scan_schedules.tenant_id == tenant_id)
        )
        rows = db(query).select(orderby=db.sbom_scan_schedules.next_run_at)
        return rows

    rows = await run_in_threadpool(get_schedules)

    # Convert to DTOs
    items = from_pydal_rows(rows, SBOMScanScheduleDTO)

    # Return list directly (not paginated)
    return jsonify([asdict(item) for item in items]), 200
