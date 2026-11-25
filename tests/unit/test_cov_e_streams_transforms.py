"""Coverage tests for streams transform nodes: delay, set_fields, filter,
expression, json_transform, merge and split.

All pure in-process logic; only asyncio.sleep is stubbed so delays are instant.
"""

from __future__ import annotations

import pytest

import tests.unit.conftest_worker_stubs  # noqa: F401
from apps.worker.streams.nodes.regex_guard import UnsafeRegexError
from apps.worker.streams.nodes.transforms import delay as delay_mod
from apps.worker.streams.nodes.transforms import filter as filter_mod
from apps.worker.streams.nodes.transforms.delay import DelayTransform
from apps.worker.streams.nodes.transforms.expression import ExpressionTransform
from apps.worker.streams.nodes.transforms.filter import FilterTransform
from apps.worker.streams.nodes.transforms.json_transform import JsonTransform
from apps.worker.streams.nodes.transforms.merge import MergeTransform
from apps.worker.streams.nodes.transforms.set_fields import (
    SetFieldsTransform,
    _assign_path,
    _cast_value,
)
from apps.worker.streams.nodes.transforms.split import SplitTransform


def _ctx(config=None):
    """Build a node context."""
    return {
        "execution_id": "e",
        "playbook_id": "p",
        "node_id": "n",
        "config": config or {},
    }


def _data(res, port="out"):
    """Extract the data of one output port."""
    return res[port]["data"]


class TestDelay:
    """DelayTransform duration handling."""

    def test_ports(self):
        """Declared ports."""
        assert DelayTransform.inputs()[0]["name"] == "in"
        assert DelayTransform.outputs()[0]["name"] == "out"

    @pytest.mark.asyncio
    async def test_durations(self, monkeypatch):
        """delayMs, delaySeconds, cap, none, and non-dict passthrough."""
        slept = []

        async def fake(s):
            """Record requested sleep."""
            slept.append(s)

        monkeypatch.setattr(delay_mod.asyncio, "sleep", fake)
        res = await DelayTransform(_ctx({"delayMs": 500})).execute({"in": {"a": 1}})
        assert slept == [0.5] and _data(res)["_delay"]["duration_seconds"] == 0.5
        await DelayTransform(_ctx({"delaySeconds": 9999})).execute({"in": {}})
        assert slept[-1] == 300.0
        res = await DelayTransform(_ctx()).execute({"in": {"a": 1}})
        assert _data(res)["_delay"]["duration_seconds"] == 0.0 and len(slept) == 2
        res = await DelayTransform(_ctx({"delaySeconds": 1})).execute({"in": [1]})
        assert _data(res) == [1]


class TestSetFields:
    """SetFieldsTransform casting, assignment and validation."""

    @pytest.mark.parametrize(
        "val,typ,expected",
        [
            (1, "auto", 1),
            (1, "", 1),
            (None, "string", ""),
            (3, "string", "3"),
            (3, "number", 3),
            (" 7 ", "number", 7),
            ("2.5", "number", 2.5),
            ("true", "boolean", True),
            ("no", "boolean", False),
            (True, "boolean", True),
            (0, "boolean", False),
            ('{"a": 1}', "json", {"a": 1}),
            ("  ", "json", None),
            ([1], "json", [1]),
            (5, "json", 5),
            (5, "weird", 5),
        ],
    )
    def test_cast(self, val, typ, expected):
        """Every cast branch."""
        assert _cast_value(val, typ) == expected

    def test_cast_bad_number(self):
        """Unparseable numbers raise ValueError."""
        with pytest.raises(ValueError, match="Cannot cast"):
            _cast_value("abc", "number")
        with pytest.raises(ValueError):
            _cast_value(True, "number")

    def test_assign_path(self):
        """Nested creation, overwrite of non-dict, empty path."""
        t = {"a": 1}
        _assign_path(t, "a.b.c", 2)
        assert t == {"a": {"b": {"c": 2}}}
        _assign_path(t, "", 3)
        assert t == {"a": {"b": {"c": 2}}}

    def test_ports_validate(self):
        """Ports and validation errors."""
        assert SetFieldsTransform.inputs()[0]["name"] == "in"
        assert SetFieldsTransform.outputs()[0]["name"] == "out"
        v = SetFieldsTransform(_ctx()).validate_config
        assert v({}) == []
        assert v({"assignments": "x"}) == ["assignments must be a list"]
        errs = v({"assignments": [{"name": "a"}, {"name": " "}, "str"]})
        assert len(errs) == 2

    @pytest.mark.asyncio
    async def test_execute(self):
        """Object, list, keep_only and default-port inputs."""
        cfg = {
            "assignments": [
                {"name": "full", "value": "{{first}}"},
                {"name": "n.x", "value": "{{n}}", "type": "string"},
                {"name": " ", "value": "skip"},
            ]
        }
        item = {"first": "A", "last": "B", "n": 4}
        res = await SetFieldsTransform(_ctx(cfg)).execute({"in": item})
        out = _data(res)
        assert out["full"] == "A" and out["n"]["x"] == "4" and out["first"] == "A"
        res = await SetFieldsTransform(_ctx(dict(cfg, keep_only_set=True))).execute(
            {"in": [item, "scalar"]}
        )
        assert _data(res)[0].keys() == {"full", "n"}
        assert len(_data(res)) == 2
        res = await SetFieldsTransform(_ctx(cfg)).execute({"default": {"first": "z"}})
        assert _data(res)["first"] == "z"
        res = await SetFieldsTransform(_ctx({"assignments": None})).execute({})
        assert _data(res) == {}


