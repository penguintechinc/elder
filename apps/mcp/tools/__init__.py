"""Elder MCP tool functions grouped by domain."""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from apps.mcp.tools import (
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
            entities,
            knowledge,
            operations,
            relationships,
            resources,
            security,
            work_management,
        )

__all__ = [
    "entities",
    "knowledge",
    "operations",
    "relationships",
    "resources",
    "security",
    "work_management",
]
