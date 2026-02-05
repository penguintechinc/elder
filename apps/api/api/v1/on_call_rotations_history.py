"""On-call rotation history, escalations, and current on-call endpoints."""

# flake8: noqa: E501


import datetime
from dataclasses import asdict

from flask import Blueprint, current_app, jsonify, request

from apps.api.auth.decorators import login_required, resource_role_required
from apps.api.models.dataclasses import PaginatedResponse
from apps.api.utils.api_responses import ApiResponse
from apps.api.utils.pydal_helpers import PaginationParams
from apps.api.utils.validation_helpers import (
    validate_json_body,
    validate_required_fields,
    validate_resource_exists,
)
from apps.api.utils.async_utils import run_in_threadpool

bp = Blueprint("on_call_rotations_history", __name__)


def _get_current_oncall_for_rotation(db, rotation_id: int) -> dict:
    """Get the current on-call person for a rotation."""
    now = datetime.datetime.now(datetime.timezone.utc)

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


@bp.route("/current/organization/<int:org_id>", methods=["GET"])
@login_required
async def get_current_oncall_for_org(org_id: int):
    """
    Get current on-call person for all rotations in an organization.

    Path Parameters:
        - org_id: Organization ID

    Returns:
        200: List of current on-call for each rotation in the org
        404: Organization not found
    """
    db = current_app.db

    org, error = await validate_resource_exists(
        db.organizations, org_id, "Organization"
    )
    if error:
        return error

    def get_oncalls():
        rotations = db(
            (db.on_call_rotations.organization_id == org_id)
            & (db.on_call_rotations.is_active is True)
        ).select()

        result = []
        for rotation in rotations:
            current = _get_current_oncall_for_rotation(db, rotation.id)
            if current:
                result.append(
                    {
                        "rotation_id": rotation.id,
                        "rotation_name": rotation.name,
                        "current_oncall": current,
                    }
                )

        return result

    oncalls = await run_in_threadpool(get_oncalls)

    return ApiResponse.success({"organization_id": org_id, "rotations": oncalls})


@bp.route("/current/service/<int:service_id>", methods=["GET"])
@login_required
async def get_current_oncall_for_service(service_id: int):
    """
    Get current on-call person for all rotations associated with a service.

    Path Parameters:
        - service_id: Service ID

    Returns:
        200: List of current on-call for each rotation for the service
        404: Service not found
    """
    db = current_app.db

    org, error = await validate_resource_exists(db.services, service_id, "Service")
    if error:
        return error

    def get_oncalls():
        rotations = db(
            (db.on_call_rotations.service_id == service_id)
            & (db.on_call_rotations.is_active is True)
        ).select()

        result = []
        for rotation in rotations:
            current = _get_current_oncall_for_rotation(db, rotation.id)
            if current:
                result.append(
                    {
                        "rotation_id": rotation.id,
                        "rotation_name": rotation.name,
                        "current_oncall": current,
                    }
                )

        return result

    oncalls = await run_in_threadpool(get_oncalls)

    return ApiResponse.success({"service_id": service_id, "rotations": oncalls})


@bp.route("/<int:rotation_id>/history", methods=["GET"])
@login_required
async def get_shift_history(rotation_id: int):
    """
    Get shift history for a rotation.

    Path Parameters:
        - rotation_id: Rotation ID

    Query Parameters:
        - start_date: Filter by start date (ISO format)
        - end_date: Filter by end date (ISO format)
        - identity_id: Filter by specific identity
        - page: Page number (default: 1)
        - per_page: Items per page (default: 50)

    Returns:
        200: List of shifts
        404: Rotation not found
    """
    db = current_app.db

    rotation, error = await validate_resource_exists(
        db.on_call_rotations, rotation_id, "On-Call Rotation"
    )
    if error:
        return error

    pagination = PaginationParams.from_request()

    def get_history():
        query = db.on_call_shifts.rotation_id == rotation_id

        # Apply filters
        if request.args.get("start_date"):
            try:
                start_date = datetime.datetime.fromisoformat(
                    request.args.get("start_date")
                )
                query &= db.on_call_shifts.shift_start >= start_date
            except Exception:
                pass

        if request.args.get("end_date"):
            try:
                end_date = datetime.datetime.fromisoformat(request.args.get("end_date"))
                query &= db.on_call_shifts.shift_end <= end_date
            except Exception:
                pass

        if request.args.get("identity_id"):
            identity_id = request.args.get("identity_id", type=int)
            query &= db.on_call_shifts.identity_id == identity_id

        total = db(query).count()
        rows = db(query).select(
            db.on_call_shifts.ALL,
            db.identities.username,
            join=db.identities.on(db.on_call_shifts.identity_id == db.identities.id),
            orderby=~db.on_call_shifts.shift_start,
            limitby=(pagination.offset, pagination.offset + pagination.per_page),
        )

        return total, rows

    total, rows = await run_in_threadpool(get_history)

    pages = pagination.calculate_pages(total)
    items = []

    for row in rows:
        shift = row.on_call_shifts
        identity = row.identities
        items.append(
            {
                "id": shift.id,
                "rotation_id": shift.rotation_id,
                "identity_id": shift.identity_id,
                "identity_name": identity.username,
                "shift_start": shift.shift_start,
                "shift_end": shift.shift_end,
                "is_override": shift.is_override,
                "override_id": shift.override_id,
                "alerts_received": shift.alerts_received,
                "incidents_created": shift.incidents_created,
                "created_at": shift.created_at,
            }
        )

    response = PaginatedResponse(
        items=items,
        total=total,
        page=pagination.page,
        per_page=pagination.per_page,
        pages=pages,
    )

    return jsonify(asdict(response)), 200


