"""Public lookup endpoint for entities by ID using PyDAL with async/await.

NOTE: Original unique_id (64-bit) functionality not yet migrated to PyDAL schema.
Currently uses regular entity ID. TODO: Add unique_id field to entities table.
"""

# flake8: noqa: E501


from dataclasses import asdict

from flask import Blueprint, current_app, jsonify, request

from apps.api.models.dataclasses import EntityDTO, from_pydal_row
from apps.api.utils.async_utils import run_in_threadpool

bp = Blueprint("lookup", __name__)


@bp.route("/<int:entity_id>", methods=["GET"])
async def lookup_entity(entity_id: int):
    """
    Lookup entity by entity ID.

    Public endpoint - no authentication required.
    Returns entity type and details (not including children/dependencies).

    NOTE: Original used unique_id (64-bit). Currently uses regular id.

    Path Parameters:
        - entity_id: Entity identifier

    Returns:
        200: Entity details in JSON format
        404: Entity not found

    Example:
        GET /lookup/42
        {
            "id": 42,
            "name": "Web Server 01",
            "description": "Primary web server",
            "entity_type": "compute",
            "organization_id": 1,
            "attributes": {
                "hostname": "web-01.example.com",
                "ip": "10.0.1.5",
                "os": "Ubuntu 22.04"
            },
            "created_at": "2024-10-23T10:00:00Z",
            "updated_at": "2024-10-23T15:30:00Z"
        }
    """
    db = current_app.db

    # Find entity by id
    entity = await run_in_threadpool(lambda: db.entities[entity_id])

    if not entity:
        return jsonify({"error": f"Entity with id {entity_id} not found"}), 404

    # Convert to DTO
    entity_dto = from_pydal_row(entity, EntityDTO)
    return jsonify(asdict(entity_dto)), 200


@bp.route("/batch", methods=["POST"])
async def lookup_entities_batch():
    """
    Lookup multiple entities by IDs in a single request.

    Public endpoint - no authentication required.

    NOTE: Original used unique_ids (64-bit). Currently uses regular ids.

    Request Body:
        {
            "ids": [1, 2, 3, ...]
        }

    Returns:
        200: Array of entity details (up to 100 entities)
        400: Invalid request

    Example:
        POST /lookup/batch
        {
            "ids": [42, 43]
        }

        Response:
        {
            "results": [
                {
                    "id": 42,
                    "found": true,
                    "entity": { ... }
                },
                {
                    "id": 43,
                    "found": false,
                    "entity": null
                }
            ]
        }
    """
    db = current_app.db

    data = request.get_json() or {}

    if "ids" not in data or not isinstance(data["ids"], list):
        return jsonify({"error": "Request must include 'ids' array"}), 400

    entity_ids = data["ids"]

    if len(entity_ids) == 0:
        return jsonify({"error": "At least one id required"}), 400

    if len(entity_ids) > 100:
        return jsonify({"error": "Maximum 100 entities per batch lookup"}), 400

    # Query all entities
    def batch_lookup():
        entities = db(db.entities.id.belongs(entity_ids)).select()

        # Build lookup map
        entity_map = {e.id: e for e in entities}

        # Build response
        results = []
        for eid in entity_ids:
            if eid in entity_map:
                entity_dto = from_pydal_row(entity_map[eid], EntityDTO)
                results.append(
                    {
                        "id": eid,
                        "found": True,
                        "entity": asdict(entity_dto),
                    }
                )
            else:
                results.append(
                    {
                        "id": eid,
                        "found": False,
                        "entity": None,
                    }
                )

        return results

    results = await run_in_threadpool(batch_lookup)

    return jsonify({"results": results}), 200
