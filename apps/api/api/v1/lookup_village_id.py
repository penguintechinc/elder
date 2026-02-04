"""Village ID lookup endpoint for resolving village_id to resource type and location.

Searches all resource tables for a matching village_id and returns the resource
type, ID, and redirect URL for navigation.
"""

# flake8: noqa: E501


from flask import Blueprint, current_app, jsonify

from apps.api.utils.async_utils import run_in_threadpool

bp = Blueprint("lookup_village_id", __name__)

# Resource type to URL mapping
RESOURCE_URL_MAP = {
    "tenant": "/tenants/{id}",
    "organization": "/organizations/{id}",
    "entity": "/entities/{id}",
    "identity": "/identities/{id}",
    "software": "/software/{id}",
    "service": "/services/{id}",
    "ipam_prefix": "/ipam/prefixes/{id}",
    "ipam_address": "/ipam/addresses/{id}",
    "ipam_vlan": "/ipam/vlans/{id}",
    "issue": "/issues/{id}",
    "project": "/projects/{id}",
    "milestone": "/milestones/{id}",
}


@bp.route("/id/<village_id>", methods=["GET"])
async def lookup_village_id(village_id: str):
    """
    Lookup a resource by its village_id.

    Searches all resource tables for the village_id and returns the resource
    type, ID, and redirect URL.

    Path Parameters:
        - village_id: The village_id to lookup (16-character hex string)

    Returns:
        200: Resource found with type, ID, and redirect URL
        404: Resource not found

    Example:
        GET /id/a1b2c3d4e5f67890
        {
            "village_id": "a1b2c3d4e5f67890",
            "resource_type": "entity",
            "resource_id": 123,
            "redirect_url": "/entities/123"
        }
    """
    db = current_app.db

    def search_tables():
        """Search all tables for the village_id."""
        # Tables to search with their resource type names
        tables_to_search = [
            ("tenants", "tenant"),
            ("organizations", "organization"),
            ("entities", "entity"),
            ("identities", "identity"),
            ("software", "software"),
            ("services", "service"),
            ("ipam_prefixes", "ipam_prefix"),
            ("ipam_addresses", "ipam_address"),
            ("ipam_vlans", "ipam_vlan"),
            ("issues", "issue"),
            ("projects", "project"),
            ("milestones", "milestone"),
        ]

        for table_name, resource_type in tables_to_search:
            # Check if table exists in database
            if not hasattr(db, table_name):
                continue

            table = getattr(db, table_name)

            # Check if table has village_id field
            if not hasattr(table, "village_id"):
                continue

            # Search for the village_id
            row = db(table.village_id == village_id).select().first()

            if row:
                return {
                    "resource_type": resource_type,
                    "resource_id": row.id,
                    "table_name": table_name,
                }

        return None

    result = await run_in_threadpool(search_tables)

    if not result:
        return (
            jsonify({"error": f"Resource with village_id '{village_id}' not found"}),
            404,
        )

    # Build redirect URL
    url_template = RESOURCE_URL_MAP.get(result["resource_type"], "/{type}/{id}")
    redirect_url = url_template.format(id=result["resource_id"])

    return (
        jsonify(
            {
                "village_id": village_id,
                "resource_type": result["resource_type"],
                "resource_id": result["resource_id"],
                "redirect_url": redirect_url,
            }
        ),
        200,
    )
