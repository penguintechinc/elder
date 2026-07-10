"""JSON Transform Node for Streams Workflow.

Configurable JSON transformation node that processes data using various
operations including path extraction, value setting, deletion, field renaming,
merging, flattening, and unflattening.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List

from ...executor.node_registry import register_node
from ..base import BaseNode

logger = logging.getLogger(__name__)


@register_node("transform_json", "transforms", "JSON Transform")
class JsonTransform(BaseNode):
    """Transform JSON data using path queries and manipulation operations."""

    node_type = "transform_json"
    name = "JSON Transform"
    description = "Transform JSON data using path queries and operations"
    category = "transforms"

    @classmethod
    def inputs(cls) -> List[Dict[str, Any]]:
        """Define input ports for the JSON transform node."""
        return [
            {
                "name": "in",
                "description": "Input data to transform",
                "required": True,
                "data_type": "any",
            },
        ]

    @classmethod
    def outputs(cls) -> List[Dict[str, Any]]:
        """Define output ports for the JSON transform node."""
        return [
            {
                "name": "out",
                "description": "Transformed output",
                "data_type": "any",
            },
        ]

    def validate_config(self, config: Dict[str, Any]) -> List[str]:
        """Validate JSON transform configuration."""
        errors = []

        operation = config.get("operation", "extract")
        valid_ops = {
            "extract",
            "set",
            "delete",
            "rename",
            "merge",
            "flatten",
            "unflatten",
        }
        if operation not in valid_ops:
            errors.append(f"Invalid operation: {operation}. Valid: {sorted(valid_ops)}")

        if operation == "extract":
            path = config.get("jsonPath", "")
            if not path:
                errors.append("jsonPath is required for extract operation")

        if operation == "set":
            if not config.get("jsonPath"):
                errors.append("jsonPath is required for set operation")
            if "value" not in config:
                errors.append("value is required for set operation")

        if operation == "rename":
            if not config.get("fromPath"):
                errors.append("fromPath is required for rename operation")
            if not config.get("toPath"):
                errors.append("toPath is required for rename operation")

        return errors

    def _extract_path(self, data: Any, path: str) -> Any:
        """Extract value from data using JMESPath with fallback to dot notation."""
        try:
            import jmespath

            return jmespath.search(path, data)
        except ImportError:
            # Fallback to simple dot notation
            return self._extract_dot_path(data, path)

    def _extract_dot_path(self, data: Any, path: str) -> Any:
        """Extract value using simple dot notation (no JMESPath)."""
        parts = path.split(".")
        current = data

        for part in parts:
            if isinstance(current, dict):
                current = current.get(part)
                if current is None:
                    return None
            elif isinstance(current, list) and part.isdigit():
                idx = int(part)
                if idx < len(current):
                    current = current[idx]
                else:
                    return None
            else:
                return None

        return current

    def _set_path(self, data: Any, path: str, value: Any) -> Any:
        """Set value at specified path, creating nested structure as needed."""
        if not isinstance(data, dict):
            data = {}

        parts = path.split(".")
        current = data

        # Navigate/create path to parent of target
        for part in parts[:-1]:
            if part not in current or not isinstance(current[part], dict):
                current[part] = {}
            current = current[part]

        # Set the final value
        current[parts[-1]] = value
        return data

    def _delete_path(self, data: Dict, path: str) -> Dict:
        """Delete value at specified path."""
        parts = path.split(".")
        current = data

        # Navigate to parent of target
        for part in parts[:-1]:
            if isinstance(current, dict) and part in current:
                current = current[part]
            else:
                # Path doesn't exist, return unchanged
                return data

        # Delete the final key
        if isinstance(current, dict) and parts[-1] in current:
            del current[parts[-1]]

        return data

    def _rename_path(self, data: Dict, from_path: str, to_path: str) -> Dict:
        """Rename a field by extracting from one path and setting at another."""
        value = self._extract_dot_path(data, from_path)
        data = self._delete_path(data, from_path)
        data = self._set_path(data, to_path, value)
        return data

    def _flatten(self, data: Dict, prefix: str = "", sep: str = ".") -> Dict:
        """Flatten nested dictionary to single level with concatenated keys."""
        result = {}
        for key, value in data.items():
            new_key = f"{prefix}{sep}{key}" if prefix else key

            if isinstance(value, dict):
                # Recursively flatten nested dicts
                result.update(self._flatten(value, new_key, sep))
            elif isinstance(value, list):
                # Handle lists by indexing
                for idx, item in enumerate(value):
                    list_key = f"{new_key}{sep}{idx}"
                    if isinstance(item, dict):
                        result.update(self._flatten(item, list_key, sep))
                    else:
                        result[list_key] = item
            else:
                result[new_key] = value

        return result

    def _unflatten(self, data: Dict, sep: str = ".") -> Dict:
        """Unflatten single-level dictionary back to nested structure."""
        result = {}

        for key, value in data.items():
            parts = key.split(sep)
            current = result

            # Navigate/create nested structure
            for part in parts[:-1]:
                if part not in current:
                    current[part] = {}
                current = current[part]

            current[parts[-1]] = value

        return result

    def _perform_operation(
        self, operation: str, input_data: Any, config: Dict[str, Any]
    ) -> Any:
        """Perform the specified JSON transformation operation."""
        if operation == "extract":
            path = config.get("jsonPath", "$")
            return self._extract_path(input_data, path)

        elif operation == "set":
            path = config.get("jsonPath")
            value = config.get("value")

            # If value is a string starting with "$.", treat it as a path to extract
            if isinstance(value, str) and value.startswith("$."):
                value = self._extract_path(input_data, value[2:])

            # Ensure we have a dict to modify
            data = dict(input_data) if isinstance(input_data, dict) else {}
            return self._set_path(data, path, value)

        elif operation == "delete":
            path = config.get("jsonPath")
            if not isinstance(input_data, dict):
                raise ValueError("Cannot delete path from non-object data")
            return self._delete_path(dict(input_data), path)

        elif operation == "rename":
            from_path = config.get("fromPath")
            to_path = config.get("toPath")
            if not isinstance(input_data, dict):
                raise ValueError("Cannot rename paths in non-object data")
            return self._rename_path(dict(input_data), from_path, to_path)

        elif operation == "merge":
            merge_data = config.get("mergeData", {})

            # If merge_data is a JSON string, parse it
            if isinstance(merge_data, str):
                try:
                    merge_data = json.loads(merge_data)
                except json.JSONDecodeError as e:
                    raise ValueError(f"Invalid JSON in mergeData: {e}")

            # Merge input and merge data
            if isinstance(input_data, dict):
                result = dict(input_data)
                result.update(merge_data)
                return result
            else:
                return merge_data

        elif operation == "flatten":
            sep = config.get("separator", ".")
            if not isinstance(input_data, dict):
                return input_data
            return self._flatten(input_data, sep=sep)

        elif operation == "unflatten":
            sep = config.get("separator", ".")
            if not isinstance(input_data, dict):
                return input_data
            return self._unflatten(input_data, sep=sep)

        else:
            raise ValueError(f"Unknown operation: {operation}")

    async def execute(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        """Execute the JSON transform operation."""
        if "in" not in inputs:
            raise ValueError("Required input 'in' is missing")

        input_data = inputs.get("in", {})
        operation = self.get_config_value("operation", "extract")

        try:
            result = self._perform_operation(
                operation, input_data, self.context.get("config", {})
            )
            self.log_info(f"JSON transform '{operation}' completed")

            return {
                "out": {
                    "data": result,
                    "metadata": {},
                    "source_node_id": self.context.get("node_id", ""),
                }
            }

        except Exception as e:
            self.log_error(f"JSON transform failed: {e}")
            raise
