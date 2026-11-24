"""Comprehensive unit & integration tests for the Playbook Node Catalog,
live module/route validation, canvas sync, execution gating, comparisons,
templating, connector routing, and worker execution.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import jwt
import pytest
import pytest_asyncio

from apps.api.common.modules.tenant_toggle import set_module_enabled
from apps.api.modules.streams.node_catalog import (
    get_node_catalog,
    is_route_mounted,
    validate_playbook_nodes,
)
from apps.api.utils.async_utils import run_in_threadpool
from apps.worker.streams.connectors.base import ConnectorManifest
from apps.worker.streams.connectors.executor import (
    ConnectorActionExecutor,
    ConnectorExecutionError,
)
from apps.worker.streams.connectors.registry import discover_connectors
from apps.worker.streams.executor.node_registry import NodeRegistry, discover_nodes
from apps.worker.streams.executor.playbook_executor import NodeStatus, PlaybookExecutor
from apps.worker.streams.nodes.conditionals.comparisons import (
    ContainsConditional,
    EqualsConditional,
    GreaterThanConditional,
    LessThanConditional,
    RegexConditional,
)
from apps.worker.streams.nodes.template import (
    _MISSING,
    interpolate_value,
    resolve_expression,
)
from shared.jobbus import JobEnvelope


def _elder_yaml_path() -> Path:
    return (
        Path(__file__).resolve().parents[2]
        / "apps"
        / "worker"
        / "streams"
        / "connectors"
        / "manifests"
        / "elder.yaml"
    )


def _token(app, tenant_id: int, identity_id: int, scopes=None) -> str:
    scopes = scopes or ["streams:read", "streams:write", "streams:execute"]
    now = datetime.now(UTC)
    payload = {
        "sub": str(identity_id),
        "iat": now,
        "exp": now + timedelta(hours=1),
        "scope": scopes,
        "tenant": str(tenant_id),
        "identity_id": identity_id,
        "roles": ["admin"],
    }
    secret = app.config.get("JWT_SECRET_KEY") or app.config.get("SECRET_KEY")
    return jwt.encode(payload, secret, algorithm="HS256")


class TestElderManifestAndRouteAlignment:
    """Verify every Elder manifest entry fits DB limits and maps to a live route."""

    def test_all_elder_node_types_fit_db_column_limit(self):
        discover_connectors()
        manifest = ConnectorManifest.from_yaml(str(_elder_yaml_path()))
        generated_types = (
            [f"trigger_elder_{t.id}" for t in manifest.triggers]
            + [f"transform_elder_{t.id}" for t in manifest.transforms]
            + [f"action_elder_{a.id}" for a in manifest.actions]
        )
        assert len(generated_types) > 50
        too_long = [nt for nt in generated_types if len(nt) > 50]
        assert too_long == [], f"Node types exceed VARCHAR(50): {too_long}"

    def test_all_elder_endpoints_match_live_url_map(self, app):
        discover_connectors()
        manifest = ConnectorManifest.from_yaml(str(_elder_yaml_path()))
        unmounted: list[str] = []

        for transform in manifest.transforms:
            if transform.endpoint:
                if not is_route_mounted(app, transform.method, transform.endpoint):
                    unmounted.append(
                        f"transform:{transform.id} {transform.method} {transform.endpoint}"
                    )

        for action in manifest.actions:
            if action.endpoint:
                if not is_route_mounted(app, action.method, action.endpoint):
                    unmounted.append(
                        f"action:{action.id} {action.method} {action.endpoint}"
                    )

        assert unmounted == [], f"Unmounted Elder manifest endpoints: {unmounted}"

    def test_is_route_mounted_rejects_spa_catch_all(self, app):
        assert (
            is_route_mounted(app, "GET", "/api/v1/this-route-does-not-exist-xyz")
            is False
        )
        assert is_route_mounted(app, "GET", "/api/v1/streams") is True


class TestNodeCatalogAndValidation:
    """Test live per-tenant catalog gating, validation, PUT/PATCH sync & execute."""

    @pytest_asyncio.fixture(autouse=True)
    async def setup_db(self, app, test_database_url):
        if not test_database_url:
            pytest.skip("DATABASE_URL not set")
        db = app.db

        def _setup():
            now = datetime.now(UTC)
            tenant_id = db.tenants.insert(
                name="Catalog Tenant",
                slug=f"cat-{uuid.uuid4().hex[:8]}",
                is_active=True,
                created_at=now,
                updated_at=now,
            )
            email = f"catalog-user-{uuid.uuid4().hex[:8]}@test.local"
            identity_id = db.identities.insert(
                tenant_id=tenant_id,
                username=email,
                email=email,
                identity_type="human",
                auth_provider="local",
                is_active=True,
                is_superuser=False,
                mfa_enabled=False,
                must_change_password=False,
                portal_role="admin",
                full_name="Catalog User",
                created_at=now,
                updated_at=now,
            )
            stream_id = db.stream_playbooks.insert(
                tenant_id=tenant_id,
                village_id=f"test-{uuid.uuid4().hex[:24]}",
                name="Catalog Test Playbook",
                description="",
                owner_identity_id=identity_id,
                created_by_identity_id=identity_id,
                trigger_type="manual",
                is_public=False,
                is_template=False,
                is_enabled=False,
                tags=[],
                status="draft",
                execution_count=0,
                success_count=0,
                failure_count=0,
                created_at=now,
                updated_at=now,
            )
            db.commit()
            return {
                "tenant_id": tenant_id,
                "identity_id": identity_id,
                "stream_id": stream_id,
            }

        self.fixtures = await run_in_threadpool(_setup)

    @pytest.mark.asyncio
    async def test_node_catalog_endpoint_auth_and_contents(self, app, client):
        unauth = await client.get("/api/v1/streams/node-catalog")
        assert unauth.status_code == 401

        tok = _token(app, self.fixtures["tenant_id"], self.fixtures["identity_id"])
        resp = await client.get(
            "/api/v1/streams/node-catalog",
            headers={"Authorization": f"Bearer {tok}"},
        )
        assert resp.status_code == 200
        body = await resp.get_json()
        assert body["total"] >= 100
        node_types = {n["node_type"] for n in body["nodes"]}
        assert {
            "trigger_manual",
            "trigger_webhook",
            "trigger_schedule",
            "transform_set_fields",
            "conditional_if_then",
            "action_respond",
            "transform_elder_list_entities",
            "action_elder_create_issue",
        }.issubset(node_types)

    @pytest.mark.asyncio
    async def test_catalog_reflects_tenant_module_toggle_and_license(self, app):
        db = app.db
        t = self.fixtures["tenant_id"]

        from unittest.mock import patch

        def _toggle_and_fetch():
            set_module_enabled(db, app.extensions.get("module_redis"), t, "ipam", False)
            db.commit()
            with patch(
                "apps.api.modules.streams.node_catalog.module_licensed",
                side_effect=lambda _app, manifest: manifest.name != "access_reviews",
            ):
                return get_node_catalog(app, db, t)

        catalog = await run_in_threadpool(_toggle_and_fetch)

        by_type = {n["node_type"]: n for n in catalog["nodes"]}

        assert by_type["transform_elder_list_ipam_prefixes"]["enabled"] is False
        assert (
            by_type["transform_elder_list_ipam_prefixes"]["disabled_code"]
            == "MODULE_DISABLED"
        )
        assert by_type["transform_elder_list_entities"]["enabled"] is True
        assert by_type["transform_elder_list_access_reviews"]["enabled"] is False
        assert (
            by_type["transform_elder_list_access_reviews"]["disabled_code"]
            == "MODULE_UNLICENSED"
        )

    @pytest.mark.asyncio
    async def test_catalog_reflects_posthog_feature_flag(self, app):
        db = app.db
        t = self.fixtures["tenant_id"]
        discover_nodes()
        discover_connectors()
        cls = NodeRegistry.get("transform_elder_list_entities")
        original_flag = getattr(cls, "required_feature_flag", None)
        fake_ph = MagicMock()
        fake_ph.flag_enabled.return_value = False
        old_ph = app.extensions.get("posthog")
        try:
            cls.required_feature_flag = "elder.test-catalog-gate"
            app.extensions["posthog"] = fake_ph
            catalog = await run_in_threadpool(lambda: get_node_catalog(app, db, t))
            by_type = {n["node_type"]: n for n in catalog["nodes"]}
            assert by_type["transform_elder_list_entities"]["enabled"] is False
            assert (
                by_type["transform_elder_list_entities"]["disabled_code"]
                == "FLAG_DISABLED"
            )
        finally:
            cls.required_feature_flag = original_flag
            if old_ph is None:
                app.extensions.pop("posthog", None)
            else:
                app.extensions["posthog"] = old_ph

    @pytest.mark.asyncio
    async def test_validate_playbook_nodes_rules(self, app):
        db = app.db
        t = self.fixtures["tenant_id"]

        def _run_validations():
            empty = validate_playbook_nodes(app, db, t, [], [])
            cycle = validate_playbook_nodes(
                app,
                db,
                t,
                [
                    {"id": "n1", "type": "trigger_manual", "config": {}},
                    {"id": "n2", "type": "action_log", "config": {"message": "hi"}},
                ],
                [
                    {"source": "n1", "target": "n2"},
                    {"source": "n2", "target": "n1"},
                ],
            )
            unknown = validate_playbook_nodes(
                app,
                db,
                t,
                [{"id": "n1", "type": "does_not_exist_node", "config": {}}],
                [],
            )
            # show_when: transform_json operation=flatten does NOT require jsonPath
            flatten_ok = validate_playbook_nodes(
                app,
                db,
                t,
                [
                    {
                        "id": "n1",
                        "type": "transform_json",
                        "config": {"operation": "flatten"},
                    }
                ],
                [],
            )
            extract_missing = validate_playbook_nodes(
                app,
                db,
                t,
                [
                    {
                        "id": "n1",
                        "type": "transform_json",
                        "config": {"operation": "extract"},
                    }
                ],
                [],
            )
            return empty, cycle, unknown, flatten_ok, extract_missing

        empty, cycle, unknown, flatten_ok, extract_missing = await run_in_threadpool(
            _run_validations
        )
        assert empty["valid"] is True
        assert any(w["code"] == "EMPTY_PLAYBOOK" for w in empty["warnings"])

        assert cycle["valid"] is False
        assert any(e["code"] == "CYCLE_DETECTED" for e in cycle["errors"])

        assert unknown["valid"] is False
        assert any(e["code"] == "UNKNOWN_NODE_TYPE" for e in unknown["errors"])

        assert flatten_ok["valid"] is True
        assert extract_missing["valid"] is False
        assert any(
            e["code"] == "MISSING_REQUIRED_CONFIG" for e in extract_missing["errors"]
        )

    @pytest.mark.asyncio
    async def test_put_and_patch_sync_nodes_edges_and_execute_gates(self, app, client):
        t = self.fixtures["tenant_id"]
        uid = self.fixtures["identity_id"]
        sid = self.fixtures["stream_id"]
        tok = _token(app, t, uid)

        # Save valid canvas via PUT
        canvas_payload = {
            "canvas_data": {
                "nodes": [
                    {
                        "id": "trig-1",
                        "type": "stream",
                        "position": {"x": 100.4, "y": 50.9},
                        "data": {
                            "label": "Manual Start",
                            "nodeType": "trigger_manual",
                            "category": "triggers",
                            "config": {"sample_payload": {"severity": "high"}},
                        },
                    },
                    {
                        "id": "log-1",
                        "type": "stream",
                        "position": {"x": 100.0, "y": 240.0},
                        "data": {
                            "label": "Log Alert",
                            "nodeType": "action_log",
                            "category": "actions",
                            "config": {"message": "Severity={{$json.severity}}"},
                        },
                    },
                ],
                "edges": [
                    {
                        "id": "e1",
                        "source": "trig-1",
                        "target": "log-1",
                        "sourceHandle": "out",
                        "targetHandle": "in",
                    }
                ],
            }
        }
        put_resp = await client.put(
            f"/api/v1/streams/{sid}",
            json=canvas_payload,
            headers={"Authorization": f"Bearer {tok}"},
        )
        assert put_resp.status_code == 200
        put_body = await put_resp.get_json()
        assert put_body["validation"]["valid"] is True
        assert len(put_body["canvas_data"]["nodes"]) == 2

        # Verify stream_nodes & stream_edges tables synced
        def _check_synced():
            db = app.db
            sn = db(db.stream_nodes.playbook_id == sid).select()
            se = db(db.stream_edges.playbook_id == sid).select()
            return len(sn), len(se)

        sn_count, se_count = await run_in_threadpool(_check_synced)
        assert sn_count == 2
        assert se_count == 1

        # Dry run returns validation summary
        dry_resp = await client.post(
            f"/api/v1/streams/{sid}/execute",
            json={"dry_run": True, "input_data": {"severity": "high"}},
            headers={"Authorization": f"Bearer {tok}"},
        )
        assert dry_resp.status_code == 200
        dry_body = await dry_resp.get_json()
        assert dry_body["dry_run"] is True
        assert dry_body["valid"] is True

        # Save via PATCH with an IPAM node while IPAM disabled -> execute returns 403 MODULE_DISABLED
        await run_in_threadpool(
            lambda: (
                set_module_enabled(
                    app.db, app.extensions.get("module_redis"), t, "ipam", False
                ),
                app.db.commit(),
            )
        )
        patch_resp = await client.patch(
            f"/api/v1/streams/{sid}",
            json={
                "canvas_data": {
                    "nodes": [
                        {
                            "id": "ipam-1",
                            "type": "stream",
                            "position": {"x": 50, "y": 50},
                            "data": {
                                "label": "List Prefixes",
                                "nodeType": "transform_elder_list_ipam_prefixes",
                                "category": "transforms",
                                "config": {},
                            },
                        }
                    ],
                    "edges": [],
                }
            },
            headers={"Authorization": f"Bearer {tok}"},
        )
        assert patch_resp.status_code == 200
        patch_body = await patch_resp.get_json()
        assert patch_body["validation"]["has_disabled_modules"] is True

        val_resp = await client.post(
            f"/api/v1/streams/{sid}/validate",
            headers={"Authorization": f"Bearer {tok}"},
        )
        assert val_resp.status_code == 200
        assert (await val_resp.get_json())["has_disabled_modules"] is True

        blocked_exec = await client.post(
            f"/api/v1/streams/{sid}/execute",
            json={"input_data": {}},
            headers={"Authorization": f"Bearer {tok}"},
        )
        assert blocked_exec.status_code == 403
        blocked_body = await blocked_exec.get_json()
        assert blocked_body["code"] == "MODULE_DISABLED"
        assert "ipam" in blocked_body["disabled_modules"]

    @pytest.mark.asyncio
    async def test_execute_enqueues_short_lived_jwt_in_transient_payload(
        self, app, client
    ):
        t = self.fixtures["tenant_id"]
        uid = self.fixtures["identity_id"]
        sid = self.fixtures["stream_id"]
        tok = _token(app, t, uid)

        # Put empty or valid canvas
        await client.put(
            f"/api/v1/streams/{sid}",
            json={
                "canvas_data": {
                    "nodes": [
                        {
                            "id": "trig-1",
                            "type": "stream",
                            "position": {"x": 0, "y": 0},
                            "data": {
                                "label": "Manual Start",
                                "nodeType": "trigger_manual",
                                "category": "triggers",
                                "config": {},
                            },
                        }
                    ],
                    "edges": [],
                }
            },
            headers={"Authorization": f"Bearer {tok}"},
        )

        enqueued_calls: list[tuple] = []

        class _FakeJobBus:
            def __init__(self, _rc):
                pass

            async def ensure_group(self, group: str):
                return None

            async def enqueue(self, group, job_type, payload, **kwargs):
                enqueued_calls.append((group, job_type, payload, kwargs))
                return "msg-1"

        fake_redis = MagicMock()
        fake_redis.close = AsyncMock()

        from apps.worker.config.settings import settings

        with (
            patch.object(settings, "redis_url", "redis://fake-test:6379/0"),
            patch("redis.asyncio.from_url", return_value=fake_redis),
            patch("shared.jobbus.JobBus", _FakeJobBus),
        ):
            resp = await client.post(
                f"/api/v1/streams/{sid}/execute",
                json={"input_data": {"hello": "world"}},
                headers={"Authorization": f"Bearer {tok}"},
            )

        assert resp.status_code == 202
        assert len(enqueued_calls) == 1
        _grp, _jtype, job_payload, _kw = enqueued_calls[0]
        auth_token = job_payload.get("auth_token")
        assert isinstance(auth_token, str) and len(auth_token) > 20

        secret = app.config.get("JWT_SECRET_KEY") or app.config.get("SECRET_KEY")
        decoded = jwt.decode(
            auth_token, secret, algorithms=["HS256"], options={"verify_aud": False}
        )
        assert decoded["tenant"] == str(t)
        assert decoded["iss"] == app.config.get("JWT_ISSUER", "elder-api")
        assert decoded["aud"] == app.config.get("JWT_AUDIENCE", "elder-api")
        assert 850 <= (decoded["exp"] - decoded["iat"]) <= 910


class TestWorkerUpgrades:
    """Test template redaction, single-wire comparisons, branch skip, connectors & worker."""

    def test_template_redacts_internal_elder_auth_token(self):
        ctx = {
            "global_config": {
                "_elder_auth_token": "secret-jwt-should-never-leak",
                "trigger_data": {"id": 99},
            }
        }
        assert resolve_expression("_elder_auth_token", ctx, {}) is _MISSING
        assert (
            interpolate_value("tok={{_elder_auth_token}} id={{$trigger.id}}", ctx, {})
            == "tok={{_elder_auth_token}} id=99"
        )

    @pytest.mark.asyncio
    async def test_single_wire_comparisons_with_templates(self):
        in_payload = {
            "in": {
                "severity": "critical",
                "score": 85,
                "title": "Database CPU overload",
                "host": "prod-db-01",
            }
        }

        eq_out = await EqualsConditional(
            {"config": {"field": "severity", "expected": "critical"}}
        ).execute(in_payload)
        assert eq_out["true"]["data"]["severity"] == "critical"
        assert eq_out["false"]["data"] is None

        gt_out = await GreaterThanConditional(
            {"config": {"field": "score", "threshold": 80}}
        ).execute(in_payload)
        assert gt_out["true"]["data"]["score"] == 85

        lt_out = await LessThanConditional(
            {"config": {"field": "score", "threshold": 50}}
        ).execute(in_payload)
        assert lt_out["false"]["data"]["score"] == 85
        assert lt_out["true"]["data"] is None

        contains_out = await ContainsConditional(
            {"config": {"field": "title", "needle": "CPU"}}
        ).execute(in_payload)
        assert contains_out["true"]["data"]["title"] == "Database CPU overload"

        regex_out = await RegexConditional(
            {"config": {"field": "host", "pattern": r"^prod-.*-\d+$"}}
        ).execute(in_payload)
        assert regex_out["true"]["data"]["host"] == "prod-db-01"

    @pytest.mark.asyncio
    async def test_playbook_executor_skips_inactive_branch_when_enabled(self):
        discover_nodes()
        executor = PlaybookExecutor("exec-skip", "pb-skip")
        playbook = {
            "nodes": [
                {
                    "id": "t1",
                    "type": "trigger_manual",
                    "label": "Start",
                    "config": {"sample_payload": {"status": "closed"}},
                },
                {
                    "id": "c1",
                    "type": "conditional_equals",
                    "label": "Check Open",
                    "config": {"field": "status", "expected": "open"},
                },
                {
                    "id": "true_branch",
                    "type": "action_log",
                    "label": "True Log",
                    "config": {"message": "was open"},
                },
                {
                    "id": "false_branch",
                    "type": "action_log",
                    "label": "False Log",
                    "config": {"message": "was closed"},
                },
            ],
            "edges": [
                {
                    "source": "t1",
                    "target": "c1",
                    "sourceHandle": "out",
                    "targetHandle": "in",
                },
                {
                    "source": "c1",
                    "target": "true_branch",
                    "sourceHandle": "true",
                    "targetHandle": "in",
                },
                {
                    "source": "c1",
                    "target": "false_branch",
                    "sourceHandle": "false",
                    "targetHandle": "in",
                },
            ],
            "config": {"skip_inactive_branches": True},
        }
        res = await executor.execute(playbook)
        assert res.success is True
        assert res.node_results["true_branch"].status == NodeStatus.SKIPPED
        assert res.node_results["false_branch"].status == NodeStatus.SUCCESS

    @pytest.mark.asyncio
    async def test_playbook_executor_blocks_disabled_module_loudly(self):
        discover_nodes()
        discover_connectors()
        executor = PlaybookExecutor("exec-mod", "pb-mod")
        res = await executor.execute(
            {
                "nodes": [
                    {
                        "id": "ip1",
                        "type": "transform_elder_list_ipam_prefixes",
                        "label": "List Prefixes",
                        "config": {},
                    }
                ],
                "edges": [],
                "config": {"enabled_modules": {"ipam": False}},
            }
        )
        assert res.success is False
        assert res.node_results["ip1"].status == NodeStatus.FAILED
        assert "ipam" in (res.node_results["ip1"].error or "")

    @pytest.mark.asyncio
    async def test_connector_executor_elder_trusted_path_and_ssrf(self):
        executor = ConnectorActionExecutor()

        # Non-elder SSRF target MUST still be blocked
        with pytest.raises(
            ConnectorExecutionError, match="SSRF|blocked|private|loopback"
        ):
            await executor.execute_action(
                connector_id="webhook",
                action_id="post",
                endpoint="/steal",
                method="POST",
                request_body_template=None,
                config_schema=[],
                config={},
                inputs={},
                variables={},
                base_url="http://169.254.169.254/latest/meta-data",
            )

        # Elder path traversal / scheme injection MUST be rejected
        with pytest.raises(ConnectorExecutionError):
            await executor.execute_action(
                connector_id="elder",
                action_id="bad",
                endpoint="/api/v1/entities/../secrets",
                method="GET",
                request_body_template=None,
                config_schema=[],
                config={},
                inputs={},
                variables={"_elder_auth_token": "tok"},
                base_url="http://api:5000",
            )

        # Path param containing '..' traversal is ALSO rejected loudly
        with pytest.raises(ConnectorExecutionError):
            await executor.execute_action(
                connector_id="elder",
                action_id="bad_param",
                endpoint="/api/v1/entities/{{id}}",
                method="DELETE",
                request_body_template=None,
                config_schema=[],
                config={"id": "42/../../etc"},
                inputs={},
                variables={"_elder_auth_token": "tok"},
                base_url="http://api:5000",
                path_params=("id",),
            )

        # Trusted Elder relative route sends Bearer JWT + URL-encodes path params + handles 204
        captured: dict = {}

        class _FakeResponse:
            status_code = 204
            content = b""
            text = ""
            headers = {}

            def raise_for_status(self):
                return None

        class _FakeClient:
            def __init__(self, **kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return None

            async def request(self, method, url, **kwargs):
                captured["method"] = method
                captured["url"] = url
                captured["headers"] = kwargs.get("headers") or {}
                captured["params"] = kwargs.get("params")
                return _FakeResponse()

        with patch(
            "apps.worker.streams.connectors.executor.httpx.AsyncClient", _FakeClient
        ):
            out = await executor.execute_action(
                connector_id="elder",
                action_id="delete_entity",
                endpoint="/api/v1/entities/{{id}}",
                method="DELETE",
                request_body_template=None,
                config_schema=[],
                config={"id": "42/sub"},
                inputs={},
                variables={"_elder_auth_token": "jwt-123"},
                base_url="http://elder-api:5000",
                path_params=("id",),
            )

        assert captured["method"] == "DELETE"
        assert captured["url"] == "http://elder-api:5000/api/v1/entities/42%2Fsub"
        assert captured["headers"]["Authorization"] == "Bearer jwt-123"
        assert out == {}

    @pytest.mark.asyncio
    async def test_handle_streams_falls_back_to_canvas_json_and_records_node_type(
        self, app, test_database_url
    ):
        if not test_database_url:
            pytest.skip("DATABASE_URL not set")
        from apps.worker.jobs.registry import handle_streams

        db = app.db

        def _seed():
            now = datetime.now(UTC)
            tid = db.tenants.insert(
                name="Worker Tenant",
                slug=f"wk-{uuid.uuid4().hex[:8]}",
                is_active=True,
                created_at=now,
                updated_at=now,
            )
            uid = db.identities.insert(
                tenant_id=tid,
                username=f"wk-{uuid.uuid4().hex[:6]}@test.local",
                email=f"wk-{uuid.uuid4().hex[:6]}@test.local",
                identity_type="human",
                auth_provider="local",
                is_active=True,
                is_superuser=False,
                mfa_enabled=False,
                must_change_password=False,
                portal_role="admin",
                full_name="Worker User",
                created_at=now,
                updated_at=now,
            )
            pid = db.stream_playbooks.insert(
                tenant_id=tid,
                village_id=f"test-{uuid.uuid4().hex[:24]}",
                name="Version Fallback Stream",
                description="",
                owner_identity_id=uid,
                created_by_identity_id=uid,
                trigger_type="manual",
                is_public=False,
                is_template=False,
                is_enabled=False,
                tags=[],
                status="draft",
                execution_count=0,
                success_count=0,
                failure_count=0,
                created_at=now,
                updated_at=now,
            )
            # Only populate stream_versions (leave stream_nodes empty)
            db.stream_versions.insert(
                tenant_id=tid,
                playbook_id=pid,
                version_number=1,
                created_by_identity_id=uid,
                nodes_json=[
                    {
                        "id": "node-start",
                        "type": "stream",
                        "data": {
                            "label": "Start",
                            "nodeType": "trigger_manual",
                            "category": "triggers",
                            "config": {"sample_payload": {"env": "prod"}},
                        },
                    },
                    {
                        "id": "node-resp",
                        "type": "stream",
                        "data": {
                            "label": "Respond",
                            "nodeType": "action_respond",
                            "category": "actions",
                            "config": {"summary": "env={{$json.env}}"},
                        },
                    },
                ],
                edges_json=[
                    {
                        "source": "node-start",
                        "target": "node-resp",
                        "sourceHandle": "out",
                        "targetHandle": "in",
                    }
                ],
                canvas_json={
                    "nodes": [
                        {
                            "id": "node-start",
                            "type": "stream",
                            "data": {
                                "label": "Start",
                                "nodeType": "trigger_manual",
                                "category": "triggers",
                                "config": {"sample_payload": {"env": "prod"}},
                            },
                        },
                        {
                            "id": "node-resp",
                            "type": "stream",
                            "data": {
                                "label": "Respond",
                                "nodeType": "action_respond",
                                "category": "actions",
                                "config": {"summary": "env={{$json.env}}"},
                            },
                        },
                    ],
                    "edges": [
                        {
                            "source": "node-start",
                            "target": "node-resp",
                            "sourceHandle": "out",
                            "targetHandle": "in",
                        }
                    ],
                },
                change_summary="v1",
                created_at=now,
                updated_at=now,
            )
            eid = str(uuid.uuid4())
            db.stream_executions.insert(
                tenant_id=tid,
                playbook_id=pid,
                execution_id=eid,
                status="queued",
                trigger_type="manual",
                triggered_by_identity_id=uid,
                input_json={},
                started_at=now,
                created_at=now,
                updated_at=now,
            )
            db.commit()
            return tid, pid, eid

        tid, pid, eid = await run_in_threadpool(_seed)
        env = JobEnvelope(
            job_id=str(uuid.uuid4()),
            job_type="execute_playbook",
            payload={
                "execution_id": eid,
                "playbook_id": pid,
                "tenant_id": tid,
                "auth_token": "should-be-redacted-from-logs",
            },
            enqueued_at=datetime.now(UTC).isoformat(),
            tenant_id=tid,
        )
        res = await handle_streams(env)
        assert res["status"] == "success"

        def _read_exec():
            rows = app.db(app.db.stream_node_executions.execution_id == eid).select()
            return {r.node_id: r.node_type for r in rows}

        nt_map = await run_in_threadpool(_read_exec)
        assert nt_map["node-start"] == "trigger_manual"
        assert nt_map["node-resp"] == "action_respond"
