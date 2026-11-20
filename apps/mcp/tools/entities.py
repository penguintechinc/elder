"""MCP tools for Elder infrastructure entities, organizations, networks, data stores, and geo."""

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from apps.mcp.client import ElderClient
else:
    try:
        from apps.mcp.client import ElderClient
    except ImportError:
        from client import ElderClient


async def search_entities(
    client: ElderClient,
    name: str | None = None,
    organization_id: int | None = None,
    entity_type: str | None = None,
    sub_type: str | None = None,
    limit: int = 50,
    page: int = 1,
) -> Any:
    """Search infrastructure entities by name, organization, type, or subtype."""
    params: dict[str, Any] = {
        "per_page": limit,
        "page": page,
        "name": name,
        "organization_id": organization_id,
        "entity_type": entity_type,
        "sub_type": sub_type,
    }
    return await client.get("/api/v1/entities", params=params)


async def get_entity(client: ElderClient, entity_id: int) -> Any:
    """Get full details for a single infrastructure entity by ID."""
    return await client.get(f"/api/v1/entities/{entity_id}")


async def update_entity(
    client: ElderClient,
    entity_id: int,
    name: str | None = None,
    description: str | None = None,
    attributes: dict[str, Any] | None = None,
    metadata: dict[str, Any] | None = None,
) -> Any:
    """Update an entity's name, description, or structured attributes (write operation)."""
    payload: dict[str, Any] = {}
    if name is not None:
        payload["name"] = name
    if description is not None:
        payload["description"] = description
    merged_attrs: dict[str, Any] = {}
    if isinstance(metadata, dict):
        merged_attrs.update(metadata)
    if isinstance(attributes, dict):
        merged_attrs.update(attributes)
    if merged_attrs:
        payload["attributes"] = merged_attrs
    return await client.patch(f"/api/v1/entities/{entity_id}", json=payload)


async def list_data_stores(
    client: ElderClient,
    search: str | None = None,
    organization_id: int | None = None,
    data_classification: str | None = None,
    storage_type: str | None = None,
    contains_pii: bool | None = None,
    contains_phi: bool | None = None,
    contains_pci: bool | None = None,
    label: str | None = None,
    limit: int = 50,
    page: int = 1,
) -> Any:
    """List data stores with optional classification, compliance flag, and org filters."""
    params: dict[str, Any] = {
        "search": search,
        "organization_id": organization_id,
        "data_classification": data_classification,
        "storage_type": storage_type,
        "contains_pii": str(contains_pii).lower() if contains_pii is not None else None,
        "contains_phi": str(contains_phi).lower() if contains_phi is not None else None,
        "contains_pci": str(contains_pci).lower() if contains_pci is not None else None,
        "label": label,
        "per_page": limit,
        "page": page,
    }
    return await client.get("/api/v1/data-stores", params=params)


async def get_data_store(client: ElderClient, data_store_id: int) -> Any:
    """Get details for a specific data store by ID."""
    return await client.get(f"/api/v1/data-stores/{data_store_id}")


async def list_networks(
    client: ElderClient,
    organization_id: int | None = None,
    network_type: str | None = None,
    parent_id: int | None = None,
    region: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> Any:
    """List networks / VPCs / subnets with optional organization and region filters."""
    params: dict[str, Any] = {
        "organization_id": organization_id,
        "network_type": network_type,
        "parent_id": parent_id,
        "region": region,
        "limit": limit,
        "offset": offset,
    }
    return await client.get("/api/v1/networking/networks", params=params)


async def get_network(client: ElderClient, network_id: int) -> Any:
    """Get details for a specific network by ID."""
    return await client.get(f"/api/v1/networking/networks/{network_id}")


async def get_networking_topology(
    client: ElderClient,
    organization_id: int,
    include_entities: bool = True,
) -> Any:
    """Get the network topology graph for an organization."""
    params: dict[str, Any] = {
        "organization_id": organization_id,
        "include_entities": str(include_entities).lower(),
    }
    return await client.get("/api/v1/networking/topology/graph", params=params)


async def list_organizations(
    client: ElderClient,
    search: str | None = None,
    parent_id: int | None = None,
    limit: int = 50,
    page: int = 1,
) -> Any:
    """List organizations in the tenant with optional search and hierarchy filters."""
    params: dict[str, Any] = {
        "search": search,
        "parent_id": parent_id,
        "per_page": limit,
        "page": page,
    }
    return await client.get("/api/v1/organizations", params=params)


async def get_organization(client: ElderClient, organization_id: int) -> Any:
    """Get full details for an organization by ID."""
    return await client.get(f"/api/v1/organizations/{organization_id}")


async def get_organization_children(
    client: ElderClient,
    organization_id: int,
    recursive: bool = False,
) -> Any:
    """Get direct or recursive child organizations for a parent organization."""
    params: dict[str, Any] = {"recursive": str(recursive).lower()}
    return await client.get(
        f"/api/v1/organizations/{organization_id}/children",
        params=params,
    )


async def get_organization_tree_stats(client: ElderClient, org_id: int) -> Any:
    """Get rollup infrastructure/resource statistics for an organization tree."""
    return await client.get(f"/api/v1/organizations/{org_id}/tree-stats")


async def list_geo_locations(
    client: ElderClient,
    region_code: str | None = None,
) -> Any:
    """List geographic locations / datacenters optionally filtered by region code."""
    params: dict[str, Any] = {"region_code": region_code}
    return await client.get("/api/v1/geo/locations", params=params)


async def list_geo_regions(
    client: ElderClient,
    provider: str | None = None,
) -> Any:
    """List geographic cloud/datacenter regions optionally filtered by provider."""
    params: dict[str, Any] = {"provider": provider}
    return await client.get("/api/v1/geo/regions", params=params)
