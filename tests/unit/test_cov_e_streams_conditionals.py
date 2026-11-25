"""Coverage tests for streams conditional nodes: comparisons, logic gates,
if_then (incl. resource lookups), switch, for_each and while_loop.

Only the httpx client used for Elder API resource lookups is faked.
"""

from __future__ import annotations

import pytest

import tests.unit.conftest_worker_stubs  # noqa: F401
from apps.worker.streams.nodes.conditionals import comparisons as cmp_mod
from apps.worker.streams.nodes.conditionals import if_then as it
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
from apps.worker.streams.nodes.regex_guard import UnsafeRegexError


def _ctx(config=None, **extra):
    """Build a node context."""
    ctx = {
        "execution_id": "e",
        "playbook_id": "p",
        "node_id": "n",
        "config": config or {},
    }
    ctx.update(extra)
    return ctx


def _d(res, port):
    """Extract one port's data."""
    return res[port]["data"]


class TestComparisonCommon:
    """Shared helpers in comparisons.py."""

    def test_helpers(self):
        """Field extraction and single-wire detection."""
        assert cmp_mod._extract_field({"a": 1}, "") == {"a": 1}
        assert cmp_mod._extract_field({"a": {"b": 2}}, "a.b") == 2
        assert cmp_mod._extract_field({"a": 1}, "zz") is None
        assert cmp_mod._has_single_wire_input({"default": 1})
        assert not cmp_mod._has_single_wire_input({"x": 1})
        assert cmp_mod._get_single_wire_payload({"default": 3}) == 3
        assert cmp_mod._get_single_wire_payload({"in": 4, "default": 3}) == 4
        out = cmp_mod._branch_outputs("n", True, 5)
        assert out["true"]["data"] == 5 and out["false"]["data"] is None

    def test_ports(self):
        """Every comparison node declares ports."""
        for cls in (
            EqualsConditional,
            GreaterThanConditional,
            LessThanConditional,
            ContainsConditional,
            RegexConditional,
        ):
            assert len(cls.inputs()) == 2 and len(cls.outputs()) == 2


class TestEquals:
    """EqualsConditional wiring modes."""

    @pytest.mark.asyncio
    async def test_modes(self):
        """Two-port, config expected, single-wire with field."""
        r = await EqualsConditional(_ctx()).execute({"value": 1, "expected": 1})
        assert _d(r, "true") == 1
        r = await EqualsConditional(_ctx({"expected": "{{x}}"})).execute(
            {"value": 3, "x": 2}
        )
        assert _d(r, "false") == 3
        cfg = {"expected": 5, "field": "a"}
        r = await EqualsConditional(_ctx(cfg)).execute({"in": {"a": 5}})
        assert _d(r, "true") == {"a": 5}
        r = await EqualsConditional(_ctx()).execute({"default": 1, "expected": 1})
        assert _d(r, "true") == 1

    @pytest.mark.asyncio
    async def test_missing(self):
        """Each missing required input raises."""
        with pytest.raises(ValueError, match="expected"):
            await EqualsConditional(_ctx()).execute({"value": 1})
        with pytest.raises(ValueError, match="expected"):
            await EqualsConditional(_ctx()).execute({"in": 1})
        with pytest.raises(ValueError, match="value"):
            await EqualsConditional(_ctx()).execute({})


@pytest.mark.parametrize(
    "cls,key,ok,bad",
    [
        (GreaterThanConditional, "threshold", (5, 1), (1, 5)),
        (LessThanConditional, "threshold", (1, 5), (5, 1)),
    ],
)
class TestNumeric:
    """Greater/Less than share their structure."""

    @pytest.mark.asyncio
    async def test_modes(self, cls, key, ok, bad):
        """Two-port, config threshold, single-wire and error paths."""
        r = await cls(_ctx()).execute({"value": ok[0], key: ok[1]})
        assert _d(r, "true") == float(ok[0])
        r = await cls(_ctx({key: "{{t}}"})).execute({"value": bad[0], "t": bad[1]})
        assert _d(r, "false") == float(bad[0])
        r = await cls(_ctx({key: ok[1], "field": "v"})).execute({"in": {"v": ok[0]}})
        assert _d(r, "true") == {"v": ok[0]}
        r = await cls(_ctx({key: ok[1]})).execute({"default": ok[0]})
        assert _d(r, "true") == ok[0]
        with pytest.raises(ValueError, match="threshold"):
            await cls(_ctx()).execute({"value": 1})
        with pytest.raises(ValueError, match="threshold"):
            await cls(_ctx()).execute({"in": 1})
        with pytest.raises(ValueError, match="'value'"):
            await cls(_ctx()).execute({})
        with pytest.raises(ValueError, match="Invalid numeric"):
            await cls(_ctx()).execute({"value": "abc", key: 1})
        with pytest.raises(ValueError, match="Invalid numeric"):
            await cls(_ctx()).execute({"value": None, key: 1})


