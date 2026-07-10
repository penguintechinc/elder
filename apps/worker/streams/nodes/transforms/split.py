"""Split Transform Node for Streams Workflow.

Configurable split node that divides arrays or strings into separate output
items. Supports multiple splitting modes including array passthrough,
string delimited splitting, chunk-based splitting, and field extraction.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List

from ...executor.node_registry import register_node
from ..base import BaseNode

logger = logging.getLogger(__name__)


@register_node("transform_split", "transforms", "Split")
class SplitTransform(BaseNode):
    """Split arrays or strings into separate outputs."""

    node_type = "transform_split"
    name = "Split"
    description = "Split arrays or strings into multiple items"
    category = "transforms"

    @classmethod
    def inputs(cls) -> List[Dict[str, Any]]:
        """Define input ports for the split node."""
        return [
            {
                "name": "in",
                "description": "Array or string to split",
                "required": True,
                "data_type": "any",
            },
        ]

    @classmethod
    def outputs(cls) -> List[Dict[str, Any]]:
        """Define output ports for the split node."""
        return [
            {
                "name": "out",
                "description": "Array of split items",
                "data_type": "array",
            },
            {
                "name": "count",
                "description": "Number of items after split",
                "data_type": "number",
            },
            {
                "name": "first",
                "description": "First item",
                "data_type": "any",
            },
            {
                "name": "last",
                "description": "Last item",
                "data_type": "any",
            },
        ]

    def validate_config(self, config: Dict[str, Any]) -> List[str]:
        """Validate split node configuration."""
        errors = []

        mode = config.get("mode", "array")
        valid_modes = {"array", "string", "chunks", "field"}
        if mode not in valid_modes:
            errors.append(
                f"Invalid mode: {mode}. Valid modes: {', '.join(sorted(valid_modes))}"
            )

        if mode == "string":
            delimiter = config.get("delimiter")
            if delimiter is None:
                errors.append("delimiter is required for string mode")

        if mode == "chunks":
            chunk_size = config.get("chunkSize")
            if not isinstance(chunk_size, int) or chunk_size < 1:
                errors.append("chunkSize must be a positive integer for chunks mode")

        if mode == "field":
            field = config.get("field")
            if not field:
                errors.append("field is required for field mode")

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

    def _chunk_list(self, lst: List, size: int) -> List[List]:
        """Split a list into chunks of given size."""
        return [lst[i : i + size] for i in range(0, len(lst), size)]

    async def execute(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        """Execute the split transform."""
        if "in" not in inputs:
            raise ValueError("Required input 'in' is missing")

        input_data = inputs.get("in")
        mode = self.get_config_value("mode", "array")

        items: List[Any] = []

        try:
            if mode == "array":
                # Input is already an array, just pass through
                if isinstance(input_data, list):
                    items = input_data
                else:
                    items = [input_data]

            elif mode == "string":
                # Split string by delimiter
                delimiter = self.get_config_value("delimiter", ",")
                if isinstance(input_data, str):
                    items = input_data.split(delimiter)
                    # Optionally strip whitespace
                    if self.get_config_value("trim", True):
                        items = [item.strip() for item in items]
                else:
                    items = [str(input_data)]

            elif mode == "chunks":
                # Split into chunks of size N
                chunk_size = self.get_config_value("chunkSize", 10)
                if isinstance(input_data, list):
                    items = self._chunk_list(input_data, chunk_size)
                elif isinstance(input_data, str):
                    items = [
                        input_data[i : i + chunk_size]
                        for i in range(0, len(input_data), chunk_size)
                    ]
                else:
                    items = [input_data]

            elif mode == "field":
                # Extract field from each item in array
                field = self.get_config_value("field", "")
                if isinstance(input_data, list):
                    items = [
                        self._get_field_value(item, field)
                        for item in input_data
                        if isinstance(item, dict)
                    ]
                elif isinstance(input_data, dict):
                    value = self._get_field_value(input_data, field)
                    items = value if isinstance(value, list) else [value]
                else:
                    items = [input_data]

            # Remove None/empty values if configured
            if self.get_config_value("removeEmpty", False):
                items = [item for item in items if item is not None and item != ""]

            count = len(items)
            first = items[0] if items else None
            last = items[-1] if items else None

            self.log_info(f"Split into {count} items using mode '{mode}'")

            return {
                "out": {
                    "data": items,
                    "metadata": {},
                    "source_node_id": self.context.get("node_id", ""),
                },
                "count": {
                    "data": count,
                    "metadata": {},
                    "source_node_id": self.context.get("node_id", ""),
                },
                "first": {
                    "data": first,
                    "metadata": {},
                    "source_node_id": self.context.get("node_id", ""),
                },
                "last": {
                    "data": last,
                    "metadata": {},
                    "source_node_id": self.context.get("node_id", ""),
                },
            }

        except Exception as e:
            self.log_error(f"Split failed: {e}")
            raise
