"""Coverage tests for streams connector executor, node generator and registry.

Only the httpx client and DNS resolution (true external boundaries) are faked;
manifests are real dataclasses and generated nodes run through the real
executor.
"""

from __future__ import annotations

import sys
import types
from uuid import uuid4

import httpx
import pytest

import tests.unit.conftest_worker_stubs  # noqa: F401
from apps.worker.streams.connectors import executor as cx
from apps.worker.streams.connectors import node_generator as ng
from apps.worker.streams.connectors import registry as creg
from apps.worker.streams.connectors.base import (
    ActionDefinition,
    ConfigField,
    ConnectorManifest,
    PortDefinition,
    TransformDefinition,
    TriggerDefinition,
)
from apps.worker.streams.executor import node_registry as nr


@pytest.fixture
def registry_guard():
    """Snapshot and restore the global node registry around a test."""
    snap = dict(nr.NodeRegistry._registry)
    init = nr.NodeRegistry._initialized
    yield nr.NodeRegistry
    nr.NodeRegistry._registry.clear()
    nr.NodeRegistry._registry.update(snap)
    nr.NodeRegistry._initialized = init


def F(field, type="string", **kw):
    """Build a ConfigField quickly."""
    return ConfigField(field=field, type=type, label=field, **kw)


class TestPathValidation:
    """Elder relative-path validation."""

    @pytest.mark.parametrize(
        "path",
        [
            "",
            "/other/x",
            "/api/v1/x://y",
            "/api/v1//x@host",
            "/api/v1/a\\b",
            "/api/v1/a b",
            "/api/v1/../x",
            "/api/v1/%2e%2e/x",
        ],
    )
    def test_rejected(self, path):
        """Unsafe paths raise ConnectorExecutionError."""
        with pytest.raises(cx.ConnectorExecutionError):
            cx._validate_elder_relative_path(path)

    def test_accepted(self):
        """A plain /api/v1 path passes."""
        assert cx._validate_elder_relative_path("/api/v1/entities/3") is None

    def test_scheme_netloc_branch(self, monkeypatch):
        """urlparse reporting a netloc is still rejected."""
        fake = types.SimpleNamespace(scheme="", netloc="h")
        monkeypatch.setattr(cx, "urlparse", lambda p: fake)
        with pytest.raises(cx.ConnectorExecutionError, match="scheme/host"):
            cx._validate_elder_relative_path("/api/v1/ok")


class TestHelpers:
    """Template context and endpoint parameter name helpers."""

    def test_context_strips_token(self):
        """Transient auth token never reaches the template context."""
        ctx = cx._build_template_context(
            {},
            {"_elder_auth_token": "x", "v": 1},
            {"c": 2},
            {"global_config": {"g": 3}},
        )
        assert ctx["global_config"] == {"g": 3, "v": 1} and ctx["config"] == {"c": 2}
        assert cx._build_template_context({}, {}, {})["config"] == {}

    def test_param_names(self):
        """config./input./bare names are extracted, $ expressions ignored."""
        names = cx._extract_endpoint_param_names(
            "/a/{{config.id.x}}/{{input.b}}/{{c.d}}/{{$json.z}}"
        )
        assert names == {"id", "b", "c"}
        assert cx._extract_endpoint_param_names("") == set()


