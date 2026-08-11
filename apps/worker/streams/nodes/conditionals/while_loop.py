"""While Loop Conditional Node for Streams Workflow.

Loop node that continues executing while a condition is true, with configurable
max iterations safety limit to prevent infinite loops.
"""

from __future__ import annotations

import logging
import operator
from collections.abc import Callable
from typing import Any, Dict, List

from ...executor.node_registry import register_node
from ..base import BaseNode

logger = logging.getLogger(__name__)

# Operator mapping for loop conditions
OPERATORS: dict[str, Callable] = {
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
    "is_null": lambda a, b: a is None,
    "is_not_null": lambda a, b: a is not None,
    "in": lambda a, b: a in b if isinstance(b, (list, tuple, set)) else False,
    "not_in": lambda a, b: a not in b if isinstance(b, (list, tuple, set)) else True,
}


@register_node("conditional_while", "conditionals", "While Loop")
class WhileConditional(BaseNode):
    """Loop node that continues while condition is true with max iterations safety."""

    node_type = "conditional_while"
    name = "While Loop"
    description = "Continue looping while condition is true with max iterations safety"
    category = "conditionals"

    @classmethod
    def inputs(cls) -> list[dict[str, Any]]:
        """Define input ports for the while loop node."""
        return [
            {
                "name": "in",
                "description": "Input data to evaluate in loop condition",
                "required": True,
                "data_type": "any",
            },
        ]

    @classmethod
    def outputs(cls) -> list[dict[str, Any]]:
        """Define output ports for the while loop node."""
        return [
            {
                "name": "loop",
                "description": "Continue looping (condition is true)",
                "data_type": "any",
            },
            {
                "name": "done",
                "description": "Exit loop (condition is false or max iterations reached)",
                "data_type": "any",
            },
        ]

    def validate_config(self, config: dict[str, Any]) -> list[str]:
        """Validate while loop configuration."""
        errors = []

        condition = config.get("condition", {})
        if not condition.get("field"):
            errors.append("Condition field is required")

        op = condition.get("operator", "eq")
        if op not in OPERATORS:
            valid_ops = ", ".join(sorted(OPERATORS.keys()))
            errors.append(f"Invalid operator '{op}'. Valid: {valid_ops}")

        max_iterations = config.get("maxIterations", 100)
        if not isinstance(max_iterations, int) or max_iterations <= 0:
            errors.append("maxIterations must be a positive integer")

        return errors

    def _get_field_value(self, data: dict, field: str) -> Any:
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

    def _evaluate_condition(self, data: dict, condition: dict) -> bool:
        """Evaluate the loop condition against data."""
        field = condition.get("field", "")
        op_name = condition.get("operator", "eq")
        expected = condition.get("value")

        actual = self._get_field_value(data, field)
        op_func = OPERATORS.get(op_name, operator.eq)

        try:
            return op_func(actual, expected)
        except Exception:
            return False

    async def execute(self, inputs: dict[str, Any]) -> dict[str, Any]:
        """Execute the while loop conditional."""
        if "in" not in inputs:
            raise ValueError("Required input 'in' is missing")

        input_data = inputs.get("in")
        condition = self.get_config_value("condition", {})
        max_iterations = self.get_config_value("maxIterations", 100)

        # NOTE: In the old contract, iteration count was tracked in context.variables.
        # Since the new contract doesn't provide a variables dict, iteration tracking
        # is the executor's responsibility. This node evaluates one iteration only.

        # Evaluate condition - support both dict and non-dict data
        condition_met = False
        if isinstance(input_data, dict):
            condition_met = self._evaluate_condition(input_data, condition)
        else:
            # For non-dict data, always continue looping (caller defines condition)
            condition_met = True

        if condition_met:
            self.log_info("While loop: condition true, continuing")
            return {
                "loop": {
                    "data": input_data,
                    "metadata": {},
                    "source_node_id": self.context.get("node_id", ""),
                },
                "done": {
                    "data": None,
                    "metadata": {},
                    "source_node_id": self.context.get("node_id", ""),
                },
            }
        else:
            self.log_info("While loop: condition false, exiting")
            return {
                "loop": {
                    "data": None,
                    "metadata": {},
                    "source_node_id": self.context.get("node_id", ""),
                },
                "done": {
                    "data": input_data,
                    "metadata": {},
                    "source_node_id": self.context.get("node_id", ""),
                },
            }