@bp.route("/<int:rotation_id>/escalations", methods=["GET"])
@login_required
async def list_escalations(rotation_id: int):
    """
    List escalation policies for a rotation.

    Path Parameters:
        - rotation_id: Rotation ID

    Query Parameters:
        - page: Page number (default: 1)
        - per_page: Items per page (default: 50)

    Returns:
        200: List of escalation policies
        404: Rotation not found
    """
    db = current_app.db

    rotation, error = await validate_resource_exists(
        db.on_call_rotations, rotation_id, "On-Call Rotation"
    )
    if error:
        return error

    pagination = PaginationParams.from_request()

    def get_escalations():
        query = db.on_call_escalation_policies.rotation_id == rotation_id

        total = db(query).count()
        rows = db(query).select(
            orderby=db.on_call_escalation_policies.level,
            limitby=(pagination.offset, pagination.offset + pagination.per_page),
        )

        return total, rows

    total, rows = await run_in_threadpool(get_escalations)

    pages = pagination.calculate_pages(total)
    items = []

    for row in rows:
        item = {
            "id": row.id,
            "rotation_id": row.rotation_id,
            "level": row.level,
            "escalation_type": row.escalation_type,
            "identity_id": row.identity_id,
            "group_id": row.group_id,
            "escalation_delay_minutes": row.escalation_delay_minutes,
            "notification_channels": row.notification_channels,
            "created_at": row.created_at,
            "updated_at": row.updated_at,
        }

        # Add joined info if available
        if row.escalation_type == "identity" and row.identity_id:
            identity = db.identities[row.identity_id]
            if identity:
                item["identity_name"] = identity.username

        elif row.escalation_type == "group" and row.group_id:
            group = db.identity_groups[row.group_id]
            if group:
                item["group_name"] = group.name

        items.append(item)

    response = PaginatedResponse(
        items=items,
        total=total,
        page=pagination.page,
        per_page=pagination.per_page,
        pages=pages,
    )

    return jsonify(asdict(response)), 200


