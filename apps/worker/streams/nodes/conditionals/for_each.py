"""ForEach Conditional Node for Streams Workflow.

Iterates over array input, outputting each item individually along with its index.
Emits a "done" signal upon completion.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List

from ...executor.node_registry import register_node
from ..base import BaseNode

logger = logging.getLogger(__name__)


@register_node("conditional_for_each", "conditionals", "ForEach")
class ForEachConditional(BaseNode):
    """Iterate over array input, outputting each item individually."""

    node_type = "conditional_for_each"
    name = "ForEach"
    description = "Iterate over array, output each item with index"
    category = "conditionals"

    @classmethod
    def inputs(cls) -> list[dict[str, Any]]:
        """Define input ports for the for-each node."""
        return [
            {
                "name": "array",
                "description": "Array or iterable to iterate over",
                "required": True,
                "data_type": "array",
            },
        ]

    @classmethod
    def outputs(cls) -> list[dict[str, Any]]:
        """Define output ports for the for-each node."""
        return [
            {
                "name": "item",
                "description": "Current item in iteration",
                "data_type": "any",
            },
            {
                "name": "index",
                "description": "Current iteration index (0-based)",
                "data_type": "number",
            },
            {
                "name": "done",
                "description": "Fires when iteration completes",
                "data_type": "bool",
            },
        ]

    def validate_config(self, config: dict[str, Any]) -> list[str]:
        """Validate for-each node configuration."""
        errors = []
        field = config.get("field")
        if field is not None and not isinstance(field, str):
            errors.append("field configuration must be a string path")
        return errors

    def _get_field_value(self, data: Any, field: str) -> Any:
        """Get nested field value using dot notation."""
        parts = field.split(".")
        value = data
        for part in parts:
            if isinstance(value, dict):
                value = value.get(part)
            elif isinstance(value, list) and part.isdigit():
                idx = int(part)
                value = value[idx] if idx < len(value) else None
            else:
                return None
        return value

    async def execute(self, inputs: dict[str, Any]) -> dict[str, Any]:
        """Execute the for-each iteration."""
        if "array" not in inputs:
            raise ValueError("Required input 'array' is missing")

        input_array = inputs.get("array")
        field = self.get_config_value("field")

        try:
            # Ensure input is iterable
            if not isinstance(input_array, list):
                if isinstance(input_array, (tuple, set)):
                    items = list(input_array)
                elif isinstance(input_array, dict):
                    # Iterate over dict values
                    items = list(input_array.values())
                else:
                    # Wrap single item in array
                    items = [input_array]
            else:
                items = input_array

            # Extract field from each item if configured
            if field:
                items = [self._get_field_value(item, field) for item in items]
                # Filter out None values from failed extractions
                items = [item for item in items if item is not None]

            count = len(items)
            self.log_info(
                f"ForEach iterating over {count} items"
                + (f" extracting field '{field}'" if field else "")
            )

            # NOTE: The executor handles iteration by managing the loop state.
            # This node returns the first item with metadata for the executor to process.
            return {
                "item": {
                    "data": items[0] if items else None,
                    "metadata": {},
                    "source_node_id": self.context.get("node_id", ""),
                },
                "index": {
                    "data": 0,
                    "metadata": {},
                    "source_node_id": self.context.get("node_id", ""),
                },
                "done": {
                    "data": count == 0,
                    "metadata": {"_iterations": items},
                    "source_node_id": self.context.get("node_id", ""),
                },
            }

        except Exception as e:
            self.log_error(f"ForEach failed: {e}")
            raise
