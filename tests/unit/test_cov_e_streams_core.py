"""Coverage tests for streams base node, template resolver, respond and log nodes.

Exercises the real node classes with in-memory contexts; no external boundaries
are involved, so nothing is mocked beyond logging capture.
"""

from __future__ import annotations

import json

import pytest

import tests.unit.conftest_worker_stubs  # noqa: F401
from apps.worker.streams.nodes import template as tpl
from apps.worker.streams.nodes.actions.log import LogAction
from apps.worker.streams.nodes.actions.respond import RespondAction
from apps.worker.streams.nodes.base import BaseNode


def _ctx(config=None, **extra):
    """Build a node execution context with optional config and extras."""
    ctx = {
        "execution_id": "exec-1",
        "playbook_id": "pb-1",
        "node_id": "node-1",
        "config": config or {},
    }
    ctx.update(extra)
    return ctx


class TestBaseNode:
    """BaseNode contract: required class attributes, defaults, logging helpers."""

    def test_missing_attributes_raise(self):
        """Each missing class attribute produces its own ValueError."""

        class NoType(BaseNode):
            """Node lacking node_type."""

        class NoName(BaseNode):
            """Node lacking name."""

            node_type = "x"

        class NoDesc(BaseNode):
            """Node lacking description."""

            node_type = "x"
            name = "n"

        class NoCat(BaseNode):
            """Node lacking category."""

            node_type = "x"
            name = "n"
            description = "d"

        for cls, word in (
            (NoType, "node_type"),
            (NoName, "name"),
            (NoDesc, "description"),
            (NoCat, "category"),
        ):
            with pytest.raises(ValueError, match=word):
                cls({})

    @pytest.mark.asyncio
    async def test_defaults_and_helpers(self, caplog):
        """Default ports, validation, config access, logging and repr work."""

        class Good(BaseNode):
            """Minimal valid node."""

            node_type = "good"
            name = "Good"
            description = "d"
            category = "c"

        node = Good(_ctx({"k": 1}))
        assert Good.inputs() == []
        assert Good.outputs() == []
        assert node.validate_config({}) == []
        assert node.get_config_value("k") == 1
        assert node.get_config_value("missing", 7) == 7
        with pytest.raises(NotImplementedError):
            await node.execute({})
        with caplog.at_level("INFO"):
            node.log_info("hi")
            node.log_warning("warn")
            node.log_error("err")
        text = caplog.text
        assert "[exec-1][node-1] hi" in text
        assert "warn" in text and "err" in text
        assert "Good(node_type='good'" in repr(node)


class TestResolvePath:
    """resolve_path handles dicts, lists, brackets and misses."""

    def test_paths(self):
        """Dotted, bracketed and wrapped paths resolve or return missing."""
        data = {"a": {"b": [10, {"c": 5}]}}
        assert tpl.resolve_path(data, "a.b[1].c") == 5
        assert tpl.resolve_path(data, "a.b.0") == 10
        assert tpl.resolve_path(data, None) is data
        assert tpl.resolve_path(data, "a.b.9") is tpl._MISSING
        assert tpl.resolve_path(data, "a.zzz") is tpl._MISSING
        assert tpl.resolve_path(data, "a.b.x") is tpl._MISSING
        assert tpl.resolve_path("str", "x") is tpl._MISSING
        assert tpl.resolve_path(None, "x") is tpl._MISSING
        assert tpl.resolve_path(tpl._MISSING, "x") is tpl._MISSING
        wrapped = {"w": {"data": {"v": 1}, "source_node_id": "n"}}
        assert tpl.resolve_path(wrapped, "w.v") == 1


class TestPrimaryPayload:
    """_primary_json_payload port preference order."""

    def test_order(self):
        """in, default, payload, then any non-None port, else empty dict."""
        p = tpl._primary_json_payload
        assert p("raw") == "raw"
        assert p({"in": 1, "default": 2}) == 1
        assert p({"in": None, "default": 2}) == 2
        assert p({"payload": 3}) == 3
        assert p({"zzz": 4}) == 4
        assert p({"zzz": None}) == {}
        assert p({"in": {"data": 9, "source_node_id": "x"}}) == 9


