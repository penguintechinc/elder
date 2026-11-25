"""Uvicorn ASGI entrypoint for the hosted ``hub-mcp`` Quart service."""

from __future__ import annotations

import os

import structlog

try:
    from apps.mcp.envutil import env_int
except ImportError:  # executed from apps/mcp/
    from envutil import env_int

logger = structlog.get_logger(__name__)


def main() -> None:
    """Start the ``hub-mcp`` ASGI server via uvicorn (mirrors ``apps/api/start.py``)."""
    import uvicorn

    host = os.getenv("MCP_HOST", "0.0.0.0")  # nosec B104
    port = env_int("MCP_PORT", 8080)

    logger.info("starting_hub_mcp_server", host=host, port=port)

    uvicorn.run(
        "apps.mcp.http_app:create_app",
        factory=True,
        host=host,
        port=port,
        loop="uvloop",
        http="h11",
        access_log=True,
        log_level="info",
    )


if __name__ == "__main__":
    main()