class TestContains:
    """ContainsConditional haystack types."""

    @pytest.mark.asyncio
    async def test_modes(self):
        """String, list, dict, other haystacks across wiring modes."""
        run = lambda cfg, inp: ContainsConditional(_ctx(cfg)).execute(inp)  # noqa: E731
        assert _d(await run({}, {"haystack": "abc", "needle": "b"}), "true") == "abc"
        assert _d(await run({}, {"haystack": "a1", "needle": 1}), "true") == "a1"
        assert _d(await run({}, {"haystack": [1, 2], "needle": 2}), "true") == [1, 2]
        assert _d(await run({}, {"haystack": {"k": "v"}, "needle": "v"}), "true")
        assert _d(await run({}, {"haystack": 5, "needle": 5}), "false") == 5
        assert _d(await run({"needle": "{{n}}"}, {"haystack": "xyz", "n": "y"}), "true")
        r = await run({"needle": "a", "field": "t"}, {"in": {"t": "cat"}})
        assert _d(r, "true") == {"t": "cat"}
        r = await run({}, {"default": "cat", "needle": "z"})
        assert _d(r, "false") == "cat"
        with pytest.raises(ValueError, match="needle"):
            await run({}, {"haystack": "a"})
        with pytest.raises(ValueError, match="needle"):
            await run({}, {"in": "a"})
        with pytest.raises(ValueError, match="haystack"):
            await run({}, {})


class TestRegexConditional:
    """RegexConditional wiring and error translation."""

    @pytest.mark.asyncio
    async def test_modes(self):
        """Two-port, config pattern, single-wire."""
        run = lambda cfg, inp: RegexConditional(_ctx(cfg)).execute(inp)  # noqa: E731
        assert _d(await run({}, {"text": "abc", "pattern": "^a"}), "true") == "abc"
        assert (
            _d(await run({"pattern": "{{p}}"}, {"text": "abc", "p": "^z"}), "false")
            == "abc"
        )
        r = await run({"pattern": "^c", "field": "t"}, {"in": {"t": "cat"}})
        assert _d(r, "true") == {"t": "cat"}
        r = await run({"pattern": "^c", "field": "zz"}, {"default": {"t": "cat"}})
        assert _d(r, "false") == {"t": "cat"}
        r = await run({}, {"in": "x", "pattern": "x"})
        assert _d(r, "true") == "x"
        with pytest.raises(ValueError, match="pattern"):
            await run({}, {"text": "a"})
        with pytest.raises(ValueError, match="pattern"):
            await run({}, {"in": "a"})
        with pytest.raises(ValueError, match="'text'"):
            await run({}, {})

    @pytest.mark.asyncio
    async def test_error_translation(self, monkeypatch):
        """Each regex failure type becomes ValueError."""
        node = RegexConditional(_ctx())
        for exc, msg in (
            (UnsafeRegexError("unsafe"), "unsafe"),
            (TimeoutError(), "timed out"),
            (RuntimeError("boom"), "failed"),
        ):

            async def boom(*a, _e=exc, **k):
                """Raise the parametrised error."""
                raise _e

            monkeypatch.setattr(cmp_mod, "safe_regex_search", boom)
            with pytest.raises(ValueError, match=msg):
                await node.execute({"text": "a", "pattern": "a"})
        import re

        async def bad(*a, **k):
            """Raise a regex compile error."""
            raise re.error("bad")

        monkeypatch.setattr(cmp_mod, "safe_regex_search", bad)
        with pytest.raises(ValueError, match="Invalid regex"):
            await node.execute({"text": "a", "pattern": "("})


