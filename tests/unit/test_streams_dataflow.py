"""Executor data-flow contract test: raw-in / wrapped-out across a node chain.

Nodes EMIT dict-wrapped output ports ({"data", "metadata", "source_node_id"})
but CONSUME raw input values (inputs.get("field") -> the value itself). The
executor must unwrap an upstream port's "data" before handing it downstream.
This test wires producer -> consumer and asserts the consumer received the raw
payload, not the envelope.

regression: streams-executor-unwrap-phase4b
"""

from __future__ import annotations

from typing import Any, Dict

import pytest

from apps.worker.streams.executor.node_registry import NodeRegistry, register_node
from apps.worker.streams.nodes.base import BaseNode


@register_node("_test_producer", "test", "Test Producer")
class _Producer(BaseNode):
    node_type = "_test_producer"
    name = "Test Producer"
    description = "Emits a fixed wrapped payload"
    category = "test"

    async def execute(self, inputs: dict[str, Any]) -> dict[str, Any]:
        return {
            "out": {
                "data": {"hello": "world"},
                "metadata": {},
                "source_node_id": self.context.get("node_id", ""),
            }
        }


# Module-level sink so the test can read what the consumer actually received.
_RECEIVED: dict[str, Any] = {}


@register_node("_test_consumer", "test", "Test Consumer")
class _Consumer(BaseNode):
    node_type = "_test_consumer"
    name = "Test Consumer"
    description = "Records the raw value it received"
    category = "test"

    async def execute(self, inputs: dict[str, Any]) -> dict[str, Any]:
        _RECEIVED["in"] = inputs.get("in")
        node_id = self.context.get("node_id", "")
        return {
            "out": {"data": inputs.get("in"), "metadata": {}, "source_node_id": node_id}
        }


@pytest.mark.asyncio
async def test_downstream_node_receives_raw_unwrapped_value():
    """The consumer must see the raw dict payload, not the {"data": ...} envelope."""
    # Import lazily so the @register_node decorators above have run.
    from apps.worker.streams.executor.playbook_executor import PlaybookExecutor

    assert NodeRegistry.get("_test_producer", raise_on_missing=False) is not None
    assert NodeRegistry.get("_test_consumer", raise_on_missing=False) is not None

    _RECEIVED.clear()
    executor = PlaybookExecutor(execution_id="e1", playbook_id="p1")
    playbook = {
        "nodes": [
            {"id": "a", "type": "_test_producer", "config": {}},
            {"id": "b", "type": "_test_consumer", "config": {}},
        ],
        "edges": [
            {
                "source": "a",
                "target": "b",
                "sourceHandle": "out",
                "targetHandle": "in",
            }
        ],
    }

    result = await executor.execute(playbook)

    assert result.success is True
    # Consumer received the RAW payload, not the wrapper.
    assert _RECEIVED["in"] == {"hello": "world"}
    # And the consumer's own output re-wraps that raw value.
    consumer_result = result.node_results["b"]
    assert consumer_result.outputs["out"]["data"] == {"hello": "world"}
