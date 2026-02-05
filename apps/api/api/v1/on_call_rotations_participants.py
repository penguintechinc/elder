"""On-call rotation participants and overrides management."""

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

bp = Blueprint("on_call_rotations_participants", __name__)


@bp.route("/<int:rotation_id>/participants", methods=["GET"])
@login_required
async def list_participants(rotation_id: int):
    """
    List all participants in a rotation.

    Path Parameters:
        - rotation_id: Rotation ID

    Query Parameters:
        - page: Page number (default: 1)
        - per_page: Items per page (default: 50)

    Returns:
        200: List of participants
        404: Rotation not found
    """
    db = current_app.db

    rotation, error = await validate_resource_exists(
        db.on_call_rotations, rotation_id, "On-Call Rotation"
    )
    if error:
        return error

    pagination = PaginationParams.from_request()

    def get_participants():
        query = db.on_call_rotation_participants.rotation_id == rotation_id

        total = db(query).count()
        rows = db(query).select(
            db.on_call_rotation_participants.ALL,
            db.identities.username,
            db.identities.email,
            join=db.identities.on(
                db.on_call_rotation_participants.identity_id == db.identities.id
            ),
            orderby=db.on_call_rotation_participants.order_index,
            limitby=(pagination.offset, pagination.offset + pagination.per_page),
        )

        return total, rows

    total, rows = await run_in_threadpool(get_participants)

    pages = pagination.calculate_pages(total)
    items = []

    for row in rows:
        participant = row.on_call_rotation_participants
        identity = row.identities
        items.append(
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

    response = PaginatedResponse(
        items=items,
        total=total,
        page=pagination.page,
        per_page=pagination.per_page,
        pages=pages,
    )

    return jsonify(asdict(response)), 200


@bp.route("/<int:rotation_id>/participants", methods=["POST"])
@login_required
@resource_role_required("maintainer")
async def add_participant(rotation_id: int):
    """
    Add a participant to a rotation.

    Requires maintainer role.

    Path Parameters:
        - rotation_id: Rotation ID

    Request Body:
        - identity_id: Identity ID - required
        - order_index: Position in rotation - required
        - is_active: Active status (default: true)
        - start_date: Optional start date (ISO format)
        - end_date: Optional end date (ISO format)
        - notification_email: Optional email for notifications
        - notification_phone: Optional phone for notifications
        - notification_slack: Optional Slack handle for notifications

    Returns:
        201: Participant added
        400: Invalid request
        403: Insufficient permissions
        404: Rotation or identity not found
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

    if error := validate_required_fields(data, ["identity_id", "order_index"]):
        return error

    # Validate identity exists
    def validate_identity():
        return db.identities[data["identity_id"]]

    identity = await run_in_threadpool(validate_identity)
    if not identity:
        return ApiResponse.not_found("Identity", data["identity_id"])

    def create():
        insert_data = {
            "rotation_id": rotation_id,
            "identity_id": data["identity_id"],
            "order_index": data["order_index"],
            "is_active": data.get("is_active", True),
        }

        for field in ["notification_email", "notification_phone", "notification_slack"]:
            if field in data:
                insert_data[field] = data[field]

        # Handle dates
        if "start_date" in data:
            if isinstance(data["start_date"], str):
                insert_data["start_date"] = datetime.date.fromisoformat(
                    data["start_date"]
                )
            else:
                insert_data["start_date"] = data["start_date"]

        if "end_date" in data:
            if isinstance(data["end_date"], str):
                insert_data["end_date"] = datetime.date.fromisoformat(data["end_date"])
            else:
                insert_data["end_date"] = data["end_date"]

        participant_id = db.on_call_rotation_participants.insert(**insert_data)
        db.commit()

        participant = db.on_call_rotation_participants[participant_id]

        # Get identity info for response
        identity = db.identities[participant.identity_id]

        return {
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

    participant = await run_in_threadpool(create)

    return ApiResponse.created(participant)


@bp.route("/<int:rotation_id>/participants/<int:participant_id>", methods=["PUT"])
@login_required
@resource_role_required("maintainer")
async def update_participant(rotation_id: int, participant_id: int):
    """
    Update a rotation participant.

    Requires maintainer role.

    Path Parameters:
        - rotation_id: Rotation ID
        - participant_id: Participant ID

    Request Body: (all optional)
        - order_index: Position in rotation
        - is_active: Active status
        - start_date: Start date (ISO format)
        - end_date: End date (ISO format)
        - notification_email: Email for notifications
        - notification_phone: Phone for notifications
        - notification_slack: Slack handle for notifications

    Returns:
        200: Participant updated
        403: Insufficient permissions
        404: Rotation or participant not found
    """
    db = current_app.db

    rotation, error = await validate_resource_exists(
        db.on_call_rotations, rotation_id, "On-Call Rotation"
    )
    if error:
        return error

    participant, error = await validate_resource_exists(
        db.on_call_rotation_participants, participant_id, "Participant"
    )
    if error:
        return error

    data = request.get_json()
    if error := validate_json_body(data):
        return error

    def update():
        update_dict = {}
        updateable_fields = [
            "order_index",
            "is_active",
            "notification_email",
            "notification_phone",
            "notification_slack",
        ]

        for field in updateable_fields:
            if field in data:
                update_dict[field] = data[field]

        # Handle dates
        if "start_date" in data:
            if isinstance(data["start_date"], str):
                update_dict["start_date"] = datetime.date.fromisoformat(
                    data["start_date"]
                )
            else:
                update_dict["start_date"] = data["start_date"]

        if "end_date" in data:
            if isinstance(data["end_date"], str):
                update_dict["end_date"] = datetime.date.fromisoformat(data["end_date"])
            else:
                update_dict["end_date"] = data["end_date"]

        if update_dict:
            db(db.on_call_rotation_participants.id == participant_id).update(
                **update_dict
            )
            db.commit()

        participant = db.on_call_rotation_participants[participant_id]
        identity = db.identities[participant.identity_id]

        return {
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

    updated = await run_in_threadpool(update)

    return ApiResponse.success(updated)


@bp.route("/<int:rotation_id>/participants/<int:participant_id>", methods=["DELETE"])
@login_required
@resource_role_required("maintainer")
async def remove_participant(rotation_id: int, participant_id: int):
    """
    Remove a participant from a rotation.

    Requires maintainer role.

    Path Parameters:
        - rotation_id: Rotation ID
        - participant_id: Participant ID

    Returns:
        204: Participant removed
        403: Insufficient permissions
        404: Rotation or participant not found
    """
    db = current_app.db

    rotation, error = await validate_resource_exists(
        db.on_call_rotations, rotation_id, "On-Call Rotation"
    )
    if error:
        return error

    participant, error = await validate_resource_exists(
        db.on_call_rotation_participants, participant_id, "Participant"
    )
    if error:
        return error

    def delete():
        del db.on_call_rotation_participants[participant_id]
        db.commit()

    await run_in_threadpool(delete)

    return ApiResponse.no_content()


@bp.route("/<int:rotation_id>/overrides", methods=["GET"])
@login_required
async def list_overrides(rotation_id: int):
    """
    List all overrides for a rotation.

    Path Parameters:
        - rotation_id: Rotation ID

    Query Parameters:
        - page: Page number (default: 1)
        - per_page: Items per page (default: 50)

    Returns:
        200: List of overrides
        404: Rotation not found
    """
    db = current_app.db

    rotation, error = await validate_resource_exists(
        db.on_call_rotations, rotation_id, "On-Call Rotation"
    )
    if error:
        return error

    pagination = PaginationParams.from_request()

    def get_overrides():
        query = db.on_call_overrides.rotation_id == rotation_id

        total = db(query).count()
        rows = db(query).select(
            orderby=~db.on_call_overrides.created_at,
            limitby=(pagination.offset, pagination.offset + pagination.per_page),
        )

        return total, rows

    total, rows = await run_in_threadpool(get_overrides)

    pages = pagination.calculate_pages(total)
    items = []

    for row in rows:
        original_identity = db.identities[row.original_identity_id]
        override_identity = db.identities[row.override_identity_id]
        items.append(
            {
                "id": row.id,
                "rotation_id": row.rotation_id,
                "original_identity_id": row.original_identity_id,
                "original_identity_name": original_identity.username,
                "original_identity_email": original_identity.email,
                "override_identity_id": row.override_identity_id,
                "override_identity_name": override_identity.username,
                "override_identity_email": override_identity.email,
                "start_datetime": row.start_datetime,
                "end_datetime": row.end_datetime,
                "reason": row.reason,
                "created_by_id": row.created_by_id,
                "created_at": row.created_at,
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


@bp.route("/<int:rotation_id>/overrides", methods=["POST"])
@login_required
@resource_role_required("maintainer")
async def create_override(rotation_id: int):
    """
    Create an override for a rotation.

    Requires maintainer role.

    Path Parameters:
        - rotation_id: Rotation ID

    Request Body:
        - original_identity_id: Identity being replaced - required
        - override_identity_id: Replacement identity - required
        - start_datetime: Override start (ISO format) - required
        - end_datetime: Override end (ISO format) - required
        - reason: Optional reason for override
        - created_by_id: Optional ID of person creating override

    Returns:
        201: Override created
        400: Invalid request or overlapping override
        403: Insufficient permissions
        404: Rotation or identity not found
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

    required_fields = [
        "original_identity_id",
        "override_identity_id",
        "start_datetime",
        "end_datetime",
    ]
    if error := validate_required_fields(data, required_fields):
        return error

    # Validate identities exist
    def validate_identities():
        original = db.identities[data["original_identity_id"]]
        override = db.identities[data["override_identity_id"]]
        return original, override

    original_id, override_id = await run_in_threadpool(validate_identities)
    if not original_id:
        return ApiResponse.not_found("Identity", data["original_identity_id"])
    if not override_id:
        return ApiResponse.not_found("Identity", data["override_identity_id"])

    # Parse datetimes
    try:
        if isinstance(data["start_datetime"], str):
            start_dt = datetime.datetime.fromisoformat(
                data["start_datetime"].replace("Z", "+00:00")
            )
        else:
            start_dt = data["start_datetime"]

        if isinstance(data["end_datetime"], str):
            end_dt = datetime.datetime.fromisoformat(
                data["end_datetime"].replace("Z", "+00:00")
            )
        else:
            end_dt = data["end_datetime"]
    except Exception as e:
        return ApiResponse.error(f"Invalid datetime format: {str(e)}", 400)

    if start_dt >= end_dt:
        return ApiResponse.error("start_datetime must be before end_datetime", 400)

    # Check for overlapping overrides
    def check_overlap():
        overlap = db(
            (db.on_call_overrides.rotation_id == rotation_id)
            & (
                db.on_call_overrides.original_identity_id
                == data["original_identity_id"]
            )
            & (db.on_call_overrides.start_datetime < end_dt)
            & (db.on_call_overrides.end_datetime > start_dt)
        ).count()
        return overlap > 0

    has_overlap = await run_in_threadpool(check_overlap)
    if has_overlap:
        return ApiResponse.error(
            "Override period overlaps with existing override for this person", 409
        )

    def create():
        insert_data = {
            "rotation_id": rotation_id,
            "original_identity_id": data["original_identity_id"],
            "override_identity_id": data["override_identity_id"],
            "start_datetime": start_dt,
            "end_datetime": end_dt,
        }

        if "reason" in data:
            insert_data["reason"] = data["reason"]
        if "created_by_id" in data:
            insert_data["created_by_id"] = data["created_by_id"]

        override_id = db.on_call_overrides.insert(**insert_data)
        db.commit()

        override = db.on_call_overrides[override_id]
        original = db.identities[override.original_identity_id]
        override_identity = db.identities[override.override_identity_id]

        return {
            "id": override.id,
            "rotation_id": override.rotation_id,
            "original_identity_id": override.original_identity_id,
            "original_identity_name": original.username,
            "original_identity_email": original.email,
            "override_identity_id": override.override_identity_id,
            "override_identity_name": override_identity.username,
            "override_identity_email": override_identity.email,
            "start_datetime": override.start_datetime,
            "end_datetime": override.end_datetime,
            "reason": override.reason,
            "created_by_id": override.created_by_id,
            "created_at": override.created_at,
        }

    override = await run_in_threadpool(create)

    return ApiResponse.created(override)


@bp.route("/overrides/<int:override_id>", methods=["PUT"])
@login_required
@resource_role_required("maintainer")
async def update_override(override_id: int):
    """
    Update an override.

    Requires maintainer role.

    Path Parameters:
        - override_id: Override ID

    Request Body: (all optional)
        - reason: Reason for override
        - start_datetime: New start (ISO format)
        - end_datetime: New end (ISO format)

    Returns:
        200: Override updated
        403: Insufficient permissions
        404: Override not found
    """
    db = current_app.db

    override, error = await validate_resource_exists(
        db.on_call_overrides, override_id, "Override"
    )
    if error:
        return error

    data = request.get_json()
    if error := validate_json_body(data):
        return error

    def update():
        update_dict = {}

        if "reason" in data:
            update_dict["reason"] = data["reason"]

        # Handle datetimes
        if "start_datetime" in data:
            if isinstance(data["start_datetime"], str):
                update_dict["start_datetime"] = datetime.datetime.fromisoformat(
                    data["start_datetime"].replace("Z", "+00:00")
                )
            else:
                update_dict["start_datetime"] = data["start_datetime"]

        if "end_datetime" in data:
            if isinstance(data["end_datetime"], str):
                update_dict["end_datetime"] = datetime.datetime.fromisoformat(
                    data["end_datetime"].replace("Z", "+00:00")
                )
            else:
                update_dict["end_datetime"] = data["end_datetime"]

        if update_dict:
            db(db.on_call_overrides.id == override_id).update(**update_dict)
            db.commit()

        override = db.on_call_overrides[override_id]
        original = db.identities[override.original_identity_id]
        override_identity = db.identities[override.override_identity_id]

        return {
            "id": override.id,
            "rotation_id": override.rotation_id,
            "original_identity_id": override.original_identity_id,
            "original_identity_name": original.username,
            "original_identity_email": original.email,
            "override_identity_id": override.override_identity_id,
            "override_identity_name": override_identity.username,
            "override_identity_email": override_identity.email,
            "start_datetime": override.start_datetime,
            "end_datetime": override.end_datetime,
            "reason": override.reason,
            "created_by_id": override.created_by_id,
            "created_at": override.created_at,
        }

    updated = await run_in_threadpool(update)

    return ApiResponse.success(updated)


@bp.route("/overrides/<int:override_id>", methods=["DELETE"])
@login_required
@resource_role_required("maintainer")
async def delete_override(override_id: int):
    """
    Delete an override.

    Requires maintainer role.

    Path Parameters:
        - override_id: Override ID

    Returns:
        204: Override deleted
        403: Insufficient permissions
        404: Override not found
    """
    db = current_app.db

    override, error = await validate_resource_exists(
        db.on_call_overrides, override_id, "Override"
    )
    if error:
        return error

    def delete():
        del db.on_call_overrides[override_id]
        db.commit()

    await run_in_threadpool(delete)

    return ApiResponse.no_content()