class TestLogicGates:
    """AND / OR / NOT gates in both modes."""

    def test_ports(self):
        """Declared ports."""
        assert len(AndConditional.inputs()) == 4
        assert len(OrConditional.outputs()) == 2
        assert NotConditional.inputs()[0]["name"] == "in"

    @pytest.mark.asyncio
    async def test_wired_inputs(self):
        """Boolean evaluation of wired ports."""
        r = await AndConditional(_ctx()).execute({"in1": 1, "in2": 1, "in3": 0})
        assert _d(r, "true") is False and _d(r, "false") is True
        r = await AndConditional(_ctx()).execute({"in1": 1, "in2": 1})
        assert _d(r, "true") is True
        r = await OrConditional(_ctx()).execute({"in1": 0, "in2": 1})
        assert _d(r, "true") is True
        r = await OrConditional(_ctx()).execute({"in1": 0, "in2": 0})
        assert _d(r, "true") is False
        r = await NotConditional(_ctx()).execute({"in": 0})
        assert _d(r, "true") is True
        for gate in (AndConditional, OrConditional):
            with pytest.raises(ValueError, match="in1"):
                await gate(_ctx()).execute({})
            with pytest.raises(ValueError, match="in2"):
                await gate(_ctx()).execute({"in1": 1})
        with pytest.raises(ValueError, match="'in'"):
            await NotConditional(_ctx()).execute({})

    @pytest.mark.asyncio
    async def test_conditions(self):
        """Condition-mode routing and logic."""
        conds = [
            {"field": "a", "operator": "eq", "value": 1},
            {"field": "b", "operator": "eq", "value": 2},
            "ignored",
        ]
        pay = {"a": 1, "b": 0}
        r = await AndConditional(_ctx({"conditions": conds})).execute({"in": pay})
        assert _d(r, "false") == pay and _d(r, "true") is None
        r = await OrConditional(_ctx({"conditions": conds})).execute({"in1": pay})
        assert _d(r, "true") == pay
        r = await NotConditional(_ctx({"conditions": conds})).execute({"in": pay})
        assert _d(r, "true") == pay
        r = await NotConditional(_ctx({"conditions": conds, "logic": "or"})).execute(
            {"x": pay}
        )
        assert _d(r, "false") == pay
        r = await AndConditional(_ctx({"conditions": ["x"]})).execute({"in": 1})
        assert _d(r, "true") == 1
        r = await OrConditional(_ctx({"conditions": ["x"]})).execute({"in": 1})
        assert _d(r, "false") == 1


class FakeResp:
    """Minimal HTTP response double."""

    def __init__(self, status=200, data=None):
        """Store status and JSON payload."""
        self.status_code = status
        self._data = data

    def json(self):
        """Return the canned JSON."""
        return self._data


def _fake_client(routes):
    """Build an AsyncClient replacement answering GETs from a routes callable."""

    class C:
        """Async client double."""

        def __init__(self, *a, **k):
            """Ignore construction args."""

        async def __aenter__(self):
            """Enter context."""
            return self

        async def __aexit__(self, *e):
            """Leave context."""
            return False

        async def get(self, url, **kw):
            """Dispatch to the routing callable."""
            return routes(url, kw)

    return C


class TestIfThenHelpers:
    """Module-level helpers in if_then."""

    def test_small_helpers(self, monkeypatch):
        """get_field_value, base/token resolution, coercion, leaf walk."""
        assert it.get_field_value({"a": [{"b": 1}]}, "a.0.b") == 1
        assert it.get_field_value({"a": [1]}, "a.5") is None
        assert it.get_field_value({"a": 1}, "a.b") is None
        assert it.get_field_value("str", "a") is None
        monkeypatch.delenv("ELDER_API_URL", raising=False)
        monkeypatch.delenv("ELDER_INTERNAL_TOKEN", raising=False)
        monkeypatch.delenv("ELDER_API_TOKEN", raising=False)
        assert it._resolve_elder_api_base({}) == "http://hub-api:8080"
        monkeypatch.setenv("ELDER_API_URL", "http://e.example.com/")
        assert it._resolve_elder_api_base({}) == "http://e.example.com"
        assert (
            it._resolve_elder_api_base(
                {"global_config": {"elder_api_url": "http://g.example.com/"}}
            )
            == "http://g.example.com"
        )
        assert it._resolve_elder_auth_token({}) is None
        monkeypatch.setenv("ELDER_INTERNAL_TOKEN", "x")
        assert it._resolve_elder_auth_token({}) == "x"
        assert (
            it._resolve_elder_auth_token({"global_config": {"_elder_auth_token": "g"}})
            == "g"
        )
        assert it._coerce_comparable(True, "TRUE") == (True, True)
        assert it._coerce_comparable(True, " false ") == (True, False)
        assert it._coerce_comparable(True, "maybe") == (True, "maybe")
        assert it._coerce_comparable(3, "3") == (3, 3)
        assert it._coerce_comparable(3, "3.5") == (3, 3.5)
        assert it._coerce_comparable(3, "x") == (3, "x")
        assert it._coerce_comparable("a", "b") == ("a", "b")
        assert sorted(it._collect_leaf_values({"a": [1, {"b": 2}], "c": 3})) == [
            1,
            2,
            3,
        ]
        deep = cur = {}
        for _ in range(15):
            cur["k"] = {}
            cur = cur["k"]
        cur["v"] = 1
        assert it._collect_leaf_values(deep) == []
        assert it._collect_leaf_values({"a": 1}, _depth=11) == []
        assert len(it._collect_leaf_values(list(range(600)))) == 500