class TestCoerce:
    """_coerce_field_value for every field type."""

    def test_variables_and_types(self):
        """Interpolation toggles and json/number/boolean/multiselect coercion."""
        c = cx._coerce_field_value
        assert c(F("a"), None, {}, {}) is None
        ctx, inp = {}, {"in": {"n": 5, "o": {"k": 1}}}
        assert c(F("a", supports_variables=True), "x{{n}}", ctx, inp) == "x5"
        assert c(F("a", "number", supports_variables=True), "{{n}}", ctx, inp) == 5
        assert c(F("a", "json", supports_variables=True), "{{o}}", ctx, inp) == {"k": 1}
        assert c(F("a", "json", supports_variables=True), {"z": "{{n}}"}, ctx, inp) == {
            "z": 5
        }
        assert c(F("a", "json"), '{"x": 1}', ctx, inp) == {"x": 1}
        assert c(F("a", "array"), "  ", ctx, inp) is None
        assert c(F("a", "object"), {"d": 1}, ctx, inp) == {"d": 1}
        with pytest.raises(cx.ConnectorExecutionError, match="valid JSON"):
            c(F("a", "json"), "{bad", ctx, inp)
        assert c(F("a", "multiselect"), '["a","b"]', ctx, inp) == ["a", "b"]
        assert c(F("a", "multiselect"), "a, b", ctx, inp) == ["a", "b"]
        assert c(F("a", "multiselect"), '{"a":1}', ctx, inp) == ['{"a":1}']
        assert c(F("a", "multiselect"), " ", ctx, inp) is None
        assert c(F("a", "multiselect"), [], ctx, inp) is None
        assert c(F("a", "multiselect", required=True), [], ctx, inp) == []
        assert c(F("a", "multiselect"), ["x"], ctx, inp) == ["x"]
        assert c(F("a", "integer"), "7", ctx, inp) == 7
        assert c(F("a", "number"), "1.5", ctx, inp) == 1.5
        assert c(F("a", "number"), "  ", ctx, inp) is None
        assert c(F("a", "number"), 3, ctx, inp) == 3
        with pytest.raises(cx.ConnectorExecutionError, match="valid number"):
            c(F("a", "number"), "abc", ctx, inp)
        assert c(F("a", "boolean"), "Yes", ctx, inp) is True
        assert c(F("a", "checkbox"), "no", ctx, inp) is False
        assert c(F("a", "boolean"), 1, ctx, inp) is True
        assert c(F("a"), "", ctx, inp) is None
        assert c(F("a", required=True), "", ctx, inp) == ""
        assert c(F("a"), "plain", ctx, inp) == "plain"


class TestExecutorBuilders:
    """Interpolation, body and query building."""

    def setup_method(self):
        """Create an executor instance."""
        self.ex = cx.ConnectorActionExecutor()

    def test_interpolate(self):
        """Values, endpoints (encoded, missing) and dicts."""
        ex = self.ex
        assert ex._interpolate_value(5, {}, {}, {}) == 5
        assert ex._interpolate_value("a{{x}}", {"x": 1}, {}, {}) == "a1"
        assert ex._interpolate_endpoint("", {}, {}, {}) == ""
        assert (
            ex._interpolate_endpoint("/a/{{id}}", {"id": "x y"}, {}, {}) == "/a/x%20y"
        )
        for bad in ({}, {"id": None}, {"id": ""}):
            with pytest.raises(cx.ConnectorExecutionError, match="Missing required"):
                ex._interpolate_endpoint("/a/{{id}}", bad, {}, {})
        out = ex._interpolate_dict({"k": "{{n}}"}, {"in": {"n": 3}}, {}, {})
        assert out == {"k": 3}

    def test_body(self):
        """Body construction in every branch."""
        ex = self.ex
        schema = (F("name"), F("n", "number"), F("skip"), F("empty"))
        cfg = {"name": "x", "n": "3", "skip": "s", "empty": ""}
        assert ex._build_request_body("/e", "GET", "", schema, cfg, {}, {}) is None
        body = ex._build_request_body(
            "/e/{{skip}}",
            "POST",
            "",
            schema,
            cfg,
            {},
            {},
            query_params=("q",),
            path_params=("p",),
        )
        assert body == {"name": "x", "n": 3}
        assert ex._build_request_body(
            "/e", "POST", '{"t": "{{name}}"}', schema, cfg, {"in": {"name": "z"}}, {}
        ) == {"t": "z"}
        body = ex._build_request_body("/e", "POST", "{bad", schema, cfg, {}, {})
        assert body["name"] == "x"
        assert ex._build_request_body("/e", "DELETE", "", schema, {}, {}, {}) is None
        assert ex._build_request_body(
            "/e", "DELETE", "", schema, {"name": "a"}, {}, {}
        ) == {"name": "a"}
        assert ex._build_request_body(
            "/e", "POST", "", (), {}, {"in": {"a": 1}}, {}
        ) == {"a": 1}
        assert ex._build_request_body("/e", "POST", "", (), {}, {"in": 5}, {}) == {
            "data": 5
        }
        assert ex._build_request_body("/e", "POST", "", (), {}, {}, {}) is None

    def test_query(self):
        """Query parameter selection rules."""
        ex = self.ex
        schema = (F("a"), F("b", "number"), F("p"))
        cfg = {"a": "1", "b": "2", "p": "x", "free": "{{v}}"}
        q = ex._build_query_params("/e/{{p}}", "GET", schema, cfg, {"v": 9}, {})
        assert q == {"a": "1", "b": 2}
        q = ex._build_query_params(
            "/e",
            "POST",
            schema,
            cfg,
            {"v": 9},
            {},
            query_params=("a", "free", "p", "gone"),
            path_params=("p",),
        )
        assert q == {"a": "1", "free": 9}
        assert ex._build_query_params("/e", "POST", schema, cfg, {}, {}) is None
        assert ex._build_query_params("/e", "GET", schema, {"a": ""}, {}, {}) is None


