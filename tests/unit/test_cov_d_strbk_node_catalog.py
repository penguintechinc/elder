"""Tests for the streams node catalog and live playbook validator.

Pure helpers run directly; availability and validation run against the real
Quart app and Postgres.  Only license / feature-flag clients (external
boundaries) are replaced where a specific denial branch must be forced.
"""

# flake8: noqa: E501

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from apps.api.modules.streams import node_catalog as nc
from apps.api.utils.async_utils import run_in_threadpool

NC = "apps.api.modules.streams.node_catalog"


class TestPureHelpers:
    """Stateless helpers in the catalog module."""

    def test_category_to_stage(self):
        """Categories map to the three palette stages."""
        assert nc._category_to_stage("triggers") == "triggers"
        assert nc._category_to_stage("transforms") == "process"
        assert nc._category_to_stage("conditionals") == "process"
        assert nc._category_to_stage("actions") == "actions"

    def test_module_display_name(self):
        """Names resolve from installed manifests, the registry, or title-case."""
        assert nc._module_display_name("core", None) == "Core Platform"
        assert (
            nc._module_display_name("x", {"x": SimpleNamespace(title="X Title")})
            == "X Title"
        )
        known = next(iter(nc._MODULES_BY_NAME))
        assert nc._module_display_name(known, None) == nc._MODULES_BY_NAME[known].title
        assert nc._module_display_name("some_mod", {}) == "Some Mod"

    def test_serialize_config_schema(self):
        """Dict items pass through; attribute items are normalised."""
        item = SimpleNamespace(
            field="f",
            label="L",
            type="select",
            required=1,
            default=None,
            placeholder=None,
            description=None,
            options=["a", {"value": "b", "label": "B"}],
        )
        bare = SimpleNamespace(
            field="g",
            label="G",
            type="text",
            required=0,
            default=1,
            placeholder="p",
            description="d",
        )
        out = nc._serialize_config_schema([{"field": "raw"}, item, bare])
        assert out[0] == {"field": "raw"}
        assert out[1]["options"] == [
            {"value": "a", "label": "a"},
            {"value": "b", "label": "B"},
        ]
        assert out[1]["required"] is True and out[1]["placeholder"] == ""
        assert out[2]["options"] is None and out[2]["show_when"] is None
        assert nc._serialize_config_schema(None) == []

    def test_strip_placeholder_and_route_matching(self):
        """Template placeholders never match literal route segments."""
        for seg in ("{{x}}", "{x}", "<x>"):
            assert nc._strip_placeholder(seg)
        assert not nc._strip_placeholder("plain")
        m = nc._match_route_segments
        assert m("/api/v1/a/<int:id>", "/api/v1/a/5?x=1")
        assert m("/api/v1/a/<int:id>", "/api/v1/a/{{id}}")
        assert not m("/api/v1/a/b", "/api/v1/a/{{id}}")
        assert not m("/api/v1/a/<int:id>", "/api/v1/a")
        assert not m("/api/v1/a/b", "/api/v1/a/c")
        assert not m("/api/v1/a", "/api/v1/a/b")
        assert m("/api/v1/files/<path:p>", "/api/v1/files/x/y")
        assert not m("/api/v1/files/<path:p>", "/api/v1/files")
        assert not m("/api/v1/a/<id>", "/api/v1/a/")

    def test_is_route_mounted(self):
        """Mounted checks honour method, prefix, SPA fallback and missing maps."""
        assert nc.is_route_mounted(object(), "GET", "") is True
        assert nc.is_route_mounted(SimpleNamespace(), "GET", "/api/v1/x") is True
        rules = [
            SimpleNamespace(
                endpoint="web.spa_catch_all", rule="/api/<path:p>", methods={"GET"}
            ),
            SimpleNamespace(endpoint="other", rule="/health", methods={"GET"}),
            SimpleNamespace(endpoint="a", rule="/api/v1/x", methods={"POST"}),
            SimpleNamespace(endpoint="b", rule="/api/v1/y", methods=None),
            SimpleNamespace(endpoint="c", rule="/api/v1/z", methods={"GET"}),
        ]
        app = SimpleNamespace(url_map=SimpleNamespace(iter_rules=lambda: iter(rules)))
        assert nc.is_route_mounted(app, "post", "/api/v1/x")
        assert not nc.is_route_mounted(app, "GET", "/api/v1/x")
        assert not nc.is_route_mounted(app, "GET", "/api/v1/y")
        assert not nc.is_route_mounted(app, "GET", "/health")
        assert nc.is_route_mounted(app, None, "/api/v1/z")

    def test_show_field_and_empty_required(self):
        """show_when operators and empty-value detection."""
        s = nc._should_show_field
        assert (
            s({}, {})
            and s({"show_when": {"nofield": 1}}, {})
            and s({"show_when": "x"}, {})
        )
        assert s({"show_when": {"field": "a", "equals": 1}}, {"a": 1})
        assert not s({"show_when": {"field": "a", "equals": 1}}, {"a": 2})
        assert s({"show_when": {"field": "a", "not_equals": 1}}, {"a": 2})
        assert not s({"show_when": {"field": "a", "not_equals": 1}}, {"a": 1})
        assert s({"show_when": {"field": "a", "in": [1, 2]}}, {"a": 2})
        assert not s({"show_when": {"field": "a", "in": [1]}}, {"a": 9})
        assert s({"show_when": {"field": "a", "in": "notlist"}}, {})
        e = nc._is_empty_required
        assert e(None) and e("  ") and e([]) and e({})
        assert not e("x") and not e(0) and not e([1])

    def test_extract_node_parts(self):
        """Node id/type/label/config are read from flat or nested shapes."""
        nid, nt, label, cfg = nc._extract_node_parts(
            {"id": 7, "data": {"nodeType": "t", "label": "L", "config": {"a": 1}}}
        )
        assert (nid, nt, label, cfg) == ("7", "t", "L", {"a": 1})
        nid, nt, label, cfg = nc._extract_node_parts(
            {"node_id": "n", "node_type": "x", "config": "bad", "data": "bad"}
        )
        assert (nid, nt, label, cfg) == ("n", "x", "x", {})
        assert nc._extract_node_parts({})[2] == ""