class TestFilter:
    """FilterTransform operators, logic and execution."""

    def test_ports_validate(self):
        """Ports and every validation branch."""
        assert len(FilterTransform.outputs()) == 2
        assert FilterTransform.inputs()[0]["name"] == "in"
        v = FilterTransform(_ctx()).validate_config
        assert v({"conditions": [{"field": "a"}]}) == []
        errs = v({"conditions": [{"operator": "bogus"}], "logic": "xor"})
        assert len(errs) == 3
        assert v({}) == ["At least one condition is required"]

    def test_get_field_value(self):
        """Dotted lookup through dicts and lists."""
        n = FilterTransform(_ctx())
        d = {"a": {"b": [{"c": 1}]}}
        assert n._get_field_value(d, "a.b.0.c") == 1
        assert n._get_field_value(d, "a.b.5") is None
        assert n._get_field_value(d, "a.b.x") is None
        assert n._get_field_value(d, "a.zz.y") is None

    @pytest.mark.parametrize(
        "op,actual,expected,result",
        [
            ("eq", 1, 1, True),
            ("ne", 1, 2, True),
            ("gt", 3, 2, True),
            ("gte", 2, 2, True),
            ("lt", 1, 2, True),
            ("lte", 2, 2, True),
            ("contains", "abc", "b", True),
            ("contains", 5, "b", False),
            ("not_contains", "abc", "z", True),
            ("not_contains", 5, "z", True),
            ("starts_with", "abc", "a", True),
            ("ends_with", "abc", "c", True),
            ("is_null", None, None, True),
            ("is_not_null", 1, None, True),
            ("in", 1, [1, 2], True),
            ("in", 1, "x", False),
            ("not_in", 1, [2], True),
            ("not_in", 1, "x", True),
            ("gt", "a", 1, False),
            ("nonexistent", 1, 1, True),
        ],
    )
    @pytest.mark.asyncio
    async def test_operators(self, op, actual, expected, result):
        """Each operator including error-swallowing comparisons."""
        node = FilterTransform(_ctx())
        cond = {"field": "v", "operator": op, "value": expected}
        assert await node._evaluate_condition({"v": actual}, cond) is result

    @pytest.mark.asyncio
    async def test_regex(self, monkeypatch):
        """Regex matches and each guard failure returns False."""
        node = FilterTransform(_ctx())
        cond = {"field": "v", "operator": "regex", "value": "^ab"}
        assert await node._evaluate_condition({"v": "abc"}, cond) is True
        assert await node._evaluate_condition({"v": "xbc"}, cond) is False

        for exc in (UnsafeRegexError("bad"), TimeoutError(), RuntimeError("x")):

            async def boom(*a, _e=exc, **k):
                """Raise the parametrised error."""
                raise _e

            monkeypatch.setattr(filter_mod, "safe_regex_search", boom)
            assert await node._evaluate_condition({"v": "abc"}, cond) is False

    @pytest.mark.asyncio
    async def test_execute(self):
        """Arrays, single objects, logic or/and, non-dicts, empty conditions."""
        conds = [
            {"field": "a", "operator": "eq", "value": 1},
            {"field": "b", "operator": "eq", "value": 2},
        ]
        items = [{"a": 1, "b": 2}, {"a": 1, "b": 0}, {"a": 0, "b": 0}, "scalar"]
        res = await FilterTransform(_ctx({"conditions": conds})).execute({"in": items})
        assert _data(res) == [items[0], "scalar"]
        assert _data(res, "rejected") == [items[1], items[2]]
        res = await FilterTransform(_ctx({"conditions": conds, "logic": "or"})).execute(
            {"in": items}
        )
        assert len(_data(res)) == 3
        res = await FilterTransform(_ctx({"conditions": conds})).execute(
            {"in": items[0]}
        )
        assert _data(res) == items[0] and _data(res, "rejected") is None
        res = await FilterTransform(_ctx({"conditions": conds})).execute(
            {"in": items[2]}
        )
        assert _data(res) is None and _data(res, "rejected") == items[2]
        res = await FilterTransform(_ctx({})).execute({"in": [{"a": 1}]})
        assert _data(res) == [{"a": 1}]
        with pytest.raises(ValueError):
            await FilterTransform(_ctx()).execute({})


