"""Unit tests for Streams transform nodes."""

from __future__ import annotations

import pytest

from apps.worker.streams.nodes.transforms.expression import ExpressionTransform
from apps.worker.streams.nodes.transforms.filter import FilterTransform
from apps.worker.streams.nodes.transforms.json_transform import JsonTransform
from apps.worker.streams.nodes.transforms.merge import MergeTransform
from apps.worker.streams.nodes.transforms.split import SplitTransform


@pytest.fixture
def base_context():
    """Provide basic execution context."""
    return {
        "execution_id": "exec-123",
        "playbook_id": "pb-456",
        "node_id": "node-789",
        "config": {},
    }


class TestExpressionTransform:
    @pytest.mark.asyncio
    async def test_expression_arithmetic(self, base_context):
        ctx = {**base_context, "config": {"expression": "data * 2 + 5"}}
        node = ExpressionTransform(ctx)
        result = await node.execute({"in": 10})
        assert result["out"]["data"] == 25

    @pytest.mark.asyncio
    async def test_expression_with_dict_data(self, base_context):
        ctx = {**base_context, "config": {"expression": "value + 10"}}
        node = ExpressionTransform(ctx)
        result = await node.execute({"in": {"value": 5}})
        assert result["out"]["data"] == 15

    @pytest.mark.asyncio
    async def test_expression_string_function(self, base_context):
        ctx = {**base_context, "config": {"expression": "upper(data)"}}
        node = ExpressionTransform(ctx)
        result = await node.execute({"in": "hello"})
        assert result["out"]["data"] == "HELLO"

    @pytest.mark.asyncio
    async def test_expression_invalid_syntax(self, base_context):
        ctx = {**base_context, "config": {"expression": "data +"}}
        node = ExpressionTransform(ctx)
        errors = node.validate_config(ctx["config"])
        assert len(errors) > 0

    @pytest.mark.asyncio
    async def test_expression_missing_input(self, base_context):
        ctx = {**base_context, "config": {"expression": "data * 2"}}
        node = ExpressionTransform(ctx)
        with pytest.raises(ValueError, match="Required input"):
            await node.execute({})


class TestFilterTransform:
    @pytest.mark.asyncio
    async def test_filter_single_object_match(self, base_context):
        ctx = {
            **base_context,
            "config": {
                "conditions": [
                    {"field": "status", "operator": "eq", "value": "active"}
                ],
                "logic": "and",
            },
        }
        node = FilterTransform(ctx)
        result = await node.execute({"in": {"status": "active", "name": "test"}})
        assert result["out"]["data"] == {"status": "active", "name": "test"}
        assert result["rejected"]["data"] is None

    @pytest.mark.asyncio
    async def test_filter_array_match(self, base_context):
        ctx = {
            **base_context,
            "config": {
                "conditions": [
                    {"field": "status", "operator": "eq", "value": "active"}
                ],
                "logic": "and",
            },
        }
        node = FilterTransform(ctx)
        data = [{"status": "active"}, {"status": "inactive"}]
        result = await node.execute({"in": data})
        assert result["out"]["data"] == [{"status": "active"}]
        assert result["rejected"]["data"] == [{"status": "inactive"}]

    @pytest.mark.asyncio
    async def test_filter_no_conditions(self, base_context):
        ctx = {**base_context, "config": {"conditions": [], "logic": "and"}}
        node = FilterTransform(ctx)
        errors = node.validate_config(ctx["config"])
        assert len(errors) > 0


class TestJsonTransform:
    @pytest.mark.asyncio
    async def test_json_extract_path(self, base_context):
        ctx = {
            **base_context,
            "config": {"operation": "extract", "jsonPath": "user.name"},
        }
        node = JsonTransform(ctx)
        result = await node.execute({"in": {"user": {"name": "Alice"}}})
        assert result["out"]["data"] == "Alice"

    @pytest.mark.asyncio
    async def test_json_set_path(self, base_context):
        ctx = {
            **base_context,
            "config": {"operation": "set", "jsonPath": "user.age", "value": 30},
        }
        node = JsonTransform(ctx)
        result = await node.execute({"in": {}})
        assert result["out"]["data"] == {"user": {"age": 30}}

    @pytest.mark.asyncio
    async def test_json_delete_path(self, base_context):
        ctx = {
            **base_context,
            "config": {"operation": "delete", "jsonPath": "user.secret"},
        }
        node = JsonTransform(ctx)
        result = await node.execute({"in": {"user": {"secret": "xxx", "name": "Bob"}}})
        assert result["out"]["data"] == {"user": {"name": "Bob"}}

    @pytest.mark.asyncio
    async def test_json_flatten(self, base_context):
        ctx = {**base_context, "config": {"operation": "flatten"}}
        node = JsonTransform(ctx)
        result = await node.execute({"in": {"a": {"b": {"c": 1}}}})
        assert result["out"]["data"] == {"a.b.c": 1}

    @pytest.mark.asyncio
    async def test_json_unflatten(self, base_context):
        ctx = {**base_context, "config": {"operation": "unflatten"}}
        node = JsonTransform(ctx)
        result = await node.execute({"in": {"a.b.c": 1}})
        assert result["out"]["data"] == {"a": {"b": {"c": 1}}}


