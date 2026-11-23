"""Unit tests for Elder Runs SDK facade (sync & async) and OpenAPI schema."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import yaml

from elder_sdk import AsyncElderClient, ElderClient
from elder_sdk.catalog import ApiCatalog

REPO_ROOT = Path(__file__).resolve().parents[3]
OPENAPI_PATH = REPO_ROOT / "openapi" / "v4.yaml"

SAMPLE_SHA256 = "a" * 64


def _sample_function(
    *,
    func_id: int = 1,
    storage_backend: str = "s3",
    storage_key: str | None = "tenants/1/runs/1/v1.py",
    code_sha256: str | None = SAMPLE_SHA256,
    latest_version: int | None = 1,
    recent_executions: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "id": func_id,
        "tenant_id": 1,
        "village_id": "00000001-0000000000000099",
        "name": "alert-enricher",
        "description": "Enriches incoming alerts",
        "runtime": "python",
        "code": "def handler(event): return {'ok': True}",
        "entrypoint": "handler",
        "timeout_seconds": 60,
        "memory_mb": 256,
        "env_vars": {"LOG_LEVEL": "info"},
        "schedule_cron": "*/5 * * * *",
        "execution_namespace": "elder-runs",
        "worker_cluster": "dal2-alpha",
        "is_enabled": True,
        "storage_backend": storage_backend,
        "storage_key": storage_key,
        "code_sha256": code_sha256,
        "created_by_identity_id": 42,
        "created_at": "2026-11-23T12:00:00+00:00",
        "updated_at": "2026-11-23T12:00:00+00:00",
    }
    if latest_version is not None:
        payload["latest_version"] = latest_version
    if recent_executions is not None:
        payload["recent_executions"] = recent_executions
    return payload


def _sample_execution(
    *,
    execution_id: str = "exec-uuid-1",
    function_id: int = 1,
    status: str = "completed",
    trigger_type: str = "manual",
) -> dict[str, Any]:
    return {
        "id": 10,
        "tenant_id": 1,
        "execution_id": execution_id,
        "function_id": function_id,
        "status": status,
        "trigger_type": trigger_type,
        "triggered_by_identity_id": 42,
        "worker_id": "runworker-1",
        "input_data": {"alert_id": 7},
        "output_data": {"ok": True},
        "stdout_log": "enriched alert 7\n",
        "stderr_log": "",
        "exit_code": 0,
        "error_message": None,
        "started_at": "2026-11-23T12:01:00+00:00",
        "completed_at": "2026-11-23T12:01:01+00:00",
        "duration_ms": 1000,
        "created_at": "2026-11-23T12:01:00+00:00",
    }


def test_openapi_spec_declares_runs_paths_and_storage_metadata() -> None:
    spec = yaml.safe_load(OPENAPI_PATH.read_text(encoding="utf-8"))
    paths = spec["paths"]
    schemas = spec["components"]["schemas"]

    expected_paths = {
        "/api/v1/runs": {"get", "post"},
        "/api/v1/runs/{func_id}": {"get", "put", "delete"},
        "/api/v1/runs/{func_id}/invoke": {"post"},
        "/api/v1/runs/executions": {"get"},
        "/api/v1/runs/executions/{execution_id}": {"get"},
        "/api/v1/runs/internal/pending": {"get"},
        "/api/v1/runs/internal/executions/{execution_id}/result": {"post"},
    }
    for path, methods in expected_paths.items():
        assert path in paths, f"Missing OpenAPI path: {path}"
        assert methods.issubset(set(paths[path].keys()))

    run_fn_schema = schemas["RunFunction"]
    props = run_fn_schema["properties"]
    assert props["storage_backend"]["enum"] == ["database", "s3"]
    assert props["storage_key"]["anyOf"] == [{"type": "string"}, {"type": "null"}]
    assert props["code_sha256"]["anyOf"] == [{"type": "string"}, {"type": "null"}]
    assert "latest_version" in props
    assert "recent_executions" in props
    for req_field in ("storage_backend", "storage_key", "code_sha256"):
        assert req_field in run_fn_schema["required"]

    # Verify catalog exposes all runs operations
    catalog = ApiCatalog.load_default()
    assert catalog.has_service("runs")
    op_names = {op.operation for op in catalog.operations("runs")}
    assert {
        "list",
        "create",
        "get",
        "update",
        "delete",
        "invoke_run_function",
        "list_run_executions",
        "get_run_execution",
        "claim_pending_executions",
        "report_execution_result",
    }.issubset(op_names)


def test_runs_sync_crud_invoke_executions_and_storage_metadata() -> None:
    recorded: list[tuple[str, str, dict[str, str], Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode()) if request.content else None
        params = dict(request.url.params)
        recorded.append((request.method, request.url.path, params, body))

        if request.method == "GET" and request.url.path == "/api/v1/runs":
            page = int(params.get("page", "1"))
            if page == 1:
                return httpx.Response(
                    200,
                    json={
                        "items": [_sample_function(func_id=1, storage_backend="s3")],
                        "total": 2,
                        "page": 1,
                        "per_page": 1,
                        "pages": 2,
                    },
                )
            return httpx.Response(
                200,
                json={
                    "items": [
                        _sample_function(
                            func_id=2,
                            storage_backend="database",
                            storage_key=None,
                            code_sha256=SAMPLE_SHA256,
                        )
                    ],
                    "total": 2,
                    "page": 2,
                    "per_page": 1,
                    "pages": 2,
                },
            )

        if request.method == "POST" and request.url.path == "/api/v1/runs":
            return httpx.Response(201, json=_sample_function(func_id=1))

        if request.method == "GET" and request.url.path == "/api/v1/runs/1":
            return httpx.Response(
                200,
                json=_sample_function(
                    func_id=1,
                    latest_version=2,
                    recent_executions=[_sample_execution()],
                ),
            )

        if request.method == "PUT" and request.url.path == "/api/v1/runs/1":
            return httpx.Response(
                200,
                json=_sample_function(
                    func_id=1,
                    latest_version=2,
                    storage_backend="s3",
                    storage_key="tenants/1/runs/1/v2.py",
                    code_sha256="b" * 64,
                ),
            )

        if request.method == "DELETE" and request.url.path == "/api/v1/runs/1":
            return httpx.Response(204)

        if request.method == "POST" and request.url.path == "/api/v1/runs/1/invoke":
            ex = _sample_execution(status="pending", trigger_type="manual")
            return httpx.Response(
                202, json={"execution_id": ex["execution_id"], "execution": ex}
            )

        if request.method == "GET" and request.url.path == "/api/v1/runs/executions":
            return httpx.Response(
                200,
                json={
                    "items": [_sample_execution(execution_id="exec-uuid-1")],
                    "total": 1,
                    "page": 1,
                    "per_page": 50,
                    "pages": 1,
                },
            )

        if (
            request.method == "GET"
            and request.url.path == "/api/v1/runs/executions/exec-uuid-1"
        ):
            return httpx.Response(
                200, json=_sample_execution(execution_id="exec-uuid-1")
            )

        if (
            request.method == "GET"
            and request.url.path == "/api/v1/runs/internal/pending"
        ):
            return httpx.Response(
                200, json={"items": [_sample_execution(status="running")], "count": 1}
            )

        if (
            request.method == "POST"
            and request.url.path
            == "/api/v1/runs/internal/executions/exec-uuid-1/result"
        ):
            return httpx.Response(
                200, json=_sample_execution(execution_id="exec-uuid-1")
            )

        return httpx.Response(404, json={"error": "not found"})

    with ElderClient(
        base_url="https://elder.example.com",
        token="jwt-runs-test",
        transport=httpx.MockTransport(handler),
    ) as client:
        # List with query filters
        listing = client.runs.list(
            page=1,
            per_page=1,
            runtime="python",
            search="enricher",
            worker_cluster="dal2-alpha",
            is_enabled=True,
        )
        assert listing["total"] == 2
        assert listing["items"][0]["storage_backend"] == "s3"
        assert listing["items"][0]["storage_key"] == "tenants/1/runs/1/v1.py"
        assert listing["items"][0]["code_sha256"] == SAMPLE_SHA256

        # Auto-pagination across both S3 and database-backed functions
        all_funcs = list(client.runs.iter_all(per_page=1))
        assert [f["id"] for f in all_funcs] == [1, 2]
        assert all_funcs[1]["storage_backend"] == "database"
        assert all_funcs[1]["storage_key"] is None

        # Create
        created = client.runs.create(
            name="alert-enricher",
            runtime="python",
            code="def handler(event): return {'ok': True}",
            description="Enriches incoming alerts",
            entrypoint="handler",
            timeout_seconds=60,
            memory_mb=256,
            env_vars={"LOG_LEVEL": "info"},
            schedule_cron="*/5 * * * *",
            execution_namespace="elder-runs",
            worker_cluster="dal2-alpha",
            is_enabled=True,
        )
        assert created["id"] == 1
        assert created["storage_backend"] == "s3"

        # Get with latest_version, recent_executions, and storage metadata
        fetched = client.runs.get(1)
        assert fetched["latest_version"] == 2
        assert len(fetched["recent_executions"]) == 1
        assert fetched["storage_backend"] == "s3"
        assert fetched["storage_key"] == "tenants/1/runs/1/v1.py"
        assert fetched["code_sha256"] == SAMPLE_SHA256

        # Update
        updated = client.runs.update(1, code="def handler(event): return {'v': 2}")
        assert updated["storage_key"] == "tenants/1/runs/1/v2.py"
        assert updated["code_sha256"] == "b" * 64

        # Invoke with input_data and trigger_type
        invoked = client.runs.invoke(
            1, input_data={"alert_id": 7}, trigger_type="manual"
        )
        assert invoked["execution_id"] == "exec-uuid-1"
        assert invoked["execution"]["status"] == "pending"

        # List executions & iter_executions
        exec_page = client.runs.list_executions(
            function_id=1, status="completed", trigger_type="manual"
        )
        assert exec_page["total"] == 1
        exec_items = list(client.runs.iter_executions(function_id=1, per_page=50))
        assert [e["execution_id"] for e in exec_items] == ["exec-uuid-1"]

        # Get single execution
        exec_detail = client.runs.get_execution("exec-uuid-1")
        assert exec_detail["exit_code"] == 0
        assert exec_detail["stdout_log"] == "enriched alert 7\n"

        # Internal worker operations
        claimed = client.runs.claim_pending(limit=5, worker_cluster="dal2-alpha")
        assert claimed["count"] == 1
        reported = client.runs.report_result(
            "exec-uuid-1", status="completed", exit_code=0, output_data={"ok": True}
        )
        assert reported["status"] == "completed"

        # Delete
        assert client.runs.delete(1) is None

    # Verify exact HTTP wire calls
    list_call = recorded[0]
    assert list_call[0] == "GET"
    assert list_call[1] == "/api/v1/runs"
    assert list_call[2]["runtime"] == "python"
    assert list_call[2]["worker_cluster"] == "dal2-alpha"
    assert list_call[2]["search"] == "enricher"

    invoke_call = next(
        c for c in recorded if c[1] == "/api/v1/runs/1/invoke" and c[0] == "POST"
    )
    assert invoke_call[3] == {"input_data": {"alert_id": 7}, "trigger_type": "manual"}


async def test_runs_async_crud_invoke_and_executions() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST" and request.url.path == "/api/v1/runs":
            return httpx.Response(201, json=_sample_function(func_id=3))
        if request.method == "GET" and request.url.path == "/api/v1/runs/3":
            return httpx.Response(
                200,
                json=_sample_function(
                    func_id=3,
                    storage_backend="s3",
                    storage_key="tenants/1/runs/3/v1.py",
                    code_sha256=SAMPLE_SHA256,
                    latest_version=1,
                    recent_executions=[],
                ),
            )
        if request.method == "POST" and request.url.path == "/api/v1/runs/3/invoke":
            ex = _sample_execution(execution_id="exec-async-1", function_id=3)
            return httpx.Response(
                202, json={"execution_id": ex["execution_id"], "execution": ex}
            )
        if request.method == "GET" and request.url.path == "/api/v1/runs/executions":
            return httpx.Response(
                200,
                json={
                    "items": [
                        _sample_execution(execution_id="exec-async-1", function_id=3)
                    ],
                    "total": 1,
                    "page": 1,
                    "per_page": 25,
                    "pages": 1,
                },
            )
        if (
            request.method == "GET"
            and request.url.path == "/api/v1/runs/executions/exec-async-1"
        ):
            return httpx.Response(
                200,
                json=_sample_execution(execution_id="exec-async-1", function_id=3),
            )
        return httpx.Response(404, json={"error": "not found"})

    async with AsyncElderClient(
        base_url="https://elder.example.com",
        token="jwt-async-runs",
        transport=httpx.MockTransport(handler),
    ) as client:
        created = await client.runs.create(
            name="async-fn", runtime="rust", code="fn main() {}"
        )
        assert created["id"] == 3
        assert created["storage_backend"] == "s3"

        fetched = await client.runs.get(3)
        assert fetched["storage_key"] == "tenants/1/runs/3/v1.py"
        assert fetched["code_sha256"] == SAMPLE_SHA256

        invoked = await client.runs.invoke(3, payload={"hello": "world"})
        assert invoked["execution_id"] == "exec-async-1"

        execs = [
            item
            async for item in client.runs.iter_executions(function_id=3, per_page=25)
        ]
        assert len(execs) == 1
        assert execs[0]["execution_id"] == "exec-async-1"

        detail = await client.runs.get_execution("exec-async-1")
        assert detail["function_id"] == 3
