"""MCP server for Elder RMS - provides AI agents access to entity/relationship data."""

from __future__ import annotations

import asyncio
import json
import os
import sys

import httpx
from auth import ElderSession
from client import ElderClient
from mcp.server.fastmcp import FastMCP
from tools import entities, relationships, resources

# Initialize MCP server
mcp = FastMCP("elder")

# Module-level session (initialized on startup)
_session: ElderSession | None = None
_client: ElderClient | None = None


async def _get_session() -> ElderSession:
    """
    Get or initialize the Elder session.

    Reads credentials from environment:
    - ELDER_API_URL: Elder API base URL (required)
    - ELDER_API_TOKEN: Pre-issued Bearer token (recommended)
    - Or ELDER_USERNAME + ELDER_PASSWORD for login

    Returns:
        Authenticated ElderSession

    Raises:
        ValueError: If no credentials provided or authentication fails
    """
    global _session

    if _session is not None and _session.is_valid():
        return _session

    api_url = os.environ.get("ELDER_API_URL")
    if not api_url:
        raise ValueError("ELDER_API_URL environment variable required")

    api_token = os.environ.get("ELDER_API_TOKEN")

    if api_token:
        try:
            _session = ElderSession.from_token(api_url, api_token)
            return _session
        except ValueError as e:
            raise ValueError(f"Invalid ELDER_API_TOKEN: {e}")

    # Fall back to username/password
    username = os.environ.get("ELDER_USERNAME")
    password = os.environ.get("ELDER_PASSWORD")

    if not username or not password:
        raise ValueError(
            "Either ELDER_API_TOKEN or (ELDER_USERNAME + ELDER_PASSWORD) required"
        )

    try:
        _session = await ElderSession.from_credentials(api_url, username, password)
        return _session
    except (httpx.HTTPError, ValueError) as e:
        raise ValueError(f"Failed to authenticate with Elder API: {e}")


async def _get_client() -> ElderClient:
    """Get or initialize the Elder API client."""
    global _client

    if _client is None:
        session = await _get_session()
        _client = ElderClient(session)

    return _client


# ============================================================================
# Entity Tools
# ============================================================================


@mcp.tool()
async def search_entities(
    query: str,
    organization_id: int | None = None,
    entity_type: str | None = None,
    limit: int = 20,
) -> str:
    """
    Search entities by name, type, and organization.

    Search allows you to find entities by partial name match, filter by entity type
    (e.g., 'service', 'database', 'host'), and narrow results to a specific organization.
    Returns entity summaries suitable for further detail queries.

    Args:
        query: Search query - partial match on entity name
        organization_id: Filter by organization ID (optional)
        entity_type: Filter by entity type (optional, e.g., 'service', 'database')
        limit: Maximum results to return (default: 20, max: 100)

    Returns:
        JSON-formatted list of matching entities with basic metadata

    Raises:
        ValueError: If session expired or API returns error
    """
    try:
        client = await _get_client()
        result = await entities.search_entities(
            client,
            query=query,
            organization_id=organization_id,
            entity_type=entity_type,
            limit=min(limit, 100),
        )
        return json.dumps(result, indent=2)
    except ValueError as e:
        return json.dumps({"error": str(e)}, indent=2)


@mcp.tool()
async def get_entity(entity_id: int) -> str:
    """
    Get full details for a single entity.

    Returns the complete entity record including metadata, relationships metadata,
    and all properties. Use this to examine entity details before updating.

    Args:
        entity_id: Entity ID

    Returns:
        JSON-formatted full entity object

    Raises:
        ValueError: If entity not found, session expired, or API error
    """
    try:
        client = await _get_client()
        result = await entities.get_entity(client, entity_id)
        return json.dumps(result, indent=2)
    except ValueError as e:
        return json.dumps({"error": str(e)}, indent=2)


