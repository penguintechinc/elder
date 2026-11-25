"""Logic Gate Nodes for Streams Workflow.

Boolean logic gate nodes (AND, OR, NOT) that enable conditional branching
and logical operations in workflow execution. Each gate supports either:
- 1 or more configured ``conditions`` evaluated against the incoming payload
  (matching upstream fields or ``{{expression}}`` against freeform strings or
  upstream field values), routing the payload to ``true`` or ``false``; OR
- Direct boolean evaluation of wired input ports when no conditions are configured.
"""

from __future__ import annotations

from typing import Any

from ...executor.node_registry import register_node
from ..base import BaseNode
from .if_then import evaluate_condition


def _primary_payload(inputs: dict[str, Any]) -> Any:
    """Return the primary incoming payload from wired ports."""
    for key in ("in", "in1", "in2", "in3", "in4"):
        if key in inputs and inputs[key] is not None:
            return inputs[key]
    for val in inputs.values():
        if val is not None:
            return val
    return None


def _route_payload(node_id: str, payload: Any, passed: bool) -> dict[str, Any]:
    """Route payload to the 'true' or 'false' branch based on condition outcome."""
    return {
        "true": {
            "data": payload if passed else None,
            "metadata": {},
            "source_node_id": node_id,
        },
        "false": {
            "data": None if passed else payload,
            "metadata": {},
            "source_node_id": node_id,
        },
    }


@register_node("conditional_and", "conditionals", "AND Gate")
class AndConditional(BaseNode):
    """AND logic gate: outputs true only if ALL conditions or inputs match."""

    node_type = "conditional_and"
    name = "AND Gate"
    description = "Outputs true only if ALL configured conditions match (or all wired inputs are truthy)"
    category = "conditionals"

    @classmethod
    def inputs(cls) -> list[dict[str, Any]]:
        """Define input ports for the AND gate."""
        return [
            {
                "name": "in1",
                "description": "Primary payload or first input condition",
                "required": True,
                "data_type": "any",
            },
            {
                "name": "in2",
                "description": "Second input condition (optional when using configured conditions)",
                "required": False,
                "data_type": "any",
            },
            {
                "name": "in3",
                "description": "Third input condition",
                "required": False,
                "data_type": "any",
            },
            {
                "name": "in4",
                "description": "Fourth input condition",
                "required": False,
                "data_type": "any",
            },
        ]

    @classmethod
    def outputs(cls) -> list[dict[str, Any]]:
        """Define output ports for the AND gate."""
        return [
            {
                "name": "true",
                "description": "Output when all conditions/inputs are truthy",
                "data_type": "any",
            },
            {
                "name": "false",
                "description": "Output when any condition/input is falsy",
                "data_type": "any",
            },
        ]

    async def execute(self, inputs: dict[str, Any]) -> dict[str, Any]:
        """Execute the AND gate logic."""
        node_id = self.context.get("node_id", "")
        conditions = self.get_config_value("conditions", [])

        if isinstance(conditions, list) and len(conditions) > 0:
            payload = _primary_payload(inputs)
            eval_inputs = {"in": payload, **inputs}
            results = [
                await evaluate_condition(
                    payload,
                    cond,
                    self.context,
                    eval_inputs,
                    log_warning=self.log_warning,
                )
                for cond in conditions
                if isinstance(cond, dict)
            ]
            result = all(results) if results else True
            self.log_info(f"AND Gate (conditions): {results} -> {result}")
            return _route_payload(node_id, payload, result)

        if "in1" not in inputs:
            raise ValueError("Required input 'in1' is missing")
        if "in2" not in inputs:
            raise ValueError("Required input 'in2' is missing")

        # Gather provided inputs
        values = []
        for key in ["in1", "in2", "in3", "in4"]:
            if key in inputs:
                values.append(bool(inputs[key]))

        # AND logic: all must be True
        result = all(values) if values else True

        self.log_info(f"AND Gate: {values} -> {result}")

        return {
            "true": {
                "data": result,
                "metadata": {},
                "source_node_id": node_id,
            },
            "false": {
                "data": not result,
                "metadata": {},
                "source_node_id": node_id,
            },
        }


