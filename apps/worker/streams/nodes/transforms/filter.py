"""Filter Transform Node for Streams Workflow.

Configurable filter node that processes data based on multiple conditions
with AND/OR logic. Supports various comparison operators.
"""

from __future__ import annotations

import logging
import operator
import re
from typing import Any, Callable, Dict, List

from ...executor.node_registry import register_node
from ..base import BaseNode

logger = logging.getLogger(__name__)

# Operator mapping for filter conditions
OPERATORS: Dict[str, Callable] = {
    "eq": operator.eq,
    "ne": operator.ne,
    "gt": operator.gt,
    "gte": operator.ge,
    "lt": operator.lt,
    "lte": operator.le,
    "contains": lambda a, b: b in a if hasattr(a, "__contains__") else False,
    "not_contains": lambda a, b: b not in a if hasattr(a, "__contains__") else True,
    "starts_with": lambda a, b: str(a).startswith(str(b)),
    "ends_with": lambda a, b: str(a).endswith(str(b)),
    "regex": lambda a, b: bool(re.search(b, str(a))),
    "is_null": lambda a, b: a is None,
    "is_not_null": lambda a, b: a is not None,
    "in": lambda a, b: a in b if isinstance(b, (list, tuple, set)) else False,
    "not_in": lambda a, b: a not in b if isinstance(b, (list, tuple, set)) else True,
}


@register_node("transform_filter", "transforms", "Filter")
class FilterTransform(BaseNode):
    """Filter data based on configurable conditions with AND/OR logic."""

    node_type = "transform_filter"
    name = "Filter"
    description = "Filter data based on configurable conditions"
    category = "transforms"

    @classmethod
    def inputs(cls) -> List[Dict[str, Any]]:
        """Define input ports for the filter node."""
        return [
            {
                "name": "in",
                "description": "Input data (object or array)",
                "required": True,
                "data_type": "any",
            },
        ]

    @classmethod
    def outputs(cls) -> List[Dict[str, Any]]:
        """Define output ports for the filter node."""
        return [
            {
                "name": "out",
                "description": "Filtered data (matching items)",
                "data_type": "any",
            },
            {
                "name": "rejected",
                "description": "Rejected data (non-matching items)",
                "data_type": "any",
            },
        ]

    def validate_config(self, config: Dict[str, Any]) -> List[str]:
        """Validate filter node configuration."""
        errors = []

        conditions = config.get("conditions", [])
        if not conditions:
            errors.append("At least one condition is required")

        for i, cond in enumerate(conditions):
            if not cond.get("field"):
                errors.append(f"Condition {i + 1}: field is required")
            op = cond.get("operator", "eq")
            if op not in OPERATORS:
                valid_ops = ", ".join(sorted(OPERATORS.keys()))
                errors.append(
                    f"Condition {i + 1}: invalid operator '{op}'. Valid: {valid_ops}"
                )

        logic = config.get("logic", "and")
        if logic not in ("and", "or"):
            errors.append(f"Invalid logic: {logic}. Must be 'and' or 'or'")

        return errors

    def _get_field_value(self, data: Dict, field: str) -> Any:
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

    def _evaluate_condition(self, data: Dict, condition: Dict) -> bool:
        """Evaluate a single condition against data."""
        field = condition.get("field", "")
        op_name = condition.get("operator", "eq")
        expected = condition.get("value")

        actual = self._get_field_value(data, field)
        op_func = OPERATORS.get(op_name, operator.eq)

        try:
            return op_func(actual, expected)
        except Exception:
            return False

    def _matches(self, data: Dict, conditions: List[Dict], logic: str) -> bool:
        """Check if data matches all or any conditions based on logic."""
        if not conditions:
            return True

        results = [self._evaluate_condition(data, c) for c in conditions]

        if logic == "and":
            return all(results)
        else:  # or
            return any(results)

    async def execute(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        """Execute the filter transform."""
        if "in" not in inputs:
            raise ValueError("Required input 'in' is missing")

        input_data = inputs.get("in")
        conditions = self.get_config_value("conditions", [])
        logic = self.get_config_value("logic", "and")

        # Handle single object vs array
        is_array = isinstance(input_data, list)
        items = input_data if is_array else [input_data]

        matching = []
        rejected = []

        # Filter items
        for item in items:
            if isinstance(item, dict):
                if self._matches(item, conditions, logic):
                    matching.append(item)
                else:
                    rejected.append(item)
            else:
                # Non-dict items pass through to matching
                matching.append(item)

        # Return same type as input
        if is_array:
            result = matching
            rejected_result = rejected
        else:
            result = matching[0] if matching else None
            rejected_result = rejected[0] if rejected else None

        self.log_info(f"Filter: {len(matching)} matched, {len(rejected)} rejected")

        return {
            "out": {
                "data": result,
                "metadata": {},
                "source_node_id": self.context.get("node_id", ""),
            },
            "rejected": {
                "data": rejected_result,
                "metadata": {},
                "source_node_id": self.context.get("node_id", ""),
            },
        }