@bp.route("/<int:rotation_id>/escalations", methods=["POST"])
@login_required
@resource_role_required("maintainer")
async def create_escalation(rotation_id: int):
    """
    Create an escalation policy for a rotation.

    Requires maintainer role.

    Path Parameters:
        - rotation_id: Rotation ID

    Request Body:
        - level: Escalation level (1, 2, 3, ...) - required
        - escalation_type: "identity", "group", or "rotation_participant" - required
        - identity_id: Required if escalation_type is "identity"
        - group_id: Required if escalation_type is "group"
        - escalation_delay_minutes: Wait before escalating (default: 15)
        - notification_channels: List of channels (email, sms, slack)

    Returns:
        201: Escalation policy created
        400: Invalid request
        403: Insufficient permissions
        404: Rotation or target not found
    """
    db = current_app.db

    rotation, error = await validate_resource_exists(
        db.on_call_rotations, rotation_id, "On-Call Rotation"
    )
    if error:
        return error

    data = request.get_json()
    if error := validate_json_body(data):
        return error

    required_fields = ["level", "escalation_type"]
    if error := validate_required_fields(data, required_fields):
        return error

    escalation_type = data["escalation_type"]

    # Validate target exists based on escalation_type
    def validate_target():
        if escalation_type == "identity":
            if "identity_id" not in data:
                return None, "identity_id required for identity escalation"
            return db.identities[data["identity_id"]], None
        elif escalation_type == "group":
            if "group_id" not in data:
                return None, "group_id required for group escalation"
            return db.identity_groups[data["group_id"]], None
        elif escalation_type == "rotation_participant":
            return "rotation_participant", None
        return None, "escalation_type must be identity, group, or rotation_participant"

    target, target_error = await run_in_threadpool(validate_target)
    if target_error:
        return ApiResponse.error(target_error, 400)
    if target is None and escalation_type != "rotation_participant":
        return ApiResponse.not_found(
            "Target", data.get("identity_id") or data.get("group_id")
        )

    def create():
        insert_data = {
            "rotation_id": rotation_id,
            "level": data["level"],
            "escalation_type": escalation_type,
            "escalation_delay_minutes": data.get("escalation_delay_minutes", 15),
        }

        if "identity_id" in data:
            insert_data["identity_id"] = data["identity_id"]
        if "group_id" in data:
            insert_data["group_id"] = data["group_id"]
        if "notification_channels" in data:
            insert_data["notification_channels"] = data["notification_channels"]

        policy_id = db.on_call_escalation_policies.insert(**insert_data)
        db.commit()

        policy = db.on_call_escalation_policies[policy_id]

        result = {
            "id": policy.id,
            "rotation_id": policy.rotation_id,
            "level": policy.level,
            "escalation_type": policy.escalation_type,
            "identity_id": policy.identity_id,
            "group_id": policy.group_id,
            "escalation_delay_minutes": policy.escalation_delay_minutes,
            "notification_channels": policy.notification_channels,
            "created_at": policy.created_at,
            "updated_at": policy.updated_at,
        }

        if policy.escalation_type == "identity" and policy.identity_id:
            identity = db.identities[policy.identity_id]
            if identity:
                result["identity_name"] = identity.username

        elif policy.escalation_type == "group" and policy.group_id:
            group = db.identity_groups[policy.group_id]
            if group:
                result["group_name"] = group.name

        return result

    policy = await run_in_threadpool(create)

    return ApiResponse.created(policy)


@bp.route("/escalations/<int:policy_id>", methods=["PUT"])
@login_required
@resource_role_required("maintainer")
async def update_escalation(policy_id: int):
    """
    Update an escalation policy.

    Requires maintainer role.

    Path Parameters:
        - policy_id: Escalation policy ID

    Request Body: (all optional)
        - level: Escalation level
        - escalation_delay_minutes: Wait before escalating
        - notification_channels: Notification channels list

    Returns:
        200: Policy updated
        403: Insufficient permissions
        404: Policy not found
    """
    db = current_app.db

    policy, error = await validate_resource_exists(
        db.on_call_escalation_policies, policy_id, "Escalation Policy"
    )
    if error:
        return error

    data = request.get_json()
    if error := validate_json_body(data):
        return error

    def update():
        update_dict = {}
        updateable_fields = [
            "level",
            "escalation_delay_minutes",
            "notification_channels",
        ]

        for field in updateable_fields:
            if field in data:
                update_dict[field] = data[field]

        if update_dict:
            db(db.on_call_escalation_policies.id == policy_id).update(**update_dict)
            db.commit()

        policy = db.on_call_escalation_policies[policy_id]

        result = {
            "id": policy.id,
            "rotation_id": policy.rotation_id,
            "level": policy.level,
            "escalation_type": policy.escalation_type,
            "identity_id": policy.identity_id,
            "group_id": policy.group_id,
            "escalation_delay_minutes": policy.escalation_delay_minutes,
            "notification_channels": policy.notification_channels,
            "created_at": policy.created_at,
            "updated_at": policy.updated_at,
        }

        if policy.escalation_type == "identity" and policy.identity_id:
            identity = db.identities[policy.identity_id]
            if identity:
                result["identity_name"] = identity.username

        elif policy.escalation_type == "group" and policy.group_id:
            group = db.identity_groups[policy.group_id]
            if group:
                result["group_name"] = group.name

        return result

    updated = await run_in_threadpool(update)

    return ApiResponse.success(updated)


@bp.route("/escalations/<int:policy_id>", methods=["DELETE"])
@login_required
@resource_role_required("maintainer")
async def delete_escalation(policy_id: int):
    """
    Delete an escalation policy.

    Requires maintainer role.

    Path Parameters:
        - policy_id: Escalation policy ID

    Returns:
        204: Policy deleted
        403: Insufficient permissions
        404: Policy not found
    """
    db = current_app.db

    policy, error = await validate_resource_exists(
        db.on_call_escalation_policies, policy_id, "Escalation Policy"
    )
    if error:
        return error

    def delete():
        del db.on_call_escalation_policies[policy_id]
        db.commit()

    await run_in_threadpool(delete)

    return ApiResponse.no_content()
