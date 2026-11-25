"""Elder MCP server and programmatic tool registry package."""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from apps.mcp.auth import CallerCredentials, ElderAuthError, ElderSession
    from apps.mcp.client import ElderAPIError, ElderClient
else:
    try:
        from apps.mcp.auth import CallerCredentials, ElderAuthError, ElderSession
        from apps.mcp.client import ElderAPIError, ElderClient
    except ImportError:
        from auth import CallerCredentials, ElderAuthError, ElderSession
        from client import ElderAPIError, ElderClient

__all__ = [
    "CallerCredentials",
    "ElderAPIError",
    "ElderAuthError",
    "ElderClient",
    "ElderSession",
]