class TestExpression:
    """ExpressionTransform safe AST evaluator."""

    async def _run(self, expr, data=None, **kw):
        """Evaluate an expression against input data."""
        res = await ExpressionTransform(_ctx({"expression": expr})).execute(
            {"in": data if data is not None else {}}
        )
        return _data(res)

    def test_ports_validate(self):
        """Ports and validation outcomes."""
        assert ExpressionTransform.inputs()[0]["name"] == "in"
        assert ExpressionTransform.outputs()[0]["name"] == "out"
        v = ExpressionTransform(_ctx()).validate_config
        assert v({"expression": "1+1"}) == []
        assert v({}) == ["expression is required"]
        assert "syntax" in v({"expression": "1 +"})[0]

    @pytest.mark.asyncio
    async def test_values(self):
        """Arithmetic, comparisons, calls, subscripts, attributes, literals."""
        d = {"x": 4, "name": " Bob ", "lst": [1, 2, 3], "m": {"k": 9}}
        assert await self._run("x * 2 + 1 - 3 / 3", d) == 8.0
        assert await self._run("x // 3 + x % 3 + 2 ** 2", d) == 1 + 1 + 4
        assert await self._run("-x + +x", d) == 0
        assert await self._run("1 < x <= 4 != 5", d) is True
        assert await self._run("1 < x < 2", d) is False
        assert await self._run("x >= 4 and x > 1", d) is True
        assert await self._run("x == 0 or x == 4", d) is True
        assert await self._run("x == 0 or x == 1", d) is False
        assert await self._run("x == 0 and x", d) is False
        assert await self._run("upper(strip(name))", d) == "BOB"
        assert (
            await self._run("len(lst) + sum(lst) + max(lst) + min(lst)", d)
            == 3 + 6 + 3 + 1
        )
        assert await self._run("lst[1] + m['k']", d) == 11
        assert await self._run("m.k", d) == 9
        assert await self._run("m.missing", d) is None
        assert await self._run("[x, 1]", d) == [4, 1]
        assert await self._run("{'a': x}", d) == {"a": 4}
        assert await self._run("'yes' if x else 'no'", d) == "yes"
        assert await self._run("'yes' if 0 else 'no'", d) == "no"
        assert await self._run("true and not_ident", {"not_ident": 1}) is True
        assert await self._run("null", d) is None
        assert await self._run("name.strip()", d) == "Bob"
        assert await self._run("data", [1]) == [1]
        assert await self._run("floor(sqrt(17))", d) == 4

    @pytest.mark.asyncio
    async def test_unsafe_rejected(self):
        """Unknown names, operators, nodes and non-callables raise."""
        for expr in (
            "unknown_var",
            "1 << 2",
            "~1",
            "1 in [1]",
            "x.nope.deeper",
            "(lambda: 1)()",
            "x()",
            "lst[0:2]",
            "{1: 2}[3]",
        ):
            with pytest.raises((ValueError, KeyError)):
                await self._run(expr, {"x": 1})
        with pytest.raises(ValueError, match="missing"):
            await ExpressionTransform(_ctx()).execute({})

    @pytest.mark.asyncio
    async def test_attr_error_and_default(self):
        """Attribute on a non-dict without the attr raises; default expr is data."""
        with pytest.raises(ValueError, match="attribute"):
            await self._run("x.zzz", {"x": 1})
        res = await ExpressionTransform(_ctx()).execute({"in": 5})
        assert _data(res) == 5


