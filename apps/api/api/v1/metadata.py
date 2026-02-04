"""Metadata management API endpoints for Elder enterprise features using PyDAL with async/await."""

# flake8: noqa: E501


import json
from datetime import datetime
from typing import Optional, Union

from flask import Blueprint, current_app, jsonify
from penguin_libs.pydantic import RequestModel
from penguin_libs.pydantic.flask_integration import validated_request

from apps.api.auth.decorators import login_required, resource_role_required
from apps.api.utils.async_utils import run_in_threadpool
from penguin_licensing import license_required

bp = Blueprint("metadata", __name__)


# ============================================================================
# Pydantic Models for Request Validation
# ============================================================================


class CreateMetadataRequest(RequestModel):
    """Request model for creating metadata fields."""

    key: str
    field_type: str
    value: Union[str, int, float, bool, dict, list]


class UpdateMetadataRequest(RequestModel):
    """Request model for updating metadata fields."""

    value: Union[str, int, float, bool, dict, list]
    field_type: Optional[str] = None


# Helper functions for type conversion
def _coerce_value(value, field_type: str):
    """Convert value to appropriate type based on field_type."""
    if field_type == "string":
        return str(value)
    elif field_type == "number":
        try:
            return float(value) if "." in str(value) else int(value)
        except (ValueError, TypeError):
            raise ValueError(f"Invalid number value: {value}")
    elif field_type == "boolean":
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.lower() in ("true", "1", "yes")
        return bool(value)
    elif field_type == "date":
        if isinstance(value, datetime):
            return value.isoformat()
        return str(value)
    elif field_type == "json":
        if isinstance(value, (dict, list)):
            return json.dumps(value)
        return str(value)
    else:
        return str(value)


def _parse_value(value_str: str, field_type: str):
    """Parse stored string value back to original type."""
    if value_str is None:
        return None

    if field_type == "string":
        return value_str
    elif field_type == "number":
        try:
            return float(value_str) if "." in value_str else int(value_str)
        except ValueError:
            return value_str
    elif field_type == "boolean":
        return value_str.lower() in ("true", "1", "yes")
    elif field_type == "date":
        return value_str
    elif field_type == "json":
        try:
            return json.loads(value_str)
        except json.JSONDecodeError:
            return value_str
    else:
        return value_str


# ============================================================================
# Entity Metadata Endpoints
# ============================================================================


@bp.route("/entities/<int:id>/metadata", methods=["GET"])
@login_required
@license_required("enterprise")
@resource_role_required("viewer", resource_param="id")
async def get_entity_metadata(id: int):
    """
    Get all metadata for an entity.

    Requires viewer role on the entity.

    Path Parameters:
        - id: Entity ID

    Returns:
        200: Metadata as key-value dictionary
        403: License required or insufficient permissions
        404: Entity not found

    Example:
        GET /api/v1/metadata/entities/42/metadata
        {
            "metadata": {
                "hostname": "web-01.example.com",
                "ip_address": "10.0.1.5",
                "last_updated": "2024-10-23T10:00:00Z",
                "is_production": true,
                "cpu_count": 8
            }
        }
    """
    db = current_app.db

    def get_metadata():
        # Verify entity exists
        entity = db.entities[id]
        if not entity:
            return None, "Entity not found", 404

        # Get all metadata fields
        fields = db(
            (db.metadata_fields.resource_type == "entity")
            & (db.metadata_fields.resource_id == id)
        ).select()

        # Build metadata dictionary with type conversion
        metadata = {}
        for field in fields:
            metadata[field.key] = _parse_value(field.value, field.field_type)

        return {"metadata": metadata}, None, None

    result, error, status = await run_in_threadpool(get_metadata)

    if error:
        return jsonify({"error": error}), status

    return jsonify(result), 200


