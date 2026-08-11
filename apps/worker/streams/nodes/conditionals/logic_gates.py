"""Logic Gate Nodes for Streams Workflow.

Boolean logic gate nodes (AND, OR, NOT) that enable conditional branching
and logical operations in workflow execution.
"""

from __future__ import annotations

from typing import Any, Dict, List

from ...executor.node_registry import register_node
from ..base import BaseNode


@register_node("conditional_and", "conditionals", "AND Gate")
class AndConditional(BaseNode):
    """AND logic gate: outputs true only if ALL inputs are truthy."""

    node_type = "conditional_and"
    name = "AND Gate"
    description = "Outputs true only if ALL inputs are truthy"
    category = "conditionals"

    @classmethod
    def inputs(cls) -> list[dict[str, Any]]:
        """Define input ports for the AND gate."""
        return [
            {
                "name": "in1",
                "description": "First input condition",
                "required": True,
                "data_type": "any",
            },
            {
                "name": "in2",
                "description": "Second input condition",
                "required": True,
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
                "description": "Output when all inputs are truthy",
                "data_type": "bool",
            },
            {
                "name": "false",
                "description": "Output when any input is falsy",
                "data_type": "bool",
            },
        ]

    async def execute(self, inputs: dict[str, Any]) -> dict[str, Any]:
        """Execute the AND gate logic."""
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
                "source_node_id": self.context.get("node_id", ""),
            },
            "false": {
                "data": not result,
                "metadata": {},
                "source_node_id": self.context.get("node_id", ""),
            },
        }


@register_node("conditional_or", "conditionals", "OR Gate")
class OrConditional(BaseNode):
    """OR logic gate: outputs true if ANY input is truthy."""

    node_type = "conditional_or"
    name = "OR Gate"
    description = "Outputs true if ANY input is truthy"
    category = "conditionals"

    @classmethod
    def inputs(cls) -> list[dict[str, Any]]:
        """Define input ports for the OR gate."""
        return [
            {
                "name": "in1",
                "description": "First input condition",
                "required": True,
                "data_type": "any",
            },
            {
                "name": "in2",
                "description": "Second input condition",
                "required": True,
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
                "description": "Output when any input is truthy",
                "data_type": "bool",
            },
            {
                "name": "false",
                "description": "Output when all inputs are falsy",
                "data_type": "bool",
            },
        ]

    async def execute(self, inputs: dict[str, Any]) -> dict[str, Any]:
        """Execute the OR gate logic."""
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
                "source_node_id": self.context.get("node_id", ""),
            },
            "false": {
                "data": not result,
                "metadata": {},
                "source_node_id": self.context.get("node_id", ""),
            },
        }


@register_node("conditional_not", "conditionals", "NOT Gate")
class NotConditional(BaseNode):
    """NOT logic gate: inverts the boolean value of input."""

    node_type = "conditional_not"
    name = "NOT Gate"
    description = "Inverts the boolean value of input"
    category = "conditionals"

    @classmethod
    def inputs(cls) -> list[dict[str, Any]]:
        """Define input ports for the NOT gate."""
        return [
            {
                "name": "in",
                "description": "Input condition to invert",
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
                "description": "Output when input is falsy",
                "data_type": "bool",
            },
            {
                "name": "false",
                "description": "Output when input is truthy",
                "data_type": "bool",
            },
        ]

    async def execute(self, inputs: dict[str, Any]) -> dict[str, Any]:
        """Execute the NOT gate logic."""
        if "in" not in inputs:
            raise ValueError("Required input 'in' is missing")

        input_value = inputs.get("in")
        result = not bool(input_value)

        self.log_info(f"NOT Gate: {bool(input_value)} -> {result}")

        return {
            "true": {
                "data": result,
                "metadata": {},
                "source_node_id": self.context.get("node_id", ""),
            },
            "false": {
                "data": not result,
                "metadata": {},
                "source_node_id": self.context.get("node_id", ""),
            },
        }