class TestJsonTransform:
    """JsonTransform operations and helpers."""

    async def _run(self, cfg, data):
        """Run a configured transform and return the data."""
        return _data(await JsonTransform(_ctx(cfg)).execute({"in": data}))

    def test_ports_validate(self):
        """Ports and validation branches."""
        assert JsonTransform.inputs()[0]["name"] == "in"
        assert JsonTransform.outputs()[0]["name"] == "out"
        v = JsonTransform(_ctx()).validate_config
        assert "Invalid operation" in v({"operation": "bogus"})[0]
        assert v({"operation": "extract"}) == [
            "jsonPath is required for extract operation"
        ]
        assert len(v({"operation": "set"})) == 2
        assert len(v({"operation": "rename"})) == 2
        assert v({"operation": "set", "jsonPath": "a", "value": 1}) == []
        assert v({"operation": "delete"}) == []

    @pytest.mark.asyncio
    async def test_extract_and_set(self):
        """Extract via jmespath, set with literal and path values."""
        d = {"a": {"b": [1, 2]}}
        assert await self._run({"operation": "extract", "jsonPath": "a.b[1]"}, d) == 2
        assert (
            await self._run({"operation": "set", "jsonPath": "x.y", "value": 5}, d)
        )["x"] == {"y": 5}
        out = await self._run(
            {"operation": "set", "jsonPath": "z", "value": "$.a.b[0]"}, d
        )
        assert out["z"] == 1
        assert await self._run(
            {"operation": "set", "jsonPath": "k", "value": 1}, [1]
        ) == {"k": 1}

    def test_dot_path_fallback(self, monkeypatch):
        """Dot-notation extractor handles dicts, lists and misses."""
        n = JsonTransform(_ctx())
        d = {"a": {"b": [10, 20]}}
        assert n._extract_dot_path(d, "a.b.1") == 20
        assert n._extract_dot_path(d, "a.b.5") is None
        assert n._extract_dot_path(d, "a.c") is None
        assert n._extract_dot_path(d, "a.b.x") is None
        assert n._extract_dot_path(d, "a.b.1.q") is None

    def test_extract_without_jmespath(self, monkeypatch):
        """ImportError of jmespath falls back to dot notation."""
        import builtins

        real = builtins.__import__

        def fake(name, *a, **k):
            """Block jmespath imports."""
            if name == "jmespath":
                raise ImportError(name)
            return real(name, *a, **k)

        monkeypatch.setattr(builtins, "__import__", fake)
        assert JsonTransform(_ctx())._extract_path({"a": 1}, "a") == 1

    @pytest.mark.asyncio
    async def test_delete_rename(self):
        """Delete and rename including missing paths and bad input types."""
        d = {"a": {"b": 1}, "c": 2}
        assert await self._run({"operation": "delete", "jsonPath": "a.b"}, d) == {
            "a": {},
            "c": 2,
        }
        assert await self._run({"operation": "delete", "jsonPath": "zz.y"}, d) == d
        assert await self._run(
            {"operation": "delete", "jsonPath": "nope"}, {"c": 1}
        ) == {"c": 1}
        out = await self._run(
            {"operation": "rename", "fromPath": "c", "toPath": "d.e"}, d
        )
        assert out["d"] == {"e": 2} and "c" not in out
        with pytest.raises(ValueError, match="non-object"):
            await self._run({"operation": "delete", "jsonPath": "a"}, [1])
        with pytest.raises(ValueError, match="non-object"):
            await self._run(
                {"operation": "rename", "fromPath": "a", "toPath": "b"}, [1]
            )

    @pytest.mark.asyncio
    async def test_merge_flatten(self):
        """Merge (dict/str/invalid/non-dict) and flatten/unflatten."""
        assert await self._run(
            {"operation": "merge", "mergeData": {"z": 1}}, {"a": 1}
        ) == {"a": 1, "z": 1}
        assert await self._run(
            {"operation": "merge", "mergeData": '{"z": 1}'}, {"a": 1}
        ) == {"a": 1, "z": 1}
        assert await self._run({"operation": "merge", "mergeData": {"z": 1}}, 5) == {
            "z": 1
        }
        with pytest.raises(ValueError, match="mergeData"):
            await self._run({"operation": "merge", "mergeData": "{bad"}, {})
        nested = {"a": {"b": 1}, "l": [1, {"x": 2}]}
        flat = await self._run({"operation": "flatten"}, nested)
        assert flat == {"a.b": 1, "l.0": 1, "l.1.x": 2}
        assert await self._run(
            {"operation": "flatten", "separator": "/"}, {"a": {"b": 1}}
        ) == {"a/b": 1}
        assert await self._run({"operation": "flatten"}, 5) == 5
        assert await self._run({"operation": "unflatten"}, {"a.b": 1, "a.c": 2}) == {
            "a": {"b": 1, "c": 2}
        }
        assert await self._run({"operation": "unflatten"}, [1]) == [1]

    @pytest.mark.asyncio
    async def test_errors(self):
        """Unknown operation and missing input raise."""
        with pytest.raises(ValueError, match="Unknown operation"):
            await self._run({"operation": "bogus"}, {})
        with pytest.raises(ValueError, match="missing"):
            await JsonTransform(_ctx()).execute({})


