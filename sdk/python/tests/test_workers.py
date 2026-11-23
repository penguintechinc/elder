"""Unit tests for the Remote Workers Fleet SDK facade (sync & async)."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from elder_sdk import AsyncElderClient, ElderClient
from elder_sdk.catalog import ApiCatalog, ApiOperation
from elder_sdk.resources import AsyncWorkersResource, WorkersResource


def _sample_worker(
    *,
    worker_id: str = "wrk-01",
    name: str = "dal2-runworker-01",
    worker_type: str = "runworker",
    cluster: str = "dal2-alpha",
    status: str = "online",
    capabilities: list[str] | None = None,
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "id": worker_id,
        "name": name,
        "worker_type": worker_type,
        "cluster": cluster,
        "status": status,
        "hostname": f"{name}.internal",
        "version": "4.0.0",
        "capabilities": capabilities
        if capabilities is not None
        else ["python", "rust"],
        "config": config if config is not None else {"max_concurrency": 4},
        "last_heartbeat_at": "2026-11-23T22:00:00+00:00",
        "created_at": "2026-11-23T21:00:00+00:00",
    }


def _make_handler(
    recorded: list[tuple[str, str, dict[str, str], Any]],
) -> Any:
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode()) if request.content else None
        params = dict(request.url.params)
        recorded.append((request.method, request.url.path, params, body))

        if request.method == "GET" and request.url.path == "/api/v1/admin/workers":
            return httpx.Response(
                200,
                json={
                    "items": [_sample_worker(worker_id="wrk-01")],
                    "total": 1,
                    "page": int(params.get("page", "1")),
                    "per_page": int(params.get("per_page", "50")),
                    "pages": 1,
                },
            )

        if request.method == "POST" and request.url.path == "/api/v1/admin/workers":
            return httpx.Response(
                201,
                json={
                    "worker": _sample_worker(
                        worker_id="wrk-02",
                        name=body["name"],
                        worker_type=body["worker_type"],
                        cluster=body.get("cluster", "default"),
                    ),
                    "token": "ewk_live_onboard_token",
                },
            )

        if (
            request.method == "GET"
            and request.url.path == "/api/v1/admin/workers/wrk-01"
        ):
            return httpx.Response(200, json=_sample_worker(worker_id="wrk-01"))

        if (
            request.method == "PUT"
            and request.url.path == "/api/v1/admin/workers/wrk-01"
        ):
            updated = _sample_worker(worker_id="wrk-01")
            updated.update(body or {})
            return httpx.Response(200, json=updated)

        if (
            request.method == "POST"
            and request.url.path == "/api/v1/admin/workers/wrk-01/rotate-token"
        ):
            return httpx.Response(
                200,
                json={"worker_id": "wrk-01", "token": "ewk_live_rotated_token"},
            )

        if (
            request.method == "DELETE"
            and request.url.path == "/api/v1/admin/workers/wrk-01"
        ):
            return httpx.Response(204)

        if (
            request.method == "POST"
            and request.url.path == "/api/v1/admin/workers/heartbeat"
        ):
            return httpx.Response(
                200,
                json={
                    "status": "ok",
                    "worker_id": body.get("worker_id", "wrk-01"),
                    "worker_type": body["worker_type"],
                },
            )

        return httpx.Response(404, json={"error": "not found"})

    return handler


def test_workers_sync_crud_onboard_rotate_offboard_and_heartbeat() -> None:
    recorded: list[tuple[str, str, dict[str, str], Any]] = []

    with ElderClient(
        base_url="https://elder.example.com",
        token="jwt-workers-sync",
        transport=httpx.MockTransport(_make_handler(recorded)),
    ) as client:
        assert isinstance(client.workers, WorkersResource)
        assert client.workers is client.workers
        assert client.workers.service_name == "workers"

        # List with defaults
        default_list = client.workers.list()
        assert default_list["total"] == 1
        assert default_list["items"][0]["id"] == "wrk-01"

        # List with all filters
        filtered = client.workers.list(
            worker_type="runworker",
            cluster="dal2-alpha",
            status="online",
            page=2,
            per_page=25,
        )
        assert filtered["page"] == 2
        assert filtered["per_page"] == 25

        # Get
        fetched = client.workers.get("wrk-01")
        assert fetched["name"] == "dal2-runworker-01"

        # Onboard (minimal)
        minimal_onboard = client.workers.onboard("edge-sync-01", "syncworker")
        assert minimal_onboard["token"] == "ewk_live_onboard_token"

        # Onboard (full options)
        full_onboard = client.workers.onboard(
            "dal2-runworker-02",
            "runworker",
            cluster="dal2-alpha",
            config={"max_concurrency": 8},
            capabilities=["python", "rust"],
        )
        assert full_onboard["worker"]["cluster"] == "dal2-alpha"

        # Update
        updated = client.workers.update(
            "wrk-01", cluster="dal2-beta", status="draining"
        )
        assert updated["cluster"] == "dal2-beta"
        assert updated["status"] == "draining"

        # Rotate token
        rotated = client.workers.rotate_token("wrk-01")
        assert rotated["token"] == "ewk_live_rotated_token"

        # Offboard
        assert client.workers.offboard("wrk-01") is None

        # Heartbeat (minimal)
        hb_min = client.workers.heartbeat("runworker")
        assert hb_min["status"] == "ok"

        # Heartbeat (full options)
        hb_full = client.workers.heartbeat(
            "runworker",
            worker_id="wrk-01",
            hostname="dal2-runworker-01.internal",
            version="4.0.0",
            cluster="dal2-alpha",
            capabilities=["python", "rust"],
        )
        assert hb_full["worker_id"] == "wrk-01"

    assert recorded[0] == (
        "GET",
        "/api/v1/admin/workers",
        {"page": "1", "per_page": "50"},
        None,
    )
    assert recorded[1] == (
        "GET",
        "/api/v1/admin/workers",
        {
            "page": "2",
            "per_page": "25",
            "worker_type": "runworker",
            "cluster": "dal2-alpha",
            "status": "online",
        },
        None,
    )
    assert recorded[2] == ("GET", "/api/v1/admin/workers/wrk-01", {}, None)
    assert recorded[3] == (
        "POST",
        "/api/v1/admin/workers",
        {},
        {"name": "edge-sync-01", "worker_type": "syncworker"},
    )
    assert recorded[4] == (
        "POST",
        "/api/v1/admin/workers",
        {},
        {
            "name": "dal2-runworker-02",
            "worker_type": "runworker",
            "cluster": "dal2-alpha",
            "config": {"max_concurrency": 8},
            "capabilities": ["python", "rust"],
        },
    )
    assert recorded[5] == (
        "PUT",
        "/api/v1/admin/workers/wrk-01",
        {},
        {"cluster": "dal2-beta", "status": "draining"},
    )
    assert recorded[6] == (
        "POST",
        "/api/v1/admin/workers/wrk-01/rotate-token",
        {},
        None,
    )
    assert recorded[7] == ("DELETE", "/api/v1/admin/workers/wrk-01", {}, None)
    assert recorded[8] == (
        "POST",
        "/api/v1/admin/workers/heartbeat",
        {},
        {"worker_type": "runworker"},
    )
    assert recorded[9] == (
        "POST",
        "/api/v1/admin/workers/heartbeat",
        {},
        {
            "worker_type": "runworker",
            "worker_id": "wrk-01",
            "hostname": "dal2-runworker-01.internal",
            "version": "4.0.0",
            "cluster": "dal2-alpha",
            "capabilities": ["python", "rust"],
        },
    )


async def test_workers_async_crud_onboard_rotate_offboard_and_heartbeat() -> None:
    recorded: list[tuple[str, str, dict[str, str], Any]] = []

    async with AsyncElderClient(
        base_url="https://elder.example.com",
        token="jwt-workers-async",
        transport=httpx.MockTransport(_make_handler(recorded)),
    ) as client:
        assert isinstance(client.workers, AsyncWorkersResource)
        assert client.workers is client.workers

        listing = await client.workers.list(
            worker_type="runworker",
            cluster="dal2-alpha",
            status="online",
            page=1,
            per_page=10,
        )
        assert listing["total"] == 1

        fetched = await client.workers.get("wrk-01")
        assert fetched["id"] == "wrk-01"

        onboarded = await client.workers.onboard(
            "async-worker-01",
            "runworker",
            cluster="dal2-alpha",
            config={"max_concurrency": 2},
            capabilities=["python"],
        )
        assert onboarded["token"] == "ewk_live_onboard_token"

        updated = await client.workers.update("wrk-01", status="maintenance")
        assert updated["status"] == "maintenance"

        rotated = await client.workers.rotate_token("wrk-01")
        assert rotated["token"] == "ewk_live_rotated_token"

        assert await client.workers.offboard("wrk-01") is None

        hb = await client.workers.heartbeat(
            "runworker",
            worker_id="wrk-01",
            hostname="async-worker-01.internal",
            version="4.0.0",
            cluster="dal2-alpha",
            capabilities=["python"],
        )
        assert hb["status"] == "ok"

    assert [r[0:2] for r in recorded] == [
        ("GET", "/api/v1/admin/workers"),
        ("GET", "/api/v1/admin/workers/wrk-01"),
        ("POST", "/api/v1/admin/workers"),
        ("PUT", "/api/v1/admin/workers/wrk-01"),
        ("POST", "/api/v1/admin/workers/wrk-01/rotate-token"),
        ("DELETE", "/api/v1/admin/workers/wrk-01"),
        ("POST", "/api/v1/admin/workers/heartbeat"),
    ]


def test_workers_validation_introspection_and_catalog_binding() -> None:
    transport = httpx.MockTransport(lambda req: httpx.Response(200, json={"ok": True}))
    with ElderClient(
        base_url="https://elder.example.com",
        transport=transport,
    ) as client:
        with pytest.raises(ValueError, match="worker_id"):
            client.workers.get("   ")
        assert client.workers.operations() == []
        assert "onboard" in dir(client.workers)
        with pytest.raises(AttributeError):
            _ = client.workers.nonexistent_op

    # When a catalog does contain a 'workers' service, proxy binding is active
    custom_catalog = ApiCatalog(
        [
            ApiOperation(
                service="workers",
                operation="fleet_summary",
                method="GET",
                path="/api/v1/admin/workers/summary",
                path_params=(),
                summary="Fleet summary",
                has_body=False,
            )
        ]
    )
    with ElderClient(
        base_url="https://elder.example.com",
        catalog=custom_catalog,
        transport=transport,
    ) as client:
        assert client.workers.operations() == ["fleet_summary"]
        assert client.workers.fleet_summary() == {"ok": True}


async def test_workers_async_catalog_binding_and_validation() -> None:
    transport = httpx.MockTransport(lambda req: httpx.Response(200, json={"ok": True}))
    custom_catalog = ApiCatalog(
        [
            ApiOperation(
                service="workers",
                operation="fleet_summary",
                method="GET",
                path="/api/v1/admin/workers/summary",
                path_params=(),
                summary="Fleet summary",
                has_body=False,
            )
        ]
    )
    async with AsyncElderClient(
        base_url="https://elder.example.com",
        catalog=custom_catalog,
        transport=transport,
    ) as client:
        assert client.workers.operations() == ["fleet_summary"]
        assert await client.workers.fleet_summary() == {"ok": True}
        with pytest.raises(ValueError, match="worker_id"):
            await client.workers.offboard("")
