"""MCP tools for Elder knowledge & automation modules: documents, pages, diagrams, AI resources, streams, and flows."""

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from apps.mcp.client import ElderClient
else:
    try:
        from apps.mcp.client import ElderClient
    except ImportError:
        from client import ElderClient


async def list_documents(
    client: ElderClient,
    status: str | None = None,
    category: str | None = None,
    q: str | None = None,
) -> Any:
    """List knowledge-base documents with optional status, category, or search filters."""
    params: dict[str, Any] = {
        "status": status,
        "category": category,
        "q": q,
    }
    return await client.get("/api/v1/documents", params=params)


async def get_document(client: ElderClient, slug: str) -> Any:
    """Fetch full content for a knowledge-base document by slug."""
    return await client.get(f"/api/v1/documents/{slug}")


async def list_pages(
    client: ElderClient,
    status: str | None = None,
    visibility: str | None = None,
) -> Any:
    """List status/runbook pages with optional status and visibility filters."""
    params: dict[str, Any] = {
        "status": status,
        "visibility": visibility,
    }
    return await client.get("/api/v1/pages", params=params)


async def get_page(client: ElderClient, slug: str) -> Any:
    """Fetch a single page by slug."""
    return await client.get(f"/api/v1/pages/{slug}")


async def list_diagrams(
    client: ElderClient,
    status: str | None = None,
    q: str | None = None,
    is_template: bool | None = None,
) -> Any:
    """List architecture and network diagrams."""
    params: dict[str, Any] = {
        "status": status,
        "q": q,
        "is_template": str(is_template).lower() if is_template is not None else None,
    }
    return await client.get("/api/v1/diagrams", params=params)


async def get_diagram(client: ElderClient, diagram_id: int) -> Any:
    """Fetch full definition and nodes/edges for a diagram by ID."""
    return await client.get(f"/api/v1/diagrams/{diagram_id}")


async def list_ai_resources(
    client: ElderClient,
    organization_id: int | None = None,
    resource_type: str | None = None,
    status: str | None = None,
    agreement_level: str | None = None,
    search: str | None = None,
) -> Any:
    """List registered AI models, agents, and governance resources."""
    params: dict[str, Any] = {
        "organization_id": organization_id,
        "resource_type": resource_type,
        "status": status,
        "agreement_level": agreement_level,
        "search": search,
    }
    return await client.get("/api/v1/ai/resources", params=params)


async def get_ai_resource(client: ElderClient, resource_id: int) -> Any:
    """Get details for a specific AI governance resource by ID."""
    return await client.get(f"/api/v1/ai/resources/{resource_id}")


async def list_streams(
    client: ElderClient,
    status: str | None = None,
    q: str | None = None,
    is_template: bool | None = None,
) -> Any:
    """List automation workflow streams."""
    params: dict[str, Any] = {
        "status": status,
        "q": q,
        "is_template": str(is_template).lower() if is_template is not None else None,
    }
    return await client.get("/api/v1/streams", params=params)


async def get_stream(client: ElderClient, stream_id: int) -> Any:
    """Get definition for a specific automation stream by ID."""
    return await client.get(f"/api/v1/streams/{stream_id}")


async def list_stream_executions(
    client: ElderClient,
    stream_id: int | None = None,
    status: str | None = None,
) -> Any:
    """List workflow stream execution runs globally or for a specific stream."""
    params: dict[str, Any] = {"status": status}
    if stream_id is not None:
        return await client.get(
            f"/api/v1/streams/{stream_id}/executions", params=params
        )
    return await client.get("/api/v1/streams/executions", params=params)


async def list_flows(
    client: ElderClient,
    status: str | None = None,
    repository_provider: str | None = None,
    is_enabled: bool | None = None,
    search: str | None = None,
    page: int = 1,
    per_page: int = 20,
) -> Any:
    """List CI/CD deployment flow pipelines."""
    params: dict[str, Any] = {
        "status": status,
        "repository_provider": repository_provider,
        "is_enabled": str(is_enabled).lower() if is_enabled is not None else None,
        "search": search,
        "page": page,
        "per_page": per_page,
    }
    return await client.get("/api/v1/flows", params=params)


async def list_flow_promotions(
    client: ElderClient,
    status: str | None = None,
    flow_id: str | None = None,
    page: int = 1,
    per_page: int = 20,
) -> Any:
    """List CI/CD environment promotion requests and states."""
    params: dict[str, Any] = {
        "status": status,
        "flow_id": flow_id,
        "page": page,
        "per_page": per_page,
    }
    return await client.get("/api/v1/flows/promotions", params=params)
