"""Unit tests for Streams conditional nodes."""

from __future__ import annotations

import pytest

from apps.worker.streams.nodes.conditionals.comparisons import (
    ContainsConditional,
    EqualsConditional,
    GreaterThanConditional,
    LessThanConditional,
    RegexConditional,
)
from apps.worker.streams.nodes.conditionals.for_each import ForEachConditional
from apps.worker.streams.nodes.conditionals.if_then import IfThenConditional
from apps.worker.streams.nodes.conditionals.logic_gates import (
    AndConditional,
    NotConditional,
    OrConditional,
)
from apps.worker.streams.nodes.conditionals.switch import SwitchConditional
from apps.worker.streams.nodes.conditionals.while_loop import WhileConditional


@pytest.fixture
def base_context():
    """Provide basic execution context."""
    return {
        "execution_id": "exec-123",
        "playbook_id": "pb-456",
        "node_id": "node-789",
        "config": {},
    }


class TestEqualsConditional:
    @pytest.mark.asyncio
    async def test_equals_true(self, base_context):
        node = EqualsConditional(base_context)
        result = await node.execute({"value": 42, "expected": 42})
        assert result["true"]["data"] == 42
        assert result["false"]["data"] is None

    @pytest.mark.asyncio
    async def test_equals_false(self, base_context):
        node = EqualsConditional(base_context)
        result = await node.execute({"value": 42, "expected": 43})
        assert result["true"]["data"] is None
        assert result["false"]["data"] == 42

    @pytest.mark.asyncio
    async def test_equals_missing_input(self, base_context):
        node = EqualsConditional(base_context)
        with pytest.raises(ValueError, match="Required input"):
            await node.execute({"value": 42})


class TestGreaterThanConditional:
    @pytest.mark.asyncio
    async def test_greater_than_true(self, base_context):
        node = GreaterThanConditional(base_context)
        result = await node.execute({"value": 50, "threshold": 40})
        assert result["true"]["data"] == 50
        assert result["false"]["data"] is None

    @pytest.mark.asyncio
    async def test_greater_than_false(self, base_context):
        node = GreaterThanConditional(base_context)
        result = await node.execute({"value": 30, "threshold": 40})
        assert result["true"]["data"] is None
        assert result["false"]["data"] == 30

    @pytest.mark.asyncio
    async def test_greater_than_invalid_numeric(self, base_context):
        node = GreaterThanConditional(base_context)
        with pytest.raises(ValueError, match="Invalid numeric"):
            await node.execute({"value": "not a number", "threshold": 40})


class TestLessThanConditional:
    @pytest.mark.asyncio
    async def test_less_than_true(self, base_context):
        node = LessThanConditional(base_context)
        result = await node.execute({"value": 30, "threshold": 40})
        assert result["true"]["data"] == 30
        assert result["false"]["data"] is None

    @pytest.mark.asyncio
    async def test_less_than_false(self, base_context):
        node = LessThanConditional(base_context)
        result = await node.execute({"value": 50, "threshold": 40})
        assert result["true"]["data"] is None
        assert result["false"]["data"] == 50


class TestContainsConditional:
    @pytest.mark.asyncio
    async def test_contains_string_true(self, base_context):
        node = ContainsConditional(base_context)
        result = await node.execute({"haystack": "hello world", "needle": "world"})
        assert result["true"]["data"] == "hello world"
        assert result["false"]["data"] is None

    @pytest.mark.asyncio
    async def test_contains_list_true(self, base_context):
        node = ContainsConditional(base_context)
        result = await node.execute({"haystack": [1, 2, 3], "needle": 2})
        assert result["true"]["data"] == [1, 2, 3]
        assert result["false"]["data"] is None

    @pytest.mark.asyncio
    async def test_contains_false(self, base_context):
        node = ContainsConditional(base_context)
        result = await node.execute({"haystack": "hello world", "needle": "xyz"})
        assert result["true"]["data"] is None
        assert result["false"]["data"] == "hello world"


