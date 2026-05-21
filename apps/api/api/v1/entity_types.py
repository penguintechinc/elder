"""Entity types API endpoints for Elder v1.2.0."""

# flake8: noqa: E501


from quart import Blueprint, jsonify, request

from apps.api.auth.decorators import login_required
from apps.api.models.entity_types import (
    DEFAULT_METADATA_TEMPLATES,
    get_all_entity_types,
    get_default_metadata_for_subtype,
    get_subtypes_for_type,
)

bp = Blueprint("entity_types", __name__)


@bp.route("/", methods=["GET"])
@login_required
def list_entity_types():
    """
    List all entity types with their sub-types.

    Returns:
        200: List of entity types with sub-types
    """
    entity_types = []

    for entity_type in get_all_entity_types():
        subtypes = get_subtypes_for_type(entity_type)
        entity_types.append(
            {"type": entity_type, "subtypes": subtypes, "subtype_count": len(subtypes)}
        )

    return jsonify({"entity_types": entity_types, "total": len(entity_types)}), 200


@bp.route("/<entity_type>", methods=["GET"])
@login_required
def get_entity_type(entity_type):
    """
    Get details for a specific entity type including all sub-types.

    Args:
        entity_type: Entity type to retrieve

    Returns:
        200: Entity type details with sub-types
        404: Entity type not found
    """
    if entity_type not in get_all_entity_types():
        return jsonify({"error": "Entity type not found"}), 404

    subtypes = get_subtypes_for_type(entity_type)

    return (
        jsonify(
            {"type": entity_type, "subtypes": subtypes, "subtype_count": len(subtypes)}
        ),
        200,
    )


@bp.route("/<entity_type>/subtypes", methods=["GET"])
@login_required
def list_subtypes(entity_type):
    """
    List all sub-types for a given entity type.

    Args:
        entity_type: Entity type to get sub-types for

    Returns:
        200: List of sub-types
        404: Entity type not found
    """
    if entity_type not in get_all_entity_types():
        return jsonify({"error": "Entity type not found"}), 404

    subtypes = get_subtypes_for_type(entity_type)

    return (
        jsonify(
            {"entity_type": entity_type, "subtypes": subtypes, "total": len(subtypes)}
        ),
        200,
    )


@bp.route("/<entity_type>/metadata", methods=["GET"])
@login_required
def get_type_metadata_templates(entity_type):
    """
    Get default metadata templates for all sub-types of an entity type.

    Args:
        entity_type: Entity type to get metadata templates for

    Returns:
        200: Metadata templates for all sub-types
        404: Entity type not found
    """
    if entity_type not in get_all_entity_types():
        return jsonify({"error": "Entity type not found"}), 404

    type_templates = DEFAULT_METADATA_TEMPLATES.get(entity_type, {})

    return (
        jsonify({"entity_type": entity_type, "metadata_templates": type_templates}),
        200,
    )


@bp.route("/<entity_type>/<sub_type>/metadata", methods=["GET"])
@login_required
def get_subtype_metadata_template(entity_type, sub_type):
    """
    Get default metadata template for a specific sub-type.

    Args:
        entity_type: Entity type
        sub_type: Sub-type to get metadata template for

    Returns:
        200: Metadata template for the sub-type
        404: Entity type or sub-type not found
    """
    if entity_type not in get_all_entity_types():
        return jsonify({"error": "Entity type not found"}), 404

    subtypes = get_subtypes_for_type(entity_type)
    if sub_type not in subtypes:
        return jsonify({"error": "Sub-type not found"}), 404

    template = get_default_metadata_for_subtype(entity_type, sub_type)

    return (
        jsonify(
            {
                "entity_type": entity_type,
                "sub_type": sub_type,
                "metadata_template": template,
            }
        ),
        200,
    )


@bp.route("/validate", methods=["POST"])
@login_required
def validate_entity_type():
    """
    Validate an entity type and sub-type combination.

    Request body:
        {
            "entity_type": "compute",
            "sub_type": "server"
        }

    Returns:
        200: Validation result
        400: Missing required fields
    """
    data = request.get_json()

    if not data:
        return jsonify({"error": "Request body required"}), 400

    entity_type = data.get("entity_type")
    sub_type = data.get("sub_type")

    if not entity_type:
        return jsonify({"error": "entity_type required"}), 400

    # Validate entity type
    type_valid = entity_type in get_all_entity_types()

    result = {"entity_type": entity_type, "entity_type_valid": type_valid}

    # Validate sub-type if provided
    if sub_type:
        if type_valid:
            subtypes = get_subtypes_for_type(entity_type)
            subtype_valid = sub_type in subtypes
        else:
            subtype_valid = False

        result["sub_type"] = sub_type
        result["sub_type_valid"] = subtype_valid

    result["valid"] = result["entity_type_valid"] and (
        not sub_type or result.get("sub_type_valid", False)
    )

    return jsonify(result), 200
