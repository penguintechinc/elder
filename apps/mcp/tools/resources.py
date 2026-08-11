"""Resource query tools for Elder MCP server (read-only)."""

from __future__ import annotations

from typing import Optional

from client import ElderClient


async def list_organizations(
    client: ElderClient,
    search: str | None = None,
    limit: int = 20,
) -> dict:
    """
    List organizations.

    Args:
        client: ElderClient instance
        search: Search filter (partial name match)
        limit: Maximum results (default: 20)

    Returns:
        List of organizations with metadata

    Raises:
        ValueError: If session expired or API error
    """
    params = {"per_page": limit}

    if search is not None:
        params["name"] = search

    return await client.get("/api/v1/organizations", params=params)


async def get_organization(client: ElderClient, organization_id: int) -> dict:
    """
    Get organization details.

    Args:
        client: ElderClient instance
        organization_id: Organization ID

    Returns:
        Full organization object

    Raises:
        ValueError: If organization not found, session expired, or API error
    """
    return await client.get(f"/api/v1/organizations/{organization_id}")


async def list_identities(
    client: ElderClient,
    search: str | None = None,
    auth_provider: str | None = None,
    limit: int = 20,
) -> dict:
    """
    List identities (users, service accounts).

    Args:
        client: ElderClient instance
        search: Search filter (partial name/email match)
        auth_provider: Filter by auth provider (local, oidc, etc.)
        limit: Maximum results (default: 20)

    Returns:
        List of identities

    Raises:
        ValueError: If session expired or API error
    """
    params = {"per_page": limit}

    if search is not None:
        params["search"] = search

    if auth_provider is not None:
        params["auth_provider"] = auth_provider

    return await client.get("/api/v1/identities", params=params)


async def search_services(
    client: ElderClient,
    query: str | None = None,
    organization_id: int | None = None,
    limit: int = 20,
) -> dict:
    """
    Search services.

    Args:
        client: ElderClient instance
        query: Search query (name partial match)
        organization_id: Filter by organization ID
        limit: Maximum results (default: 20)

    Returns:
        List of services

    Raises:
        ValueError: If session expired or API error
    """
    params = {"per_page": limit}

    if query is not None:
        params["name"] = query

    if organization_id is not None:
        params["organization_id"] = organization_id

    return await client.get("/api/v1/services", params=params)