@register_node("conditional_or", "conditionals", "OR Gate")
class OrConditional(BaseNode):
    """OR logic gate: outputs true if ANY condition or input matches."""

    node_type = "conditional_or"
    name = "OR Gate"
    description = "Outputs true if ANY configured condition matches (or any wired input is truthy)"
    category = "conditionals"

    @classmethod
    def inputs(cls) -> list[dict[str, Any]]:
        """Define input ports for the OR gate."""
        return [
            {
                "name": "in1",
                "description": "Primary payload or first input condition",
                "required": True,
                "data_type": "any",
            },
            {
                "name": "in2",
                "description": "Second input condition (optional when using configured conditions)",
                "required": False,
                "data_type": "any",
            },
            {
                "name": "in3",
                "description": "Third input condition",
                "required": False,
                "data_type": "any",
            },
            {
                "name": "in4",
                "description": "Fourth input condition",
                "required": False,
                "data_type": "any",
            },
        ]

    @classmethod
    def outputs(cls) -> list[dict[str, Any]]:
        """Define output ports for the OR gate."""
        return [
            {
                "name": "true",
                "description": "Output when any condition/input is truthy",
                "data_type": "any",
            },
            {
                "name": "false",
                "description": "Output when all conditions/inputs are falsy",
                "data_type": "any",
            },
        ]

    async def execute(self, inputs: dict[str, Any]) -> dict[str, Any]:
        """Execute the OR gate logic."""
        node_id = self.context.get("node_id", "")
        conditions = self.get_config_value("conditions", [])

        if isinstance(conditions, list) and len(conditions) > 0:
            payload = _primary_payload(inputs)
            eval_inputs = {"in": payload, **inputs}
            results = [
                await evaluate_condition(
                    payload,
                    cond,
                    self.context,
                    eval_inputs,
                    log_warning=self.log_warning,
                )
                for cond in conditions
                if isinstance(cond, dict)
            ]
            result = any(results) if results else False
            self.log_info(f"OR Gate (conditions): {results} -> {result}")
            return _route_payload(node_id, payload, result)

        if "in1" not in inputs:
            raise ValueError("Required input 'in1' is missing")
        if "in2" not in inputs:
            raise ValueError("Required input 'in2' is missing")

        # Gather provided inputs
        values = []
        for key in ["in1", "in2", "in3", "in4"]:
            if key in inputs:
                values.append(bool(inputs[key]))

        # OR logic: any must be True
        result = any(values) if values else False

        self.log_info(f"OR Gate: {values} -> {result}")

        return {
            "true": {
                "data": result,
                "metadata": {},
                "source_node_id": node_id,
            },
            "false": {
                "data": not result,
                "metadata": {},
                "source_node_id": node_id,
            },
        }


@register_node("conditional_not", "conditionals", "NOT Gate")
class NotConditional(BaseNode):
    """NOT logic gate: inverts configured condition(s) or boolean input."""

    node_type = "conditional_not"
    name = "NOT Gate"
    description = (
        "Inverts the result of 1 or more configured conditions (or boolean input)"
    )
    category = "conditionals"

    @classmethod
    def inputs(cls) -> list[dict[str, Any]]:
        """Define input ports for the NOT gate."""
        return [
            {
                "name": "in",
                "description": "Input payload or condition to invert",
                "required": True,
                "data_type": "any",
            },
        ]

    @classmethod
    def outputs(cls) -> list[dict[str, Any]]:
        """Define output ports for the NOT gate."""
        return [
            {
                "name": "true",
                "description": "Output when condition(s)/input evaluate to false",
                "data_type": "any",
            },
            {
                "name": "false",
                "description": "Output when condition(s)/input evaluate to true",
                "data_type": "any",
            },
        ]

    async def execute(self, inputs: dict[str, Any]) -> dict[str, Any]:
        """Execute the NOT gate logic."""
        node_id = self.context.get("node_id", "")
        conditions = self.get_config_value("conditions", [])

        if isinstance(conditions, list) and len(conditions) > 0:
            payload = _primary_payload(inputs)
            eval_inputs = {"in": payload, **inputs}
            logic = str(self.get_config_value("logic", "and") or "and").lower()
            results = [
                await evaluate_condition(
                    payload,
                    cond,
                    self.context,
                    eval_inputs,
                    log_warning=self.log_warning,
                )
                for cond in conditions
                if isinstance(cond, dict)
            ]
            matched = any(results) if logic == "or" else all(results)
            result = not matched
            self.log_info(
                f"NOT Gate (conditions, logic={logic}): {results} -> {result}"
            )
            return _route_payload(node_id, payload, result)

        if "in" not in inputs:
            raise ValueError("Required input 'in' is missing")

        input_value = inputs.get("in")
        result = not bool(input_value)

        self.log_info(f"NOT Gate: {bool(input_value)} -> {result}")

        return {
            "true": {
                "data": result,
                "metadata": {},
                "source_node_id": node_id,
            },
            "false": {
                "data": not result,
                "metadata": {},
                "source_node_id": node_id,
            },
        }