class TestMerge:
    """MergeTransform modes."""

    async def _run(self, cfg, inputs):
        """Run a merge and return the data."""
        return _data(await MergeTransform(_ctx(cfg)).execute(inputs))

    def test_ports_validate(self):
        """Ports and validation."""
        assert len(MergeTransform.inputs()) == 4
        assert MergeTransform.outputs()[0]["name"] == "out"
        v = MergeTransform(_ctx()).validate_config
        assert v({}) == []
        assert "Invalid mode" in v({"mode": "x"})[0]
        assert v({"mode": "concat", "separator": 1}) == [
            "separator must be a string when using concat mode"
        ]
        assert v({"mode": "concat", "separator": ","}) == []

    @pytest.mark.asyncio
    async def test_modes(self):
        """object/array/concat/deep/unknown behaviours."""
        i = {"in1": {"a": 1}, "in2": 5, "in3": {"b": {"c": 1}}, "in4": {"b": {"d": 2}}}
        out = await self._run({}, i)
        assert out["a"] == 1 and out["value_1"] == 5 and out["b"] == {"d": 2}
        assert await self._run({"mode": "array"}, {"in1": [1], "in2": 2}) == [1, 2]
        assert (
            await self._run(
                {"mode": "concat", "separator": "-"}, {"in1": "a", "in2": "b"}
            )
            == "a-b"
        )
        assert await self._run({"mode": "concat"}, {"in1": [1], "in2": [2]}) == [1, 2]
        assert (
            await self._run(
                {"mode": "concat", "separator": "+"}, {"in1": "a", "in2": 2}
            )
            == "a+2"
        )
        deep = await self._run({"mode": "deep"}, i)
        assert deep["b"] == {"c": 1, "d": 2} and deep["value_1"] == 5
        assert await self._run({"mode": "wat"}, {"in1": 1, "in2": 2}) == [1, 2]

    @pytest.mark.asyncio
    async def test_edges(self):
        """Missing in1, all-None inputs and an internal failure."""
        with pytest.raises(ValueError, match="in1"):
            await self._run({}, {})
        assert await self._run({}, {"in1": None}) is None
        with pytest.raises(AttributeError):
            await self._run(
                {"mode": "concat", "separator": 5}, {"in1": "a", "in2": "b"}
            )