class TestTargetResolution:
    """URL/header resolution and SSRF enforcement."""

    def setup_method(self):
        """Create an executor instance."""
        self.ex = cx.ConnectorActionExecutor()

    def test_elder_relative(self, monkeypatch):
        """Trusted Elder routing, token and API-key headers, bad config."""
        r = self.ex._resolve_target_url_and_headers
        for k in ("ELDER_API_URL", "ELDER_TOKEN", "ELDER_API_KEY"):
            monkeypatch.delenv(k, raising=False)
        with pytest.raises(cx.ConnectorExecutionError, match="ELDER_API_URL"):
            r("elder", "op", "/api/v1/x", {}, "")
        url, h = r(
            "elder",
            "op",
            "/api/v1/x",
            {"_elder_auth_token": "t"},
            "http://h.example.com/",
        )
        assert (
            url == "http://h.example.com/api/v1/x" and h["Authorization"] == "Bearer t"
        )
        monkeypatch.setenv("ELDER_API_KEY", "k")
        monkeypatch.setenv("ELDER_API_URL", "https://env.example.com")
        url, h = r("elder", "op", "/api/v1/x", {}, "")
        assert url.startswith("https://env.example.com") and h == {"X-API-Key": "k"}
        monkeypatch.setenv("ELDER_TOKEN", "e")
        _, h = r("elder", "op", "/api/v1/x", None, "")
        assert h == {"Authorization": "Bearer e"}
        with pytest.raises(cx.ConnectorExecutionError):
            r("elder", "op", "/not-api", {}, "http://h.example.com")

    def test_external_ssrf(self, monkeypatch):
        """External URLs go through the SSRF guard; blocks surface as errors."""
        r = self.ex._resolve_target_url_and_headers
        seen = []
        monkeypatch.setattr(cx, "guard_ssrf", lambda u: seen.append(u))
        assert (
            r("x", "op", "https://a.example.com/p", {}, "")[0]
            == "https://a.example.com/p"
        )
        assert (
            r("x", "op", "/p/q", {}, "https://b.example.com/")[0]
            == "https://b.example.com/p/q"
        )
        assert (
            r("elder", "op", "https://c.example.com/p", {}, "")[0]
            == "https://c.example.com/p"
        )
        assert len(seen) == 3

        def block(u):
            """Block every URL."""
            raise ValueError("internal")

        monkeypatch.setattr(cx, "guard_ssrf", block)
        with pytest.raises(cx.ConnectorExecutionError, match="SSRF"):
            r("x", "op", "/p", {}, "https://b.example.com")

    def test_parse_response(self):
        """204, empty, JSON and text bodies."""
        p = cx.ConnectorActionExecutor._parse_response

        class R:
            """Response double."""

            def __init__(self, status=200, content=b"x", text="x", js=None, err=False):
                """Store fields."""
                self.status_code, self.content, self.text = status, content, text
                self._js, self._err = js, err

            def json(self):
                """Return JSON or raise ValueError."""
                if self._err:
                    raise ValueError("no")
                return self._js

        assert p(R(204)) == {}
        assert p(R(content=b"")) == {}
        assert p(R(text="  ")) == {}
        assert p(R(js={"a": 1})) == {"a": 1}
        assert p(R(err=True, text="hello")) == {"text": "hello"}


class FakeClient:
    """httpx.AsyncClient double replaying a script."""

    script: list = []
    calls: list = []

    def __init__(self, *a, **k):
        """Ignore construction args."""

    async def __aenter__(self):
        """Enter context."""
        return self

    async def __aexit__(self, *e):
        """Leave context."""
        return False

    async def request(self, **kw):
        """Replay the next scripted response or exception."""
        FakeClient.calls.append(kw)
        item = FakeClient.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


class Resp:
    """Response double supporting raise_for_status."""

    def __init__(self, status=200, js=None):
        """Store status and JSON."""
        self.status_code = status
        self._js = js if js is not None else {"ok": True}
        self.content = b"x"
        self.text = "x"

    def json(self):
        """Return JSON."""
        return self._js

    def raise_for_status(self):
        """Raise HTTPStatusError for 4xx/5xx."""
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("e", request=None, response=self)