class TestFetchAssociatedResource:
    """fetch_associated_resource HTTP handling, caching and relationships."""

    @pytest.mark.asyncio
    async def test_guards_and_cache(self, monkeypatch):
        """Unknown type / blank id return None; second call is cached."""
        assert await it.fetch_associated_resource("nope", 1, {}) is None
        assert await it.fetch_associated_resource("identity", None, {}) is None
        assert await it.fetch_associated_resource("identity", " ", {}) is None
        calls = []

        def routes(url, kw):
            """Record and answer."""
            calls.append(url)
            return FakeResp(200, {"status": "Locked"})

        monkeypatch.setattr(it.httpx, "AsyncClient", _fake_client(routes))
        monkeypatch.setenv("ELDER_API_TOKEN", "tok")
        ctx = {"global_config": {}}
        r1 = await it.fetch_associated_resource("user", "a b", ctx)
        r2 = await it.fetch_associated_resource("user", "a b", ctx)
        assert r1 is r2 and r1["is_locked"] is True and len(calls) == 1
        assert "a%20b" in calls[0]

    @pytest.mark.asyncio
    async def test_status_variants(self, monkeypatch):
        """Non-200, list payload, active identity, exceptions."""
        warns = []
        monkeypatch.setattr(
            it.httpx, "AsyncClient", _fake_client(lambda u, k: FakeResp(404))
        )
        assert (
            await it.fetch_associated_resource(
                "entity", 1, {}, log_warning=warns.append
            )
            is None
        )
        assert await it.fetch_associated_resource("entity", 1, {}) is None
        assert "HTTP 404" in warns[0]
        monkeypatch.setattr(
            it.httpx, "AsyncClient", _fake_client(lambda u, k: FakeResp(200, [1]))
        )
        assert await it.fetch_associated_resource("entity", 1, {}) == {"value": [1]}
        monkeypatch.setattr(
            it.httpx,
            "AsyncClient",
            _fake_client(lambda u, k: FakeResp(200, {"is_active": False})),
        )
        assert (await it.fetch_associated_resource("identity", 1, {}))[
            "is_locked"
        ] is True
        monkeypatch.setattr(
            it.httpx,
            "AsyncClient",
            _fake_client(lambda u, k: FakeResp(200, {"is_locked": False})),
        )
        assert (await it.fetch_associated_resource("identity", 1, {}))[
            "is_locked"
        ] is False

        def boom(u, k):
            """Fail the request."""
            raise RuntimeError("net")

        monkeypatch.setattr(it.httpx, "AsyncClient", _fake_client(boom))
        assert (
            await it.fetch_associated_resource(
                "entity", 1, {}, log_warning=warns.append
            )
            is None
        )
        assert await it.fetch_associated_resource("entity", 1, {}) is None
        assert "error" in warns[-1]

    @pytest.mark.asyncio
    async def test_relationships(self, monkeypatch):
        """Outgoing and incoming dependencies are summarised."""

        def routes(url, kw):
            """Answer detail and dependency queries."""
            params = kw.get("params") or {}
            if "source_type" in params:
                return FakeResp(
                    200,
                    {
                        "items": [
                            {
                                "target_id": 2,
                                "target_type": "entity",
                                "dependency_type": "uses",
                            },
                            "junk",
                        ]
                    },
                )
            if "target_type" in params:
                return FakeResp(
                    200,
                    {
                        "items": [
                            {
                                "source_id": 3,
                                "source_type": "service",
                                "dependency_type": "calls",
                            },
                            "junk",
                        ]
                    },
                )
            return FakeResp(200, {"id": 1})

        monkeypatch.setattr(it.httpx, "AsyncClient", _fake_client(routes))
        res = await it.fetch_associated_resource(
            "user", 1, {}, include_relationships=True
        )
        rel = res["relationships"]
        assert rel["count"] == 4 and rel["related_ids"] == [2, 3]
        assert rel["related_types"] == ["entity", "service"]
        assert rel["dependency_types"] == ["uses", "calls"]
        monkeypatch.setattr(
            it.httpx,
            "AsyncClient",
            _fake_client(
                lambda u, k: (
                    FakeResp(200, {"id": 1})
                    if "dependencies" not in u
                    else FakeResp(500)
                )
            ),
        )
        res = await it.fetch_associated_resource(
            "entity", 1, {}, include_relationships=True
        )
        assert res["relationships"]["count"] == 0