@mcp.tool()
async def update_entity(
    entity_id: int,
    name: str | None = None,
    description: str | None = None,
    metadata: str | None = None,
) -> str:
    """
    Update entity properties (name, description, metadata).

    Only specified fields are updated; omitted fields remain unchanged.
    Metadata must be provided as JSON string and replaces entire metadata dict.

    Args:
        entity_id: Entity ID
        name: New entity name (optional)
        description: New entity description (optional)
        metadata: Metadata as JSON string (optional, replaces all metadata)

    Returns:
        JSON-formatted updated entity object

    Raises:
        ValueError: If validation fails, session expired, or API error
    """
    try:
        meta_dict = None
        if metadata is not None:
            try:
                meta_dict = json.loads(metadata)
            except json.JSONDecodeError as e:
                return json.dumps({"error": f"Invalid metadata JSON: {e}"}, indent=2)

        client = await _get_client()
        result = await entities.update_entity(
            client,
            entity_id=entity_id,
            name=name,
            description=description,
            metadata=meta_dict,
        )
        return json.dumps(result, indent=2)
    except ValueError as e:
        return json.dumps({"error": str(e)}, indent=2)


# ============================================================================
# Relationship Tools
# ============================================================================


@mcp.tool()
async def get_entity_relationships(entity_id: int, direction: str = "both") -> str:
    """
    Get all relationships for an entity.

    Returns both inbound (relationships targeting this entity) and outbound
    (relationships originating from this entity) relationships, or filter by direction.

    Args:
        entity_id: Entity ID
        direction: "inbound", "outbound", or "both" (default: "both")

    Returns:
        JSON-formatted relationships grouped by direction

    Raises:
        ValueError: If entity not found, session expired, or API error
    """
    try:
        if direction not in ("inbound", "outbound", "both"):
            return json.dumps(
                {"error": "direction must be 'inbound', 'outbound', or 'both'"},
                indent=2,
            )

        client = await _get_client()
        result = await relationships.get_entity_relationships(
            client, entity_id=entity_id, direction=direction
        )
        return json.dumps(result, indent=2)
    except ValueError as e:
        return json.dumps({"error": str(e)}, indent=2)


@mcp.tool()
async def search_relationships(
    source_entity_id: int | None = None,
    target_entity_id: int | None = None,
    relationship_type: str | None = None,
    limit: int = 20,
) -> str:
    """
    Search relationships by source, target entity, and type.

    At least one filter parameter should be provided. Returns matching relationships
    with full details including source, target, and type information.

    Args:
        source_entity_id: Source entity ID (optional)
        target_entity_id: Target entity ID (optional)
        relationship_type: Relationship type filter (optional, e.g., 'depends_on')
        limit: Maximum results (default: 20, max: 100)

    Returns:
        JSON-formatted list of relationships

    Raises:
        ValueError: If session expired or API error
    """
    try:
        if not any(
            [
                source_entity_id is not None,
                target_entity_id is not None,
                relationship_type,
            ]
        ):
            return json.dumps(
                {
                    "error": "At least one filter (source_entity_id, target_entity_id, relationship_type) required"
                },
                indent=2,
            )

        client = await _get_client()
        result = await relationships.search_relationships(
            client,
            source_entity_id=source_entity_id,
            target_entity_id=target_entity_id,
            relationship_type=relationship_type,
            limit=min(limit, 100),
        )
        return json.dumps(result, indent=2)
    except ValueError as e:
        return json.dumps({"error": str(e)}, indent=2)


@mcp.tool()
async def create_relationship(
    source_entity_id: int,
    target_entity_id: int,
    relationship_type: str,
    description: str | None = None,
) -> str:
    """
    Create a new relationship between two entities.

    Establishes a directed relationship from source to target with a type label
    (e.g., 'depends_on', 'manages', 'owns'). Duplicate relationships are allowed.

    Args:
        source_entity_id: Source entity ID
        target_entity_id: Target entity ID
        relationship_type: Relationship type label
        description: Optional relationship description

    Returns:
        JSON-formatted created relationship object

    Raises:
        ValueError: If entities not found, session expired, or API error
    """
    try:
        client = await _get_client()
        result = await relationships.create_relationship(
            client,
            source_entity_id=source_entity_id,
            target_entity_id=target_entity_id,
            relationship_type=relationship_type,
            description=description,
        )
        return json.dumps(result, indent=2)
    except ValueError as e:
        return json.dumps({"error": str(e)}, indent=2)


