"""MCP tools for Elder dependency relationships and infrastructure graph queries."""

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from apps.mcp.client import ElderClient
else:
    try:
        from apps.mcp.client import ElderClient
    except ImportError:
        from client import ElderClient

_DIRECTION_MAP = {
    "both": "all",
    "all": "all",
    "inbound": "incoming",
    "incoming": "incoming",
    "outbound": "outgoing",
    "outgoing": "outgoing",
}


async def get_entity_relationships(
    client: ElderClient,
    entity_id: int,
    direction: str = "both",
) -> Any:
    """Get inbound, outbound, or all dependencies for a given infrastructure entity."""
    normalized = _DIRECTION_MAP.get(direction.lower(), "all")
    return await client.get(
        f"/api/v1/entities/{entity_id}/dependencies",
        params={"direction": normalized},
    )


async def search_relationships(
    client: ElderClient,
    source_id: int | None = None,
    target_id: int | None = None,
    dependency_type: str | None = None,
    source_type: str | None = None,
    target_type: str | None = None,
    source_entity_id: int | None = None,
    target_entity_id: int | None = None,
    relationship_type: str | None = None,
    limit: int = 50,
    page: int = 1,
) -> Any:
    """Search dependency relationships across Elder resources and entities."""
    params: dict[str, Any] = {
        "source_id": source_id if source_id is not None else source_entity_id,
        "target_id": target_id if target_id is not None else target_entity_id,
        "dependency_type": dependency_type or relationship_type,
        "source_type": source_type,
        "target_type": target_type,
        "per_page": limit,
        "page": page,
    }
    return await client.get("/api/v1/dependencies", params=params)


async def create_relationship(
    client: ElderClient,
    source_id: int,
    target_id: int,
    dependency_type: str,
    source_type: str = "entity",
    target_type: str = "entity",
    metadata: dict[str, Any] | None = None,
) -> Any:
    """Create a dependency relationship between two Elder resources (write operation)."""
    payload: dict[str, Any] = {
        "source_type": source_type,
        "source_id": source_id,
        "target_type": target_type,
        "target_id": target_id,
        "dependency_type": dependency_type,
    }
    if metadata is not None:
        payload["metadata"] = metadata
    return await client.post("/api/v1/dependencies", json=payload)


async def delete_relationship(client: ElderClient, dependency_id: int) -> Any:
    """Delete a dependency relationship by ID (write operation)."""
    return await client.delete(f"/api/v1/dependencies/{dependency_id}")


async def get_graph(
    client: ElderClient,
    organization_id: int | None = None,
    entity_type: str | None = None,
    entity_id: int | None = None,
    depth: int = 2,
    include_metadata: bool = False,
) -> Any:
    """Fetch the infrastructure dependency graph centered on an org or entity."""
    params: dict[str, Any] = {
        "organization_id": organization_id,
        "entity_type": entity_type,
        "entity_id": entity_id,
        "depth": depth,
        "include_metadata": str(include_metadata).lower(),
    }
    return await client.get("/api/v1/graph", params=params)


async def analyze_graph(
    client: ElderClient,
    organization_id: int | None = None,
) -> Any:
    """Analyze graph health: single points of failure, cycles, and centrality metrics."""
    params: dict[str, Any] = {"organization_id": organization_id}
    return await client.get("/api/v1/graph/analyze", params=params)


async def find_graph_path(
    client: ElderClient,
    source_entity_id: int,
    target_entity_id: int,
) -> Any:
    """Find shortest dependency paths between two infrastructure entities."""
    params: dict[str, Any] = {
        "from": source_entity_id,
        "to": target_entity_id,
    }
    return await client.get("/api/v1/graph/path", params=params)
