"""If-Then Conditional Node for Streams Workflow.

Evaluates conditions and routes execution through "true" or "false" outputs.
Supports multiple conditions with AND/OR logic.
Ported from icestreams-worker.
"""

from __future__ import annotations

import logging
import operator
import re
import time
from typing import Any, Callable, Dict, List

from ...executor.node_registry import register_node
from ..base import BaseNode

logger = logging.getLogger(__name__)

# Operator mapping for conditional evaluation
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


@register_node("conditional_if_then", "conditionals", "If-Then")
class IfThenConditional(BaseNode):
    """Conditional branching node that routes to true or false outputs."""

    node_type = "conditional_if_then"
    name = "If-Then"
    description = "Evaluate conditions and route execution to true or false outputs"
    category = "conditionals"

    @classmethod
    def inputs(cls) -> List[Dict[str, Any]]:
        """Define input ports for the conditional node."""
        return [
            {
                "name": "in",
                "description": "Input data to evaluate against conditions",
                "required": True,
                "data_type": "any",
            },
        ]

    @classmethod
    def outputs(cls) -> List[Dict[str, Any]]:
        """Define output ports for the conditional node."""
        return [
            {
                "name": "true",
                "description": "Output when condition evaluates to true",
                "data_type": "any",
            },
            {
                "name": "false",
                "description": "Output when condition evaluates to false",
                "data_type": "any",
            },
        ]

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

    def _evaluate_condition(self, data: Any, condition: Dict) -> bool:
        """Evaluate a single condition against data."""
        field = condition.get("field", "")
        op_name = condition.get("operator", "eq")
        expected = condition.get("value")

        actual = self._get_field_value(data, field) if isinstance(data, dict) else None
        op_func = OPERATORS.get(op_name, operator.eq)

        try:
            return op_func(actual, expected)
        except Exception:
            return False

    def _matches(self, data: Any, conditions: List[Dict], logic: str) -> bool:
        """Check if data matches all or any conditions based on logic."""
        if not conditions:
            return True

        results = [self._evaluate_condition(data, c) for c in conditions]

        if logic == "and":
            return all(results)
        else:  # or
            return any(results)

    async def execute(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        """Execute the if-then conditional."""
        start_time = time.perf_counter()

        input_data = inputs.get("in")
        conditions = self.get_config_value("conditions", [])
        logic = self.get_config_value("logic", "and")

        # Evaluate conditions
        result = self._matches(input_data, conditions, logic)

        # Route to appropriate output
        if result:
            outputs = {
                "true": {
                    "data": input_data,
                    "metadata": {},
                    "source_node_id": self.context.get("node_id", ""),
                },
                "false": {
                    "data": None,
                    "metadata": {},
                    "source_node_id": self.context.get("node_id", ""),
                },
            }
            self.log_info("Condition evaluated to true")
        else:
            outputs = {
                "true": {
                    "data": None,
                    "metadata": {},
                    "source_node_id": self.context.get("node_id", ""),
                },
                "false": {
                    "data": input_data,
                    "metadata": {},
                    "source_node_id": self.context.get("node_id", ""),
                },
            }
            self.log_info("Condition evaluated to false")

        return outputs