@bp.route("/entities/<int:id>/metadata", methods=["POST"])
@login_required
@license_required("enterprise")
@resource_role_required("maintainer", resource_param="id")
@validated_request(body_model=CreateMetadataRequest)
async def create_entity_metadata(id: int, body: CreateMetadataRequest):
    """
    Create or update a metadata field for an entity.

    Requires maintainer role on the entity.

    Path Parameters:
        - id: Entity ID

    Request Body:
        {
            "key": "hostname",
            "field_type": "string",
            "value": "web-01.example.com"
        }

    Returns:
        201: Metadata field created/updated
        400: Invalid request
        403: License required or insufficient permissions
        404: Entity not found

    Example:
        POST /api/v1/metadata/entities/42/metadata
    """
    db = current_app.db

    def create_or_update():
        # Verify entity exists
        entity = db.entities[id]
        if not entity:
            return None, "Entity not found", 404

        # Check if system metadata
        existing = (
            db(
                (db.metadata_fields.resource_type == "entity")
                & (db.metadata_fields.resource_id == id)
                & (db.metadata_fields.key == body.key)
            )
            .select()
            .first()
        )

        if existing and existing.is_system:
            return None, "System metadata fields cannot be modified", 403

        # Coerce value to correct type
        try:
            coerced_value = _coerce_value(body.value, body.field_type)
            value_str = str(coerced_value)
        except ValueError as e:
            return None, str(e), 400

        # Create or update metadata field
        if existing:
            # Update existing
            db(db.metadata_fields.id == existing.id).update(
                field_type=body.field_type,
                value=value_str,
            )
            db.commit()
            field = db.metadata_fields[existing.id]
        else:
            # Create new
            field_id = db.metadata_fields.insert(
                key=body.key,
                value=value_str,
                field_type=body.field_type,
                resource_type="entity",
                resource_id=id,
                is_system=False,
            )
            db.commit()
            field = db.metadata_fields[field_id]

        # Build response with parsed value
        field_dict = field.as_dict()
        field_dict["value"] = _parse_value(field.value, field.field_type)

        return field_dict, None, None

    result, error, status = await run_in_threadpool(create_or_update)

    if error:
        return jsonify({"error": error}), status

    return jsonify(result), 201


@bp.route("/entities/<int:id>/metadata/<string:field_key>", methods=["PATCH"])
@login_required
@license_required("enterprise")
@resource_role_required("maintainer", resource_param="id")
@validated_request(body_model=UpdateMetadataRequest)
async def update_entity_metadata(id: int, field_key: str, body: UpdateMetadataRequest):
    """
    Update a metadata field for an entity.

    Requires maintainer role on the entity.

    Path Parameters:
        - id: Entity ID
        - field_key: Metadata field key

    Request Body:
        {
            "value": "web-02.example.com",
            "field_type": "string" (optional)
        }

    Returns:
        200: Metadata field updated
        400: Invalid request
        403: License required or insufficient permissions
        404: Entity or metadata field not found

    Example:
        PATCH /api/v1/metadata/entities/42/metadata/hostname
    """
    db = current_app.db

    def update():
        # Verify entity exists
        entity = db.entities[id]
        if not entity:
            return None, "Entity not found", 404

        # Get metadata field
        field = (
            db(
                (db.metadata_fields.resource_type == "entity")
                & (db.metadata_fields.resource_id == id)
                & (db.metadata_fields.key == field_key)
            )
            .select()
            .first()
        )

        if not field:
            return None, f"Metadata field '{field_key}' not found", 404

        # Check if system metadata
        if field.is_system:
            return None, "System metadata fields cannot be modified", 403

        # Get field type (use provided or existing)
        field_type = body.field_type if body.field_type else field.field_type

        # Coerce value to correct type
        try:
            coerced_value = _coerce_value(body.value, field_type)
            value_str = str(coerced_value)
        except ValueError as e:
            return None, str(e), 400

        # Update field
        update_fields = {"value": value_str}
        if body.field_type:
            update_fields["field_type"] = body.field_type

        db(db.metadata_fields.id == field.id).update(**update_fields)
        db.commit()

        # Fetch updated field
        updated_field = db.metadata_fields[field.id]

        # Build response with parsed value
        field_dict = updated_field.as_dict()
        field_dict["value"] = _parse_value(
            updated_field.value, updated_field.field_type
        )

        return field_dict, None, None

    result, error, status = await run_in_threadpool(update)

    if error:
        return jsonify({"error": error}), status

    return jsonify(result), 200


