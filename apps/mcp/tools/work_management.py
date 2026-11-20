"""MCP tools for Elder issues, projects, milestones, labels, metadata, and helpdesk."""

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from apps.mcp.client import ElderClient
else:
    try:
        from apps.mcp.client import ElderClient
    except ImportError:
        from client import ElderClient


async def list_issues(
    client: ElderClient,
    status: str | None = None,
    priority: str | None = None,
    assignee_id: int | None = None,
    reporter_id: int | None = None,
    organization_id: int | None = None,
    milestone_id: int | None = None,
    project_id: int | None = None,
    search: str | None = None,
    limit: int = 50,
    page: int = 1,
) -> Any:
    """List issues with optional status, priority, assignee, project, and milestone filters."""
    params: dict[str, Any] = {
        "status": status,
        "priority": priority,
        "assignee_id": assignee_id,
        "reporter_id": reporter_id,
        "organization_id": organization_id,
        "milestone_id": milestone_id,
        "project_id": project_id,
        "search": search,
        "per_page": limit,
        "page": page,
    }
    return await client.get("/api/v1/issues", params=params)


async def get_issue(client: ElderClient, issue_id: int) -> Any:
    """Get full details for a specific issue by ID."""
    return await client.get(f"/api/v1/issues/{issue_id}")


async def get_issue_comments(client: ElderClient, issue_id: int) -> Any:
    """Get comments attached to a specific issue."""
    return await client.get(f"/api/v1/issues/{issue_id}/comments")


async def list_projects(
    client: ElderClient,
    organization_id: int | None = None,
    status: str | None = None,
    search: str | None = None,
    limit: int = 50,
    page: int = 1,
) -> Any:
    """List tracked projects with optional organization, status, and search filters."""
    params: dict[str, Any] = {
        "organization_id": organization_id,
        "status": status,
        "search": search,
        "per_page": limit,
        "page": page,
    }
    return await client.get("/api/v1/projects", params=params)


async def get_project(client: ElderClient, project_id: int) -> Any:
    """Get details for a specific project by ID."""
    return await client.get(f"/api/v1/projects/{project_id}")


async def list_milestones(
    client: ElderClient,
    organization_id: int | None = None,
    project_id: int | None = None,
    status: str | None = None,
    search: str | None = None,
    limit: int = 50,
    page: int = 1,
) -> Any:
    """List milestones with optional organization, project, status, and search filters."""
    params: dict[str, Any] = {
        "organization_id": organization_id,
        "project_id": project_id,
        "status": status,
        "search": search,
        "per_page": limit,
        "page": page,
    }
    return await client.get("/api/v1/milestones", params=params)


async def get_milestone_issues(client: ElderClient, milestone_id: int) -> Any:
    """List issues belonging to a specific milestone."""
    return await client.get(f"/api/v1/milestones/{milestone_id}/issues")


async def list_labels(
    client: ElderClient,
    limit: int = 100,
    page: int = 1,
) -> Any:
    """List configured resource labels in the tenant."""
    params: dict[str, Any] = {"per_page": limit, "page": page}
    return await client.get("/api/v1/labels", params=params)


async def get_entity_metadata(client: ElderClient, entity_id: int) -> Any:
    """Get structured custom metadata key-value fields attached to an entity."""
    return await client.get(f"/api/v1/metadata/entities/{entity_id}/metadata")


async def list_helpdesk_tickets(
    client: ElderClient,
    status: str | None = None,
    priority: str | None = None,
    assignee_id: int | None = None,
    requester_id: int | None = None,
    hd_team_id: int | None = None,
    search: str | None = None,
    limit: int = 50,
    page: int = 1,
) -> Any:
    """List helpdesk tickets filtered by status, priority, assignee, requester, or team."""
    params: dict[str, Any] = {
        "status": status,
        "priority": priority,
        "assignee_id": assignee_id,
        "requester_id": requester_id,
        "hd_team_id": hd_team_id,
        "search": search,
        "per_page": limit,
        "page": page,
    }
    return await client.get("/api/v1/helpdesk/tickets", params=params)


async def get_helpdesk_ticket(client: ElderClient, ticket_id: int) -> Any:
    """Get full details for a single helpdesk ticket."""
    return await client.get(f"/api/v1/helpdesk/tickets/{ticket_id}")


async def get_helpdesk_stats(client: ElderClient) -> Any:
    """Get summary dashboard statistics for helpdesk queues and SLAs."""
    return await client.get("/api/v1/helpdesk/dashboard/stats")


async def list_helpdesk_teams(client: ElderClient) -> Any:
    """List helpdesk support teams configured in the tenant."""
    return await client.get("/api/v1/helpdesk/teams")