@pytest.fixture
def http(monkeypatch):
    """Install the FakeClient and a permissive SSRF guard."""
    FakeClient.script, FakeClient.calls = [], []
    monkeypatch.setattr(cx.httpx, "AsyncClient", FakeClient)
    monkeypatch.setattr(cx, "guard_ssrf", lambda u: None)
    return FakeClient


class TestExecuteHttp:
    """execute_action / execute_transform end to end with a fake client."""

    @pytest.mark.asyncio
    async def test_action_success_and_errors(self, http):
        """Success sends params/body/headers; HTTP and generic errors wrap."""
        ex = cx.ConnectorActionExecutor()
        schema = (F("name"), F("q"))
        kw = dict(
            connector_id="x",
            action_id="a",
            endpoint="/p/{{id}}",
            method="POST",
            request_body_template="",
            config_schema=schema,
            config={"name": "n", "q": "1", "id": "7"},
            inputs={},
            variables={},
            base_url="https://h.example.com",
            query_params=("q",),
        )
        http.script = [Resp(200, {"r": 1})]
        assert await ex.execute_action(**kw) == {"r": 1}
        c = http.calls[-1]
        assert c["url"] == "https://h.example.com/p/7" and c["json"] == {"name": "n"}
        assert c["params"] == {"q": "1"} and "headers" not in c
        http.script = [Resp(500)]
        with pytest.raises(cx.ConnectorExecutionError, match="HTTP 500"):
            await ex.execute_action(**kw)
        http.script = [httpx.ConnectError("down")]
        with pytest.raises(cx.ConnectorExecutionError, match="failed: down"):
            await ex.execute_action(**kw)
        with pytest.raises(cx.ConnectorExecutionError, match="Missing required"):
            await ex.execute_action(**dict(kw, config={}))

    @pytest.mark.asyncio
    async def test_elder_headers_sent(self, http, monkeypatch):
        """Elder calls carry bearer headers and no body for GET."""
        monkeypatch.setenv("ELDER_API_URL", "http://hub.example.com")
        ex = cx.ConnectorActionExecutor()
        http.script = [Resp(200, [1])]
        out = await ex.execute_action(
            "elder",
            "a",
            "/api/v1/x",
            "GET",
            "",
            (),
            {},
            {},
            {"_elder_auth_token": "t"},
            "",
        )
        assert out == [1] and http.calls[-1]["headers"] == {"Authorization": "Bearer t"}
        assert "json" not in http.calls[-1]

    @pytest.mark.asyncio
    async def test_transform(self, http):
        """Endpointless transforms pass through; endpoint ones call HTTP."""
        ex = cx.ConnectorActionExecutor()
        assert (
            await ex.execute_transform("x", "t", "", "GET", (), {}, {"in": 5}, {}, "")
            == 5
        )
        assert await ex.execute_transform("x", "t", "", "GET", (), {}, {}, {}, "") == {}
        http.script = [Resp(200, {"t": 1})]
        out = await ex.execute_transform(
            "x",
            "t",
            "/look",
            "GET",
            (F("a"),),
            {"a": "1"},
            {},
            {},
            "https://h.example.com",
        )
        assert out == {"t": 1} and http.calls[-1]["params"] == {"a": "1"}


def _manifest(cid=None, **kw):
    """Build a manifest with one trigger, action and transform."""
    cid = cid or ("cove" + uuid4().hex[:6])
    trig = TriggerDefinition(
        id="t",
        name="Trig",
        description="d",
        outputs=(PortDefinition("pr"), PortDefinition("issue")),
        config_schema=(F("need", required=True),),
        required_module="mod",
    )
    trig_single = TriggerDefinition(id="t2", name="Trig2", description="d")
    act = ActionDefinition(
        id="a",
        name="Act",
        description="d",
        endpoint="/do",
        method="POST",
        inputs=(PortDefinition("in"),),
        outputs=(PortDefinition("res"),),
        config_schema=(F("need", required=True),),
    )
    act_plain = ActionDefinition(id="a2", name="Act2", description="d", endpoint="/do2")
    tr = TransformDefinition(
        id="x",
        name="Tr",
        description="d",
        endpoint="/look",
        inputs=(PortDefinition("in"),),
        outputs=(PortDefinition("o"),),
        config_schema=(F("need", required=True),),
    )
    tr_plain = TransformDefinition(id="x2", name="Tr2", description="d")
    base = dict(
        id=cid,
        name="Cov Conn",
        description="d",
        base_url_env="COV_E_BASE",
        default_url="https://default.example.com",
        triggers=(trig, trig_single),
        actions=(act, act_plain),
        transforms=(tr, tr_plain),
    )
    base.update(kw)
    return ConnectorManifest(**base)


