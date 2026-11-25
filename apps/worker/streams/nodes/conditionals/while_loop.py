"""While Loop Conditional Node for Streams Workflow.

Loop node that continues executing while 1 or more conditions match (AND/OR),
supporting upstream fields, prior node fields, bare-string any-field search,
and associated resource states/relationships, with a configurable max
iterations safety limit. Backwards-compatible with legacy single ``condition``
dict configuration.
"""

from __future__ import annotations

import logging
from typing import Any

from ...executor.node_registry import register_node
from ..base import BaseNode
from .if_then import OPERATORS, evaluate_condition, get_field_value

logger = logging.getLogger(__name__)


@register_node("conditional_while", "conditionals", "While Loop")
class WhileConditional(BaseNode):
    """Loop node that continues while condition(s) are true with max iterations safety."""

    node_type = "conditional_while"
    name = "While Loop"
    description = (
        "Continue looping while 1 or more conditions match (supports upstream fields, "
        "bare string search, and associated resource states/relationships)"
    )
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

        conditions = config.get("conditions")
        legacy_condition = config.get("condition")

        if isinstance(conditions, list) and len(conditions) > 0:
            for idx, cond in enumerate(conditions):
                if not isinstance(cond, dict):
                    errors.append(f"Condition {idx + 1} must be an object")
                    continue
                op = cond.get("operator", "eq")
                if op not in OPERATORS:
                    valid_ops = ", ".join(sorted(OPERATORS.keys()))
                    errors.append(
                        f"Condition {idx + 1}: invalid operator '{op}'. Valid: {valid_ops}"
                    )
        elif isinstance(legacy_condition, dict) and legacy_condition:
            if not legacy_condition.get("field"):
                errors.append("Condition field is required")
            op = legacy_condition.get("operator", "eq")
            if op not in OPERATORS:
                valid_ops = ", ".join(sorted(OPERATORS.keys()))
                errors.append(f"Invalid operator '{op}'. Valid: {valid_ops}")
        else:
            errors.append("At least one loop condition is required")

        max_iterations = config.get("maxIterations", 100)
        if not isinstance(max_iterations, int) or max_iterations <= 0:
            errors.append("maxIterations must be a positive integer")

        return errors

    def _get_field_value(self, data: dict, field: str) -> Any:
        """Get nested field value using dot notation."""
        return get_field_value(data, field)

    async def execute(self, inputs: dict[str, Any]) -> dict[str, Any]:
        """Execute the while loop conditional."""
        if "in" not in inputs:
            raise ValueError("Required input 'in' is missing")

        input_data = inputs.get("in")
        conditions = self.get_config_value("conditions", [])
        legacy_condition = self.get_config_value("condition", {})
        logic = str(self.get_config_value("logic", "and") or "and").lower()

        active_conditions: list[dict[str, Any]] = []
        if isinstance(conditions, list) and len(conditions) > 0:
            active_conditions = [c for c in conditions if isinstance(c, dict)]
        elif isinstance(legacy_condition, dict) and legacy_condition:
            active_conditions = [legacy_condition]

        if active_conditions:
            results = [
                await evaluate_condition(
                    input_data,
                    cond,
                    self.context,
                    {"in": input_data},
                    log_warning=self.log_warning,
                )
                for cond in active_conditions
            ]
            condition_met = any(results) if logic == "or" else all(results)
        else:
            condition_met = False

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
