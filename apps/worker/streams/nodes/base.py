"""Base node interface for Streams workflow nodes.

All node implementations inherit from BaseNode.
Ported from icestreams-worker.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List

logger = logging.getLogger(__name__)


class BaseNode:
    """Base class for all Streams workflow nodes.

    Subclasses must:
    - Override node_type, name, description, category class attributes
    - Implement async execute(context, inputs) -> Dict[str, Any]
    """

    node_type: str = ""
    name: str = ""
    description: str = ""
    category: str = ""

    def __init__(self, context: dict[str, Any]) -> None:
        """Initialize the node with execution context.

        Args:
            context: Execution context dict with execution_id, playbook_id, node_id,
                    config, global_config, metadata
        """
        if not self.node_type:
            raise ValueError(
                f"{self.__class__.__name__} must define node_type class attribute"
            )
        if not self.name:
            raise ValueError(
                f"{self.__class__.__name__} must define name class attribute"
            )
        if not self.description:
            raise ValueError(
                f"{self.__class__.__name__} must define description class attribute"
            )
        if not self.category:
            raise ValueError(
                f"{self.__class__.__name__} must define category class attribute"
            )
        self.context = context

    @classmethod
    def inputs(cls) -> list[dict[str, Any]]:
        """Define input ports (optional). Each port: {name, description, required, data_type}."""
        return []

    @classmethod
    def outputs(cls) -> list[dict[str, Any]]:
        """Define output ports (optional). Each port: {name, description, data_type}."""
        return []

    async def execute(self, inputs: dict[str, Any]) -> dict[str, Any]:
        """Execute the node.

        Args:
            inputs: Dictionary of input data keyed by port name.

        Returns:
            Dictionary of output data keyed by port name (typically one output per node,
            keyed by "out" or "default", unless a conditional routing to multiple outputs).
        """
        raise NotImplementedError("Node must implement execute method")

    def validate_config(self, config: dict[str, Any]) -> list[str]:
        """Validate node configuration. Return list of error messages."""
        return []

    def get_config_value(self, key: str, default: Any = None) -> Any:
        """Safely get a config value."""
        return self.context.get("config", {}).get(key, default)

    def log_info(self, message: str) -> None:
        """Log info message with context."""
        logger.info(
            f"[{self.context.get('execution_id')}][{self.context.get('node_id')}] {message}"
        )

    def log_warning(self, message: str) -> None:
        """Log warning message with context."""
        logger.warning(
            f"[{self.context.get('execution_id')}][{self.context.get('node_id')}] {message}"
        )

    def log_error(self, message: str) -> None:
        """Log error message with context."""
        logger.error(
            f"[{self.context.get('execution_id')}][{self.context.get('node_id')}] {message}"
        )

    def __repr__(self) -> str:
        """String representation."""
        return (
            f"{self.__class__.__name__}(node_type={self.node_type!r}, "
            f"name={self.name!r}, category={self.category!r})"
        )
