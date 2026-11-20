"""Catalog-backed Elder MCP tools providing 1:1 API-first parity with hub-api."""

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from apps.mcp.catalog import (
        get_operation,
        list_operations,
        list_services,
        resolve_operation_path,
    )
    from apps.mcp.client import ElderClient
else:
    try:
        from apps.mcp.catalog import (
            get_operation,
            list_operations,
            list_services,
            resolve_operation_path,
        )
        from apps.mcp.client import ElderClient
    except ImportError:
        from catalog import (
            get_operation,
            list_operations,
            list_services,
            resolve_operation_path,
        )
        from client import ElderClient


CONFIRM_REQUIRED_METHODS = frozenset({"DELETE", "PUT", "PATCH"})


async def list_api_services(client: ElderClient) -> dict[str, Any]:
    """List all cataloged Elder API service namespaces and operation counts."""
    _ = client
    services = list_services()
    return {
        "services": services,
        "total_services": len(services),
        "total_operations": sum(item["operation_count"] for item in services),
    }


async def list_api_operations(
    client: ElderClient,
    service: str | None = None,
    method: str | None = None,
    search: str | None = None,
) -> dict[str, Any]:
    """Discover available ``hub-api`` operations optionally filtered by service, HTTP method, or keyword."""
    _ = client
    matched = list_operations(service=service, method=method, search=search)
    return {
        "service": service,
        "method": method.upper()
        if isinstance(method, str) and method.strip()
        else None,
        "search": search,
        "count": len(matched),
        "operations": [op.to_dict() for op in matched],
    }


async def describe_api_operation(
    client: ElderClient,
    service: str,
    operation: str,
) -> dict[str, Any]:
    """Return the exact HTTP method, path template, path parameters, and summary for an API operation."""
    _ = client
    op = get_operation(service, operation)
    return op.to_dict()


async def invoke_api_operation(
    client: ElderClient,
    service: str,
    operation: str,
    path_params: dict[str, Any] | None = None,
    query_params: dict[str, Any] | None = None,
    body: dict[str, Any] | list[Any] | None = None,
    confirm: bool | None = None,
) -> Any:
    """Thinly invoke any cataloged ``hub-api`` REST operation using the caller's credentials.

    State-changing DELETE/PUT/PATCH operations require an explicit ``confirm=true``.
    """
    op = get_operation(service, operation)
    resolved_path = resolve_operation_path(op, path_params)

    if op.method in CONFIRM_REQUIRED_METHODS and confirm is not True:
        raise ValueError(
            f"{op.method} operation {op.service}.{op.operation} changes or deletes data; "
            "pass confirm=true to proceed"
        )

    if query_params is not None and not isinstance(query_params, dict):
        raise ValueError("query_params must be a JSON object")

    if body is not None and op.method in ("GET", "HEAD"):
        raise ValueError(
            f"{op.method} operation {op.service}.{op.operation} does not accept a request body"
        )

    return await client.request(
        op.method,
        resolved_path,
        params=query_params,
        json=body,
    )


async def call_api_operation(
    client: ElderClient,
    service: str,
    operation: str,
    path_params: dict[str, Any] | None = None,
    query_params: dict[str, Any] | None = None,
    body: dict[str, Any] | list[Any] | None = None,
    confirm: bool | None = None,
) -> Any:
    """Alias for ``invoke_api_operation`` calling any cataloged ``hub-api`` operation."""
    return await invoke_api_operation(
        client,
        service=service,
        operation=operation,
        path_params=path_params,
        query_params=query_params,
        body=body,
        confirm=confirm,
    )
