"""On-call rotation CRUD operations."""

# flake8: noqa: E501


import datetime
from dataclasses import asdict

from croniter import croniter
from flask import Blueprint, current_app, jsonify, request

from apps.api.auth.decorators import login_required, resource_role_required
from apps.api.models.dataclasses import (
    OnCallRotationDTO,
    PaginatedResponse,
    from_pydal_row,
    from_pydal_rows,
)
from apps.api.utils.api_responses import ApiResponse
from apps.api.utils.pydal_helpers import PaginationParams
from apps.api.utils.validation_helpers import (
    validate_json_body,
    validate_required_fields,
    validate_resource_exists,
)
from apps.api.utils.async_utils import run_in_threadpool

bp = Blueprint("on_call_rotations_crud", __name__)


def _get_current_oncall_for_rotation(db, rotation_id: int) -> dict:
    """
    Get the current on-call person for a rotation.

    Args:
        db: PyDAL database instance
        rotation_id: ID of the rotation

    Returns:
        Dictionary with current on-call info or None
    """
    now = datetime.datetime.now(datetime.timezone.utc)

    # Get active shift for this moment
    shift = (
        db(
            (db.on_call_shifts.rotation_id == rotation_id)
            & (db.on_call_shifts.shift_start <= now)
            & (db.on_call_shifts.shift_end > now)
        )
        .select()
        .first()
    )

    if not shift:
        return None

    # Get identity details
    identity = db.identities[shift.identity_id]
    if not identity:
        return None

    return {
        "shift_id": shift.id,
        "identity_id": identity.id,
        "identity_name": identity.username,
        "identity_email": identity.email,
        "shift_start": shift.shift_start,
        "shift_end": shift.shift_end,
        "is_override": shift.is_override,
    }