@bp.route("/entities/<int:id>/metadata/<string:field_key>", methods=["DELETE"])
@login_required
@license_required("enterprise")
@resource_role_required("maintainer", resource_param="id")
async def delete_entity_metadata(id: int, field_key: str):
    """
    Delete a metadata field from an entity.

    Requires maintainer role on the entity.

    Path Parameters:
        - id: Entity ID
        - field_key: Metadata field key

    Returns:
        204: Metadata field deleted
        403: License required or insufficient permissions
        404: Entity or metadata field not found

    Example:
        DELETE /api/v1/metadata/entities/42/metadata/hostname
    """
    db = current_app.db

    def delete():
        # Verify entity exists
        entity = db.entities[id]
        if not entity:
            return None, "Entity not found", 404

        # Get metadata field
        field = (
            db(
                (db.metadata_fields.resource_type == "entity")
                & (db.metadata_fields.resource_id == id)
                & (db.metadata_fields.key == field_key)
            )
            .select()
            .first()
        )

        if not field:
            return None, f"Metadata field '{field_key}' not found", 404

        # Check if system metadata
        if field.is_system:
            return None, "System metadata fields cannot be deleted", 403

        # Delete field
        db(db.metadata_fields.id == field.id).delete()
        db.commit()

        return True, None, None

    result, error, status = await run_in_threadpool(delete)

    if error:
        return jsonify({"error": error}), status

    return "", 204


# ============================================================================
# Organization Metadata Endpoints
# ============================================================================


@bp.route("/organizations/<int:id>/metadata", methods=["GET"])
@login_required
@license_required("enterprise")
@resource_role_required("viewer", resource_param="id")
async def get_organization_metadata(id: int):
    """
    Get all metadata for an organization.

    Requires viewer role on the organization.

    Path Parameters:
        - id: Organization ID

    Returns:
        200: Metadata as key-value dictionary
        403: License required or insufficient permissions
        404: Organization not found

    Example:
        GET /api/v1/metadata/organizations/1/metadata
        {
            "metadata": {
                "budget": 1000000,
                "fiscal_year": "2024",
                "cost_center": "CC-1234"
            }
        }
    """
    db = current_app.db

    def get_metadata():
        # Verify organization exists
        org = db.organizations[id]
        if not org:
            return None, "Organization not found", 404

        # Get all metadata fields
        fields = db(
            (db.metadata_fields.resource_type == "organization")
            & (db.metadata_fields.resource_id == id)
        ).select()

        # Build metadata dictionary with type conversion
        metadata = {}
        for field in fields:
            metadata[field.key] = _parse_value(field.value, field.field_type)

        return {"metadata": metadata}, None, None

    result, error, status = await run_in_threadpool(get_metadata)

    if error:
        return jsonify({"error": error}), status

    return jsonify(result), 200


@bp.route("/organizations/<int:id>/metadata", methods=["POST"])
@login_required
@license_required("enterprise")
@resource_role_required("maintainer", resource_param="id")
@validated_request(body_model=CreateMetadataRequest)
async def create_organization_metadata(id: int, body: CreateMetadataRequest):
    """
    Create or update a metadata field for an organization.

    Requires maintainer role on the organization.

    Path Parameters:
        - id: Organization ID

    Request Body:
        {
            "key": "budget",
            "field_type": "number",
            "value": 1000000
        }

    Returns:
        201: Metadata field created/updated
        400: Invalid request
        403: License required or insufficient permissions
        404: Organization not found

    Example:
        POST /api/v1/metadata/organizations/1/metadata
    """
    db = current_app.db

    def create_or_update():
        # Verify organization exists
        org = db.organizations[id]
        if not org:
            return None, "Organization not found", 404

        # Check if system metadata
        existing = (
            db(
                (db.metadata_fields.resource_type == "organization")
                & (db.metadata_fields.resource_id == id)
                & (db.metadata_fields.key == body.key)
            )
            .select()
            .first()
        )

        if existing and existing.is_system:
            return None, "System metadata fields cannot be modified", 403

        # Coerce value to correct type
        try:
            coerced_value = _coerce_value(body.value, body.field_type)
            value_str = str(coerced_value)
        except ValueError as e:
            return None, str(e), 400

        # Create or update metadata field
        if existing:
            # Update existing
            db(db.metadata_fields.id == existing.id).update(
                field_type=body.field_type,
                value=value_str,
            )
            db.commit()
            field = db.metadata_fields[existing.id]
        else:
            # Create new
            field_id = db.metadata_fields.insert(
                key=body.key,
                value=value_str,
                field_type=body.field_type,
                resource_type="organization",
                resource_id=id,
                is_system=False,
            )
            db.commit()
            field = db.metadata_fields[field_id]

        # Build response with parsed value
        field_dict = field.as_dict()
        field_dict["value"] = _parse_value(field.value, field.field_type)

        return field_dict, None, None

    result, error, status = await run_in_threadpool(create_or_update)

    if error:
        return jsonify({"error": error}), status

    return jsonify(result), 201


