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
        }
        assert TERMINAL_EXECUTION_STATUSES == {"completed", "failed", "timed_out"}
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
        assert node_cls.name == "Run Function"
        assert node_cls.required_module == "runs"

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