class TestMergeTransform:
    @pytest.mark.asyncio
    async def test_merge_object_mode(self, base_context):
        ctx = {**base_context, "config": {"mode": "object"}}
        node = MergeTransform(ctx)
        result = await node.execute({"in1": {"a": 1}, "in2": {"b": 2}})
        assert result["out"]["data"] == {"a": 1, "b": 2}

    @pytest.mark.asyncio
    async def test_merge_array_mode(self, base_context):
        ctx = {**base_context, "config": {"mode": "array"}}
        node = MergeTransform(ctx)
        result = await node.execute({"in1": [1, 2], "in2": [3, 4]})
        assert result["out"]["data"] == [1, 2, 3, 4]

    @pytest.mark.asyncio
    async def test_merge_concat_mode_strings(self, base_context):
        ctx = {**base_context, "config": {"mode": "concat", "separator": "-"}}
        node = MergeTransform(ctx)
        result = await node.execute({"in1": "hello", "in2": "world"})
        assert result["out"]["data"] == "hello-world"

    @pytest.mark.asyncio
    async def test_merge_deep_mode(self, base_context):
        ctx = {**base_context, "config": {"mode": "deep"}}
        node = MergeTransform(ctx)
        result = await node.execute({"in1": {"a": {"b": 1}}, "in2": {"a": {"c": 2}}})
        assert result["out"]["data"] == {"a": {"b": 1, "c": 2}}

    @pytest.mark.asyncio
    async def test_merge_no_inputs(self, base_context):
        ctx = {**base_context, "config": {"mode": "object"}}
        node = MergeTransform(ctx)
        with pytest.raises(ValueError, match="Required input"):
            await node.execute({})


class TestSplitTransform:
    @pytest.mark.asyncio
    async def test_split_array_mode(self, base_context):
        ctx = {**base_context, "config": {"mode": "array"}}
        node = SplitTransform(ctx)
        result = await node.execute({"in": [1, 2, 3]})
        assert result["out"]["data"] == [1, 2, 3]
        assert result["count"]["data"] == 3
        assert result["first"]["data"] == 1
        assert result["last"]["data"] == 3

    @pytest.mark.asyncio
    async def test_split_string_mode(self, base_context):
        ctx = {**base_context, "config": {"mode": "string", "delimiter": ","}}
        node = SplitTransform(ctx)
        result = await node.execute({"in": "a,b,c"})
        assert result["out"]["data"] == ["a", "b", "c"]
        assert result["count"]["data"] == 3

    @pytest.mark.asyncio
    async def test_split_chunks_mode(self, base_context):
        ctx = {**base_context, "config": {"mode": "chunks", "chunkSize": 2}}
        node = SplitTransform(ctx)
        result = await node.execute({"in": [1, 2, 3, 4, 5]})
        assert result["out"]["data"] == [[1, 2], [3, 4], [5]]
        assert result["count"]["data"] == 3

    @pytest.mark.asyncio
    async def test_split_field_mode(self, base_context):
        ctx = {**base_context, "config": {"mode": "field", "field": "value"}}
        node = SplitTransform(ctx)
        result = await node.execute({"in": [{"value": 1}, {"value": 2}]})
        assert result["out"]["data"] == [1, 2]
        assert result["count"]["data"] == 2

    @pytest.mark.asyncio
    async def test_split_string_trim(self, base_context):
        ctx = {
            **base_context,
            "config": {"mode": "string", "delimiter": ",", "trim": True},
        }
        node = SplitTransform(ctx)
        result = await node.execute({"in": "a , b , c"})
        assert result["out"]["data"] == ["a", "b", "c"]

    @pytest.mark.asyncio
    async def test_split_missing_input(self, base_context):
        ctx = {**base_context, "config": {"mode": "array"}}
        node = SplitTransform(ctx)
        with pytest.raises(ValueError, match="Required input"):
            await node.execute({})
