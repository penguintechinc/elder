"""Relationship management tools for Elder MCP server."""

from __future__ import annotations

from typing import Optional

from client import ElderClient


async def get_entity_relationships(
    client: ElderClient,
    entity_id: int,
    direction: str = "both",
) -> dict:
    """
    Get all relationships for an entity.

    Args:
        client: ElderClient instance
        entity_id: Entity ID
        direction: "inbound", "outbound", or "both" (default)

    Returns:
        Dictionary with inbound and/or outbound relationships

    Raises:
        ValueError: If entity not found, session expired, or API error
    """
    params = {"direction": direction}
    return await client.get(f"/api/v1/entities/{entity_id}/relationships", params=params)


async def search_relationships(
    client: ElderClient,
    source_entity_id: Optional[int] = None,
    target_entity_id: Optional[int] = None,
    relationship_type: Optional[str] = None,
    limit: int = 20,
) -> dict:
    """
    Search relationships by source, target, and type.

    Args:
        client: ElderClient instance
        source_entity_id: Source entity ID (optional)
        target_entity_id: Target entity ID (optional)
        relationship_type: Relationship type filter (optional)
        limit: Maximum results (default: 20)

    Returns:
        List of relationships matching criteria

    Raises:
        ValueError: If session expired or API error
    """
    params = {"per_page": limit}

    if source_entity_id is not None:
        params["source_entity_id"] = source_entity_id

    if target_entity_id is not None:
        params["target_entity_id"] = target_entity_id

    if relationship_type is not None:
        params["relationship_type"] = relationship_type

    return await client.get("/api/v1/dependencies", params=params)


async def create_relationship(
    client: ElderClient,
    source_entity_id: int,
    target_entity_id: int,
    relationship_type: str,
    description: Optional[str] = None,
) -> dict:
    """
    Create a new relationship between entities.

    Args:
        client: ElderClient instance
        source_entity_id: Source entity ID
        target_entity_id: Target entity ID
        relationship_type: Type of relationship
        description: Optional relationship description

    Returns:
        Created relationship object

    Raises:
        ValueError: If entities not found, session expired, or API error
    """
    body = {
        "source_entity_id": source_entity_id,
        "target_entity_id": target_entity_id,
        "relationship_type": relationship_type,
    }

    if description is not None:
        body["description"] = description

    return await client.post("/api/v1/dependencies", body)


async def delete_relationship(client: ElderClient, relationship_id: int) -> None:
    """
    Delete a relationship.

    Args:
        client: ElderClient instance
        relationship_id: Relationship ID

    Raises:
        ValueError: If relationship not found, session expired, or API error
    """
    await client.delete(f"/api/v1/dependencies/{relationship_id}")