class TestResolveExpression:
    """resolve_expression covers every namespace."""

    def _c(self):
        """Return a context with node outputs, trigger and global config."""
        return _ctx(
            {"cfgk": "cv"},
            global_config={"trigger_data": {"t": 1}, "tenant_id": 5, "gk": "g"},
            node_outputs={"Label A": {"x": 1}, "nid": {"y": {"z": 2}}},
        )

    def test_namespaces(self):
        """json/input/node/trigger/execution/in/config/direct all resolve."""
        c = self._c()
        inps = {"in": {"f": {"g": 3}}, "port": {"q": 8}}
        r = tpl.resolve_expression
        assert r("$json", c, inps) == {"f": {"g": 3}}
        assert r("$json.f.g", c, inps) == 3
        assert r("$input", c, inps) == {"f": {"g": 3}}
        assert r("$input.f", c, inps) == {"g": 3}
        assert r('$node["Label A"].x', c, inps) == 1
        assert r("$node['Label A']", c, inps) == {"x": 1}
        assert r('$node["nope"].x', c, inps) is tpl._MISSING
        assert r("$node.nid.y.z", c, inps) == 2
        assert r("$node.nid", c, inps) == {"y": {"z": 2}}
        assert r("$node.zzz.y", c, inps) is tpl._MISSING
        assert r("$trigger", c, inps) == {"t": 1}
        assert r("$trigger.t", c, inps) == 1
        assert r("$execution.id", c, inps) == "exec-1"
        assert r("$execution.tenant_id", c, inps) == 5
        assert r("in", c, inps) == {"f": {"g": 3}}
        assert r("input", c, inps) == {"f": {"g": 3}}
        assert r("in.f.g", c, inps) == 3
        assert r("input.port.q", c, inps) == 8
        assert r("input.f.g", c, inps) == 3
        assert r("config.cfgk", c, inps) == "cv"

    def test_direct_lookup_chain(self):
        """Bare names fall through ports, payload, global, config, trigger."""
        c = self._c()
        inps = {"in": {"f": 1}, "port": {"q": 8}}
        r = tpl.resolve_expression
        assert r("port.q", c, inps) == 8
        assert r("port.nothere", c, inps) is tpl._MISSING
        assert r("f", c, inps) == 1
        assert r("gk", c, inps) == "g"
        assert r("cfgk", c, inps) == "cv"
        assert r("t", c, inps) == 1
        assert r("absent", c, inps) is tpl._MISSING
        assert r("absent", None, None) is tpl._MISSING

    def test_auth_token_hidden(self):
        """Internal auth token is never exposed."""
        c = _ctx(global_config={"_elder_auth_token": "x"})
        assert tpl.resolve_expression("_elder_auth_token", c, {}) is tpl._MISSING
        assert tpl.resolve_expression("_elder_auth_token.a", c, {}) is tpl._MISSING


class TestInterpolate:
    """interpolate_value / interpolate_any behaviour."""

    def test_value(self):
        """Embedded, exact, preserve_type, unresolved and non-string cases."""
        c = _ctx()
        i = {"in": {"n": 5, "d": {"a": 1}, "none": None}}
        assert tpl.interpolate_value(3, c, i) == 3
        assert tpl.interpolate_value("a {{n}} b", c, i) == "a 5 b"
        assert tpl.interpolate_value("{{d}}", c, i) == '{"a": 1}'
        assert tpl.interpolate_value("{{none}}", c, i) == ""
        assert tpl.interpolate_value("{{n}}", c, i, preserve_type=True) == 5
        assert tpl.interpolate_value("{{zz}}", c, i, preserve_type=True) == "{{zz}}"
        assert tpl.interpolate_value("x {{zz}}", c, i) == "x {{zz}}"

    def test_any(self):
        """Recursion across dicts, lists, strings and scalars."""
        c = _ctx()
        i = {"in": {"n": 5}}
        out = tpl.interpolate_any({"a": ["{{n}}", 2], "b": "{{n}}"}, c, i)
        assert out == {"a": [5, 2], "b": 5}
        assert tpl.interpolate_any("{{n}}", c, i, preserve_type=False) == "5"
        assert tpl.interpolate_any(7, c, i) == 7