@bp.route("", methods=["GET"])
@login_required
async def list_rotations():
    """
    List on-call rotations with optional filtering.

    Query Parameters:
        - org_id: Filter by organization ID
        - service_id: Filter by service ID
        - scope_type: Filter by scope (organization, service)
        - schedule_type: Filter by schedule type (weekly, cron, manual, follow_the_sun)
        - is_active: Filter by active status (true/false)
        - page: Page number (default: 1)
        - per_page: Items per page (default: 50)

    Returns:
        200: List of rotations with pagination
        400: Invalid parameters
    """
    db = current_app.db

    pagination = PaginationParams.from_request()

    def get_rotations():
        query = db.on_call_rotations.id > 0

        # Apply filters
        if request.args.get("org_id"):
            org_id = request.args.get("org_id", type=int)
            query &= db.on_call_rotations.organization_id == org_id

        if request.args.get("service_id"):
            service_id = request.args.get("service_id", type=int)
            query &= db.on_call_rotations.service_id == service_id

        if request.args.get("scope_type"):
            query &= db.on_call_rotations.scope_type == request.args.get("scope_type")

        if request.args.get("schedule_type"):
            query &= db.on_call_rotations.schedule_type == request.args.get(
                "schedule_type"
            )

        if request.args.get("is_active"):
            is_active = request.args.get("is_active").lower() == "true"
            query &= db.on_call_rotations.is_active == is_active

        total = db(query).count()
        rows = db(query).select(
            orderby=~db.on_call_rotations.created_at,
            limitby=(pagination.offset, pagination.offset + pagination.per_page),
        )

        return total, rows

    total, rows = await run_in_threadpool(get_rotations)

    pages = pagination.calculate_pages(total)
    items = from_pydal_rows(rows, OnCallRotationDTO)

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
async def create_rotation():
    """
    Create a new on-call rotation.

    Requires maintainer role on the resource.

    Request Body:
        - name: Rotation name - required
        - scope_type: "organization" or "service" - required
        - organization_id: Organization ID (required if scope_type is organization) - required
        - service_id: Service ID (required if scope_type is service) - required
        - schedule_type: "weekly", "cron", "manual", or "follow_the_sun" - required
        - description: Optional description
        - is_active: Active status (default: true)

        For weekly schedule:
            - rotation_length_days: Length in days (7, 14, 21, etc.) - required
            - rotation_start_date: Start date (ISO format) - required

        For cron schedule:
            - schedule_cron: Cron expression - required
            - handoff_timezone: Timezone for cron (default: UTC) - optional

        For follow_the_sun schedule:
            - handoff_timezone: Timezone - required
            - shift_split: Whether to split shifts (default: false)
            - shift_config: JSON config with shift definitions - required

    Returns:
        201: Rotation created
        400: Invalid request
        403: Insufficient permissions
    """
    db = current_app.db

    data = request.get_json()
    if error := validate_json_body(data):
        return error

    required_fields = ["name", "scope_type", "schedule_type"]
    if error := validate_required_fields(data, required_fields):
        return error

    scope_type = data["scope_type"]
    schedule_type = data["schedule_type"]

    # Validate scope and get parent
    def validate_parent():
        if scope_type == "organization":
            if "organization_id" not in data:
                return None, "organization_id is required for organization scope"
            org = db.organizations[data["organization_id"]]
            return org, None
        elif scope_type == "service":
            if "service_id" not in data:
                return None, "service_id is required for service scope"
            service = db.services[data["service_id"]]
            return service, None
        return None, "scope_type must be organization or service"

    parent, parent_error = await run_in_threadpool(validate_parent)
    if parent_error:
        return ApiResponse.error(parent_error, 400)
    if not parent:
        parent_type = "Organization" if scope_type == "organization" else "Service"
        parent_id = data.get("organization_id") or data.get("service_id")
        return ApiResponse.not_found(parent_type, parent_id)

    # Validate schedule parameters based on schedule_type
    schedule_data = {}
    if schedule_type == "weekly":
        if not data.get("rotation_length_days") or not data.get("rotation_start_date"):
            return ApiResponse.error(
                "rotation_length_days and rotation_start_date required for weekly schedule",
                400,
            )
        schedule_data["rotation_length_days"] = data["rotation_length_days"]
        schedule_data["rotation_start_date"] = data["rotation_start_date"]

    elif schedule_type == "cron":
        if not data.get("schedule_cron"):
            return ApiResponse.error("schedule_cron required for cron schedule", 400)
        try:
            croniter(data["schedule_cron"])
        except Exception as e:
            return ApiResponse.error(f"Invalid cron expression: {str(e)}", 400)
        schedule_data["schedule_cron"] = data["schedule_cron"]
        schedule_data["handoff_timezone"] = data.get("handoff_timezone", "UTC")

    elif schedule_type == "follow_the_sun":
        if not data.get("handoff_timezone") or not data.get("shift_config"):
            return ApiResponse.error(
                "handoff_timezone and shift_config required for follow_the_sun schedule",
                400,
            )
        schedule_data["handoff_timezone"] = data["handoff_timezone"]
        schedule_data["shift_split"] = data.get("shift_split", False)
        schedule_data["shift_config"] = data["shift_config"]

    elif schedule_type == "manual":
        pass  # Manual schedule has no special requirements

    def create():
        insert_data = {
            "name": data["name"],
            "scope_type": scope_type,
            "schedule_type": schedule_type,
            "is_active": data.get("is_active", True),
        }

        if "description" in data:
            insert_data["description"] = data["description"]

        if scope_type == "organization":
            insert_data["organization_id"] = data["organization_id"]
        else:
            insert_data["service_id"] = data["service_id"]

        insert_data.update(schedule_data)

        rotation_id = db.on_call_rotations.insert(**insert_data)
        db.commit()

        return db.on_call_rotations[rotation_id]

    rotation = await run_in_threadpool(create)

    rotation_dto = from_pydal_row(rotation, OnCallRotationDTO)
    return ApiResponse.created(asdict(rotation_dto))


