"""MCP tools for Elder universal search, graph search, refs, VillageID lookup, and audit logs."""

from typing import TYPE_CHECKING, Any
from urllib.parse import quote, unquote

if TYPE_CHECKING:
    from apps.mcp.client import ElderClient
    from apps.mcp.tools.entities import (
        get_organization,
        list_data_stores,
        list_organizations,
    )
    from apps.mcp.tools.security import (
        get_service,
        get_service_sbom,
        get_vulnerability,
        list_vulnerabilities,
        search_services,
    )
    from apps.mcp.tools.work_management import get_issue, list_issues
else:
    try:
        from apps.mcp.client import ElderClient
        from apps.mcp.tools.entities import (
            get_organization,
            list_data_stores,
            list_organizations,
        )
        from apps.mcp.tools.security import (
            get_service,
            get_service_sbom,
            get_vulnerability,
            list_vulnerabilities,
            search_services,
        )
        from apps.mcp.tools.work_management import get_issue, list_issues
    except ImportError:
        from client import ElderClient
        from tools.entities import (
            get_organization,
            list_data_stores,
            list_organizations,
        )
        from tools.security import (
            get_service,
            get_service_sbom,
            get_vulnerability,
            list_vulnerabilities,
            search_services,
        )
        from tools.work_management import get_issue, list_issues


async def universal_search(
    client: ElderClient,
    q: str,
    types: str | None = None,
    limit: int = 20,
    offset: int = 0,
) -> Any:
    """Run Elder universal search across entities, issues, orgs, services, and docs."""
    params: dict[str, Any] = {
        "q": q,
        "types": types,
        "limit": limit,
        "offset": offset,
    }
    return await client.get("/api/v1/search", params=params)


async def search_graph(
    client: ElderClient,
    start_entity_id: int,
    max_depth: int = 2,
    dependency_types: list[str] | None = None,
    entity_filters: dict[str, Any] | None = None,
) -> Any:
    """Traverse the dependency graph starting from an entity (read-only POST query)."""
    payload: dict[str, Any] = {
        "start_entity_id": start_entity_id,
        "max_depth": max_depth,
    }
    if dependency_types is not None:
        payload["dependency_types"] = dependency_types
    if entity_filters is not None:
        payload["entity_filters"] = entity_filters
    return await client.post("/api/v1/search/graph", json=payload)


async def resolve_ref(
    client: ElderClient,
    village_id: str | None = None,
    ref: str | None = None,
) -> Any:
    """Resolve an Elder VillageID or cross-module ref URI into a resource target."""
    params: dict[str, Any] = {
        "village_id": village_id,
        "ref": ref,
    }
    return await client.get("/api/v1/refs/resolve", params=params)


async def get_ref_backlinks(client: ElderClient, target: str) -> Any:
    """Find all resources referencing a given VillageID or target identifier."""
    return await client.get("/api/v1/refs/backlinks", params={"target": target})


def _encode_village_id(village_id: str) -> str:
    cleaned = (village_id or "").strip()
    if not cleaned:
        raise ValueError("village_id must not be empty")
    for cand in (cleaned, unquote(cleaned), unquote(unquote(cleaned))):
        if any(bad in cand for bad in ("..", "/", "\\", "?", "#", "\x00", "\r", "\n")):
            raise ValueError("Invalid characters in village_id")
    return quote(cleaned, safe="")


async def lookup_village_id(client: ElderClient, village_id: str) -> Any:
    """Look up any Elder resource directly by its compact VillageID identifier."""
    encoded = _encode_village_id(village_id)
    return await client.get(f"/id/{encoded}")


async def resolve_village_id(client: ElderClient, village_id: str) -> Any:
    """Resolve any Elder resource via the v1 API by its compact VillageID identifier."""
    encoded = _encode_village_id(village_id)
    return await client.get(f"/api/v1/id/{encoded}")


async def list_audit_logs(
    client: ElderClient,
    resource_type: str | None = None,
    resource_id: str | None = None,
    action: str | None = None,
    category: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    success: bool | None = None,
    limit: int = 50,
    offset: int = 0,
) -> Any:
    """Query enterprise audit logs filtered by resource, action, category, or date range."""
    params: dict[str, Any] = {
        "resource_type": resource_type,
        "resource_id": resource_id,
        "action": action,
        "category": category,
        "start_date": start_date,
        "end_date": end_date,
        "success": str(success).lower() if success is not None else None,
        "limit": limit,
        "offset": offset,
    }
    return await client.get("/api/v1/audit-enterprise/logs", params=params)


get_audit_logs = list_audit_logs

__all__ = [
    "get_audit_logs",
    "get_issue",
    "get_organization",
    "get_ref_backlinks",
    "get_service",
    "get_service_sbom",
    "get_vulnerability",
    "list_audit_logs",
    "list_data_stores",
    "list_issues",
    "list_organizations",
    "list_vulnerabilities",
    "lookup_village_id",
    "resolve_ref",
    "resolve_village_id",
    "search_graph",
    "search_services",
    "universal_search",
]