class TestAvailability:
    """Branches of the per-node availability evaluation."""

    def _eval(self, app, **kw):
        """Call the evaluator with defaults for the unused arguments."""
        args = dict(
            required_module=None,
            required_license_feature=None,
            required_feature_flag=None,
            endpoint=None,
            method=None,
        )
        args.update(kw)
        return nc._evaluate_node_availability(app, None, None, **args)

    def test_installed_modules_paths(self):
        """Missing, unlicensed and enabled installed modules."""
        manifest = SimpleNamespace(
            title="M", default_enabled=True, license_feature="lf"
        )
        app = SimpleNamespace(extensions={"elder_modules": {"m": manifest}})
        assert self._eval(app, required_module="m2")[1] == "MODULE_NOT_INSTALLED"
        with patch(f"{NC}.module_licensed", return_value=False):
            assert self._eval(app, required_module="m")[1] == "MODULE_UNLICENSED"
        with (
            patch(f"{NC}.module_licensed", return_value=True),
            patch(f"{NC}.is_bypass_domain", return_value=False),
        ):
            assert self._eval(app, required_module="m")[0] is True
            lc = SimpleNamespace(check_feature=lambda f: False)
            app.extensions["license_client"] = lc
            res = self._eval(app, required_module="m", required_license_feature="feat")
            assert res[1] == "MODULE_UNLICENSED" and "feat" in res[2]
            app.extensions["license_client"] = SimpleNamespace(
                check_feature=lambda f: True
            )
            assert (
                self._eval(app, required_module="m", required_license_feature="feat")[0]
                is True
            )
        with (
            patch(f"{NC}.module_licensed", return_value=True),
            patch(f"{NC}.is_bypass_domain", return_value=True),
        ):
            assert (
                self._eval(app, required_module="m", required_license_feature="feat")[0]
                is True
            )

    def test_env_resolved_modules(self):
        """Without an installed-module map, the environment decides."""
        app = SimpleNamespace(extensions={})
        with patch(f"{NC}.resolve_enabled", return_value=[]):
            assert self._eval(app, required_module="zzz")[1] == "MODULE_NOT_INSTALLED"
        manifest = SimpleNamespace(name="m", title="M", default_enabled=False)
        with (
            patch(f"{NC}.resolve_enabled", return_value=[manifest]),
            patch(f"{NC}.module_licensed", return_value=True),
        ):
            assert self._eval(app, required_module="m")[0] is True

    def test_tenant_toggle(self):
        """A per-tenant disable surfaces MODULE_DISABLED."""
        manifest = SimpleNamespace(
            title="M", default_enabled=True, license_feature=None
        )
        app = SimpleNamespace(
            extensions={"elder_modules": {"m": manifest}, "module_redis": None}
        )
        with (
            patch(f"{NC}.module_licensed", return_value=True),
            patch(f"{NC}.is_module_enabled", return_value=False),
        ):
            res = nc._evaluate_node_availability(
                app,
                object(),
                3,
                required_module="m",
                required_license_feature=None,
                required_feature_flag=None,
                endpoint=None,
                method=None,
            )
        assert res[1] == "MODULE_DISABLED" and "tenant 3" in res[2]

    def test_core_license_and_flags_and_routes(self):
        """Core nodes still gate on license feature, flag and mounted route."""
        app = SimpleNamespace(
            extensions={
                "license_client": SimpleNamespace(check_feature=lambda f: False)
            }
        )
        with patch(f"{NC}.is_bypass_domain", return_value=False):
            assert (
                self._eval(app, required_license_feature="f")[1] == "MODULE_UNLICENSED"
            )
        with patch(f"{NC}.is_bypass_domain", return_value=True):
            assert self._eval(app, required_license_feature="f")[0] is True
        app2 = SimpleNamespace(extensions={})
        with patch(f"{NC}.flag_enabled", return_value=False):
            assert self._eval(app2, required_feature_flag="fl")[1] == "FLAG_DISABLED"
        with patch(f"{NC}.flag_enabled", return_value=True):
            assert self._eval(app2, required_feature_flag="fl")[0] is True
        ph = SimpleNamespace(flag_enabled=lambda f, d, default=False: d == "global")
        assert (
            self._eval(
                SimpleNamespace(extensions={"posthog": ph}), required_feature_flag="fl"
            )[0]
            is True
        )
        empty = SimpleNamespace(
            extensions={}, url_map=SimpleNamespace(iter_rules=lambda: iter([]))
        )
        res = self._eval(empty, endpoint="/api/v1/none", method="post")
        assert res[1] == "ROUTE_NOT_MOUNTED" and "POST" in res[2]


