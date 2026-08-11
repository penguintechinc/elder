"""Entity management tools for Elder MCP server."""

from __future__ import annotations

from typing import Optional

from client import ElderClient


async def search_entities(
    client: ElderClient,
    query: str,
    organization_id: int | None = None,
    entity_type: str | None = None,
    limit: int = 20,
) -> dict:
    """
    Search entities by name, type, and organization.

    Args:
        client: ElderClient instance
        query: Search query (name partial match)
        organization_id: Filter by organization ID
        entity_type: Filter by entity type
        limit: Maximum results (default: 20)

    Returns:
        List of entity summaries with pagination metadata

    Raises:
        ValueError: If session expired or API error
    """
    params = {
        "name": query,
        "per_page": limit,
    }

    if organization_id is not None:
        params["organization_id"] = organization_id

    if entity_type is not None:
        params["entity_type"] = entity_type

    return await client.get("/api/v1/entities", params=params)


async def get_entity(client: ElderClient, entity_id: int) -> dict:
    """
    Get full entity details.

    Args:
        client: ElderClient instance
        entity_id: Entity ID

    Returns:
        Full entity object with metadata

    Raises:
        ValueError: If entity not found, session expired, or API error
    """
    return await client.get(f"/api/v1/entities/{entity_id}")


async def update_entity(
    client: ElderClient,
    entity_id: int,
    name: str | None = None,
    description: str | None = None,
    metadata: dict | None = None,
) -> dict:
    """
    Update entity properties (partial update).

    Args:
        client: ElderClient instance
        entity_id: Entity ID
        name: New name (optional)
        description: New description (optional)
        metadata: Metadata dict (optional, replaces entire metadata)

    Returns:
        Updated entity object

    Raises:
        ValueError: If entity not found, session expired, or API error
    """
    body = {}

    if name is not None:
        body["name"] = name

    if description is not None:
        body["description"] = description

    if metadata is not None:
        body["metadata"] = metadata

    if not body:
        raise ValueError("At least one field (name, description, metadata) required")

    return await client.patch(f"/api/v1/entities/{entity_id}", body)
