"""Coverage tests for the streams executor package and trigger nodes.

Covers node_registry, graph_utils, playbook_executor and core_triggers using
real registry/graph/executor code with small in-test node classes.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest

import tests.unit.conftest_worker_stubs  # noqa: F401
from apps.worker.streams.executor import graph_utils as gu
from apps.worker.streams.executor import node_registry as nr
from apps.worker.streams.executor import playbook_executor as pe
from apps.worker.streams.nodes.triggers import core_triggers as ct


@pytest.fixture
def registry_guard():
    """Snapshot and restore the global node registry around a test."""
    snap = dict(nr.NodeRegistry._registry)
    init = nr.NodeRegistry._initialized
    yield nr.NodeRegistry
    nr.NodeRegistry._registry.clear()
    nr.NodeRegistry._registry.update(snap)
    nr.NodeRegistry._initialized = init


class TestNodeRegistry:
    """NodeRegistry registration, lookup, clearing and discovery."""

    def test_register_validation(self, registry_guard):
        """Invalid arguments raise ValueError."""
        R = registry_guard
        with pytest.raises(ValueError):
            R.register("", object, "c")
        with pytest.raises(ValueError):
            R.register("t", None, "c")
        with pytest.raises(ValueError):
            R.register("t", object, "")

    def test_register_get_lookup(self, registry_guard):
        """Registration metadata, duplicates, overrides, lookups, counts."""
        R = registry_guard

        class Doc:
            """First line summary.

            More text.
            """

        class NoDoc:
            pass

        R.register("cov_e_one", Doc, "actions")
        info = R.get_info("cov_e_one")
        assert info.display_name == "Cov E One"
        assert info.description == "First line summary."
        R.register("cov_e_two", NoDoc, "actions", "Two", "desc")
        assert R.get_info("cov_e_two").description == "desc"
        R.register("cov_e_three", NoDoc, "actions")
        assert R.get_info("cov_e_three").description == "cov_e_three node"
        with pytest.raises(nr.DuplicateNodeError):
            R.register("cov_e_one", Doc, "actions")
        R.register("cov_e_one", NoDoc, "actions", allow_override=True)
        assert R.get("cov_e_one") is NoDoc
        assert R.is_registered("cov_e_one") and not R.is_registered("zzz")
        assert R.get("zzz", raise_on_missing=False) is None
        assert R.get_info("zzz") is None
        with pytest.raises(nr.NodeNotFoundError, match="Available types"):
            R.get("zzz")
        assert "cov_e_one" in R.list_all()
        assert R.count() == len(R.list_all())

    def test_clear_and_initialized(self, registry_guard):
        """clear() empties; mark_initialized flips the flag."""
        R = registry_guard
        R._registry.clear()
        with pytest.raises(nr.NodeNotFoundError, match="none"):
            R.get("x")
        R.register("cov_e_a", object, "c")
        assert R.clear() == 1
        assert not R.is_initialized()
        R.mark_initialized()
        assert R.is_initialized()

    def test_decorator(self, registry_guard):
        """register_node decorates and returns the class."""

        @nr.register_node("cov_e_deco", "actions", "Deco")
        class Deco:
            """Decorated."""

        assert registry_guard.get("cov_e_deco") is Deco

    def test_discover(self, registry_guard, monkeypatch):
        """Discovery imports modules once; second call short-circuits."""
        R = registry_guard
        R._initialized = False
        n = nr.discover_nodes()
        assert n == R.count() and R.is_initialized()
        assert nr.discover_nodes() == R.count()

    def test_discover_import_errors(self, registry_guard, monkeypatch):
        """ImportError is swallowed and other errors are logged."""
        R = registry_guard
        R._initialized = False
        import builtins

        real = builtins.__import__
        seen = []

        def fake(name, *a, **k):
            """Fail two of the discovery imports."""
            if name == "apps.worker.streams.nodes.triggers":
                seen.append(name)
                raise ImportError(name)
            if name == "apps.worker.streams.nodes.actions":
                seen.append(name)
                raise RuntimeError("boom")
            return real(name, *a, **k)

        monkeypatch.setattr(builtins, "__import__", fake)
        nr.discover_nodes()
        assert len(seen) == 2 and R.is_initialized()


class TestGraphUtils:
    """TopologicalSorter and graph helper functions."""

    def test_error_str(self):
        """Error classes render their messages."""
        assert str(gu.GraphError("m")) == "m"
        assert str(gu.CycleDetectedError("c", ("a", "b"))) == "c: a -> b"

    def test_sort_and_cycle(self):
        """Valid sort order, cycle detection and unrelated-node cycles."""
        nodes = [{"id": "a"}, {"id": "b"}, {"id": "c"}]
        edges = [
            {"source": "a", "target": "b"},
            {"source": "b", "target": "c"},
            {"source": "a"},
        ]
        assert gu.TopologicalSorter(nodes, edges).sort() == ["a", "b", "c"]
        cyc = edges[:2] + [{"source": "c", "target": "b"}]
        with pytest.raises(gu.CycleDetectedError) as ei:
            gu.TopologicalSorter(nodes, cyc).sort()
        assert set(ei.value.cycle_nodes) == {"b", "c"}
        selfloop = [{"id": "s"}]
        with pytest.raises(gu.CycleDetectedError):
            gu.TopologicalSorter(selfloop, [{"source": "s", "target": "s"}]).sort()

    @pytest.mark.xfail(
        strict=True,
        reason="bug: graph_utils.py _calculate_in_degree counts edges from unknown "
        "sources, so a dangling edge makes sort() report a false cycle",
    )
    def test_dangling_source_edge_not_a_cycle(self):
        """An edge whose source is not a node should be ignored, not a cycle."""
        sorter = gu.TopologicalSorter([{"id": "a"}], [{"source": "x", "target": "a"}])
        assert sorter.sort() == ["a"]

    def test_find_cycle_fallback(self):
        """A DFS that finds no cycle returns the remaining nodes."""
        s = gu.TopologicalSorter([{"id": "a"}], [])
        assert s._find_cycle(["a"]) == ["a"]
        s2 = gu.TopologicalSorter(
            [{"id": "a"}, {"id": "b"}, {"id": "c"}],
            [{"source": "a", "target": "b"}, {"source": "a", "target": "c"}],
        )
        assert sorted(s2._find_cycle(["a", "b", "c"])) == ["a", "b", "c"]

    def test_helpers(self):
        """Upstream/downstream/input/output mapping and trigger discovery."""
        edges = [
            {"source": "a", "target": "b", "sourceHandle": "true", "targetHandle": "x"},
            {"source": "a", "target": "b"},
            {"source": "c", "target": "b", "targetHandle": "y"},
            {"target": "b"},
            {"source": "b", "target": "d"},
            {"source": "b"},
        ]
        assert gu.get_upstream_nodes("b", edges) == ["a", "c"]
        assert gu.get_downstream_nodes("a", edges) == ["b"]
        assert gu.get_downstream_nodes("b", edges) == ["d"]
        ins = gu.get_node_inputs("b", edges)
        assert ins["x"] == ("a", "true") and ins["in"] == ("a", "out")
        assert ins["y"] == ("c", "out")
        outs = gu.get_node_outputs("a", edges)
        assert outs == {"true": [("b", "x")], "out": [("b", "in")]}
        nodes = [
            {"id": "t", "category": "triggers"},
            {"category": "triggers"},
            {"id": "n"},
        ]
        assert gu.find_trigger_nodes(nodes) == ["t"]

    def test_validate_graph(self):
        """Every validation error class plus the clean and cycle cases."""
        ok = gu.validate_graph(
            [{"id": "t", "category": "triggers"}, {"id": "a"}],
            [{"source": "t", "target": "a"}],
        )
        assert ok == []
        errs = gu.validate_graph(
            [{"id": "a"}, {"id": "a"}, {}, {"id": "o"}],
            [
                {"target": "a"},
                {"source": "a"},
                {"source": "zz", "target": "a"},
                {"source": "a", "target": "zz"},
            ],
        )
        text = "\n".join(errs)
        for frag in (
            "missing 'id'",
            "Duplicate node ID",
            "missing 'source'",
            "missing 'target'",
            "source node not found",
            "target node not found",
            "Orphan node",
        ):
            assert frag in text
        errs = gu.validate_graph(
            [{"id": "a"}, {"id": "b"}],
            [{"source": "a", "target": "b"}, {"source": "b", "target": "a"}],
        )
        assert "Cycle detected" in errs[0]
        assert gu.validate_graph([], []) == []

    def test_validate_generic_error(self, monkeypatch):
        """Unexpected sorter exceptions are reported as validation errors."""

        class Boom:
            """Sorter double that fails."""

            def __init__(self, *a):
                """Accept anything."""

            def sort(self):
                """Always fail."""
                raise RuntimeError("kaboom")

        monkeypatch.setattr(gu, "TopologicalSorter", Boom)
        errs = gu.validate_graph(
            [{"id": "a"}, {"id": "b"}], [{"source": "a", "target": "b"}]
        )
        assert errs == ["Graph validation error: kaboom"]


class TestExecutorHelpers:
    """Serialization and routing helpers in playbook_executor."""

    def test_serialize_and_unwrap(self):
        """NodeData, dict (with datetime), scalar forms."""
        ts = datetime.now(UTC)
        nd = pe.NodeData(data={"a": 1}, source_node_id="s", timestamp=ts)
        assert pe._serialize_output(nd)["timestamp"] == ts.isoformat()
        d = pe._serialize_output({"data": 1, "timestamp": ts})
        assert d["timestamp"] == ts.isoformat() and d["source_node_id"] == ""
        assert pe._serialize_output({"data": 1, "timestamp": "x"})["timestamp"] == "x"
        assert pe._serialize_output(5) == {
            "data": 5,
            "metadata": {},
            "source_node_id": "",
            "timestamp": None,
        }
        assert pe._unwrap_output(nd) == {"a": 1}
        assert pe._unwrap_output({"data": 3}) == 3
        assert pe._unwrap_output({"x": 3}) == {"x": 3}
        assert pe._unwrap_output(7) == 7

    def test_resolve_handle(self):
        """Handle resolution priority."""
        r = pe._resolve_source_handle
        assert r({"true": 1}, "true") == "true"
        assert r({"true": 1}, "false") is None
        assert r({"default": 1, "out": 2}, None) == "default"
        assert r({"out": 2}, "default") == "out"
        assert r({"zz": 2}, None) == "zz"
        assert r({}, None) is None

    def test_primary_output(self):
        """Primary-output extraction precedence."""
        w = lambda v: {"data": v}  # noqa: E731
        assert pe._extract_primary_output({"out": w(1)}) == 1
        assert pe._extract_primary_output({"out": w(None), "default": w(2)}) == 2
        assert pe._extract_primary_output({"a": w({"k": 1}), "b": w(None)}) == {
            "k": 1,
            "a": {"k": 1},
            "b": None,
        }
        assert pe._extract_primary_output({"a": w(5), "b": w(None)}) == 5
        assert pe._extract_primary_output({"a": w(1), "b": w(2)}) == {"a": 1, "b": 2}

    def test_result_to_dict(self):
        """NodeResult and ExecutionResult serialise."""
        now = datetime.now(UTC)
        nr_ = pe.NodeResult(
            "n",
            pe.NodeStatus.SUCCESS,
            {"out": {"data": 1}},
            started_at=now,
            completed_at=now,
        )
        d = nr_.to_dict()
        assert d["status"] == "success" and d["started_at"] == now.isoformat()
        assert pe.NodeResult("n", pe.NodeStatus.FAILED).to_dict()["started_at"] is None
        er = pe.ExecutionResult(True, {"n": nr_}, completed_nodes=["n"])
        assert er.to_dict()["node_results"]["n"]["node_id"] == "n"

    @pytest.mark.asyncio
    async def test_base_and_passthrough(self):
        """Fallback node classes."""
        with pytest.raises(NotImplementedError):
            await pe.BaseNode({}).execute({})
        assert pe.BaseNode({}).validate_config() is None
        pt = pe.PassThroughNode({})
        assert await pt.execute({"default": 1, "x": 2}) == {"default": 1}
        assert await pt.execute({"x": 2}) == {"x": 2}

    def test_resolve_node_type(self):
        """Type resolution across backend and ReactFlow shapes."""
        r = pe.PlaybookExecutor._resolve_node_type
        assert r({"type": "a"}) == "a"
        assert r({"type": "playbookNode", "data": {"nodeType": "b"}}) == "b"
        assert r({"type": None, "data": {"nodeType": "b"}}) == "b"
        assert r({"type": "x", "data": {"nodeType": "b"}}) == "x"
        assert r({}) == "unknown"
        assert r({"type": "stream"}) == "stream"


def _register(R, node_type, cls, **attrs):
    """Register a test node class and return it."""
    sub = type(cls.__name__ + "_" + node_type, (cls,), dict(attrs))
    R.register(node_type, sub, "actions", allow_override=True)
    return sub


class Emit:
    """Test node emitting its config value on out."""

    def __init__(self, context):
        """Store the context."""
        self.context = context

    async def execute(self, inputs):
        """Emit config value plus received inputs."""
        cfg = self.context["config"]
        return {
            "out": {
                "data": {"v": cfg.get("v"), "got": inputs.get("in")},
                "metadata": {},
            }
        }


class Boom:
    """Test node that always fails."""

    def __init__(self, context):
        """Store the context."""
        self.context = context

    async def execute(self, inputs):
        """Always raise."""
        raise RuntimeError("bad node")


class Slow:
    """Test node that exceeds the timeout."""

    def __init__(self, context):
        """Store the context."""
        self.context = context

    async def execute(self, inputs):
        """Sleep past the timeout."""
        await asyncio.sleep(1)
        return {}


class Branch:
    """Test node that routes to true or false by config."""

    def __init__(self, context):
        """Store the context."""
        self.context = context

    async def execute(self, inputs):
        """Route the input down true or false."""
        flag = self.context["config"].get("flag")
        return {
            "true": {"data": inputs.get("in") if flag else None},
            "false": {"data": None if flag else inputs.get("in")},
        }


class TestPlaybookExecutor:
    """End-to-end PlaybookExecutor runs against registered test nodes."""

    @pytest.fixture(autouse=True)
    def _nodes(self, registry_guard):
        """Register the in-test node classes."""
        R = registry_guard
        _register(R, "cov_e_emit", Emit)
        _register(R, "cov_e_boom", Boom)
        _register(R, "cov_e_slow", Slow)
        _register(R, "cov_e_branch", Branch)
        _register(R, "cov_e_mod", Emit, required_module="runs")
        _register(R, "cov_e_core", Emit, required_module="core")
        yield

    @pytest.mark.asyncio
    async def test_empty_and_cycle(self):
        """No nodes and cyclic graphs both fail cleanly."""
        ex = pe.PlaybookExecutor("e", "p")
        r = await ex.execute({"nodes": []})
        assert not r.success and r.error == "No nodes in playbook"
        r = await ex.execute(
            {
                "nodes": [
                    {"id": "a", "type": "cov_e_emit"},
                    {"id": "b", "type": "cov_e_emit"},
                ],
                "edges": [
                    {"source": "a", "target": "b"},
                    {"source": "b", "target": "a"},
                ],
            }
        )
        assert not r.success and "Execution failed" in r.error

    @pytest.mark.asyncio
    async def test_happy_chain(self):
        """Data flows through a chain; config merge and context outputs populate."""
        ex = pe.PlaybookExecutor("e", "p")
        pb = {
            "nodes": [
                {
                    "id": "a",
                    "type": "cov_e_emit",
                    "label": "First",
                    "config": {"v": 1},
                    "data": {"resourceType": "entity", "resourceId": 4},
                },
                {
                    "id": "b",
                    "type": "playbookNode",
                    "data": {
                        "nodeType": "cov_e_emit",
                        "config": {"v": 2},
                        "label": "Second",
                    },
                },
            ],
            "edges": [{"source": "a", "target": "b"}],
            "config": {"x": 1},
            "trigger_output": {"t": 1},
            "tenant_id": 5,
            "enabled_modules": ["runs"],
        }
        r = await ex.execute(pb)
        assert r.success and r.completed_nodes == ["a", "b"]
        assert r.node_results["b"].outputs["out"]["data"]["got"] == {
            "v": 1,
            "got": None,
        }
        assert "First" in ex._node_context_outputs and "a" in ex._node_context_outputs
        assert r.to_dict()["success"] is True

    @pytest.mark.asyncio
    async def test_trigger_data_variants(self):
        """trigger_data wins over trigger_output; bad config type is tolerated."""
        ex = pe.PlaybookExecutor("e", "p")
        r = await ex.execute(
            {
                "nodes": [{"id": "a", "type": "cov_e_emit"}],
                "config": "bad",
                "trigger_data": {"k": 1},
            }
        )
        assert r.success

    @pytest.mark.asyncio
    async def test_failure_stops_and_continue(self):
        """A failed node stops execution unless continue_on_error is set."""
        pb = {
            "nodes": [
                {"id": "a", "type": "cov_e_boom"},
                {"id": "b", "type": "cov_e_emit"},
            ],
            "edges": [{"source": "a", "target": "b"}],
        }
        r = await pe.PlaybookExecutor("e", "p").execute(pb)
        assert not r.success and r.failed_nodes == ["a"] and "b" not in r.node_results
        assert r.error == "1 node(s) failed"
        pb["config"] = {"continue_on_error": True}
        r = await pe.PlaybookExecutor("e", "p").execute(pb)
        assert r.failed_nodes == ["a"] and r.completed_nodes == ["b"]

    @pytest.mark.asyncio
    async def test_unknown_type_timeout_module(self):
        """Unknown node, timeout and module-gating failures are reported."""
        pb = {"nodes": [{"id": "a", "type": "cov_e_nope"}]}
        r = await pe.PlaybookExecutor("e", "p").execute(pb)
        assert "not yet implemented" in r.node_results["a"].error
        r = await pe.PlaybookExecutor("e", "p", node_timeout_seconds=0.01).execute(
            {"nodes": [{"id": "a", "type": "cov_e_slow"}]}
        )
        assert "timeout" in r.node_results["a"].error
        mod = {"nodes": [{"id": "a", "type": "cov_e_mod"}]}
        r = await pe.PlaybookExecutor("e", "p").execute(
            dict(mod, enabled_modules={"runs": False})
        )
        assert "disabled" in r.node_results["a"].error
        r = await pe.PlaybookExecutor("e", "p").execute(dict(mod, enabled_modules=[]))
        assert "disabled" in r.node_results["a"].error
        r = await pe.PlaybookExecutor("e", "p").execute(
            dict(mod, enabled_modules={"runs": True})
        )
        assert r.success
        r = await pe.PlaybookExecutor("e", "p").execute(dict(mod))
        assert r.success
        core = {"nodes": [{"id": "a", "type": "cov_e_core"}], "enabled_modules": []}
        assert (await pe.PlaybookExecutor("e", "p").execute(core)).success

    @pytest.mark.asyncio
    async def test_skip_inactive_branches(self):
        """Nodes only reachable through an empty branch are skipped."""
        pb = {
            "nodes": [
                {"id": "a", "type": "cov_e_emit", "config": {"v": 1}},
                {"id": "br", "type": "cov_e_branch", "config": {"flag": True}},
                {"id": "t", "type": "cov_e_emit"},
                {"id": "f", "type": "cov_e_emit"},
                {"id": "g", "type": "cov_e_emit"},
            ],
            "edges": [
                {"source": "a", "target": "br"},
                {"source": "br", "target": "t", "sourceHandle": "true"},
                {"source": "br", "target": "f", "sourceHandle": "false"},
                {"source": "br", "target": "g", "sourceHandle": "bogus"},
            ],
            "config": {"skip_inactive_branches": True},
        }
        r = await pe.PlaybookExecutor("e", "p").execute(pb)
        assert set(r.skipped_nodes) == {"f", "g"}
        assert "t" in r.completed_nodes
        assert r.node_results["f"].status == pe.NodeStatus.SKIPPED

    def test_active_incoming_edge(self):
        """_has_active_incoming_edge handles missing sources and failures."""
        ex = pe.PlaybookExecutor("e", "p")
        assert ex._has_active_incoming_edge("x", [], {})
        edges = [{"target": "x"}, {"source": "s", "target": "x"}]
        assert not ex._has_active_incoming_edge("x", edges, {})
        ex._node_outputs["s"] = {"out": {"data": 1}}
        res = {"s": pe.NodeResult("s", pe.NodeStatus.FAILED)}
        assert not ex._has_active_incoming_edge("x", edges, res)
        res = {"s": pe.NodeResult("s", pe.NodeStatus.SUCCESS)}
        assert ex._has_active_incoming_edge("x", edges, res)

    @pytest.mark.asyncio
    async def test_missing_node_id_in_order(self, monkeypatch):
        """An order entry with no matching node is skipped with an error log."""
        ex = pe.PlaybookExecutor("e", "p")
        monkeypatch.setattr(ex, "_get_execution_order", lambda n, e: ["ghost", "a"])
        r = await ex.execute({"nodes": [{"id": "a", "type": "cov_e_emit"}]})
        assert r.completed_nodes == ["a"]

    @pytest.mark.asyncio
    async def test_skipped_status_from_node(self, monkeypatch):
        """A node result with SKIPPED status is tallied as skipped."""
        ex = pe.PlaybookExecutor("e", "p")

        async def fake(node_id, node_data, inputs, cfg):
            """Return a skipped result."""
            return pe.NodeResult(node_id, pe.NodeStatus.SKIPPED)

        monkeypatch.setattr(ex, "_execute_node", fake)
        r = await ex.execute({"nodes": [{"id": "a", "type": "cov_e_emit"}]})
        assert r.skipped_nodes == ["a"] and not r.success

    def test_gather_inputs(self):
        """Input gathering across handles, None fill-in and missing sources."""
        ex = pe.PlaybookExecutor("e", "p")
        outs = {
            "s1": {"out": {"data": None}},
            "s2": {"out": {"data": 5}},
            "s3": {"out": {"data": 7}},
            "s4": {"zz": {"data": 9}},
        }
        edges = [
            {"source": "s1", "target": "n"},
            {"source": "s2", "target": "n"},
            {"source": "s3", "target": "n", "targetHandle": "extra"},
            {"source": "s4", "target": "n", "sourceHandle": "nope"},
            {"source": "missing", "target": "n"},
        ]
        got = ex._gather_inputs("n", edges, outs)
        assert got["default"] == 5 and got["in"] == 5 and got["extra"] == 7

    def test_route_outputs(self):
        """Outputs are stored under both id and label."""
        ex = pe.PlaybookExecutor("e", "p")
        res = pe.NodeResult("n", pe.NodeStatus.SUCCESS, {"out": {"data": 1}})
        ex._route_outputs("n", res, [], {"data": {"label": "L"}})
        assert ex._node_context_outputs == {"L": 1, "n": 1}
        ex._route_outputs("m", res, [])
        assert ex._node_context_outputs["m"] == 1

    @pytest.mark.asyncio
    async def test_execute_node_exception_path(self, monkeypatch):
        """Errors before node execution are converted to a failed result."""
        ex = pe.PlaybookExecutor("e", "p")

        def boom(*a, **k):
            """Fail registry lookup."""
            raise RuntimeError("lookup")

        monkeypatch.setattr(pe.NodeRegistry, "get", boom)
        res = await ex._execute_node("a", {"type": "t"}, {}, {})
        assert res.status == pe.NodeStatus.FAILED and "lookup" in res.error


class TestTriggers:
    """Core trigger nodes."""

    def _ctx(self, cfg=None, trigger=None):
        """Build a trigger context."""
        g = {} if trigger is None else {"trigger_data": trigger}
        return {
            "execution_id": "e",
            "node_id": "n",
            "config": cfg or {},
            "global_config": g,
        }

    def test_parse_json(self):
        """_parse_json_object tolerant parsing."""
        p = ct._parse_json_object
        assert p({"a": 1}) == {"a": 1}
        assert p('{"a": 1}') == {"a": 1}
        assert p("[1]") == {}
        assert p("{bad") == {}
        assert p("  ") == {}
        assert p(None) == {}

    def test_ports(self):
        """Trigger ports and validation."""
        for cls in (
            ct.ManualTriggerNode,
            ct.ScheduleTriggerNode,
            ct.WebhookTriggerNode,
        ):
            assert cls.inputs() == [] and cls.outputs()[0]["name"] == "out"
        v = ct.ScheduleTriggerNode(self._ctx()).validate_config
        assert v({}) == [] and v({"cron_expression": "* * * * *"}) == []
        assert v({"cron_expression": 5}) == ["cron_expression must be a string"]

    @pytest.mark.asyncio
    async def test_manual(self):
        """Manual trigger payload precedence."""
        run = lambda cfg, trig: ct.ManualTriggerNode(self._ctx(cfg, trig)).execute({})  # noqa: E731
        d = (await run({}, {"a": 1}))["out"]["data"]
        assert d["a"] == 1 and d["_trigger"]["type"] == "manual"
        d = (await run({"sample_payload": '{"s": 1}'}, None))["out"]["data"]
        assert d["s"] == 1
        d = (await run({}, "scalar"))["out"]["data"]
        assert d["data"] == "scalar"
        d = (await run({}, {}))["out"]["data"]
        assert set(d) == {"_trigger"}

    @pytest.mark.asyncio
    async def test_schedule(self):
        """Schedule trigger merges static payload and trigger data."""
        node = ct.ScheduleTriggerNode(self._ctx({"static_payload": {"s": 1}}, {"t": 2}))
        d = (await node.execute({}))["out"]["data"]
        assert d["s"] == 1 and d["t"] == 2 and d["cron_expression"] == "0 * * * *"
        d = (await ct.ScheduleTriggerNode(self._ctx({}, "x")).execute({}))["out"][
            "data"
        ]
        assert d["timezone"] == "UTC"

    @pytest.mark.asyncio
    async def test_webhook(self):
        """Webhook trigger payload shapes."""
        run = lambda trig: ct.WebhookTriggerNode(self._ctx({}, trig)).execute({})  # noqa: E731
        d = (
            await run(
                {
                    "payload": {"p": 1},
                    "headers": {"h": 1},
                    "source_ip": "1.2.3.4",
                    "extra": 9,
                }
            )
        )["out"]["data"]
        assert (
            d["payload"] == {"p": 1} and d["source_ip"] == "1.2.3.4" and d["extra"] == 9
        )
        d = (
            await run(
                {"__webhook__": {"remote_addr": "9.9.9.9", "timestamp": "ts"}, "k": 1}
            )
        )["out"]["data"]
        assert d["source_ip"] == "9.9.9.9" and d["received_at"] == "ts"
        d = (await run("raw"))["out"]["data"]
        assert d["payload"] == "raw"
        d = (await ct.WebhookTriggerNode(self._ctx()).execute({}))["out"]["data"]
        assert d["payload"] == {}