@pytest.mark.asyncio
class TestCatalogAndValidation:
    """Catalog build and validator over the real app and database."""

    async def test_catalog_shape(self, app):
        """The catalog lists nodes, modules and operators for a tenant."""
        cat = await run_in_threadpool(lambda: nc.get_node_catalog(app, app.db, 1))
        assert cat["total"] == len(cat["nodes"]) > 0
        assert cat["modules"][0]["name"] == "core" and cat["operators"]
        assert {n["source"] for n in cat["nodes"]} <= {"builtin", "elder"}
        assert cat["enabled_count"] <= cat["total"]

    async def test_validator_graph_errors(self, app):
        """Empty, duplicate, unknown, dangling and cyclic graphs are reported."""
        db = app.db

        def run(nodes, edges):
            """Validate for tenant 1."""
            return nc.validate_playbook_nodes(app, db, 1, nodes, edges)

        empty = await run_in_threadpool(lambda: run([], []))
        assert empty["valid"] and empty["warnings"][0]["code"] == "EMPTY_PLAYBOOK"
        cat = await run_in_threadpool(lambda: nc.get_node_catalog(app, db, 1))
        ok_nodes = [
            n
            for n in cat["nodes"]
            if n["enabled"] and not any(f.get("required") for f in n["config_schema"])
        ]
        assert ok_nodes, "expected at least one enabled node without required config"
        t = ok_nodes[0]["node_type"]
        nodes = [
            {"id": "a", "type": t},
            {"id": "a", "type": t},
            {"id": "", "type": t},
            {"id": "u", "type": "definitely_unknown"},
            "garbage",
        ]
        res = await run_in_threadpool(
            lambda: run(nodes, ["bad", {"source": "a", "target": "zz"}])
        )
        codes = {e["code"] for e in res["errors"]}
        assert {
            "DUPLICATE_NODE_ID",
            "INVALID_NODE_ID",
            "UNKNOWN_NODE_TYPE",
            "DANGLING_EDGE",
        } <= codes
        assert res["valid"] is False
        cyc = await run_in_threadpool(
            lambda: run(
                [{"id": "a", "type": t}, {"id": "b", "node_type": t}],
                [
                    {"source": "a", "target": "b"},
                    {"source_node_id": "b", "target_node_id": "a"},
                ],
            )
        )
        assert "CYCLE_DETECTED" in {e["code"] for e in cyc["errors"]}

    async def test_validator_required_config_and_disabled(self, app):
        """Missing required fields, wired ports and disabled modules are surfaced."""
        db = app.db
        cat = await run_in_threadpool(lambda: nc.get_node_catalog(app, db, 1))
        req = next(
            (
                n
                for n in cat["nodes"]
                if n["enabled"] and any(f.get("required") for f in n["config_schema"])
            ),
            None,
        )
        assert req is not None
        field = next(f for f in req["config_schema"] if f.get("required"))["field"]
        res = await run_in_threadpool(
            lambda: nc.validate_playbook_nodes(
                app, db, 1, [{"id": "r", "type": req["node_type"], "config": {}}], []
            )
        )
        assert any(
            e["code"] == "MISSING_REQUIRED_CONFIG" and e["field"] == field
            for e in res["errors"]
        )
        wired = await run_in_threadpool(
            lambda: nc.validate_playbook_nodes(
                app,
                db,
                1,
                [
                    {"id": "r", "type": req["node_type"], "config": {}},
                    {"id": "s", "type": req["node_type"], "config": {}},
                ],
                [{"source": "s", "target": "r", "targetHandle": field}],
            )
        )
        assert not any(
            e["code"] == "MISSING_REQUIRED_CONFIG"
            and e["node_id"] == "r"
            and e["field"] == field
            for e in wired["errors"]
        )
        # force every node to read as disabled to cover the disabled branches
        disabled = [n for n in cat["nodes"]]
        for n in disabled:
            n["enabled"] = False
            n["disabled_code"] = "MODULE_DISABLED"
            n["disabled_reason"] = "off"
        with patch(f"{NC}.get_node_catalog", return_value={"nodes": disabled}):
            res = await run_in_threadpool(
                lambda: nc.validate_playbook_nodes(
                    app, db, 1, [{"id": "r", "type": req["node_type"]}], []
                )
            )
        assert res["has_disabled_modules"] and res["disabled_modules"]
        # node known to the registry but absent from the catalog is evaluated live
        with patch(f"{NC}.get_node_catalog", return_value={"nodes": []}):
            res = await run_in_threadpool(
                lambda: nc.validate_playbook_nodes(
                    app, db, 1, [{"id": "r", "type": req["node_type"]}], []
                )
            )
        assert isinstance(res["valid"], bool)

    async def test_validator_config_validation_error(self, app):
        """A node class whose validation raises is reported, not propagated."""
        db = app.db
        cat = await run_in_threadpool(lambda: nc.get_node_catalog(app, db, 1))
        t = next(n["node_type"] for n in cat["nodes"] if n["enabled"])
        node_cls = nc.NodeRegistry.get(t)
        with patch.object(
            node_cls, "validate_config", side_effect=RuntimeError("kaput")
        ):
            res = await run_in_threadpool(
                lambda: nc.validate_playbook_nodes(
                    app, db, 1, [{"id": "x", "type": t}], []
                )
            )
        assert any(
            e["code"] == "CONFIG_VALIDATION_ERROR" and "kaput" in e["message"]
            for e in res["errors"]
        )
        with patch.object(
            node_cls, "validate_config", return_value=["bad thing", "hidden_f missing"]
        ):
            res = await run_in_threadpool(
                lambda: nc.validate_playbook_nodes(
                    app, db, 1, [{"id": "x", "type": t}], []
                )
            )
        assert any(
            e["code"] == "INVALID_CONFIG" and "bad thing" in e["message"]
            for e in res["errors"]
        )