class TestEvaluateCondition:
    """evaluate_condition source modes and operators."""

    @pytest.mark.asyncio
    async def test_operator_branches(self):
        """contains/not_contains coercion, errors, regex failures."""
        ev = it._eval_single_op
        assert await ev("abc", "contains", "b") is True
        assert await ev(None, "contains", None) is True
        assert await ev(123, "contains", 2) is True
        assert await ev([1, 2], "contains", 2) is True
        assert await ev("abc", "not_contains", "z") is True
        assert await ev(1, "gt", "abc") is False
        assert await ev("abc", "regex", "^a") is True
        warns = []
        import apps.worker.streams.nodes.conditionals.if_then as mod

        orig = mod.safe_regex_search
        for exc in (UnsafeRegexError("u"), TimeoutError(), RuntimeError("x")):

            async def boom(*a, _e=exc, **k):
                """Raise the parametrised error."""
                raise _e

            mod.safe_regex_search = boom
            try:
                assert await ev("a", "regex", "a", log_warning=warns.append) is False
                assert await ev("a", "regex", "a") is False
            finally:
                mod.safe_regex_search = orig
        assert len(warns) == 2

    @pytest.mark.asyncio
    async def test_input_source(self):
        """Dot path, empty field, template field, non-dict data."""
        ev = it.evaluate_condition
        d = {"a": {"b": 2}}
        assert await ev(d, {"field": "a.b", "operator": "eq", "value": "2"}, {}) is True
        assert await ev(5, {"field": "", "operator": "eq", "value": 5}, {}) is True
        c = {"field": "{{a.b}}", "operator": "gt", "value": 1}
        assert await ev(d, c, {}) is True
        assert await ev(5, {"field": "a", "operator": "is_null"}, {}) is True

    @pytest.mark.asyncio
    async def test_any_field(self):
        """Bare-string search over payload leaves and prior outputs."""
        ev = it.evaluate_condition
        d = {"x": "hello world", "y": [1, 2]}
        c = {"source": "any_field", "operator": "contains", "value": "world"}
        assert await ev(d, c, {}) is True
        c = {"field": "*", "operator": "not_contains", "value": "zzz"}
        assert await ev(d, c, {}) is True
        c = {"field": "*", "operator": "not_contains", "value": "hello"}
        assert await ev(d, c, {}) is False
        c = {"field": "*", "operator": "eq", "value": "prior"}
        assert await ev({}, c, {"node_outputs": {"n": {"v": "prior"}}}) is True
        assert await ev([], {"field": "*", "operator": "ne", "value": 1}, {}) is True
        assert await ev([], {"field": "*", "operator": "eq", "value": 1}, {}) is False

    @pytest.mark.asyncio
    async def test_resource_source(self, monkeypatch):
        """Resource lookups, fallbacks to associated_resource and missing data."""
        monkeypatch.setattr(
            it.httpx,
            "AsyncClient",
            _fake_client(lambda u, k: FakeResp(200, {"status": "active", "v": 1})),
        )
        ev = it.evaluate_condition
        cond = {
            "source": "resource",
            "resource_type": "entity",
            "resource_id": "{{rid}}",
            "field": "v",
            "operator": "eq",
            "value": 1,
        }
        assert await ev({"rid": 9}, cond, {}, {"in": {"rid": 9}}) is True
        cond2 = {
            "source": "resource",
            "field": "relationships.count",
            "operator": "eq",
            "value": 0,
        }
        ctx = {"associated_resource": {"resource_type": "entity", "resource_id": 3}}
        assert await ev({}, cond2, ctx) is True
        monkeypatch.setattr(
            it.httpx, "AsyncClient", _fake_client(lambda u, k: FakeResp(404))
        )
        assert await ev({}, {"source": "resource", "operator": "is_null"}, {}) is True
        assert (
            await ev({}, {"source": "resource", "operator": "eq", "value": 1}, {})
            is False
        )