@bp.route("/<int:rotation_id>", methods=["GET"])
@login_required
async def get_rotation(rotation_id: int):
    """
    Get a single on-call rotation with participants and current on-call.

    Path Parameters:
        - rotation_id: Rotation ID

    Returns:
        200: Rotation details with participants and current on-call info
        404: Rotation not found
    """
    db = current_app.db

    rotation, error = await validate_resource_exists(
        db.on_call_rotations, rotation_id, "On-Call Rotation"
    )
    if error:
        return error

    def get_full_rotation():
        # Get rotation DTOs
        rotation_dto = from_pydal_row(rotation, OnCallRotationDTO)

        # Get participants with identity info
        participant_rows = db(
            db.on_call_rotation_participants.rotation_id == rotation_id
        ).select(
            db.on_call_rotation_participants.ALL,
            db.identities.username,
            db.identities.email,
            join=db.identities.on(
                db.on_call_rotation_participants.identity_id == db.identities.id
            ),
            orderby=db.on_call_rotation_participants.order_index,
        )

        participants = []
        for row in participant_rows:
            participant = row.on_call_rotation_participants
            identity = row.identities
            participants.append(
                {
                    "id": participant.id,
                    "rotation_id": participant.rotation_id,
                    "identity_id": participant.identity_id,
                    "identity_name": identity.username,
                    "identity_email": identity.email,
                    "order_index": participant.order_index,
                    "is_active": participant.is_active,
                    "start_date": participant.start_date,
                    "end_date": participant.end_date,
                    "notification_email": participant.notification_email,
                    "notification_phone": participant.notification_phone,
                    "notification_slack": participant.notification_slack,
                    "created_at": participant.created_at,
                    "updated_at": participant.updated_at,
                }
            )

        # Get current on-call
        current_oncall = _get_current_oncall_for_rotation(db, rotation_id)

        return {
            "rotation": asdict(rotation_dto),
            "participants": participants,
            "current_oncall": current_oncall,
        }

    result = await run_in_threadpool(get_full_rotation)

    return ApiResponse.success(result)


@bp.route("/<int:rotation_id>", methods=["PUT"])
@login_required
@resource_role_required("maintainer")
async def update_rotation(rotation_id: int):
    """
    Update an on-call rotation.

    Requires maintainer role.

    Path Parameters:
        - rotation_id: Rotation ID

    Request Body:
        - name: Optional
        - description: Optional
        - is_active: Optional
        - rotation_length_days: Optional (for weekly schedules)
        - rotation_start_date: Optional (for weekly schedules)
        - schedule_cron: Optional (for cron schedules)
        - handoff_timezone: Optional
        - shift_split: Optional (for follow_the_sun)
        - shift_config: Optional JSON (for follow_the_sun)

    Returns:
        200: Rotation updated
        400: Invalid request
        403: Insufficient permissions
        404: Rotation not found
    """
    db = current_app.db

    data = request.get_json()
    if error := validate_json_body(data):
        return error

    rotation, error = await validate_resource_exists(
        db.on_call_rotations, rotation_id, "On-Call Rotation"
    )
    if error:
        return error

    # Validate cron if being updated
    if "schedule_cron" in data:
        try:
            croniter(data["schedule_cron"])
        except Exception as e:
            return ApiResponse.error(f"Invalid cron expression: {str(e)}", 400)

    def update():
        update_dict = {}
        updateable_fields = [
            "name",
            "description",
            "is_active",
            "rotation_length_days",
            "schedule_cron",
            "handoff_timezone",
            "shift_split",
            "shift_config",
        ]

        for field in updateable_fields:
            if field in data:
                update_dict[field] = data[field]

        # Handle date fields
        if "rotation_start_date" in data:
            if isinstance(data["rotation_start_date"], str):
                update_dict["rotation_start_date"] = datetime.date.fromisoformat(
                    data["rotation_start_date"]
                )
            else:
                update_dict["rotation_start_date"] = data["rotation_start_date"]

        if update_dict:
            db(db.on_call_rotations.id == rotation_id).update(**update_dict)
            db.commit()

        return db.on_call_rotations[rotation_id]

    updated_rotation = await run_in_threadpool(update)

    rotation_dto = from_pydal_row(updated_rotation, OnCallRotationDTO)
    return ApiResponse.success(asdict(rotation_dto))


@bp.route("/<int:rotation_id>", methods=["DELETE"])
@login_required
@resource_role_required("maintainer")
async def delete_rotation(rotation_id: int):
    """
    Delete an on-call rotation.

    Requires maintainer role.

    Path Parameters:
        - rotation_id: Rotation ID

    Returns:
        204: Rotation deleted
        403: Insufficient permissions
        404: Rotation not found
    """
    db = current_app.db

    rotation, error = await validate_resource_exists(
        db.on_call_rotations, rotation_id, "On-Call Rotation"
    )
    if error:
        return error

    def delete():
        del db.on_call_rotations[rotation_id]
        db.commit()

    await run_in_threadpool(delete)

    return ApiResponse.no_content()
