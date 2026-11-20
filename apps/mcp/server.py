"""Elder MCP server exposing all registered Elder platform tools via FastMCP."""

from __future__ import annotations

import inspect
import os
import sys
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, get_type_hints

# Ensure `apps/mcp` works both as `apps.mcp.server` and `python apps/mcp/server.py`.
_HERE = str(Path(__file__).resolve().parent)
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from mcp.server.fastmcp import FastMCP  # noqa: E402

if TYPE_CHECKING:
    from apps.mcp.client import ElderClient
    from apps.mcp.registry import TOOL_REGISTRY, ToolDefinition
else:
    try:  # noqa: E402
        from apps.mcp.client import ElderClient
        from apps.mcp.registry import TOOL_REGISTRY, ToolDefinition
    except ImportError:
        from client import ElderClient
        from registry import TOOL_REGISTRY, ToolDefinition

mcp = FastMCP(
    "elder",
    host=os.environ.get("MCP_HOST", "127.0.0.1"),
    port=int(os.environ.get("MCP_PORT", "8080")),
)

_client: ElderClient | None = None


def get_client() -> ElderClient:
    """Return the shared standalone ``ElderClient`` configured from environment vars."""
    global _client
    if _client is None:
        _client = ElderClient()
    return _client


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
        return await tool_def.handler(get_client(), **kwargs)

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
    for tool_def in TOOL_REGISTRY:
        wrapper = _build_mcp_wrapper(tool_def)
        server.tool(name=tool_def.name, description=tool_def.description)(wrapper)
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
    mcp.run(transport=transport_map[raw_transport])


if __name__ == "__main__":
    main()