class TestIfThenNode:
    """IfThenConditional execution and routing."""

    def test_ports(self):
        """Declared ports."""
        assert IfThenConditional.inputs()[0]["name"] == "in"
        assert len(IfThenConditional.outputs()) == 2

    @pytest.mark.asyncio
    async def test_execute(self):
        """True, false, logic or, no conditions, helper wrapper."""
        conds = [
            {"field": "a", "operator": "eq", "value": 1},
            {"field": "b", "operator": "eq", "value": 2},
        ]
        pay = {"a": 1, "b": 0}
        r = await IfThenConditional(_ctx({"conditions": conds})).execute({"in": pay})
        assert _d(r, "false") == pay and _d(r, "true") is None
        r = await IfThenConditional(_ctx({"conditions": conds, "logic": "or"})).execute(
            {"in": pay}
        )
        assert _d(r, "true") == pay
        r = await IfThenConditional(_ctx()).execute({"in": pay})
        assert _d(r, "true") == pay
        node = IfThenConditional(_ctx())
        assert node._get_field_value(pay, "a") == 1


class TestSwitch:
    """SwitchConditional routing."""

    def test_ports_validate(self):
        """Ports and validation errors."""
        assert SwitchConditional.inputs()[0]["name"] == "in"
        assert len(SwitchConditional.outputs()) == 5
        v = SwitchConditional(_ctx()).validate_config
        assert len(v({})) == 2
        assert v({"field": "a", "cases": [{"value": 1, "output": "case1"}]}) == []
        errs = v({"field": "a", "cases": [{}, {"value": 1, "output": "bad"}]})
        assert len(errs) == 4

    def test_helpers(self):
        """Field lookup and match resolution."""
        n = SwitchConditional(_ctx())
        assert n._get_field_value("x", "a") is None
        assert n._get_field_value({"a": [{"b": 1}]}, "a.0.b") == 1
        assert n._get_field_value({"a": [1]}, "a.4") is None
        assert n._get_field_value({"a": 1}, "a.b") is None
        cases = [{"value": 1, "output": "case2"}, {"value": 2}]
        assert n._find_matching_output(1, cases) == "case2"
        assert n._find_matching_output(2, cases) == "default"
        assert n._find_matching_output(9, cases) == "default"

    @pytest.mark.asyncio
    async def test_execute(self):
        """Matched and default routing; missing input."""
        cfg = {"field": "k", "cases": [{"value": "a", "output": "case3"}]}
        r = await SwitchConditional(_ctx(cfg)).execute({"in": {"k": "a"}})
        assert _d(r, "case3") == {"k": "a"} and _d(r, "default") is None
        r = await SwitchConditional(_ctx(cfg)).execute({"in": {"k": "z"}})
        assert _d(r, "default") == {"k": "z"}
        for port in ("case1", "case2", "case4"):
            cfg2 = {"field": "k", "cases": [{"value": 1, "output": port}]}
            r = await SwitchConditional(_ctx(cfg2)).execute({"in": {"k": 1}})
            assert _d(r, port) == {"k": 1}
        with pytest.raises(ValueError, match="missing"):
            await SwitchConditional(_ctx(cfg)).execute({})

    @pytest.mark.asyncio
    async def test_execute_failure(self):
        """A malformed case list is logged and re-raised."""
        cfg = {"field": "k", "cases": ["notadict"]}
        with pytest.raises(AttributeError):
            await SwitchConditional(_ctx(cfg)).execute({"in": {"k": 1}})