class TestRegexConditional:
    @pytest.mark.asyncio
    async def test_regex_match_true(self, base_context):
        node = RegexConditional(base_context)
        result = await node.execute({"text": "test123", "pattern": r"\d+"})
        assert result["true"]["data"] == "test123"
        assert result["false"]["data"] is None

    @pytest.mark.asyncio
    async def test_regex_match_false(self, base_context):
        node = RegexConditional(base_context)
        result = await node.execute({"text": "nodigits", "pattern": r"\d+"})
        assert result["true"]["data"] is None
        assert result["false"]["data"] == "nodigits"

    @pytest.mark.asyncio
    async def test_regex_invalid_pattern(self, base_context):
        node = RegexConditional(base_context)
        with pytest.raises(ValueError, match="Invalid regex"):
            await node.execute({"text": "test", "pattern": "(?P<invalid"})

    @pytest.mark.asyncio
    async def test_regex_unsafe_pattern_rejected_not_hung(self, base_context):
        """A catastrophic-backtracking-shaped pattern is rejected up front
        (ValueError), never executed -- the node fails safely instead of
        blocking the worker's event loop.
        """
        node = RegexConditional(base_context)
        with pytest.raises(ValueError, match="nested quantifier"):
            await node.execute({"text": "aaaa", "pattern": r"(a+)+$"})


class TestIfThenConditional:
    """Coverage for the "regex" operator's ReDoS guard in if_then.py.

    "regex" is special-cased ahead of the OPERATORS dict so it can be
    awaited through safe_regex_search (thread offload + timeout) -- these
    tests prove that wiring both matches correctly and fails safely.
    """

    @pytest.mark.asyncio
    async def test_regex_condition_true_routes_to_true_output(self, base_context):
        ctx = {
            **base_context,
            "config": {
                "conditions": [
                    {"field": "code", "operator": "regex", "value": r"^\d{3}$"}
                ],
                "logic": "and",
            },
        }
        node = IfThenConditional(ctx)
        result = await node.execute({"in": {"code": "404"}})
        assert result["true"]["data"] == {"code": "404"}
        assert result["false"]["data"] is None

    @pytest.mark.asyncio
    async def test_regex_condition_false_routes_to_false_output(self, base_context):
        ctx = {
            **base_context,
            "config": {
                "conditions": [
                    {"field": "code", "operator": "regex", "value": r"^\d{3}$"}
                ],
                "logic": "and",
            },
        }
        node = IfThenConditional(ctx)
        result = await node.execute({"in": {"code": "not-a-code"}})
        assert result["true"]["data"] is None
        assert result["false"]["data"] == {"code": "not-a-code"}

    @pytest.mark.asyncio
    async def test_unsafe_regex_condition_fails_safely_to_false(self, base_context):
        """Nested-quantifier pattern -> condition evaluates False, execute()
        never raises and never hangs.
        """
        ctx = {
            **base_context,
            "config": {
                "conditions": [
                    {"field": "code", "operator": "regex", "value": r"(a+)+$"}
                ],
                "logic": "and",
            },
        }
        node = IfThenConditional(ctx)
        result = await node.execute({"in": {"code": "aaaa"}})
        assert result["true"]["data"] is None
        assert result["false"]["data"] == {"code": "aaaa"}


class TestAndConditional:
    @pytest.mark.asyncio
    async def test_and_all_true(self, base_context):
        node = AndConditional(base_context)
        result = await node.execute({"in1": True, "in2": True})
        assert result["true"]["data"] is True
        assert result["false"]["data"] is False

    @pytest.mark.asyncio
    async def test_and_one_false(self, base_context):
        node = AndConditional(base_context)
        result = await node.execute({"in1": True, "in2": False})
        assert result["true"]["data"] is False
        assert result["false"]["data"] is True

    @pytest.mark.asyncio
    async def test_and_all_false(self, base_context):
        node = AndConditional(base_context)
        result = await node.execute({"in1": False, "in2": False})
        assert result["true"]["data"] is False
        assert result["false"]["data"] is True

    @pytest.mark.asyncio
    async def test_and_with_optional_inputs(self, base_context):
        node = AndConditional(base_context)
        result = await node.execute({"in1": True, "in2": True, "in3": True})
        assert result["true"]["data"] is True
        assert result["false"]["data"] is False


