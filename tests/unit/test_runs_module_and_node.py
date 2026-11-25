"""Unit tests for Elder Runs backend module, Alembic migration 047, and action_run stream node."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import sqlalchemy as sa

from alembic.migration import MigrationContext
from alembic.operations import Operations
from apps.api.auth.jwt_handler import SERVICE_WORKER_ALLOWED_SCOPES
from apps.api.modules import MODULES
from apps.api.modules.runs import (
    EXECUTION_STATUSES,
    SUPPORTED_RUNTIMES,
    TERMINAL_EXECUTION_STATUSES,
    TRIGGER_TYPES,
    RunExecution,
    RunFunction,
    RunFunctionVersion,
)
from apps.api.modules.streams.node_catalog import BUILTIN_UI_META
from apps.worker.streams.executor.node_registry import NodeRegistry, discover_nodes
from apps.worker.streams.nodes.actions import RunFunctionAction

REPO_ROOT = Path(__file__).resolve().parents[2]
MIGRATION_PATH = REPO_ROOT / "alembic" / "versions" / "047_runs_serverless_functions.py"


def _load_migration_module():
    """Dynamically load Alembic migration 047 for structural and execution tests."""
    spec = importlib.util.spec_from_file_location("mig_047_runs", MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _make_response(status_code: int, payload: dict[str, Any] | str) -> MagicMock:
    """Create a lightweight mock httpx.Response."""
    resp = MagicMock()
    resp.status_code = status_code
    if isinstance(payload, dict):
        resp.json.return_value = payload
        resp.text = str(payload)
    else:
        resp.text = payload
        resp.json.side_effect = ValueError("not json")
    return resp


class TestRunsModuleAndRegistry:
    """Verify Runs runtime constants, module manifest, and SQLAlchemy models."""

    def test_supported_runtimes_exact_set(self) -> None:
        assert SUPPORTED_RUNTIMES == {"nodejs", "rust", "python", "golang", "ruby"}
        assert EXECUTION_STATUSES == {
            "pending",
            "running",
            "completed",
            "failed",
            "timed_out",
            "cancelled",
        }
        assert TERMINAL_EXECUTION_STATUSES == {
            "completed",
            "failed",
            "timed_out",
            "cancelled",
        }
        assert TRIGGER_TYPES == {"manual", "schedule", "stream"}

    def test_runs_module_registered_in_manifest(self) -> None:
        runs_manifests = [m for m in MODULES if m.name == "runs"]
        assert len(runs_manifests) == 1
        manifest = runs_manifests[0]
        assert manifest.scopes == ("runs:read", "runs:write", "runs:invoke")
        assert manifest.table_prefix == "run_"
        assert "apps.api.modules.runs.models.runs" in manifest.models_import

        blueprints = manifest.blueprints()
        assert len(blueprints) == 1
        bp, prefix = blueprints[0]
        assert bp.name == "runs"
        assert prefix == "/api/v1/runs"

    def test_svc_runworker_allowed_scopes(self) -> None:
        assert "svc-runworker" in SERVICE_WORKER_ALLOWED_SCOPES
        assert {
            "runs:read",
            "runs:write",
            "runs:invoke",
        } <= SERVICE_WORKER_ALLOWED_SCOPES["svc-runworker"]

    def test_sqlalchemy_models_columns(self) -> None:
        fn_cols = set(RunFunction.__table__.columns.keys())
        assert {
            "id",
            "tenant_id",
            "village_id",
            "name",
            "description",
            "runtime",
            "code",
            "entrypoint",
            "timeout_seconds",
            "memory_mb",
            "env_vars_json",
            "schedule_cron",
            "execution_namespace",
            "worker_cluster",
            "is_enabled",
            "created_by_identity_id",
            "created_at",
            "updated_at",
        } <= fn_cols

        ver_cols = set(RunFunctionVersion.__table__.columns.keys())
        assert {
            "id",
            "tenant_id",
            "function_id",
            "version_number",
            "code",
            "runtime",
            "entrypoint",
            "created_by_identity_id",
            "created_at",
        } <= ver_cols

        exec_cols = set(RunExecution.__table__.columns.keys())
        assert {
            "id",
            "tenant_id",
            "execution_id",
            "function_id",
            "status",
            "trigger_type",
            "triggered_by_identity_id",
            "worker_id",
            "input_json",
            "output_json",
            "stdout_log",
            "stderr_log",
            "exit_code",
            "error_message",
            "started_at",
            "completed_at",
            "duration_ms",
            "created_at",
        } <= exec_cols


class TestActionRunRegistrationAndCatalog:
    """Verify action_run node registration and BUILTIN_UI_META schema."""

    def test_action_run_registered_in_node_registry(self) -> None:
        discover_nodes()
        node_cls = NodeRegistry.get("action_run")
        assert node_cls is RunFunctionAction
        assert node_cls.node_type == "action_run"
        assert node_cls.category == "actions"
        assert node_cls.name == "CodeRun"
        assert node_cls.required_module == "runs"

    @pytest.mark.asyncio
    async def test_runs_api_max_timeout_is_300_seconds(self) -> None:
        from quart import Quart

        from apps.api.modules.runs.routes.runs import (
            MAX_TIMEOUT_SECONDS,
            _validate_timeout,
        )

        assert MAX_TIMEOUT_SECONDS == 300
        app = Quart(__name__)
        async with app.app_context():
            val_ok, err_ok = _validate_timeout(300)
            assert val_ok == 300
            assert err_ok is None

            val_bad, err_bad = _validate_timeout(301)
            assert val_bad is None
            assert err_bad is not None
            resp_bad, status_bad = err_bad
            assert status_bad == 400
            body_bad = await resp_bad.get_json()
            assert "300" in body_bad["error"]

            val_old, err_old = _validate_timeout(3600)
            assert val_old is None
            assert err_old is not None
            assert err_old[1] == 400

    def test_builtin_ui_meta_action_run_schema(self) -> None:
        assert "action_run" in BUILTIN_UI_META
        meta = BUILTIN_UI_META["action_run"]
        assert meta["stage"] == "actions"
        assert meta["subcategory"] == "Serverless Runs"
        assert meta["icon"] == "Terminal"
        assert meta["color"] == "#06b6d4"
        assert meta["required_module"] == "runs"

        config_fields = {f["field"]: f for f in meta["config_schema"]}
        assert {"function_id", "payload", "wait_for_result", "timeout_seconds"} <= set(
            config_fields.keys()
        )
        assert config_fields["function_id"]["required"] is True
        assert config_fields["wait_for_result"]["default"] is True
        assert config_fields["timeout_seconds"]["default"] == 60

        output_fields = {f["name"] for f in meta["output_schema"]}
        assert {
            "execution_id",
            "status",
            "exit_code",
            "output",
            "stdout",
            "stderr",
            "error_message",
        } <= output_fields


class TestRunFunctionActionExecute:
    """Verify RunFunctionAction.execute() invoke, polling, and error handling."""

    @pytest.mark.asyncio
    async def test_invoke_and_poll_until_completed(self) -> None:
        ctx = {
            "execution_id": "stream-exec-1",
            "node_id": "node-run-1",
            "config": {
                "function_id": 42,
                "wait_for_result": True,
                "timeout_seconds": 5,
                "poll_interval": 0.001,
            },
            "global_config": {"elder_api_url": "http://elder-api.test:8000"},
            "variables": {"_elder_auth_token": "tok-test"},
        }
        node = RunFunctionAction(ctx)

        mock_client = AsyncMock()
        mock_client.__aenter__.return_value = mock_client
        mock_client.post.return_value = _make_response(
            202,
            {
                "execution_id": "exec-uuid-1",
                "execution": {"execution_id": "exec-uuid-1", "status": "pending"},
            },
        )
        mock_client.get.side_effect = [
            _make_response(200, {"execution_id": "exec-uuid-1", "status": "running"}),
            _make_response(
                200,
                {
                    "execution_id": "exec-uuid-1",
                    "status": "completed",
                    "exit_code": 0,
                    "output_data": {"sum": 7},
                    "stdout_log": "ok\n",
                    "stderr_log": "",
                    "error_message": None,
                },
            ),
        ]

        with patch(
            "apps.worker.streams.nodes.actions.run_function.httpx.AsyncClient",
            return_value=mock_client,
        ):
            outputs = await node.execute({"in": {"data": {"a": 3, "b": 4}}})

        mock_client.post.assert_awaited_once_with(
            "http://elder-api.test:8000/api/v1/runs/42/invoke",
            headers={"Authorization": "Bearer tok-test"},
            json={"trigger_type": "stream", "payload": {"a": 3, "b": 4}},
        )
        assert mock_client.get.await_count == 2
        assert outputs["out"]["data"] == {
            "execution_id": "exec-uuid-1",
            "status": "completed",
            "exit_code": 0,
            "output": {"sum": 7},
            "stdout": "ok\n",
            "stderr": "",
            "error_message": None,
        }
        assert outputs["output"]["data"] == {"sum": 7}
        assert outputs["status"]["data"] == "completed"
        assert outputs["exit_code"]["data"] == 0

    @pytest.mark.asyncio
    async def test_invoke_and_poll_failed_execution(self) -> None:
        ctx = {
            "execution_id": "stream-exec-2",
            "node_id": "node-run-2",
            "config": {
                "function_id": 7,
                "payload": '{"key": "val"}',
                "wait_for_result": True,
                "timeout_seconds": 5,
                "poll_interval": 0.001,
            },
            "global_config": {"elder_api_url": "http://elder-api.test:8000"},
        }
        node = RunFunctionAction(ctx)

        mock_client = AsyncMock()
        mock_client.__aenter__.return_value = mock_client
        mock_client.post.return_value = _make_response(
            202, {"execution_id": "exec-fail-1"}
        )
        mock_client.get.return_value = _make_response(
            200,
            {
                "execution_id": "exec-fail-1",
                "status": "failed",
                "exit_code": 1,
                "output_data": None,
                "stdout_log": "",
                "stderr_log": "Traceback: boom",
                "error_message": "Process exited with code 1",
            },
        )

        with patch(
            "apps.worker.streams.nodes.actions.run_function.httpx.AsyncClient",
            return_value=mock_client,
        ):
            outputs = await node.execute({})

        assert outputs["out"]["data"]["status"] == "failed"
        assert outputs["out"]["data"]["exit_code"] == 1
        assert outputs["out"]["data"]["stderr"] == "Traceback: boom"
        assert outputs["out"]["data"]["error_message"] == "Process exited with code 1"

    @pytest.mark.asyncio
    async def test_invoke_fire_and_forget(self) -> None:
        ctx = {
            "execution_id": "stream-exec-3",
            "node_id": "node-run-3",
            "config": {
                "function_id": 9,
                "wait_for_result": False,
            },
            "global_config": {"elder_api_url": "http://elder-api.test:8000"},
        }
        node = RunFunctionAction(ctx)

        mock_client = AsyncMock()
        mock_client.__aenter__.return_value = mock_client
        mock_client.post.return_value = _make_response(
            202, {"execution_id": "exec-async-1"}
        )

        with patch(
            "apps.worker.streams.nodes.actions.run_function.httpx.AsyncClient",
            return_value=mock_client,
        ):
            outputs = await node.execute({})

        mock_client.get.assert_not_awaited()
        assert outputs["out"]["data"]["execution_id"] == "exec-async-1"
        assert outputs["out"]["data"]["status"] == "pending"

    @pytest.mark.asyncio
    async def test_invoke_http_error_raises_runtime_error(self) -> None:
        ctx = {
            "execution_id": "stream-exec-4",
            "node_id": "node-run-4",
            "config": {"function_id": 99},
            "global_config": {"elder_api_url": "http://elder-api.test:8000"},
        }
        node = RunFunctionAction(ctx)

        mock_client = AsyncMock()
        mock_client.__aenter__.return_value = mock_client
        mock_client.post.return_value = _make_response(404, "Not Found")

        with patch(
            "apps.worker.streams.nodes.actions.run_function.httpx.AsyncClient",
            return_value=mock_client,
        ):
            with pytest.raises(RuntimeError, match="HTTP 404"):
                await node.execute({})

    @pytest.mark.asyncio
    async def test_poll_timeout_raises_timeout_error(self) -> None:
        ctx = {
            "execution_id": "stream-exec-5",
            "node_id": "node-run-5",
            "config": {
                "function_id": 12,
                "wait_for_result": True,
                "timeout_seconds": 0.01,
                "poll_interval": 0.005,
            },
            "global_config": {"elder_api_url": "http://elder-api.test:8000"},
        }
        node = RunFunctionAction(ctx)

        mock_client = AsyncMock()
        mock_client.__aenter__.return_value = mock_client
        mock_client.post.return_value = _make_response(
            202, {"execution_id": "exec-slow-1"}
        )
        mock_client.get.return_value = _make_response(
            200, {"execution_id": "exec-slow-1", "status": "running"}
        )

        with patch(
            "apps.worker.streams.nodes.actions.run_function.httpx.AsyncClient",
            return_value=mock_client,
        ):
            with pytest.raises(TimeoutError, match="exec-slow-1"):
                await node.execute({})

    @pytest.mark.asyncio
    async def test_missing_function_id_raises_value_error(self) -> None:
        node = RunFunctionAction({"config": {}})
        with pytest.raises(ValueError, match="function_id is required"):
            await node.execute({})


class TestMigration047RunsServerlessFunctions:
    """Verify Alembic migration 047 metadata and SQLite upgrade/downgrade cycle."""

    def test_migration_revision_chain(self) -> None:
        assert MIGRATION_PATH.exists()
        mig = _load_migration_module()
        assert mig.revision == "047"
        assert mig.down_revision == "046"

    def test_migration_upgrade_and_downgrade_sqlite(self) -> None:
        mig = _load_migration_module()
        engine = sa.create_engine("sqlite:///:memory:")
        with engine.begin() as conn:
            conn.execute(sa.text("CREATE TABLE tenants (id INTEGER PRIMARY KEY)"))
            conn.execute(sa.text("CREATE TABLE identities (id INTEGER PRIMARY KEY)"))

            mc = MigrationContext.configure(conn)
            with Operations.context(mc):
                mig.upgrade()

            inspector = sa.inspect(conn)
            tables = set(inspector.get_table_names())
            assert {
                "run_functions",
                "run_function_versions",
                "run_executions",
            } <= tables

            fn_cols = {c["name"] for c in inspector.get_columns("run_functions")}
            assert {
                "id",
                "tenant_id",
                "village_id",
                "name",
                "description",
                "runtime",
                "code",
                "entrypoint",
                "timeout_seconds",
                "memory_mb",
                "env_vars_json",
                "schedule_cron",
                "execution_namespace",
                "worker_cluster",
                "is_enabled",
                "created_by_identity_id",
                "created_at",
                "updated_at",
            } <= fn_cols

            exec_cols = {c["name"] for c in inspector.get_columns("run_executions")}
            assert {
                "id",
                "tenant_id",
                "execution_id",
                "function_id",
                "status",
                "trigger_type",
                "triggered_by_identity_id",
                "worker_id",
                "input_json",
                "output_json",
                "stdout_log",
                "stderr_log",
                "exit_code",
                "error_message",
                "started_at",
                "completed_at",
                "duration_ms",
                "created_at",
            } <= exec_cols

            with Operations.context(mc):
                mig.downgrade()

            remaining = set(sa.inspect(conn).get_table_names())
            assert "run_functions" not in remaining
            assert "run_function_versions" not in remaining
            assert "run_executions" not in remaining


class TestRunsLiveLogsAndEnrichedDetail:
    """Verify enriched execution detail and live-log push endpoint."""

    @staticmethod
    def _build_app_and_db(tmp_path: Path):
        from datetime import UTC, datetime
        from types import SimpleNamespace

        from penguin_dal import DB
        from quart import Quart, g

        # Ensure tables are registered on Base.metadata
        import apps.api.models.identity  # noqa: F401
        import apps.api.models.tenant  # noqa: F401
        import apps.api.modules.runs.models.runs  # noqa: F401
        from apps.api.models.base import Base
        from apps.api.modules.runs.routes.runs import bp as runs_bp

        db_path = tmp_path / "runs_live_logs.db"
        engine = sa.create_engine(f"sqlite:///{db_path}")
        Base.metadata.create_all(engine)

        db = DB(f"sqlite:///{db_path}")

        now = datetime.now(UTC)
        t1 = db.tenants.insert(
            name="Tenant One",
            slug="tenant-one",
            is_active=True,
            created_at=now,
            updated_at=now,
        )
        t2 = db.tenants.insert(
            name="Tenant Two",
            slug="tenant-two",
            is_active=True,
            created_at=now,
            updated_at=now,
        )
        fn_id = db.run_functions.insert(
            tenant_id=t1,
            village_id="eld-fn-live-1",
            name="nightly-report",
            description="Generates nightly report",
            runtime="python",
            code="print('hello')",
            storage_backend="s3",
            storage_key="runs/tenant-1/eld-fn-live-1/v1/main.py",
            code_sha256="a" * 64,
            entrypoint="main.py",
            timeout_seconds=180,
            memory_mb=512,
            env_vars_json={},
            execution_namespace="elder-runs-prod",
            is_enabled=True,
            created_at=now,
            updated_at=now,
        )
        db.run_executions.insert(
            tenant_id=t1,
            execution_id="exec-live-001",
            function_id=fn_id,
            status="running",
            trigger_type="manual",
            worker_id="runworker-pod-1",
            stdout_log="booting...\n",
            started_at=now,
            created_at=now,
        )
        db.run_executions.insert(
            tenant_id=t1,
            execution_id="exec-done-002",
            function_id=fn_id,
            status="completed",
            trigger_type="manual",
            worker_id="runworker-pod-1",
            stdout_log="final output\n",
            exit_code=0,
            started_at=now,
            completed_at=now,
            duration_ms=420,
            created_at=now,
        )
        db.commit()

        app = Quart(__name__)
        app.db = db
        app.register_blueprint(runs_bp, url_prefix="/api/v1/runs")

        auth_state: dict[str, Any] = {
            "user": SimpleNamespace(
                id=1,
                username="alice",
                tenant_id=t1,
                is_superuser=False,
            ),
            "claims": {
                "sub": "alice",
                "type": "access",
                "tenant": t1,
                "scope": ["runs:read", "runs:write", "runs:invoke"],
            },
            "tenant_id": t1,
        }

        @app.before_request
        async def _inject_auth():
            g.current_user = auth_state["user"]
            g.claims = auth_state["claims"]
            g.tenant_id = auth_state["tenant_id"]

        return app, db, auth_state, t1, t2

    @pytest.mark.asyncio
    async def test_get_execution_enriches_parent_function_fields_and_scopes_tenant(
        self, tmp_path: Path
    ) -> None:
        app, db, auth_state, t1, t2 = self._build_app_and_db(tmp_path)
        try:
            with patch(
                "apps.api.auth.decorators.get_current_user",
                side_effect=lambda: auth_state["user"],
            ):
                client = app.test_client()
                resp = await client.get("/api/v1/runs/executions/exec-live-001")
                assert resp.status_code == 200
                body = await resp.get_json()
                assert body["execution_id"] == "exec-live-001"
                assert body["status"] == "running"
                assert body["stdout_log"] == "booting...\n"
                assert body["function_name"] == "nightly-report"
                assert body["runtime"] == "python"
                assert body["storage_backend"] == "s3"
                assert body["storage_key"] == "runs/tenant-1/eld-fn-live-1/v1/main.py"
                assert body["code_sha256"] == "a" * 64
                assert body["execution_namespace"] == "elder-runs-prod"
                assert body["timeout_seconds"] == 180

                # Cross-tenant read must return 404
                auth_state["tenant_id"] = t2
                auth_state["user"].tenant_id = t2
                auth_state["claims"]["tenant"] = t2
                resp_other = await client.get("/api/v1/runs/executions/exec-live-001")
                assert resp_other.status_code == 404
        finally:
            db.close()

    @pytest.mark.asyncio
    async def test_internal_logs_endpoint_auth_running_guard_and_1mib_cap(
        self, tmp_path: Path
    ) -> None:
        from types import SimpleNamespace

        from apps.api.modules.runs.routes.runs import MAX_LIVE_LOG_BYTES

        app, db, auth_state, _t1, _t2 = self._build_app_and_db(tmp_path)
        try:
            with patch(
                "apps.api.auth.decorators.get_current_user",
                side_effect=lambda: auth_state["user"],
            ):
                client = app.test_client()

                # Regular user cannot push internal live logs -> 403
                resp_forbidden = await client.post(
                    "/api/v1/runs/internal/executions/exec-live-001/logs",
                    json={"logs": "nope", "worker_id": "rogue"},
                )
                assert resp_forbidden.status_code == 403

                # Switch caller to svc-runworker system service worker
                auth_state["user"] = SimpleNamespace(
                    id=0,
                    username="svc-runworker",
                    token_type="service_worker",
                    tenant_id=None,
                    is_superuser=False,
                )
                auth_state["claims"] = {
                    "sub": "svc-runworker",
                    "type": "service_worker",
                    "tenant": "system",
                    "scope": ["runs:read", "runs:write", "runs:invoke"],
                }
                auth_state["tenant_id"] = "system"

                # Running execution gets updated
                resp_ok = await client.post(
                    "/api/v1/runs/internal/executions/exec-live-001/logs",
                    json={"logs": "booting...\nstep 1\n", "worker_id": "rw-1"},
                )
                assert resp_ok.status_code == 200
                ok_body = await resp_ok.get_json()
                assert ok_body["updated"] is True
                assert ok_body["status"] == "running"
                row = (
                    db(db.run_executions.execution_id == "exec-live-001")
                    .select()
                    .first()
                )
                assert row.stdout_log == "booting...\nstep 1\n"

                # Completed execution is NOT overwritten
                resp_done = await client.post(
                    "/api/v1/runs/internal/executions/exec-done-002/logs",
                    json={"logs": "late chunk", "worker_id": "rw-1"},
                )
                assert resp_done.status_code == 200
                done_body = await resp_done.get_json()
                assert done_body["updated"] is False
                assert done_body["status"] == "completed"
                done_row = (
                    db(db.run_executions.execution_id == "exec-done-002")
                    .select()
                    .first()
                )
                assert done_row.stdout_log == "final output\n"

                # Logs exceeding 1 MiB are safely capped at MAX_LIVE_LOG_BYTES (tail kept)
                oversized = ("A" * (MAX_LIVE_LOG_BYTES + 2048)) + "TAIL_MARKER"
                resp_big = await client.post(
                    "/api/v1/runs/internal/executions/exec-live-001/logs",
                    json={"logs": oversized, "worker_id": "rw-1"},
                )
                assert resp_big.status_code == 200
                big_body = await resp_big.get_json()
                assert big_body["updated"] is True
                assert big_body["bytes"] <= MAX_LIVE_LOG_BYTES
                capped_row = (
                    db(db.run_executions.execution_id == "exec-live-001")
                    .select()
                    .first()
                )
                assert len(capped_row.stdout_log.encode("utf-8")) <= MAX_LIVE_LOG_BYTES
                assert capped_row.stdout_log.endswith("TAIL_MARKER")

                # Invalid payload -> 400; unknown execution -> 404
                resp_bad = await client.post(
                    "/api/v1/runs/internal/executions/exec-live-001/logs",
                    json={"logs": 12345},
                )
                assert resp_bad.status_code == 400

                resp_missing = await client.post(
                    "/api/v1/runs/internal/executions/exec-does-not-exist/logs",
                    json={"logs": "hi"},
                )
                assert resp_missing.status_code == 404
        finally:
            db.close()

    @pytest.mark.asyncio
    async def test_cancel_execution_endpoint(self, tmp_path: Path) -> None:
        from datetime import UTC, datetime

        app, db, auth_state, t1, t2 = self._build_app_and_db(tmp_path)
        try:
            # Insert a pending execution (no started_at)
            func_row = db(db.run_functions.tenant_id == t1).select().first()
            db.run_executions.insert(
                tenant_id=t1,
                execution_id="exec-pending-003",
                function_id=func_row.id,
                status="pending",
                trigger_type="manual",
                created_at=datetime.now(UTC),
            )
            db.commit()

            with patch(
                "apps.api.auth.decorators.get_current_user",
                side_effect=lambda: auth_state["user"],
            ):
                client = app.test_client()

                # Cancel running execution -> 200 with duration_ms computed
                resp_run = await client.post(
                    "/api/v1/runs/executions/exec-live-001/cancel"
                )
                assert resp_run.status_code == 200
                body_run = await resp_run.get_json()
                assert body_run["execution_id"] == "exec-live-001"
                assert body_run["status"] == "cancelled"
                assert body_run["error_message"] == "Cancelled by user"
                assert body_run["completed_at"] is not None
                assert isinstance(body_run["duration_ms"], int)
                assert body_run["duration_ms"] >= 0
                assert body_run["function_name"] == "nightly-report"

                # Cancelling again (already cancelled) -> 409
                resp_again = await client.post(
                    "/api/v1/runs/executions/exec-live-001/cancel"
                )
                assert resp_again.status_code == 409

                # Cancel pending execution -> 200 with duration_ms None
                resp_pend = await client.post(
                    "/api/v1/runs/executions/exec-pending-003/cancel"
                )
                assert resp_pend.status_code == 200
                body_pend = await resp_pend.get_json()
                assert body_pend["status"] == "cancelled"
                assert body_pend["error_message"] == "Cancelled by user"
                assert body_pend["duration_ms"] is None

                # Cancel already completed execution -> 409
                resp_done = await client.post(
                    "/api/v1/runs/executions/exec-done-002/cancel"
                )
                assert resp_done.status_code == 409

                # Missing execution -> 404
                resp_missing = await client.post(
                    "/api/v1/runs/executions/exec-does-not-exist/cancel"
                )
                assert resp_missing.status_code == 404

                # Cross-tenant execution -> 404
                auth_state["tenant_id"] = t2
                auth_state["user"].tenant_id = t2
                auth_state["claims"]["tenant"] = t2
                resp_other = await client.post(
                    "/api/v1/runs/executions/exec-done-002/cancel"
                )
                assert resp_other.status_code == 404
        finally:
            db.close()

    def test_unified_execution_serializers_include_source_field(self) -> None:
        from datetime import UTC, datetime
        from types import SimpleNamespace

        from apps.api.modules.streams.routes.streams import (
            _serialize_coderun_execution,
            _serialize_webhook_delivery,
        )

        now = datetime.now(UTC)
        run_row = SimpleNamespace(
            id=1,
            execution_id="exec-cr-1",
            function_id=42,
            status="completed",
            trigger_type="manual",
            started_at=now,
            completed_at=now,
            created_at=now,
            duration_ms=120,
            error_message=None,
        )
        fn_row = SimpleNamespace(name="enrich-alerts", runtime="python")
        cr_payload = _serialize_coderun_execution(run_row, fn_row)
        assert cr_payload["source"] == "coderun"
        assert cr_payload["source_type"] == "coderun"

        wh_delivery = SimpleNamespace(
            id=9,
            webhook_id=3,
            event_type="alert.fired",
            http_status=200,
            status="success",
            delivered_at=now,
            created_at=now,
            error_message=None,
        )
        wh_row = SimpleNamespace(name="PagerDuty")
        wh_payload = _serialize_webhook_delivery(wh_delivery, wh_row)
        assert wh_payload["source"] == "webhook"
        assert wh_payload["source_type"] == "webhook"
