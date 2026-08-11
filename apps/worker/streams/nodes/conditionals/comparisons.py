"""Comparison Conditional Nodes for Streams Workflow.

Provides conditional nodes for comparing values and making workflow decisions
based on comparison results. Each node provides "true" and "false" output ports
for branching logic.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List

from ...executor.node_registry import register_node
from ..base import BaseNode

logger = logging.getLogger(__name__)


@register_node("conditional_equals", "conditionals", "Equals")
class EqualsConditional(BaseNode):
    """Conditional node that checks if two values are equal."""

    node_type = "conditional_equals"
    name = "Equals"
    description = "Check if two values are equal"
    category = "conditionals"

    @classmethod
    def inputs(cls) -> list[dict[str, Any]]:
        """Define input ports for the equals node."""
        return [
            {
                "name": "value",
                "description": "The value to compare",
                "required": True,
                "data_type": "any",
            },
            {
                "name": "expected",
                "description": "The expected value to match",
                "required": True,
                "data_type": "any",
            },
        ]

    @classmethod
    def outputs(cls) -> list[dict[str, Any]]:
        """Define output ports for the equals node."""
        return [
            {
                "name": "true",
                "description": "Output when values are equal",
                "data_type": "any",
            },
            {
                "name": "false",
                "description": "Output when values are not equal",
                "data_type": "any",
            },
        ]

    async def execute(self, inputs: dict[str, Any]) -> dict[str, Any]:
        """Execute the equals comparison."""
        if "value" not in inputs:
            raise ValueError("Required input 'value' is missing")
        if "expected" not in inputs:
            raise ValueError("Required input 'expected' is missing")

        value = inputs.get("value")
        expected = inputs.get("expected")

        is_equal = value == expected

        self.log_info(f"Equals: {value!r} == {expected!r} -> {is_equal}")

        if is_equal:
            return {
                "true": {
                    "data": value,
                    "metadata": {},
                    "source_node_id": self.context.get("node_id", ""),
                },
                "false": {
                    "data": None,
                    "metadata": {},
                    "source_node_id": self.context.get("node_id", ""),
                },
            }
        else:
            return {
                "true": {
                    "data": None,
                    "metadata": {},
                    "source_node_id": self.context.get("node_id", ""),
                },
                "false": {
                    "data": value,
                    "metadata": {},
                    "source_node_id": self.context.get("node_id", ""),
                },
            }


@register_node("conditional_greater_than", "conditionals", "Greater Than")
class GreaterThanConditional(BaseNode):
    """Conditional node that checks if value is greater than threshold."""

    node_type = "conditional_greater_than"
    name = "Greater Than"
    description = "Check if value is greater than threshold"
    category = "conditionals"

    @classmethod
    def inputs(cls) -> list[dict[str, Any]]:
        """Define input ports for the greater than node."""
        return [
            {
                "name": "value",
                "description": "The value to compare",
                "required": True,
                "data_type": "number",
            },
            {
                "name": "threshold",
                "description": "The threshold to compare against",
                "required": True,
                "data_type": "number",
            },
        ]

    @classmethod
    def outputs(cls) -> list[dict[str, Any]]:
        """Define output ports for the greater than node."""
        return [
            {
                "name": "true",
                "description": "Output when value > threshold",
                "data_type": "any",
            },
            {
                "name": "false",
                "description": "Output when value <= threshold",
                "data_type": "any",
            },
        ]

    async def execute(self, inputs: dict[str, Any]) -> dict[str, Any]:
        """Execute the greater than comparison."""
        if "value" not in inputs:
            raise ValueError("Required input 'value' is missing")
        if "threshold" not in inputs:
            raise ValueError("Required input 'threshold' is missing")

        try:
            value = float(inputs.get("value"))
            threshold = float(inputs.get("threshold"))
        except (TypeError, ValueError) as e:
            raise ValueError(f"Invalid numeric values: {e}")

        is_greater = value > threshold

        self.log_info(f"Greater Than: {value} > {threshold} -> {is_greater}")

        if is_greater:
            return {
                "true": {
                    "data": value,
                    "metadata": {},
                    "source_node_id": self.context.get("node_id", ""),
                },
                "false": {
                    "data": None,
                    "metadata": {},
                    "source_node_id": self.context.get("node_id", ""),
                },
            }
        else:
            return {
                "true": {
                    "data": None,
                    "metadata": {},
                    "source_node_id": self.context.get("node_id", ""),
                },
                "false": {
                    "data": value,
                    "metadata": {},
                    "source_node_id": self.context.get("node_id", ""),
                },
            }


@register_node("conditional_less_than", "conditionals", "Less Than")
class LessThanConditional(BaseNode):
    """Conditional node that checks if value is less than threshold."""

    node_type = "conditional_less_than"
    name = "Less Than"
    description = "Check if value is less than threshold"
    category = "conditionals"

    @classmethod
    def inputs(cls) -> list[dict[str, Any]]:
        """Define input ports for the less than node."""
        return [
            {
                "name": "value",
                "description": "The value to compare",
                "required": True,
                "data_type": "number",
            },
            {
                "name": "threshold",
                "description": "The threshold to compare against",
                "required": True,
                "data_type": "number",
            },
        ]

    @classmethod
    def outputs(cls) -> list[dict[str, Any]]:
        """Define output ports for the less than node."""
        return [
            {
                "name": "true",
                "description": "Output when value < threshold",
                "data_type": "any",
            },
            {
                "name": "false",
                "description": "Output when value >= threshold",
                "data_type": "any",
            },
        ]

    async def execute(self, inputs: dict[str, Any]) -> dict[str, Any]:
        """Execute the less than comparison."""
        if "value" not in inputs:
            raise ValueError("Required input 'value' is missing")
        if "threshold" not in inputs:
            raise ValueError("Required input 'threshold' is missing")

        try:
            value = float(inputs.get("value"))
            threshold = float(inputs.get("threshold"))
        except (TypeError, ValueError) as e:
            raise ValueError(f"Invalid numeric values: {e}")

        is_less = value < threshold

        self.log_info(f"Less Than: {value} < {threshold} -> {is_less}")

        if is_less:
            return {
                "true": {
                    "data": value,
                    "metadata": {},
                    "source_node_id": self.context.get("node_id", ""),
                },
                "false": {
                    "data": None,
                    "metadata": {},
                    "source_node_id": self.context.get("node_id", ""),
                },
            }
        else:
            return {
                "true": {
                    "data": None,
                    "metadata": {},
                    "source_node_id": self.context.get("node_id", ""),
                },
                "false": {
                    "data": value,
                    "metadata": {},
                    "source_node_id": self.context.get("node_id", ""),
                },
            }


@register_node("conditional_contains", "conditionals", "Contains")
class ContainsConditional(BaseNode):
    """Conditional node that checks if string/array contains value."""

    node_type = "conditional_contains"
    name = "Contains"
    description = "Check if string or array contains a value"
    category = "conditionals"

    @classmethod
    def inputs(cls) -> list[dict[str, Any]]:
        """Define input ports for the contains node."""
        return [
            {
                "name": "haystack",
                "description": "The string or array to search in",
                "required": True,
                "data_type": "any",
            },
            {
                "name": "needle",
                "description": "The value to search for",
                "required": True,
                "data_type": "any",
            },
        ]

    @classmethod
    def outputs(cls) -> list[dict[str, Any]]:
        """Define output ports for the contains node."""
        return [
            {
                "name": "true",
                "description": "Output when haystack contains needle",
                "data_type": "any",
            },
            {
                "name": "false",
                "description": "Output when haystack does not contain needle",
                "data_type": "any",
            },
        ]

    async def execute(self, inputs: dict[str, Any]) -> dict[str, Any]:
        """Execute the contains comparison."""
        if "haystack" not in inputs:
            raise ValueError("Required input 'haystack' is missing")
        if "needle" not in inputs:
            raise ValueError("Required input 'needle' is missing")

        haystack = inputs.get("haystack")
        needle = inputs.get("needle")

        try:
            # Support both string and array/list contains checks
            if isinstance(haystack, str):
                contains = (
                    needle in haystack
                    if isinstance(needle, str)
                    else str(needle) in haystack
                )
            elif isinstance(haystack, (list, tuple, set)):
                contains = needle in haystack
            elif isinstance(haystack, dict):
                contains = needle in haystack.values()
            else:
                contains = False
        except Exception as e:
            raise ValueError(f"Contains check failed: {e}")

        self.log_info(
            f"Contains: {needle!r} in {type(haystack).__name__} -> {contains}"
        )

        if contains:
            return {
                "true": {
                    "data": haystack,
                    "metadata": {},
                    "source_node_id": self.context.get("node_id", ""),
                },
                "false": {
                    "data": None,
                    "metadata": {},
                    "source_node_id": self.context.get("node_id", ""),
                },
            }
        else:
            return {
                "true": {
                    "data": None,
                    "metadata": {},
                    "source_node_id": self.context.get("node_id", ""),
                },
                "false": {
                    "data": haystack,
                    "metadata": {},
                    "source_node_id": self.context.get("node_id", ""),
                },
            }


@register_node("conditional_regex", "conditionals", "Regex Match")
class RegexConditional(BaseNode):
    """Conditional node that checks if string matches regex pattern."""

    node_type = "conditional_regex"
    name = "Regex Match"
    description = "Check if string matches a regex pattern"
    category = "conditionals"

    @classmethod
    def inputs(cls) -> list[dict[str, Any]]:
        """Define input ports for the regex node."""
        return [
            {
                "name": "text",
                "description": "The text to match against",
                "required": True,
                "data_type": "string",
            },
            {
                "name": "pattern",
                "description": "The regex pattern to match",
                "required": True,
                "data_type": "string",
            },
        ]

    @classmethod
    def outputs(cls) -> list[dict[str, Any]]:
        """Define output ports for the regex node."""
        return [
            {
                "name": "true",
                "description": "Output when text matches pattern",
                "data_type": "any",
            },
            {
                "name": "false",
                "description": "Output when text does not match pattern",
                "data_type": "any",
            },
        ]

    async def execute(self, inputs: dict[str, Any]) -> dict[str, Any]:
        """Execute the regex match comparison."""
        if "text" not in inputs:
            raise ValueError("Required input 'text' is missing")
        if "pattern" not in inputs:
            raise ValueError("Required input 'pattern' is missing")

        text = str(inputs.get("text"))
        pattern = inputs.get("pattern")

        try:
            matches = bool(re.search(pattern, text))
        except re.error as e:
            raise ValueError(f"Invalid regex pattern: {e}")
        except Exception as e:
            raise ValueError(f"Regex match failed: {e}")

        self.log_info(f"Regex: '{pattern}' matches '{text}' -> {matches}")

        if matches:
            return {
                "true": {
                    "data": text,
                    "metadata": {},
                    "source_node_id": self.context.get("node_id", ""),
                },
                "false": {
                    "data": None,
                    "metadata": {},
                    "source_node_id": self.context.get("node_id", ""),
                },
            }
        else:
            return {
                "true": {
                    "data": None,
                    "metadata": {},
                    "source_node_id": self.context.get("node_id", ""),
                },
                "false": {
                    "data": text,
                    "metadata": {},
                    "source_node_id": self.context.get("node_id", ""),
                },
            }
