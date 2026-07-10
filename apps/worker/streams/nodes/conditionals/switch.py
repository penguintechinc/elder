"""Switch Conditional Node for Streams Workflow.

Switch/case branching node that routes data to different outputs based on
field value matching. Supports multiple cases with a default fallback output.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List

from ...executor.node_registry import register_node
from ..base import BaseNode

logger = logging.getLogger(__name__)


@register_node("conditional_switch", "conditionals", "Switch")
class SwitchConditional(BaseNode):
    """Switch/case conditional node that routes data based on field value matching."""

    node_type = "conditional_switch"
    name = "Switch"
    description = "Route data to different outputs based on field value matching"
    category = "conditionals"

    @classmethod
    def inputs(cls) -> List[Dict[str, Any]]:
        """Define input ports for the switch node."""
        return [
            {
                "name": "in",
                "description": "Input data to evaluate against cases",
                "required": True,
                "data_type": "any",
            },
        ]

    @classmethod
    def outputs(cls) -> List[Dict[str, Any]]:
        """Define output ports for the switch node."""
        return [
            {
                "name": "case1",
                "description": "Output when value matches case 1",
                "data_type": "any",
            },
            {
                "name": "case2",
                "description": "Output when value matches case 2",
                "data_type": "any",
            },
            {
                "name": "case3",
                "description": "Output when value matches case 3",
                "data_type": "any",
            },
            {
                "name": "case4",
                "description": "Output when value matches case 4",
                "data_type": "any",
            },
            {
                "name": "default",
                "description": "Output when no cases match",
                "data_type": "any",
            },
        ]

    def validate_config(self, config: Dict[str, Any]) -> List[str]:
        """Validate switch conditional configuration."""
        errors = []

        field = config.get("field")
        if not field:
            errors.append("field is required (field to check for value matching)")

        cases = config.get("cases", [])
        if not cases:
            errors.append("cases array is required with at least one case")

        for i, case in enumerate(cases):
            if "value" not in case:
                errors.append(f"Case {i + 1}: value is required")
            if "output" not in case:
                errors.append(f"Case {i + 1}: output is required")
            output = case.get("output", "")
            if output not in ("case1", "case2", "case3", "case4", "default"):
                errors.append(
                    f"Case {i + 1}: invalid output '{output}'. "
                    "Must be one of: case1, case2, case3, case4, default"
                )

        return errors

    def _get_field_value(self, data: Any, field: str) -> Any:
        """Get nested field value using dot notation."""
        if not isinstance(data, dict):
            return None

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

    def _find_matching_output(self, field_value: Any, cases: List[Dict]) -> str:
        """Find the first matching case output for a field value."""
        for case in cases:
            if field_value == case.get("value"):
                return case.get("output", "default")
        return "default"

    async def execute(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        """Execute the switch conditional."""
        if "in" not in inputs:
            raise ValueError("Required input 'in' is missing")

        input_data = inputs.get("in")
        field = self.get_config_value("field", "")
        cases = self.get_config_value("cases", [])

        try:
            # Extract field value from input data
            field_value = self._get_field_value(input_data, field)

            # Find matching case output
            matched_output = self._find_matching_output(field_value, cases)

            # Build outputs with only the matched case populated
            outputs = {
                "case1": {
                    "data": input_data if matched_output == "case1" else None,
                    "metadata": {},
                    "source_node_id": self.context.get("node_id", ""),
                },
                "case2": {
                    "data": input_data if matched_output == "case2" else None,
                    "metadata": {},
                    "source_node_id": self.context.get("node_id", ""),
                },
                "case3": {
                    "data": input_data if matched_output == "case3" else None,
                    "metadata": {},
                    "source_node_id": self.context.get("node_id", ""),
                },
                "case4": {
                    "data": input_data if matched_output == "case4" else None,
                    "metadata": {},
                    "source_node_id": self.context.get("node_id", ""),
                },
                "default": {
                    "data": input_data if matched_output == "default" else None,
                    "metadata": {},
                    "source_node_id": self.context.get("node_id", ""),
                },
            }

            self.log_info(
                f"Field '{field}' with value {field_value!r} "
                f"routed to output '{matched_output}'"
            )

            return outputs

        except Exception as e:
            self.log_error(f"Switch evaluation failed: {e}")
            raise