class TestForEach:
    """ForEachConditional iteration set-up."""

    def test_ports_validate(self):
        """Ports and validation."""
        assert ForEachConditional.inputs()[0]["name"] == "array"
        assert len(ForEachConditional.outputs()) == 3
        v = ForEachConditional(_ctx()).validate_config
        assert v({}) == []
        assert v({"field": 5}) == ["field configuration must be a string path"]

    def test_field_value(self):
        """Dotted lookups."""
        n = ForEachConditional(_ctx())
        assert n._get_field_value({"a": [{"b": 1}]}, "a.0.b") == 1
        assert n._get_field_value({"a": [1]}, "a.3") is None
        assert n._get_field_value({"a": 1}, "a.b") is None

    @pytest.mark.asyncio
    async def test_execute(self):
        """List, tuple, dict, scalar, field extraction, empty and failures."""
        run = lambda cfg, arr: ForEachConditional(_ctx(cfg)).execute({"array": arr})  # noqa: E731
        r = await run({}, [1, 2])
        assert _d(r, "item") == 1 and r["done"]["metadata"]["_iterations"] == [1, 2]
        assert r["done"]["data"] is False and _d(r, "index") == 0
        r = await run({}, (3, 4))
        assert r["done"]["metadata"]["_iterations"] == [3, 4]
        r = await run({}, {"a": 1, "b": 2})
        assert r["done"]["metadata"]["_iterations"] == [1, 2]
        r = await run({}, "solo")
        assert r["done"]["metadata"]["_iterations"] == ["solo"]
        r = await run({"field": "v"}, [{"v": 1}, {"x": 2}, {"v": 3}])
        assert r["done"]["metadata"]["_iterations"] == [1, 3]
        r = await run({}, [])
        assert _d(r, "item") is None and r["done"]["data"] is True
        with pytest.raises(ValueError, match="missing"):
            await ForEachConditional(_ctx()).execute({})

    @pytest.mark.asyncio
    async def test_failure_logged(self):
        """An internal error during extraction is re-raised."""
        node = ForEachConditional(_ctx({"field": "v"}))
        node._get_field_value = lambda *a: (_ for _ in ()).throw(RuntimeError("bad"))
        with pytest.raises(RuntimeError):
            await node.execute({"array": [1]})


class TestWhile:
    """WhileConditional loop control."""

    def test_ports_validate(self):
        """Ports and every validation branch."""
        assert WhileConditional.inputs()[0]["name"] == "in"
        assert len(WhileConditional.outputs()) == 2
        v = WhileConditional(_ctx()).validate_config
        assert v({"conditions": [{"operator": "eq"}]}) == []
        assert len(v({"conditions": ["x", {"operator": "zz"}]})) == 2
        assert v({"condition": {"field": "a"}}) == []
        assert len(v({"condition": {"operator": "zz"}})) == 2
        assert v({}) == ["At least one loop condition is required"]
        assert v({"conditions": [{}], "maxIterations": 0}) == [
            "maxIterations must be a positive integer"
        ]

    @pytest.mark.asyncio
    async def test_execute(self):
        """Loop vs done for conditions, logic or, legacy and none."""
        conds = [
            {"field": "n", "operator": "lt", "value": 3},
            {"field": "n", "operator": "eq", "value": 99},
        ]
        r = await WhileConditional(_ctx({"conditions": conds})).execute(
            {"in": {"n": 1}}
        )
        assert _d(r, "done") == {"n": 1} and _d(r, "loop") is None
        r = await WhileConditional(_ctx({"conditions": conds, "logic": "or"})).execute(
            {"in": {"n": 1}}
        )
        assert _d(r, "loop") == {"n": 1} and _d(r, "done") is None
        legacy = {"condition": {"field": "n", "operator": "lt", "value": 3}}
        r = await WhileConditional(_ctx(legacy)).execute({"in": {"n": 1}})
        assert _d(r, "loop") == {"n": 1}
        r = await WhileConditional(_ctx({"conditions": ["x"]})).execute({"in": 1})
        assert _d(r, "done") == 1
        r = await WhileConditional(_ctx()).execute({"in": 1})
        assert _d(r, "done") == 1
        assert WhileConditional(_ctx())._get_field_value({"a": 1}, "a") == 1
        with pytest.raises(ValueError, match="missing"):
            await WhileConditional(_ctx()).execute({})