@mcp.tool()
async def delete_relationship(relationship_id: int) -> str:
    """
    Delete a relationship by ID.

    Permanently removes the specified relationship. Provides no undo;
    verify the relationship ID before deletion.

    Args:
        relationship_id: Relationship ID

    Returns:
        JSON-formatted success message or error

    Raises:
        ValueError: If relationship not found, session expired, or API error
    """
    try:
        client = await _get_client()
        await relationships.delete_relationship(client, relationship_id)
        return json.dumps({"success": True, "message": "Relationship deleted"})
    except ValueError as e:
        return json.dumps({"error": str(e)}, indent=2)


# ============================================================================
# Resource Query Tools (Read-Only)
# ============================================================================


@mcp.tool()
async def list_organizations(search: str | None = None, limit: int = 20) -> str:
    """
    List organizations.

    Returns all organizations in the system. Use search filter for partial name match.

    Args:
        search: Filter by organization name (partial match, optional)
        limit: Maximum results (default: 20, max: 100)

    Returns:
        JSON-formatted list of organizations

    Raises:
        ValueError: If session expired or API error
    """
    try:
        client = await _get_client()
        result = await resources.list_organizations(
            client, search=search, limit=min(limit, 100)
        )
        return json.dumps(result, indent=2)
    except ValueError as e:
        return json.dumps({"error": str(e)}, indent=2)


@mcp.tool()
async def get_organization(organization_id: int) -> str:
    """
    Get organization details.

    Returns full organization record including metadata and member counts.

    Args:
        organization_id: Organization ID

    Returns:
        JSON-formatted organization object

    Raises:
        ValueError: If organization not found, session expired, or API error
    """
    try:
        client = await _get_client()
        result = await resources.get_organization(client, organization_id)
        return json.dumps(result, indent=2)
    except ValueError as e:
        return json.dumps({"error": str(e)}, indent=2)


@mcp.tool()
async def list_identities(
    search: str | None = None, auth_provider: str | None = None, limit: int = 20
) -> str:
    """
    List identities (users and service accounts).

    Returns identities (users, service accounts, applications) in the system.
    Use search for partial name/email match and auth_provider to filter by identity source.

    Args:
        search: Filter by name or email (partial match, optional)
        auth_provider: Filter by auth provider (e.g., 'local', 'oidc', optional)
        limit: Maximum results (default: 20, max: 100)

    Returns:
        JSON-formatted list of identities

    Raises:
        ValueError: If session expired or API error
    """
    try:
        client = await _get_client()
        result = await resources.list_identities(
            client, search=search, auth_provider=auth_provider, limit=min(limit, 100)
        )
        return json.dumps(result, indent=2)
    except ValueError as e:
        return json.dumps({"error": str(e)}, indent=2)


@mcp.tool()
async def search_services(
    query: str | None = None, organization_id: int | None = None, limit: int = 20
) -> str:
    """
    Search services.

    Returns services (applications, microservices, etc.) in the system.
    Filter by name and organization.

    Args:
        query: Search by service name (partial match, optional)
        organization_id: Filter by organization ID (optional)
        limit: Maximum results (default: 20, max: 100)

    Returns:
        JSON-formatted list of services

    Raises:
        ValueError: If session expired or API error
    """
    try:
        client = await _get_client()
        result = await resources.search_services(
            client, query=query, organization_id=organization_id, limit=min(limit, 100)
        )
        return json.dumps(result, indent=2)
    except ValueError as e:
        return json.dumps({"error": str(e)}, indent=2)


# ============================================================================
# Server Initialization
# ============================================================================


async def _validate_session_on_startup() -> None:
    """Validate Elder session before starting the MCP server."""
    try:
        session = await _get_session()
        if not session.is_valid():
            print("Error: Elder session token expired", file=sys.stderr)
            sys.exit(1)
    except ValueError as e:
        print(f"Error: Failed to initialize Elder session: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(_validate_session_on_startup())
    mcp.run()  # FastMCP.run() is synchronous, defaults to stdio transport