class TestNodeGenerator:
    """Generated trigger/action/transform node classes."""

    def test_base_url(self, monkeypatch):
        """Resolution order for generic and elder connectors."""
        m = _manifest()
        monkeypatch.delenv("COV_E_BASE", raising=False)
        assert ng.resolve_connector_base_url(m) == "https://default.example.com"
        monkeypatch.setenv("COV_E_BASE", "https://env.example.com")
        assert ng.resolve_connector_base_url(m) == "https://env.example.com"
        assert (
            ng.resolve_connector_base_url(_manifest(base_url_env=""))
            == "https://default.example.com"
        )
        e = _manifest(cid="elder", base_url_env="COV_E_ELDER")
        monkeypatch.delenv("ELDER_API_URL", raising=False)
        monkeypatch.delenv("COV_E_ELDER", raising=False)
        fake_mod = types.ModuleType("apps.worker.config.settings")
        fake_mod.settings = types.SimpleNamespace(
            elder_api_url="http://settings.example.com"
        )
        monkeypatch.setitem(sys.modules, "apps.worker.config.settings", fake_mod)
        assert ng.resolve_connector_base_url(e) == "http://settings.example.com"
        fake_mod.settings = types.SimpleNamespace(elder_api_url="")
        assert ng.resolve_connector_base_url(e) == "https://default.example.com"
        monkeypatch.delitem(sys.modules, "apps.worker.config.settings")
        monkeypatch.setitem(sys.modules, "apps.worker.config", None)
        assert ng.resolve_connector_base_url(e) == "https://default.example.com"
        monkeypatch.setenv("COV_E_ELDER", "http://alt.example.com")
        assert ng.resolve_connector_base_url(e) == "http://alt.example.com"
        monkeypatch.setenv("ELDER_API_URL", "http://primary.example.com")
        assert ng.resolve_connector_base_url(e) == "http://primary.example.com"
        monkeypatch.delenv("ELDER_API_URL")
        monkeypatch.delenv("COV_E_ELDER")
        assert (
            ng.resolve_connector_base_url(_manifest(cid="elder", base_url_env=""))
            == "https://default.example.com"
        )

    def test_ports(self):
        """Port conversion helpers."""
        p = PortDefinition("n", "string", "desc", False)
        assert ng._port_to_node_input(p) == {
            "name": "n",
            "description": "desc",
            "required": False,
            "data_type": "string",
        }
        assert ng._port_to_node_output(p)["data_type"] == "string"

    @pytest.mark.asyncio
    async def test_trigger_node(self):
        """Trigger class metadata, validation and event routing."""
        m = _manifest()
        cls = ng.create_trigger_node(m.id, m.triggers[0], m)
        assert cls.node_type == f"trigger_{m.id}_t" and cls.category == "triggers"
        assert cls.inputs() == [] and [o["name"] for o in cls.outputs()] == [
            "pr",
            "issue",
        ]
        ctx = {
            "node_id": "n",
            "config": {},
            "global_config": {"trigger_data": {"event_type": "pr", "x": 1}},
        }
        node = cls(ctx)
        assert node.validate_config({}) == ["Required field 'need' is missing"]
        assert node.validate_config({"need": 1}) == []
        res = await node.execute({})
        assert res["out"]["data"]["x"] == 1 and res["pr"]["data"]["x"] == 1
        node = cls(
            {
                "node_id": "n",
                "trigger_data": {"event_type": "weird"},
                "global_config": {},
            }
        )
        assert "weird" in (await node.execute({}))
        node = cls({"node_id": "n", "global_config": {}})
        assert (await node.execute({}))["out"]["data"] == {}
        single = ng.create_trigger_node(m.id, m.triggers[1], m)
        assert single.outputs()[0]["name"] == "out"
        res = await single(
            {"node_id": "n", "global_config": {"trigger_data": [1]}}
        ).execute({})
        assert res["out"]["data"] == [1]

    @pytest.mark.asyncio
    async def test_action_node(self, http, monkeypatch):
        """Action class metadata, default ports, execution and failure."""
        monkeypatch.delenv("COV_E_BASE", raising=False)
        m = _manifest()
        cls = ng.create_action_node(m.id, m.actions[0], m)
        assert cls.node_type == f"action_{m.id}_a" and cls.category == "actions"
        assert cls.outputs()[0]["name"] == "res"
        plain = ng.create_action_node(m.id, m.actions[1], m)
        assert plain.inputs()[0]["name"] == "in" and plain.outputs()[0]["name"] == "out"
        node = cls({"node_id": "n", "config": {"need": "1"}, "global_config": {}})
        assert node.validate_config({}) and not node.validate_config({"need": 1})
        http.script = [Resp(200, {"v": 1})]
        res = await node.execute({"in": {"z": 1}})
        assert res["out"]["data"] == {"v": 1}
        assert http.calls[-1]["url"] == "https://default.example.com/do"
        http.script = [Resp(500)]
        with pytest.raises(cx.ConnectorExecutionError):
            await node.execute({})

    @pytest.mark.asyncio
    async def test_transform_node(self, http, monkeypatch):
        """Transform class: API call, passthrough and failure."""
        monkeypatch.delenv("COV_E_BASE", raising=False)
        m = _manifest()
        cls = ng.create_transform_node(m.id, m.transforms[0], m)
        assert cls.node_type == f"transform_{m.id}_x" and cls.category == "transforms"
        plain = ng.create_transform_node(m.id, m.transforms[1], m)
        assert plain.inputs()[0]["name"] == "in" and plain.outputs()[0]["name"] == "out"
        node = cls({"node_id": "n", "config": {"need": "1"}, "global_config": {}})
        assert node.validate_config({}) == ["Required field 'need' is missing"]
        http.script = [Resp(200, {"t": 2})]
        assert (await node.execute({}))["out"]["data"] == {"t": 2}
        pnode = plain({"node_id": "n", "config": {}, "global_config": {}})
        assert (await pnode.execute({"in": {"p": 1}}))["out"]["data"] == {"p": 1}
        assert pnode.validate_config({}) == []
        http.script = [Resp(404)]
        with pytest.raises(cx.ConnectorExecutionError):
            await node.execute({})

    def test_generate_and_failures(self, registry_guard, monkeypatch):
        """All nodes register; creation failures are counted out."""
        m = _manifest()
        assert ng.generate_nodes_from_connector(m) == 6
        assert registry_guard.is_registered(f"action_{m.id}_a")
        m2 = _manifest()

        def boom(*a, **k):
            """Fail node creation."""
            raise RuntimeError("x")

        monkeypatch.setattr(ng, "create_trigger_node", boom)
        monkeypatch.setattr(ng, "create_action_node", boom)
        monkeypatch.setattr(ng, "create_transform_node", boom)
        assert ng.generate_nodes_from_connector(m2) == 0