class TestRespond:
    """RespondAction body selection paths."""

    async def _run(self, config, inputs):
        """Run the node and return the unwrapped data envelope."""
        res = await RespondAction(_ctx(config)).execute(inputs)
        return res["out"]["data"]

    def test_ports(self):
        """Declared ports are stable."""
        assert RespondAction.inputs()[0]["name"] == "in"
        assert RespondAction.outputs()[0]["name"] == "out"

    @pytest.mark.asyncio
    async def test_default_body_from_input(self):
        """No response_body uses the in port, then default, then empty."""
        d = await self._run({"summary": "s {{a}}"}, {"in": {"a": 1}})
        assert d == {"status_code": 200, "summary": "s 1", "body": {"a": 1}}
        d = await self._run({"response_body": ""}, {"default": 4})
        assert d["body"] == 4
        d = await self._run({}, {})
        assert d["body"] == {}

    @pytest.mark.asyncio
    async def test_string_body_json_and_plain(self):
        """String bodies parse as JSON when possible else stay strings."""
        d = await self._run({"response_body": '{"k": "{{a}}"}'}, {"in": {"a": 1}})
        assert d["body"] == {"k": "1"}
        d = await self._run({"response_body": "plain text"}, {"in": {}})
        assert d["body"] == "plain text"
        d = await self._run(
            {"response_body": "{{a}}", "status_code": "201"}, {"in": {"a": 9}}
        )
        assert d["body"] == 9 and d["status_code"] == 201

    @pytest.mark.asyncio
    async def test_structured_body(self):
        """Dict bodies are interpolated recursively."""
        d = await self._run({"response_body": {"x": "{{a}}"}}, {"in": {"a": 2}})
        assert d["body"] == {"x": 2}


class TestLog:
    """LogAction levels, message sources and data inclusion."""

    def test_ports(self):
        """Declared ports are stable."""
        assert [p["name"] for p in LogAction.inputs()] == ["message", "data"]
        assert [p["name"] for p in LogAction.outputs()] == ["out", "logged"]

    @pytest.mark.parametrize("level", ["DEBUG", "info", "WARNING", "ERROR", "TRACE"])
    @pytest.mark.asyncio
    async def test_levels(self, level, capsys):
        """Every level emits a JSON line and returns pass-through plus flag."""
        node = LogAction(_ctx({"level": level, "message": "m {{a}}"}))
        res = await node.execute({"in": {"a": 1}})
        entry = json.loads(capsys.readouterr().out.strip())
        assert entry["message"] == "m 1"
        assert entry["data"] == {"a": 1}
        assert res["logged"]["data"] is True
        assert res["out"]["data"] == {"a": 1}

    @pytest.mark.asyncio
    async def test_message_input_data_and_no_data(self, capsys):
        """Input message wins; includeData False drops data; fallback passthrough."""
        node = LogAction(_ctx({"includeData": False}))
        res = await node.execute({"message": "hello", "data": {"z": 1}})
        entry = json.loads(capsys.readouterr().out.strip())
        assert "data" not in entry
        assert res["out"]["data"] == {"z": 1}
        res = await node.execute({"message": "only"})
        assert res["out"]["data"] == "only"

    @pytest.mark.asyncio
    async def test_format_failure_reraised(self):
        """Unserialisable data propagates after being logged."""
        node = LogAction(_ctx())
        with pytest.raises(TypeError):
            await node.execute({"message": "m", "data": object()})
