"""Village ID lookup endpoint for resolving village_id to resource type and location.

Refactored to use the registry-driven resolver from apps.api.common.refs.registry.
Maintains backward compatibility with existing clients.
"""

# flake8: noqa: E501


from quart import Blueprint, current_app, jsonify

from apps.api.common.refs.registry import get_type, resolve_by_village_id
from apps.api.utils.async_utils import run_in_threadpool

bp = Blueprint("lookup_village_id", __name__)


@bp.route("/id/<village_id>", methods=["GET"])
async def lookup_village_id(village_id: str):
    """
    Lookup a resource by its village_id (registry-driven).

    Uses the cross-reference registry to resolve village_id to resource
    type, ID, and navigation URL. Maintains backward compatibility.

    Path Parameters:
        - village_id: The village_id to lookup (25-char format: TTTTTTTT-OOOOOOOOOOOOOOOO)

    Returns:
        200: Resource found with type, ID, and redirect URL
        404: Resource not found

    Example:
        GET /id/0000002a-000000000000f3c1
        {
            "village_id": "0000002a-000000000000f3c1",
            "resource_type": "entity",
            "resource_id": 123,
            "redirect_url": "/entities/123"
        }
    """
    db = current_app.db

    def search():
        """Search registry for the village_id."""
        result = resolve_by_village_id(db, village_id)
        if not result:
            return None

        resolvable = get_type(result["type"])
        if not resolvable:
            return None

        url = resolvable.url_pattern.format(id=result["id"])
        return {
            "resource_type": result["type"],
            "resource_id": result["id"],
            "redirect_url": url,
        }

    result = await run_in_threadpool(search)

    if not result:
        return (
            jsonify({"error": f"Resource with village_id '{village_id}' not found"}),
            404,
        )

    return (
        jsonify(
            {
                "village_id": village_id,
                "resource_type": result["resource_type"],
                "resource_id": result["resource_id"],
                "redirect_url": result["redirect_url"],
            }
        ),
        200,
    )
