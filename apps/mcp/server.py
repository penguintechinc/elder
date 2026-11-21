"""Elder MCP server exposing all registered Elder platform tools via FastMCP."""

from __future__ import annotations

import inspect
import json
import os
import sys
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, get_type_hints

import httpx

# Ensure `apps/mcp` works both as `apps.mcp.server` and `python apps/mcp/server.py`.
_HERE = str(Path(__file__).resolve().parent)
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from mcp.server.fastmcp import FastMCP  # noqa: E402

try:  # noqa: E402
    from apps.mcp.envutil import env_int
except ImportError:
    from envutil import env_int

if TYPE_CHECKING:
    from apps.mcp.auth import (
        CallerCredentials,
        ElderAuthError,
        ElderSession,
        extract_caller_credentials,
        get_caller_credentials,
        is_hosted_mode,
        reset_caller_context,
        resolve_elder_base_url,
        set_caller_context,
    )
    from apps.mcp.client import ElderClient
    from apps.mcp.registry import TOOL_REGISTRY, ToolDefinition, execute_tool
else:
    try:  # noqa: E402
        from apps.mcp.auth import (
            CallerCredentials,
            ElderAuthError,
            ElderSession,
            extract_caller_credentials,
            get_caller_credentials,
            is_hosted_mode,
            reset_caller_context,
            resolve_elder_base_url,
            set_caller_context,
        )
        from apps.mcp.client import ElderClient
        from apps.mcp.registry import TOOL_REGISTRY, ToolDefinition, execute_tool
    except ImportError:
        from auth import (
            CallerCredentials,
            ElderAuthError,
            ElderSession,
            extract_caller_credentials,
            get_caller_credentials,
            is_hosted_mode,
            reset_caller_context,
            resolve_elder_base_url,
            set_caller_context,
        )
        from client import ElderClient
        from registry import TOOL_REGISTRY, ToolDefinition, execute_tool

mcp = FastMCP(
    "elder",
    host=os.environ.get("MCP_HOST", "127.0.0.1"),
    port=env_int("MCP_PORT", 8080),
)

_client: ElderClient | None = None
_hosted_transport: httpx.AsyncBaseTransport | None = None


def _extract_active_mcp_request_headers() -> Any | None:
    """Return HTTP headers from the active FastMCP request context when present."""
    get_ctx = getattr(mcp, "get_context", None)
    if not callable(get_ctx):
        return None
    try:
        ctx = get_ctx()
        req_ctx = getattr(ctx, "request_context", None)
        http_req = getattr(req_ctx, "request", None) if req_ctx is not None else None
        headers = getattr(http_req, "headers", None) if http_req is not None else None
        return headers
    except Exception:
        return None