class TestDiscoverConnectors:
    """Registry-level manifest discovery."""

    @pytest.fixture(autouse=True)
    def _reset(self, registry_guard, monkeypatch):
        """Reset the idempotency flag and restore after."""
        monkeypatch.setattr(creg, "_CONNECTORS_INITIALIZED", False)

    def test_idempotent(self, monkeypatch, tmp_path):
        """Second call after init returns zero."""
        monkeypatch.setattr(creg, "__file__", str(tmp_path / "registry.py"))
        assert creg.discover_connectors() == 0
        assert creg.discover_connectors() == 0

    def test_empty_dir(self, monkeypatch, tmp_path):
        """A manifests dir without YAML files returns zero."""
        (tmp_path / "manifests").mkdir()
        monkeypatch.setattr(creg, "__file__", str(tmp_path / "registry.py"))
        assert creg.discover_connectors() == 0

    def test_loads_and_failures(self, monkeypatch, tmp_path):
        """A good manifest generates nodes; a malformed one is reported."""
        d = tmp_path / "manifests"
        d.mkdir()
        cid = "cove" + uuid4().hex[:6]
        (d / "good.yaml").write_text(
            "connector:\n"
            f"  id: {cid}\n  name: Good\n  description: d\n"
            "  connection:\n    default_url: https://g.example.com\n"
            "  actions:\n    - id: a\n      name: A\n      description: d\n      endpoint: /x\n"
        )
        (d / "bad.yaml").write_text("connector:\n  name: missing id\n")
        monkeypatch.setattr(creg, "__file__", str(tmp_path / "registry.py"))
        assert creg.discover_connectors() == 1
        assert nr.NodeRegistry.is_registered(f"action_{cid}_a")

    def test_real_manifests_load(self, registry_guard):
        """The shipped manifests all parse and generate nodes."""
        assert creg.discover_connectors() > 0