@bp.route("/organizations/<int:id>/metadata/<string:field_key>", methods=["PATCH"])
@login_required
@license_required("enterprise")
@resource_role_required("maintainer", resource_param="id")
@validated_request(body_model=UpdateMetadataRequest)
async def update_organization_metadata(
    id: int, field_key: str, body: UpdateMetadataRequest
):
    """
    Update a metadata field for an organization.

    Requires maintainer role on the organization.

    Path Parameters:
        - id: Organization ID
        - field_key: Metadata field key

    Request Body:
        {
            "value": 1500000,
            "field_type": "number" (optional)
        }

    Returns:
        200: Metadata field updated
        400: Invalid request
        403: License required or insufficient permissions
        404: Organization or metadata field not found

    Example:
        PATCH /api/v1/metadata/organizations/1/metadata/budget
    """
    db = current_app.db

    def update():
        # Verify organization exists
        org = db.organizations[id]
        if not org:
            return None, "Organization not found", 404

        # Get metadata field
        field = (
            db(
                (db.metadata_fields.resource_type == "organization")
                & (db.metadata_fields.resource_id == id)
                & (db.metadata_fields.key == field_key)
            )
            .select()
            .first()
        )

        if not field:
            return None, f"Metadata field '{field_key}' not found", 404

        # Check if system metadata
        if field.is_system:
            return None, "System metadata fields cannot be modified", 403

        # Get field type (use provided or existing)
        field_type = body.field_type if body.field_type else field.field_type

        # Coerce value to correct type
        try:
            coerced_value = _coerce_value(body.value, field_type)
            value_str = str(coerced_value)
        except ValueError as e:
            return None, str(e), 400

        # Update field
        update_fields = {"value": value_str}
        if body.field_type:
            update_fields["field_type"] = body.field_type

        db(db.metadata_fields.id == field.id).update(**update_fields)
        db.commit()

        # Fetch updated field
        updated_field = db.metadata_fields[field.id]

        # Build response with parsed value
        field_dict = updated_field.as_dict()
        field_dict["value"] = _parse_value(
            updated_field.value, updated_field.field_type
        )

        return field_dict, None, None

    result, error, status = await run_in_threadpool(update)

    if error:
        return jsonify({"error": error}), status

    return jsonify(result), 200


@bp.route("/organizations/<int:id>/metadata/<string:field_key>", methods=["DELETE"])
@login_required
@license_required("enterprise")
@resource_role_required("maintainer", resource_param="id")
async def delete_organization_metadata(id: int, field_key: str):
    """
    Delete a metadata field from an organization.

    Requires maintainer role on the organization.

    Path Parameters:
        - id: Organization ID
        - field_key: Metadata field key

    Returns:
        204: Metadata field deleted
        403: License required or insufficient permissions
        404: Organization or metadata field not found

    Example:
        DELETE /api/v1/metadata/organizations/1/metadata/budget
    """
    db = current_app.db

    def delete():
        # Verify organization exists
        org = db.organizations[id]
        if not org:
            return None, "Organization not found", 404

        # Get metadata field
        field = (
            db(
                (db.metadata_fields.resource_type == "organization")
                & (db.metadata_fields.resource_id == id)
                & (db.metadata_fields.key == field_key)
            )
            .select()
            .first()
        )

        if not field:
            return None, f"Metadata field '{field_key}' not found", 404

        # Check if system metadata
        if field.is_system:
            return None, "System metadata fields cannot be deleted", 403

        # Delete field
        db(db.metadata_fields.id == field.id).delete()
        db.commit()

        return True, None, None

    result, error, status = await run_in_threadpool(delete)

    if error:
        return jsonify({"error": error}), status

    return "", 204
