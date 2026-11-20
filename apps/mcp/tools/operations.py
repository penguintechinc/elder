"""MCP tools for Elder operations: on-call, IPAM, discovery/sync, webhooks, identities, backups, and logs."""

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from apps.mcp.client import ElderClient
else:
    try:
        from apps.mcp.client import ElderClient
    except ImportError:
        from client import ElderClient


async def list_oncall_rotations(
    client: ElderClient,
    org_id: int | None = None,
    service_id: int | None = None,
    scope_type: str | None = None,
    schedule_type: str | None = None,
    is_active: bool | None = None,
) -> Any:
    """List on-call rotations filtered by organization, service, scope, or active flag."""
    params: dict[str, Any] = {
        "org_id": org_id,
        "service_id": service_id,
        "scope_type": scope_type,
        "schedule_type": schedule_type,
        "is_active": str(is_active).lower() if is_active is not None else None,
    }
    return await client.get("/api/v1/on-call/rotations", params=params)


async def get_current_oncall_org(client: ElderClient, org_id: int) -> Any:
    """Get who is currently on call for an organization."""
    return await client.get(f"/api/v1/on-call/rotations/current/organization/{org_id}")


async def get_current_oncall_service(client: ElderClient, service_id: int) -> Any:
    """Get who is currently on call for a specific service."""
    return await client.get(f"/api/v1/on-call/rotations/current/service/{service_id}")


async def list_ipam_prefixes(
    client: ElderClient,
    organization_id: int | None = None,
    status: str | None = None,
    parent_id: int | None = None,
    search: str | None = None,
    limit: int = 50,
    page: int = 1,
) -> Any:
    """List IPAM CIDR prefixes / subnets."""
    params: dict[str, Any] = {
        "organization_id": organization_id,
        "status": status,
        "parent_id": parent_id,
        "search": search,
        "per_page": limit,
        "page": page,
    }
    return await client.get("/api/v1/ipam/prefixes", params=params)


async def list_ipam_addresses(
    client: ElderClient,
    prefix_id: int | None = None,
    status: str | None = None,
    search: str | None = None,
    limit: int = 50,
    page: int = 1,
) -> Any:
    """List allocated IPAM IP addresses."""
    params: dict[str, Any] = {
        "prefix_id": prefix_id,
        "status": status,
        "search": search,
        "per_page": limit,
        "page": page,
    }
    return await client.get("/api/v1/ipam/addresses", params=params)


async def list_ipam_vlans(
    client: ElderClient,
    organization_id: int | None = None,
    status: str | None = None,
    search: str | None = None,
    limit: int = 50,
    page: int = 1,
) -> Any:
    """List IPAM VLAN definitions."""
    params: dict[str, Any] = {
        "organization_id": organization_id,
        "status": status,
        "search": search,
        "per_page": limit,
        "page": page,
    }
    return await client.get("/api/v1/ipam/vlans", params=params)


async def list_discovery_jobs(
    client: ElderClient,
    provider: str | None = None,
    enabled: bool | None = None,
    organization_id: int | None = None,
) -> Any:
    """List configured cloud/infrastructure discovery jobs."""
    params: dict[str, Any] = {
        "provider": provider,
        "enabled": str(enabled).lower() if enabled is not None else None,
        "organization_id": organization_id,
    }
    return await client.get("/api/v1/discovery/jobs", params=params)


async def get_discovery_history(client: ElderClient, limit: int = 50) -> Any:
    """Get recent discovery job execution history."""
    return await client.get("/api/v1/discovery/history", params={"limit": limit})


async def list_sync_configs(client: ElderClient) -> Any:
    """List bidirectional integration sync configurations."""
    return await client.get("/api/v1/sync/configs")


async def get_sync_status(client: ElderClient) -> Any:
    """Get aggregate status across configured external integration syncs."""
    return await client.get("/api/v1/sync/status")


async def list_sync_conflicts(
    client: ElderClient,
    resolved: bool = False,
    config_id: int | None = None,
) -> Any:
    """List unresolved or resolved synchronization conflicts."""
    params: dict[str, Any] = {
        "resolved": str(resolved).lower(),
        "config_id": config_id,
    }
    return await client.get("/api/v1/sync/conflicts", params=params)


async def list_webhooks(
    client: ElderClient,
    enabled: bool | None = None,
    status: str | None = None,
    limit: int = 50,
) -> Any:
    """List outbound alerting webhook endpoints."""
    params: dict[str, Any] = {
        "enabled": str(enabled).lower() if enabled is not None else None,
        "status": status,
        "limit": limit,
    }
    return await client.get("/api/v1/webhooks", params=params)


async def list_inbound_webhooks(client: ElderClient) -> Any:
    """List configured inbound webhook receivers."""
    return await client.get("/api/v1/webhooks/inbound")


async def list_identities(
    client: ElderClient,
    search: str | None = None,
    identity_type: str | None = None,
    is_active: bool | None = None,
    auth_provider: str | None = None,
    limit: int = 50,
    page: int = 1,
) -> Any:
    """List user and service identities in the tenant."""
    params: dict[str, Any] = {
        "search": search,
        "identity_type": identity_type,
        "is_active": str(is_active).lower() if is_active is not None else None,
        "auth_provider": auth_provider,
        "per_page": limit,
        "page": page,
    }
    return await client.get("/api/v1/identities", params=params)


async def get_identity(client: ElderClient, identity_id: int) -> Any:
    """Get full details for a single identity by ID."""
    return await client.get(f"/api/v1/identities/{identity_id}")


async def list_identity_groups(client: ElderClient) -> Any:
    """List IAM identity groups defined in the tenant."""
    return await client.get("/api/v1/identities/groups")


async def list_backups(
    client: ElderClient,
    enabled: bool | None = None,
) -> Any:
    """List database and configuration backup records."""
    params: dict[str, Any] = {
        "enabled": str(enabled).lower() if enabled is not None else None,
    }
    return await client.get("/api/v1/backup", params=params)


async def get_backup_stats(client: ElderClient) -> Any:
    """Get summary statistics for backup schedules and snapshots."""
    return await client.get("/api/v1/backup/stats")


async def list_system_logs(
    client: ElderClient,
    q: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> Any:
    """Query recent Elder API system log entries."""
    params: dict[str, Any] = {
        "q": q,
        "limit": limit,
        "offset": offset,
    }
    return await client.get("/api/v1/logs", params=params)
