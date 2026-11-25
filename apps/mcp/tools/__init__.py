"""Elder MCP tool functions grouped by domain."""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from apps.mcp.tools import (
        api_catalog,
        entities,
        knowledge,
        operations,
        relationships,
        resources,
        security,
        work_management,
    )
else:
    try:
        from apps.mcp.tools import (
            api_catalog,
            entities,
            knowledge,
            operations,
            relationships,
            resources,
            security,
            work_management,
        )
    except ImportError:
        from tools import (
            api_catalog,
            entities,
            knowledge,
            operations,
            relationships,
            resources,
            security,
            work_management,
        )

__all__ = [
    "api_catalog",
    "entities",
    "knowledge",
    "operations",
    "relationships",
    "resources",
    "security",
    "work_management",
]
