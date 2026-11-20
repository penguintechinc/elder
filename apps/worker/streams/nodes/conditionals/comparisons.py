"""Comparison Conditional Nodes for Streams Workflow.

Provides conditional nodes for comparing values and making workflow decisions
based on comparison results. Each node provides "true" and "false" output ports
for branching logic.

Supports both:
- Legacy 2-port wiring (``value``/``expected``, ``value``/``threshold``,
  ``haystack``/``needle``, ``text``/``pattern``)
- Single-wire ``"in"`` (or ``"default"``) + config (``field`` + templated
  ``expected``/``threshold``/``needle``/``pattern``), routing the full input
  payload down ``true`` or ``false``.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from ...executor.node_registry import register_node
from ..base import BaseNode
from ..regex_guard import UnsafeRegexError, safe_regex_search
from ..template import _MISSING, interpolate_value, resolve_path

logger = logging.getLogger(__name__)


def _extract_field(payload: Any, field: str) -> Any:
    """Extract nested field from payload, or return payload when field is blank."""
    if not field:
        return payload
    resolved = resolve_path(payload, field)
    return None if resolved is _MISSING else resolved


def _has_single_wire_input(inputs: dict[str, Any]) -> bool:
    """Return True if single-wire input ('in' or 'default') is present."""
    return "in" in inputs or "default" in inputs


def _get_single_wire_payload(inputs: dict[str, Any]) -> Any:
    """Retrieve single-wire input payload preferring 'in' over 'default'."""
    if "in" in inputs:
        return inputs.get("in")
    return inputs.get("default")


def _branch_outputs(node_id: str, passed: bool, payload: Any) -> dict[str, Any]:
    """Build standard true/false branching output dictionary."""
    if passed:
        return {
            "true": {"data": payload, "metadata": {}, "source_node_id": node_id},
            "false": {"data": None, "metadata": {}, "source_node_id": node_id},
        }
    return {
        "true": {"data": None, "metadata": {}, "source_node_id": node_id},
        "false": {"data": payload, "metadata": {}, "source_node_id": node_id},
    }


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
        cfg = self.context.get("config") or {}

        if "value" in inputs:
            if "expected" in inputs:
                expected = inputs.get("expected")
            elif "expected" in cfg:
                expected = interpolate_value(
                    cfg.get("expected"), self.context, inputs, preserve_type=True
                )
            else:
                raise ValueError("Required input 'expected' is missing")
            actual = inputs.get("value")
            routed_payload = actual
        elif _has_single_wire_input(inputs):
            payload = _get_single_wire_payload(inputs)
            if "expected" in inputs:
                expected = inputs.get("expected")
            elif "expected" in cfg:
                expected = interpolate_value(
                    cfg.get("expected"), self.context, inputs, preserve_type=True
                )
            else:
                raise ValueError("Required input 'expected' is missing")
            field = str(self.get_config_value("field", "") or "")
            actual = _extract_field(payload, field)
            routed_payload = payload
        else:
            raise ValueError("Required input 'value' is missing")

        is_equal = actual == expected
        self.log_info(f"Equals: {actual!r} == {expected!r} -> {is_equal}")
        return _branch_outputs(
            self.context.get("node_id", ""), is_equal, routed_payload
        )


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
        cfg = self.context.get("config") or {}

        if "value" in inputs:
            if "threshold" in inputs:
                raw_threshold = inputs.get("threshold")
            elif "threshold" in cfg:
                raw_threshold = interpolate_value(
                    cfg.get("threshold"), self.context, inputs, preserve_type=True
                )
            else:
                raise ValueError("Required input 'threshold' is missing")
            raw_value = inputs.get("value")
            single_wire = False
            payload = raw_value
        elif _has_single_wire_input(inputs):
            payload = _get_single_wire_payload(inputs)
            if "threshold" in inputs:
                raw_threshold = inputs.get("threshold")
            elif "threshold" in cfg:
                raw_threshold = interpolate_value(
                    cfg.get("threshold"), self.context, inputs, preserve_type=True
                )
            else:
                raise ValueError("Required input 'threshold' is missing")
            field = str(self.get_config_value("field", "") or "")
            raw_value = _extract_field(payload, field)
            single_wire = True
        else:
            raise ValueError("Required input 'value' is missing")

        try:
            if raw_value is None or raw_threshold is None:
                raise TypeError("Numeric input cannot be None")
            value = float(raw_value)
            threshold = float(raw_threshold)
        except (TypeError, ValueError) as e:
            raise ValueError(f"Invalid numeric values: {e}")

        is_greater = value > threshold
        self.log_info(f"Greater Than: {value} > {threshold} -> {is_greater}")
        routed_payload = payload if single_wire else value
        return _branch_outputs(
            self.context.get("node_id", ""), is_greater, routed_payload
        )


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
        cfg = self.context.get("config") or {}

        if "value" in inputs:
            if "threshold" in inputs:
                raw_threshold = inputs.get("threshold")
            elif "threshold" in cfg:
                raw_threshold = interpolate_value(
                    cfg.get("threshold"), self.context, inputs, preserve_type=True
                )
            else:
                raise ValueError("Required input 'threshold' is missing")
            raw_value = inputs.get("value")
            single_wire = False
            payload = raw_value
        elif _has_single_wire_input(inputs):
            payload = _get_single_wire_payload(inputs)
            if "threshold" in inputs:
                raw_threshold = inputs.get("threshold")
            elif "threshold" in cfg:
                raw_threshold = interpolate_value(
                    cfg.get("threshold"), self.context, inputs, preserve_type=True
                )
            else:
                raise ValueError("Required input 'threshold' is missing")
            field = str(self.get_config_value("field", "") or "")
            raw_value = _extract_field(payload, field)
            single_wire = True
        else:
            raise ValueError("Required input 'value' is missing")

        try:
            if raw_value is None or raw_threshold is None:
                raise TypeError("Numeric input cannot be None")
            value = float(raw_value)
            threshold = float(raw_threshold)
        except (TypeError, ValueError) as e:
            raise ValueError(f"Invalid numeric values: {e}")

        is_less = value < threshold
        self.log_info(f"Less Than: {value} < {threshold} -> {is_less}")
        routed_payload = payload if single_wire else value
        return _branch_outputs(self.context.get("node_id", ""), is_less, routed_payload)


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
        cfg = self.context.get("config") or {}

        if "haystack" in inputs:
            if "needle" in inputs:
                needle = inputs.get("needle")
            elif "needle" in cfg:
                needle = interpolate_value(
                    cfg.get("needle"), self.context, inputs, preserve_type=True
                )
            else:
                raise ValueError("Required input 'needle' is missing")
            haystack = inputs.get("haystack")
            routed_payload = haystack
        elif _has_single_wire_input(inputs):
            payload = _get_single_wire_payload(inputs)
            if "needle" in inputs:
                needle = inputs.get("needle")
            elif "needle" in cfg:
                needle = interpolate_value(
                    cfg.get("needle"), self.context, inputs, preserve_type=True
                )
            else:
                raise ValueError("Required input 'needle' is missing")
            field = str(self.get_config_value("field", "") or "")
            haystack = _extract_field(payload, field)
            routed_payload = payload
        else:
            raise ValueError("Required input 'haystack' is missing")

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
        return _branch_outputs(
            self.context.get("node_id", ""), contains, routed_payload
        )


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
        cfg = self.context.get("config") or {}

        if "text" in inputs:
            if "pattern" in inputs:
                raw_pattern = inputs.get("pattern")
            elif "pattern" in cfg:
                raw_pattern = interpolate_value(
                    cfg.get("pattern"), self.context, inputs, preserve_type=False
                )
            else:
                raise ValueError("Required input 'pattern' is missing")
            text = str(inputs.get("text"))
            routed_payload: Any = text
        elif _has_single_wire_input(inputs):
            payload = _get_single_wire_payload(inputs)
            if "pattern" in inputs:
                raw_pattern = inputs.get("pattern")
            elif "pattern" in cfg:
                raw_pattern = interpolate_value(
                    cfg.get("pattern"), self.context, inputs, preserve_type=False
                )
            else:
                raise ValueError("Required input 'pattern' is missing")
            field = str(self.get_config_value("field", "") or "")
            raw_text = _extract_field(payload, field)
            text = "" if raw_text is None else str(raw_text)
            routed_payload = payload
        else:
            raise ValueError("Required input 'text' is missing")

        pattern = "" if raw_pattern is None else str(raw_pattern)
        try:
            matches = await safe_regex_search(pattern, text)
        except UnsafeRegexError as e:
            raise ValueError(str(e))
        except TimeoutError:
            raise ValueError(
                f"Regex match timed out (possible catastrophic backtracking): {pattern!r}"
            )
        except re.error as e:
            raise ValueError(f"Invalid regex pattern: {e}")
        except Exception as e:
            raise ValueError(f"Regex match failed: {e}")

        self.log_info(f"Regex: '{pattern}' matches '{text}' -> {matches}")
        return _branch_outputs(self.context.get("node_id", ""), matches, routed_payload)