class TestSplit:
    """SplitTransform modes."""

    async def _run(self, cfg, data):
        """Run split and return all ports."""
        return await SplitTransform(_ctx(cfg)).execute({"in": data})

    def test_ports_validate(self):
        """Ports and validation."""
        assert len(SplitTransform.outputs()) == 4
        assert SplitTransform.inputs()[0]["name"] == "in"
        v = SplitTransform(_ctx()).validate_config
        assert v({}) == []
        assert "Invalid mode" in v({"mode": "q"})[0]
        assert v({"mode": "string"}) == ["delimiter is required for string mode"]
        assert v({"mode": "chunks", "chunkSize": 0})
        assert v({"mode": "chunks", "chunkSize": 2}) == []
        assert v({"mode": "field"}) == ["field is required for field mode"]

    def test_helpers(self):
        """Field lookup and chunking."""
        n = SplitTransform(_ctx())
        assert n._get_field_value({"a": [{"b": 1}]}, "a.0.b") == 1
        assert n._get_field_value({"a": [1]}, "a.3") is None
        assert n._get_field_value({"a": 1}, "a.b") is None
        assert n._get_field_value({"a": 1}, "z.b") is None
        assert n._chunk_list([1, 2, 3], 2) == [[1, 2], [3]]

    @pytest.mark.asyncio
    async def test_modes(self):
        """Array, string, chunks and field modes with ports."""
        r = await self._run({}, [1, 2, 3])
        assert _data(r) == [1, 2, 3] and _data(r, "count") == 3
        assert _data(r, "first") == 1 and _data(r, "last") == 3
        assert _data(await self._run({}, 5)) == [5]
        assert _data(await self._run({"mode": "string", "delimiter": ","}, "a, b")) == [
            "a",
            "b",
        ]
        assert _data(await self._run({"mode": "string", "trim": False}, "a, b")) == [
            "a",
            " b",
        ]
        assert _data(await self._run({"mode": "string"}, 5)) == ["5"]
        assert _data(
            await self._run({"mode": "chunks", "chunkSize": 2}, [1, 2, 3])
        ) == [[1, 2], [3]]
        assert _data(await self._run({"mode": "chunks", "chunkSize": 2}, "abc")) == [
            "ab",
            "c",
        ]
        assert _data(await self._run({"mode": "chunks"}, 7)) == [7]
        assert _data(
            await self._run({"mode": "field", "field": "a"}, [{"a": 1}, "x", {"a": 2}])
        ) == [1, 2]
        assert _data(
            await self._run({"mode": "field", "field": "a"}, {"a": [1, 2]})
        ) == [1, 2]
        assert _data(await self._run({"mode": "field", "field": "a"}, {"a": 1})) == [1]
        assert _data(await self._run({"mode": "field", "field": "a"}, 4)) == [4]

    @pytest.mark.asyncio
    async def test_empty_and_errors(self):
        """removeEmpty, empty first/last, unknown mode, missing input, failure."""
        r = await self._run({"removeEmpty": True}, [1, None, "", 2])
        assert _data(r) == [1, 2]
        r = await self._run({}, [])
        assert _data(r, "first") is None and _data(r, "last") is None
        assert _data(await self._run({"mode": "zz"}, [1])) == []
        with pytest.raises(ValueError, match="missing"):
            await SplitTransform(_ctx()).execute({})
        with pytest.raises(TypeError):
            await self._run({"mode": "chunks", "chunkSize": "x"}, [1])


@pytest.mark.xfail(
    strict=True,
    reason="bug: template.py _EXACT_TOKEN_RE matches '{{a}} {{b}}' as one token "
    "so preserve_type=True leaves multi-token strings unresolved",
)
@pytest.mark.asyncio
async def test_multi_token_value_preserve_type_bug():
    """Two tokens in one value should both interpolate when preserve_type is on."""
    cfg = {"assignments": [{"name": "full", "value": "{{first}} {{last}}"}]}
    res = await SetFieldsTransform(_ctx(cfg)).execute(
        {"in": {"first": "A", "last": "B"}}
    )
    assert _data(res)["full"] == "A B"
