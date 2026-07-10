"""Merge Transform Node for Streams Workflow.

Combines multiple input sources into a single output using various merge
strategies. Supports shallow object merge, deep merge, array concatenation,
and string concatenation.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List

from ...executor.node_registry import register_node
from ..base import BaseNode

logger = logging.getLogger(__name__)


@register_node("transform_merge", "transforms", "Merge")
class MergeTransform(BaseNode):
    """Merge multiple inputs into a single output."""

    node_type = "transform_merge"
    name = "Merge"
    description = "Combine multiple data sources into one output"
    category = "transforms"

    @classmethod
    def inputs(cls) -> List[Dict[str, Any]]:
        """Define input ports for merge node."""
        return [
            {
                "name": "in1",
                "description": "First input",
                "required": True,
                "data_type": "any",
            },
            {
                "name": "in2",
                "description": "Second input",
                "required": False,
                "data_type": "any",
            },
            {
                "name": "in3",
                "description": "Third input (optional)",
                "required": False,
                "data_type": "any",
            },
            {
                "name": "in4",
                "description": "Fourth input (optional)",
                "required": False,
                "data_type": "any",
            },
        ]

    @classmethod
    def outputs(cls) -> List[Dict[str, Any]]:
        """Define output ports for merge node."""
        return [
            {
                "name": "out",
                "description": "Merged output",
                "data_type": "any",
            },
        ]

    def validate_config(self, config: Dict[str, Any]) -> List[str]:
        """Validate merge node configuration."""
        errors = []

        mode = config.get("mode", "object")
        valid_modes = {"object", "array", "concat", "deep"}
        if mode not in valid_modes:
            errors.append(
                f"Invalid mode: {mode}. Valid modes: {', '.join(sorted(valid_modes))}"
            )

        # Validate separator for concat mode
        if mode == "concat":
            separator = config.get("separator", "")
            if not isinstance(separator, str):
                errors.append("separator must be a string when using concat mode")

        return errors

    def _deep_merge(self, base: Dict, override: Dict) -> Dict:
        """Recursively merge two dictionaries with override taking precedence."""
        result = dict(base)
        for key, value in override.items():
            if (
                key in result
                and isinstance(result[key], dict)
                and isinstance(value, dict)
            ):
                result[key] = self._deep_merge(result[key], value)
            else:
                result[key] = value
        return result

    async def execute(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        """Execute the merge transform."""
        if "in1" not in inputs:
            raise ValueError("Required input 'in1' is missing")

        mode = self.get_config_value("mode", "object")

        # Collect all non-None inputs in order (in1 through in4)
        input_keys = ["in1", "in2", "in3", "in4"]
        values = [inputs.get(key) for key in input_keys if inputs.get(key) is not None]

        if not values:
            self.log_info("No inputs provided, returning None")
            return {
                "out": {
                    "data": None,
                    "metadata": {},
                    "source_node_id": self.context.get("node_id", ""),
                }
            }

        try:
            if mode == "object":
                # Shallow merge: later objects override earlier ones
                result = {}
                for value in values:
                    if isinstance(value, dict):
                        result.update(value)
                    else:
                        # Wrap non-dict values with indexed keys
                        idx = values.index(value)
                        result[f"value_{idx}"] = value

            elif mode == "array":
                # Array mode: flatten lists and append other values
                result = []
                for value in values:
                    if isinstance(value, list):
                        result.extend(value)
                    else:
                        result.append(value)

            elif mode == "concat":
                # String/array concatenation
                separator = self.get_config_value("separator", "")

                if all(isinstance(v, str) for v in values):
                    # All strings: join with separator
                    result = separator.join(values)
                elif all(isinstance(v, list) for v in values):
                    # All lists: extend result list
                    result = []
                    for v in values:
                        result.extend(v)
                else:
                    # Mixed types: convert all to strings and join
                    result = separator.join(str(v) for v in values)

            elif mode == "deep":
                # Deep merge: recursively merge nested dictionaries
                result = {}
                for idx, value in enumerate(values):
                    if isinstance(value, dict):
                        result = self._deep_merge(result, value)
                    else:
                        result[f"value_{idx}"] = value

            else:
                # Fallback: return values as-is
                result = values

            self.log_info(f"Merged {len(values)} input(s) using mode '{mode}'")

            return {
                "out": {
                    "data": result,
                    "metadata": {},
                    "source_node_id": self.context.get("node_id", ""),
                }
            }

        except Exception as e:
            self.log_error(f"Merge operation failed: {e}")
            raise