class TestOrConditional:
    @pytest.mark.asyncio
    async def test_or_all_true(self, base_context):
        node = OrConditional(base_context)
        result = await node.execute({"in1": True, "in2": True})
        assert result["true"]["data"] is True
        assert result["false"]["data"] is False

    @pytest.mark.asyncio
    async def test_or_one_true(self, base_context):
        node = OrConditional(base_context)
        result = await node.execute({"in1": True, "in2": False})
        assert result["true"]["data"] is True
        assert result["false"]["data"] is False

    @pytest.mark.asyncio
    async def test_or_all_false(self, base_context):
        node = OrConditional(base_context)
        result = await node.execute({"in1": False, "in2": False})
        assert result["true"]["data"] is False
        assert result["false"]["data"] is True


class TestNotConditional:
    @pytest.mark.asyncio
    async def test_not_true(self, base_context):
        node = NotConditional(base_context)
        result = await node.execute({"in": True})
        assert result["true"]["data"] is False
        assert result["false"]["data"] is True

    @pytest.mark.asyncio
    async def test_not_false(self, base_context):
        node = NotConditional(base_context)
        result = await node.execute({"in": False})
        assert result["true"]["data"] is True
        assert result["false"]["data"] is False

    @pytest.mark.asyncio
    async def test_not_truthy_value(self, base_context):
        node = NotConditional(base_context)
        result = await node.execute({"in": "nonempty"})
        assert result["true"]["data"] is False
        assert result["false"]["data"] is True


class TestSwitchConditional:
    @pytest.mark.asyncio
    async def test_switch_case1_match(self, base_context):
        ctx = {
            **base_context,
            "config": {"field": "type", "cases": [{"value": "A", "output": "case1"}]},
        }
        node = SwitchConditional(ctx)
        result = await node.execute({"in": {"type": "A", "data": "value"}})
        assert result["case1"]["data"] == {"type": "A", "data": "value"}
        assert result["case2"]["data"] is None
        assert result["default"]["data"] is None

    @pytest.mark.asyncio
    async def test_switch_default_match(self, base_context):
        ctx = {
            **base_context,
            "config": {"field": "type", "cases": [{"value": "A", "output": "case1"}]},
        }
        node = SwitchConditional(ctx)
        result = await node.execute({"in": {"type": "Z", "data": "value"}})
        assert result["case1"]["data"] is None
        assert result["default"]["data"] == {"type": "Z", "data": "value"}


class TestForEachConditional:
    @pytest.mark.asyncio
    async def test_for_each_array(self, base_context):
        node = ForEachConditional(base_context)
        result = await node.execute({"array": [1, 2, 3]})
        assert result["item"]["data"] == 1
        assert result["index"]["data"] == 0
        assert result["done"]["data"] is False
        assert result["done"]["metadata"]["_iterations"] == [1, 2, 3]

    @pytest.mark.asyncio
    async def test_for_each_empty_array(self, base_context):
        node = ForEachConditional(base_context)
        result = await node.execute({"array": []})
        assert result["item"]["data"] is None
        assert result["index"]["data"] == 0
        assert result["done"]["data"] is True


class TestWhileConditional:
    @pytest.mark.asyncio
    async def test_while_condition_true(self, base_context):
        ctx = {
            **base_context,
            "config": {"condition": {"field": "count", "operator": "lt", "value": 10}},
        }
        node = WhileConditional(ctx)
        result = await node.execute({"in": {"count": 5}})
        assert result["loop"]["data"] == {"count": 5}
        assert result["done"]["data"] is None

    @pytest.mark.asyncio
    async def test_while_condition_false(self, base_context):
        ctx = {
            **base_context,
            "config": {"condition": {"field": "count", "operator": "lt", "value": 10}},
        }
        node = WhileConditional(ctx)
        result = await node.execute({"in": {"count": 15}})
        assert result["loop"]["data"] is None
        assert result["done"]["data"] == {"count": 15}