def _build_request_scoped_client(
    creds: CallerCredentials,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> ElderClient:
    base_url = resolve_elder_base_url()
    session = ElderSession.from_caller(base_url, creds, transport=transport)
    return ElderClient(
        session=session,
        transport=transport,
        caller_credentials=creds,
    )


def get_client() -> ElderClient:
    """Return a caller-scoped ``ElderClient`` in hosted mode, or shared env client in stdio."""
    global _client

    req_headers = _extract_active_mcp_request_headers()
    if req_headers is not None:
        creds = extract_caller_credentials(req_headers)
        return _build_request_scoped_client(creds, transport=_hosted_transport)

    # Hosted mode fails closed: if the live request headers cannot be located we
    # never fall back to ambient context credentials (cross-caller leak risk).
    if is_hosted_mode() or get_caller_credentials() is not None:
        raise ElderAuthError("Caller authentication required in hosted HTTP/SSE mode")

    if _client is None:
        _client = ElderClient()
    return _client


class CallerAuthMiddleware:
    """Fail-closed ASGI middleware enforcing per-caller Bearer/API-Key auth."""

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        scope_type = scope.get("type")
        if scope_type == "lifespan":
            await self.app(scope, receive, send)
            return

        if scope_type not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return

        try:
            creds = extract_caller_credentials(scope.get("headers") or [])
        except ElderAuthError:
            if scope_type == "websocket":
                await send({"type": "websocket.close", "code": 1008})
                return
            body = json.dumps({"error": "Authentication required"}).encode("utf-8")
            await send(
                {
                    "type": "http.response.start",
                    "status": 401,
                    "headers": [
                        (b"content-type", b"application/json"),
                        (b"content-length", str(len(body)).encode("ascii")),
                    ],
                }
            )
            await send({"type": "http.response.body", "body": body})
            return

        patched_scope = dict(scope)
        try:
            from mcp.server.auth.middleware.bearer_auth import AuthenticatedUser
            from mcp.server.auth.provider import AccessToken

            patched_scope["user"] = AuthenticatedUser(
                AccessToken(
                    token="redacted",  # nosec B106
                    client_id=creds.credential_fingerprint(),
                    scopes=[],
                )
            )
        except ImportError:
            pass

        tokens = set_caller_context(creds, hosted=True)
        try:
            await self.app(patched_scope, receive, send)
        finally:
            reset_caller_context(tokens)


def build_hosted_asgi_app(
    transport: Literal["sse", "streamable-http"] = "streamable-http",
    *,
    mount_path: str | None = None,
    elder_transport: httpx.AsyncBaseTransport | None = None,
) -> CallerAuthMiddleware:
    """Wrap FastMCP's HTTP/SSE Starlette app with fail-closed per-caller auth."""
    global _hosted_transport
    _hosted_transport = elder_transport
    if transport == "sse":
        inner = mcp.sse_app(mount_path)
    elif transport == "streamable-http":
        inner = mcp.streamable_http_app()
    else:
        raise ValueError(f"Unsupported hosted MCP transport: {transport!r}")
    return CallerAuthMiddleware(inner)


def _build_mcp_wrapper(tool_def: ToolDefinition) -> Callable[..., Awaitable[Any]]:
    """Build an explicit-signature async wrapper required by FastMCP schema inspection."""
    sig = inspect.signature(tool_def.handler)
    hints = get_type_hints(tool_def.handler)
    params = [
        param.replace(annotation=hints.get(name, param.annotation))
        for name, param in sig.parameters.items()
        if name != "client"
    ]
    return_annotation = hints.get("return", sig.return_annotation)

    async def _tool_wrapper(**kwargs: Any) -> Any:
        client = get_client()
        try:
            return await execute_tool(
                tool_def.name,
                client,
                kwargs,
                read_only_only=False,
            )
        finally:
            if isinstance(client, ElderClient) and client is not _client:
                await client.close()

    _tool_wrapper.__name__ = tool_def.name
    _tool_wrapper.__doc__ = tool_def.description
    _tool_wrapper.__annotations__ = {
        name: hints.get(name, param.annotation)
        for name, param in sig.parameters.items()
        if name != "client"
    }
    if return_annotation is not inspect.Signature.empty:
        _tool_wrapper.__annotations__["return"] = return_annotation
    _tool_wrapper.__signature__ = inspect.Signature(  # type: ignore[attr-defined]
        parameters=params,
        return_annotation=return_annotation,
    )
    return _tool_wrapper


def register_all_tools(server: FastMCP) -> list[str]:
    """Register every tool from ``TOOL_REGISTRY`` on the given ``FastMCP`` instance."""
    registered: list[str] = []
    supports_annotations = "annotations" in inspect.signature(server.tool).parameters
    for tool_def in TOOL_REGISTRY:
        wrapper = _build_mcp_wrapper(tool_def)
        extra: dict[str, Any] = {}
        if supports_annotations:
            from mcp.types import ToolAnnotations

            extra["annotations"] = ToolAnnotations(
                readOnlyHint=tool_def.read_only,
                destructiveHint=tool_def.destructive or None,
            )
        server.tool(name=tool_def.name, description=tool_def.description, **extra)(
            wrapper
        )
        registered.append(tool_def.name)
    return registered


REGISTERED_TOOLS = register_all_tools(mcp)


def main() -> None:
    """Start the Elder MCP server using stdio (default) or SSE/streamable HTTP transport."""
    raw_transport = os.environ.get("MCP_TRANSPORT", "stdio").strip().lower()
    transport_map: dict[str, Literal["stdio", "sse", "streamable-http"]] = {
        "stdio": "stdio",
        "sse": "sse",
        "streamable-http": "streamable-http",
    }
    if raw_transport not in transport_map:
        raise ValueError(f"Unsupported MCP_TRANSPORT: {raw_transport!r}")

    selected = transport_map[raw_transport]
    if selected == "stdio":
        mcp.run(transport="stdio")
        return

    import uvicorn

    app = build_hosted_asgi_app(selected)
    uvicorn.run(
        app,
        host=mcp.settings.host,
        port=mcp.settings.port,
        log_level=mcp.settings.log_level.lower(),
    )


if __name__ == "__main__":
    main()
