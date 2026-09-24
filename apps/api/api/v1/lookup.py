"""Entity lookup endpoints (by ID / batch) using PyDAL with async/await.

NOTE: Original unique_id (64-bit) functionality not yet migrated to PyDAL schema.
Currently uses regular entity ID. TODO: Add unique_id field to entities table.
"""

# flake8: noqa: E501

from dataclasses import asdict

from quart import Blueprint, current_app, jsonify, request

from apps.api.auth.decorators import login_required, require_scope
from apps.api.models.dataclasses import EntityDTO, from_pydal_row
from apps.api.utils.api_responses import ApiResponse
from apps.api.utils.async_utils import run_in_threadpool
from apps.api.utils.tenant_scoping import get_current_tenant_id, get_tenant_scoped

bp = Blueprint("lookup", __name__)


@bp.route("/<int:entity_id>", methods=["GET"])
@login_required
@require_scope("infrastructure:read")
async def lookup_entity(entity_id: int):
    """
    Lookup entity by entity ID.

    regression: gh-237 -- this route previously had no auth at all and
    returned entity attributes (hostname/IP/OS) for ANY tenant to an
    unauthenticated caller. Now requires a valid JWT (``infrastructure:read``
    scope) and scopes the lookup to the caller's tenant via
    ``get_tenant_scoped()`` (entities have no ``tenant_id`` column of their
    own, only ``organization_id`` -- resolved through ``organizations``).

    NOTE: Original used unique_id (64-bit). Currently uses regular id.

    Path Parameters:
        - entity_id: Entity identifier

    Returns:
        200: Entity details in JSON format
        401: Authentication required
        404: Entity not found (including entities belonging to another tenant)

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
    tenant_id = get_current_tenant_id()

    entity = await run_in_threadpool(
        lambda: get_tenant_scoped(
            db, db.entities, entity_id, tenant_id, org_fk="organization_id"
        )
    )

    if not entity:
        return ApiResponse.error(f"Entity with id {entity_id} not found", 404)

    # Convert to DTO
    entity_dto = from_pydal_row(entity, EntityDTO)
    return jsonify(asdict(entity_dto)), 200


@bp.route("/batch", methods=["POST"])
@login_required
@require_scope("infrastructure:read")
async def lookup_entities_batch():
    """
    Lookup multiple entities by IDs in a single request.

    regression: gh-237 -- same unauthenticated cross-tenant leak as
    ``lookup_entity`` above, batched. Requires auth and scopes every id to
    the caller's tenant; ids belonging to another tenant (or that don't
    exist) come back ``found: false`` exactly like a nonexistent id -- never
    distinguishable from "doesn't exist", so a caller can't use this to
    enumerate other tenants' entity ids.

    NOTE: Original used unique_ids (64-bit). Currently uses regular ids.

    Request Body:
        {
            "ids": [1, 2, 3, ...]
        }

    Returns:
        200: Array of entity details (up to 100 entities)
        400: Invalid request
        401: Authentication required

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
    tenant_id = get_current_tenant_id()

    data = await request.get_json() or {}

    if "ids" not in data or not isinstance(data["ids"], list):
        return ApiResponse.bad_request("Request must include 'ids' array")

    entity_ids = data["ids"]

    if len(entity_ids) == 0:
        return ApiResponse.bad_request("At least one id required")

    if len(entity_ids) > 100:
        return ApiResponse.bad_request("Maximum 100 entities per batch lookup")

    # Query all entities, scoped to the caller's tenant via the
    # organizations they own -- never a bare id.belongs() lookup.
    def batch_lookup():
        if not tenant_id:
            entity_map: dict = {}
        else:
            tenant_org_ids = [
                row.id
                for row in db(db.organizations.tenant_id == tenant_id).select(
                    db.organizations.id
                )
            ]
            if not tenant_org_ids:
                entity_map = {}
            else:
                entities = db(
                    db.entities.id.belongs(entity_ids)
                    & db.entities.organization_id.belongs(tenant_org_ids)
                ).select()
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
