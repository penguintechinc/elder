"""Unit, ASGI concurrency, security, and API-parity tests for hub-mcp and Elder MCP."""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import types
from pathlib import Path
from typing import Any

import httpx
import pytest
from opentelemetry import metrics, trace
from opentelemetry._logs import get_logger_provider
from opentelemetry.sdk._logs import LoggerProvider
from opentelemetry.sdk._logs.export import (
    InMemoryLogRecordExporter,
    SimpleLogRecordProcessor,
)
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from apps.mcp import start as mcp_start
from apps.mcp.auth import (
    CallerCredentials,
    ElderAuthError,
    ElderSession,
    extract_caller_credentials,
    get_caller_credentials,
    is_hosted_mode,
    mask_secret,
    reset_caller_context,
    resolve_elder_base_url,
    set_caller_context,
)
from apps.mcp.catalog import (
    APIOperation,
    generate_catalog,
    get_operation,
    list_operations,
    list_services,
    load_catalog,
    resolve_operation_path,
    write_catalog,
)
from apps.mcp.client import ElderAPIError, ElderClient, create_pooled_http_client
from apps.mcp.http_app import create_app
from apps.mcp.registry import TOOL_BY_NAME, TOOL_REGISTRY, execute_tool, invoke_tool
from apps.mcp.tools import api_catalog, resources

REPO_ROOT = Path(__file__).resolve().parents[2]


def _install_fake_fastmcp(monkeypatch: pytest.MonkeyPatch):
    """Provide a FastMCP shim when running in an environment without ``mcp`` installed."""
    try:
        import mcp.server.fastmcp as real_fastmcp  # type: ignore[import-not-found]

        _ = real_fastmcp.FastMCP
        return None
    except ImportError:
        pass

    class _FakeFastMCP:
        def __init__(self, name: str, **kwargs: Any) -> None:
            self.name = name
            self.kwargs = kwargs
            self.settings = types.SimpleNamespace(
                host=kwargs.get("host", "127.0.0.1"),
                port=kwargs.get("port", 8080),
                log_level="INFO",
            )
            self._tools: dict[str, Any] = {}
            self._context: Any = None

        def tool(self, name: str, description: str):
            def _decorator(fn):
                self._tools[name] = (fn, description)
                return fn

            return _decorator

        def get_context(self):
            return self._context

        def streamable_http_app(self):
            async def _asgi(scope, receive, send):
                if scope["type"] == "lifespan":
                    return
                body = json.dumps({"transport": "streamable-http", "ok": True}).encode()
                await send(
                    {
                        "type": "http.response.start",
                        "status": 200,
                        "headers": [(b"content-type", b"application/json")],
                    }
                )
                await send({"type": "http.response.body", "body": body})

            return _asgi

        def sse_app(self, mount_path: str | None = None):
            async def _asgi(scope, receive, send):
                body = json.dumps(
                    {"transport": "sse", "mount_path": mount_path, "ok": True}
                ).encode()
                await send(
                    {
                        "type": "http.response.start",
                        "status": 200,
                        "headers": [(b"content-type", b"application/json")],
                    }
                )
                await send({"type": "http.response.body", "body": body})

            return _asgi

        def run(self, transport: str = "stdio") -> None:
            self.last_transport = transport

    fake_mcp = types.ModuleType("mcp")
    fake_server = types.ModuleType("mcp.server")
    fake_fastmcp = types.ModuleType("mcp.server.fastmcp")
    fake_fastmcp.FastMCP = _FakeFastMCP  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "mcp", fake_mcp)
    monkeypatch.setitem(sys.modules, "mcp.server", fake_server)
    monkeypatch.setitem(sys.modules, "mcp.server.fastmcp", fake_fastmcp)
    return _FakeFastMCP


async def _run_asgi(
    asgi_app: Any,
    *,
    scope_type: str = "http",
    method: str = "POST",
    path: str = "/mcp",
    headers: list[tuple[bytes, bytes]] | None = None,
    body: bytes = b"",
) -> tuple[int | None, dict[str, str], bytes, list[dict[str, Any]]]:
    events: list[dict[str, Any]] = []
    sent_body = bytearray()
    status_code: int | None = None
    resp_headers: dict[str, str] = {}

    async def _receive():
        return {"type": "http.request", "body": body, "more_body": False}

    async def _send(message: dict[str, Any]) -> None:
        nonlocal status_code, resp_headers
        events.append(message)
        if message["type"] == "http.response.start":
            status_code = int(message["status"])
            resp_headers = {
                k.decode("latin-1").lower(): v.decode("latin-1")
                for k, v in message.get("headers", [])
            }
        elif message["type"] == "http.response.body":
            sent_body.extend(message.get("body", b""))

    scope: dict[str, Any] = {
        "type": scope_type,
        "method": method,
        "path": path,
        "headers": headers or [],
    }
    await asgi_app(scope, _receive, _send)
    return status_code, resp_headers, bytes(sent_body), events


# ---------------------------------------------------------------------------
# Auth, Base URL Alias, Secret Masking, Cookie & Client Safety
# ---------------------------------------------------------------------------


def test_resolve_elder_base_url_and_api_url_alias(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ELDER_API_URL works as an alias for ELDER_URL and defaults to localhost:5000."""
    monkeypatch.delenv("ELDER_URL", raising=False)
    monkeypatch.delenv("ELDER_API_URL", raising=False)
    assert resolve_elder_base_url() == "http://localhost:5000"

    monkeypatch.setenv("ELDER_API_URL", "http://hub-api:5000/")
    assert resolve_elder_base_url() == "http://hub-api:5000"

    monkeypatch.setenv("ELDER_URL", "http://explicit-elder:6000/")
    assert resolve_elder_base_url() == "http://explicit-elder:6000"
    assert (
        resolve_elder_base_url("https://direct.example/api/")
        == "https://direct.example/api"
    )


def test_secret_masking_across_session_client_and_credentials() -> None:
    """Bearer JWTs, refresh tokens, passwords, and API keys are always masked in repr/str."""
    jwt_secret = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.very-secret-payload.signature99"
    api_key_secret = "ek_live_super_secret_key_987654"
    short_secret = "tokA_123"
    password_secret = "hunter2-super-secret-pass"
    refresh_secret = "rt_very_secret_refresh_token_123456"

    assert mask_secret(None) is None
    assert mask_secret("") == ""
    assert mask_secret(short_secret) == "****"
    assert mask_secret(jwt_secret) == "eyJh****re99"

    session = ElderSession(
        base_url="http://hub-api:5000",
        username="alice",
        password=password_secret,
        access_token=jwt_secret,
        refresh_token=refresh_secret,
        api_key=api_key_secret,
    )
    client = ElderClient(session=session)
    creds = CallerCredentials(access_token=jwt_secret, api_key=short_secret)

    for text in (
        repr(session),
        str(session),
        repr(client),
        str(client),
        repr(creds),
        str(creds),
    ):
        assert jwt_secret not in text
        assert api_key_secret not in text
        assert short_secret not in text
        assert password_secret not in text
        assert refresh_secret not in text
        assert "****" in text


def test_extract_caller_credentials_fail_closed_and_header_validation() -> None:
    """extract_caller_credentials accepts valid Bearer/X-API-Key and rejects missing/malformed."""
    creds_bearer = extract_caller_credentials(
        [
            (b"Authorization", b"bearer tok-alpha-1234567890"),
            (b"X-Request-ID", b"req-42"),
            (
                b"traceparent",
                b"00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01",
            ),
            (b"tracestate", b"congo=t61rcWkgMzE"),
            (b"X-Tenant-ID", b"999"),  # Must be ignored
        ]
    )
    assert creds_bearer.access_token == "tok-alpha-1234567890"
    assert creds_bearer.api_key is None
    assert creds_bearer.propagation_headers() == {
        "X-Request-ID": "req-42",
        "traceparent": "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01",
        "tracestate": "congo=t61rcWkgMzE",
    }
    assert "X-Tenant-ID" not in creds_bearer.propagation_headers()

    fake_key = "-".join(["placeholder", "key", "value"])
    creds_key = extract_caller_credentials({"X-API-Key": fake_key})
    assert creds_key.api_key == fake_key
    assert creds_key.access_token is None

    for bad_headers in (
        {},
        {"Authorization": ""},
        {"Authorization": "Basic dXNlcjpwYXNz"},
        {"Authorization": "Bearer "},
        {"Authorization": "Bearer two parts"},
        {"Authorization": "Bearer bad\r\nX-Injected: 1"},
        {"X-API-Key": "   "},
        {"X-API-Key": "has space"},
    ):
        with pytest.raises(ElderAuthError):
            extract_caller_credentials(bad_headers)


@pytest.mark.asyncio
async def test_pooled_http_client_strips_cookies_and_has_no_default_auth() -> None:
    """Pooled httpx client carries zero default auth headers and ignores Set-Cookie."""
    seen_headers: list[dict[str, str]] = []

    def _handler(request: httpx.Request) -> httpx.Response:
        seen_headers.append(dict(request.headers))
        if request.url.path == "/api/v1/licensing/status":
            return httpx.Response(
                200,
                json={"tier": "enterprise"},
                headers={"Set-Cookie": "elder_session=leaked_cookie; HttpOnly"},
            )
        return httpx.Response(200, json={"ok": True})

    pool = create_pooled_http_client(
        "http://hub-api:5000",
        transport=httpx.MockTransport(_handler),
    )
    try:
        assert "authorization" not in {k.lower() for k in pool.headers}
        assert "x-api-key" not in {k.lower() for k in pool.headers}

        c1 = ElderClient(
            session=ElderSession.from_caller(
                "http://hub-api:5000",
                CallerCredentials(access_token="tokA-secret"),
            ),
            http_client=pool,
        )
        c2 = ElderClient(
            session=ElderSession.from_caller(
                "http://hub-api:5000",
                CallerCredentials(api_key="keyB-secret"),
            ),
            http_client=pool,
        )
        await c1.get("/api/v1/licensing/status")
        await c2.delete("/api/v1/entities/5", json={"reason": "cleanup"})
        # Closing request-scoped ElderClient must NOT close the shared pool.
        await c1.close()
        await c2.close()
        assert not pool.is_closed
    finally:
        await pool.aclose()

    assert len(seen_headers) == 2
    assert seen_headers[0].get("authorization") == "Bearer tokA-secret"
    assert "cookie" not in seen_headers[1]
    assert seen_headers[1].get("x-api-key") == "keyB-secret"
    assert "authorization" not in seen_headers[1]


# ---------------------------------------------------------------------------
# Hosted Quart Service (hub-mcp): Fail-Closed Auth, Concurrency, MCP Protocol
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_http_app_health_and_fail_closed_auth_ignores_env_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unauthenticated requests fail closed with 401 EVEN WHEN env tokens/creds are set."""
    monkeypatch.setenv("ELDER_API_TOKEN", "env-superadmin-jwt-must-never-be-used")
    monkeypatch.setenv("ELDER_API_KEY", "env-superadmin-key-must-never-be-used")
    monkeypatch.setenv("ELDER_USERNAME", "admin")
    monkeypatch.setenv("ELDER_PASSWORD", "admin-pass")

    upstream_called = False

    def _upstream(_request: httpx.Request) -> httpx.Response:
        nonlocal upstream_called
        upstream_called = True
        return httpx.Response(200, json={"ok": True})

    app = create_app(
        elder_api_url="http://hub-api:5000",
        elder_transport=httpx.MockTransport(_upstream),
    )

    async with app.test_client() as client:
        hz = await client.get("/healthz")
        assert hz.status_code == 200
        hz_data = await hz.get_json()
        assert hz_data["status"] == "ok"
        assert hz_data["service"] == "hub-mcp"
        assert hz_data["tools"] == len(TOOL_REGISTRY)

        rz = await client.get("/readyz")
        assert rz.status_code == 200
        rz_data = await rz.get_json()
        assert rz_data["status"] == "ok"
        assert rz_data["catalog_operations"] >= 680

        for method, path, headers in (
            ("POST", "/mcp", {}),
            ("GET", "/mcp", {}),
            ("DELETE", "/mcp", {}),
            ("POST", "/mcp", {"Authorization": "Bearer "}),
            ("POST", "/mcp", {"Authorization": "Basic abc"}),
            ("POST", "/mcp", {"X-API-Key": "   "}),
            ("GET", "/nonexistent", {}),
        ):
            resp = await client.open(
                path,
                method=method,
                headers=headers,
                json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
                if method == "POST"
                else None,
            )
            assert resp.status_code == 401, (method, path, headers)
            data = await resp.get_json()
            assert data == {"error": "Authentication required"}

    assert upstream_called is False


@pytest.mark.asyncio
async def test_http_app_concurrent_caller_isolation_and_trace_forwarding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Concurrent Tenant A (Bearer tokA) and Tenant B (X-API-Key keyB) calls never bleed."""
    monkeypatch.setenv("ELDER_API_TOKEN", "env-poison-token")
    observed: list[tuple[str, dict[str, str]]] = []

    async def _async_upstream(request: httpx.Request) -> httpx.Response:
        headers = dict(request.headers)
        observed.append((request.url.path, headers))
        # Interleave coroutines intentionally to catch shared-state / ContextVar races.
        await asyncio.sleep(0.02)
        if request.url.path == "/api/v1/entities":
            if headers.get("authorization") == "Bearer tokA":
                return httpx.Response(
                    200, json={"tenant": "A", "entities": [{"id": 1}]}
                )
            if headers.get("x-api-key") == "keyB":
                return httpx.Response(
                    200, json={"tenant": "B", "entities": [{"id": 2}]}
                )
            return httpx.Response(401, json={"error": "Unexpected credential"})
        if request.url.path == "/api/v1/licensing/status":
            return httpx.Response(
                200,
                json={
                    "tier": "enterprise",
                    "caller": headers.get("x-api-key") or headers.get("authorization"),
                },
            )
        return httpx.Response(404, json={"error": "Not Found"})

    app = create_app(
        elder_api_url="http://hub-api:5000",
        elder_transport=httpx.MockTransport(_async_upstream),
    )

    async with app.test_client() as client:
        req_a = client.post(
            "/mcp",
            headers={
                "Authorization": "Bearer tokA",
                "X-Request-ID": "req-tenant-a",
                "traceparent": "00-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-1111111111111111-01",
                "X-Tenant-ID": "999",
            },
            json={
                "jsonrpc": "2.0",
                "id": "a1",
                "method": "tools/call",
                "params": {"name": "search_entities", "arguments": {"name": "web"}},
            },
        )
        req_b = client.post(
            "/mcp",
            headers={
                "X-API-Key": "keyB",
                "X-Request-ID": "req-tenant-b",
                "traceparent": "00-bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb-2222222222222222-01",
                "X-Tenant-ID": "1",
            },
            json={
                "jsonrpc": "2.0",
                "id": "b1",
                "method": "tools/call",
                "params": {
                    "name": "call_api_operation",
                    "arguments": {
                        "service": "licensing",
                        "operation": "get_licensing_status",
                    },
                },
            },
        )
        resp_a, resp_b = await asyncio.gather(req_a, req_b)

        assert resp_a.status_code == 200
        assert resp_b.status_code == 200
        body_a = await resp_a.get_json()
        body_b = await resp_b.get_json()

    assert body_a["result"]["isError"] is False
    assert json.loads(body_a["result"]["content"][0]["text"])["tenant"] == "A"
    assert body_b["result"]["isError"] is False
    assert json.loads(body_b["result"]["content"][0]["text"])["caller"] == "keyB"

    by_path = {path: hdrs for path, hdrs in observed}
    hdrs_a = by_path["/api/v1/entities"]
    hdrs_b = by_path["/api/v1/licensing/status"]
    assert hdrs_a.get("authorization") == "Bearer tokA"
    assert "x-api-key" not in hdrs_a
    assert hdrs_a.get("x-request-id") == "req-tenant-a"
    assert "x-tenant-id" not in hdrs_a

    assert hdrs_b.get("x-api-key") == "keyB"
    assert "authorization" not in hdrs_b
    assert hdrs_b.get("x-request-id") == "req-tenant-b"
    assert "x-tenant-id" not in hdrs_b

    assert get_caller_credentials() is None
    assert is_hosted_mode() is False


@pytest.mark.asyncio
async def test_http_app_mcp_jsonrpc_lifecycle_batch_and_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Streamable HTTP /mcp supports initialize, 202 notifications, batch, GET/DELETE, and error translation."""

    def _upstream(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v1/id/vid-42":
            return httpx.Response(
                200, json={"village_id": "vid-42", "resource_type": "entity"}
            )
        if request.url.path == "/api/v1/entities/99":
            return httpx.Response(403, json={"error": "Missing scope: entities:read"})
        if request.url.path == "/api/v1/entities/404":
            return httpx.Response(404, json={"message": "Entity not found"})
        return httpx.Response(200, json={"ok": True})

    app = create_app(
        elder_api_url="http://hub-api:5000",
        elder_transport=httpx.MockTransport(_upstream),
    )
    headers = {"Authorization": "Bearer valid-caller-jwt"}

    async with app.test_client() as client:
        # 1. Initialize
        init_resp = await client.post(
            "/mcp",
            headers=headers,
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {"protocolVersion": "2025-06-18"},
            },
        )
        assert init_resp.status_code == 200
        assert init_resp.headers["MCP-Protocol-Version"] == "2025-06-18"
        init_json = await init_resp.get_json()
        assert init_json["result"]["protocolVersion"] == "2025-06-18"
        assert init_json["result"]["serverInfo"]["name"] == "elder"

        # 2. Notification -> 202 empty body
        notif_resp = await client.post(
            "/mcp",
            headers=headers,
            json={"jsonrpc": "2.0", "method": "notifications/initialized"},
        )
        assert notif_resp.status_code == 202
        assert await notif_resp.get_data() == b""

        # 3. tools/list
        list_resp = await client.post(
            "/mcp",
            headers=headers,
            json={"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        )
        assert list_resp.status_code == 200
        tools_list = (await list_resp.get_json())["result"]["tools"]
        tool_names = {t["name"] for t in tools_list}
        assert len(tool_names) == len(TOOL_REGISTRY)
        assert {
            "resolve_village_id",
            "lookup_village_id",
            "list_api_services",
            "list_api_operations",
            "describe_api_operation",
            "invoke_api_operation",
            "call_api_operation",
        } <= tool_names

        # 4. resources/list, resources/read, prompts/list
        res_list = await (
            await client.post(
                "/mcp",
                headers=headers,
                json={"jsonrpc": "2.0", "id": 3, "method": "resources/list"},
            )
        ).get_json()
        assert res_list["result"]["resources"][0]["uri"] == "elder://api/catalog"

        res_read = await (
            await client.post(
                "/mcp",
                headers=headers,
                json={
                    "jsonrpc": "2.0",
                    "id": 4,
                    "method": "resources/read",
                    "params": {"uri": "elder://api/catalog"},
                },
            )
        ).get_json()
        catalog_summary = json.loads(res_read["result"]["contents"][0]["text"])
        assert catalog_summary["total_operations"] >= 680

        res_missing = await (
            await client.post(
                "/mcp",
                headers=headers,
                json={
                    "jsonrpc": "2.0",
                    "id": 5,
                    "method": "resources/read",
                    "params": {"uri": "elder://unknown"},
                },
            )
        ).get_json()
        assert res_missing["error"]["code"] == -32002

        prompts_resp = await (
            await client.post(
                "/mcp",
                headers=headers,
                json={"jsonrpc": "2.0", "id": 6, "method": "prompts/list"},
            )
        ).get_json()
        assert prompts_resp["result"] == {"prompts": []}

        # 5. Batch JSON-RPC including notification + resolve_village_id + 403 error
        batch_resp = await client.post(
            "/mcp",
            headers=headers,
            json=[
                {"jsonrpc": "2.0", "method": "notifications/initialized"},
                {"jsonrpc": "2.0", "id": 10, "method": "ping"},
                {
                    "jsonrpc": "2.0",
                    "id": 11,
                    "method": "tools/call",
                    "params": {
                        "name": "resolve_village_id",
                        "arguments": {"village_id": "vid-42"},
                    },
                },
                {
                    "jsonrpc": "2.0",
                    "id": 12,
                    "method": "tools/call",
                    "params": {"name": "get_entity", "arguments": {"entity_id": 99}},
                },
            ],
        )
        assert batch_resp.status_code == 200
        batch_items = await batch_resp.get_json()
        assert len(batch_items) == 3
        assert batch_items[0]["result"] == {}
        assert (
            batch_items[1]["result"]["structuredContent"]["resource_type"] == "entity"
        )
        assert batch_items[2]["result"]["isError"] is True
        err_403 = json.loads(batch_items[2]["result"]["content"][0]["text"])
        assert err_403["status_code"] == 403
        assert "Missing scope" in err_403["error"]

        # 6. Batch of notifications only -> 202
        batch_notif = await client.post(
            "/mcp",
            headers=headers,
            json=[{"jsonrpc": "2.0", "method": "notifications/initialized"}],
        )
        assert batch_notif.status_code == 202

        # 7. Parse / validation / unknown method / internal error handling
        bad_json = await client.post("/mcp", headers=headers, data=b"{not-json")
        assert bad_json.status_code == 400
        assert (await bad_json.get_json())["error"]["code"] == -32700

        empty_batch = await client.post("/mcp", headers=headers, json=[])
        assert empty_batch.status_code == 400
        assert (await empty_batch.get_json())["error"]["code"] == -32600

        unknown_method = await (
            await client.post(
                "/mcp",
                headers=headers,
                json={"jsonrpc": "2.0", "id": 13, "method": "unknown/method"},
            )
        ).get_json()
        assert unknown_method["error"]["code"] == -32601

        unknown_tool = await (
            await client.post(
                "/mcp",
                headers=headers,
                json={
                    "jsonrpc": "2.0",
                    "id": 14,
                    "method": "tools/call",
                    "params": {"name": "nonexistent_tool", "arguments": {}},
                },
            )
        ).get_json()
        assert unknown_tool["result"]["isError"] is True

        # 8. Authenticated GET /mcp -> 405, DELETE /mcp -> 204
        get_mcp = await client.get("/mcp", headers=headers)
        assert get_mcp.status_code == 405
        del_mcp = await client.delete("/mcp", headers=headers)
        assert del_mcp.status_code == 204

        # 9. Internal unexpected error hides traceback
        async def _boom(*_a, **_kw):
            raise RuntimeError("secret_internal_db_dsn_leak")

        monkeypatch.setitem(
            __import__("apps.mcp.registry", fromlist=["TOOL_BY_NAME"]).TOOL_BY_NAME,
            "ping_boom_test",
            TOOL_BY_NAME["list_api_services"],
        )
        try:
            monkeypatch.setattr(
                "apps.mcp.http_app.execute_tool",
                _boom,
            )
            err_resp = await (
                await client.post(
                    "/mcp",
                    headers=headers,
                    json={
                        "jsonrpc": "2.0",
                        "id": 15,
                        "method": "tools/call",
                        "params": {"name": "list_api_services", "arguments": {}},
                    },
                )
            ).get_json()
            assert err_resp["result"]["isError"] is True
            assert "secret_internal_db_dsn_leak" not in json.dumps(err_resp)
            assert "Internal tool execution error" in json.dumps(err_resp)
        finally:
            TOOL_BY_NAME.pop("ping_boom_test", None)


@pytest.mark.asyncio
async def test_http_app_otel_signals_and_start_entrypoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """hub-mcp emits OTel logs, metrics (incl. histograms), and traces, and start.py invokes uvicorn."""
    span_exporter = InMemorySpanExporter()
    metric_reader = InMemoryMetricReader()
    log_exporter = InMemoryLogRecordExporter()

    tp = TracerProvider()
    tp.add_span_processor(SimpleSpanProcessor(span_exporter))

    mp = MeterProvider(metric_readers=[metric_reader])

    lp = LoggerProvider()
    lp.add_log_record_processor(SimpleLogRecordProcessor(log_exporter))

    def _fake_init_telemetry(service_name: str) -> dict[str, Any]:
        return {
            "trace_provider": tp,
            "meter_provider": mp,
            "logger_provider": lp,
            "enabled": True,
        }

    monkeypatch.setattr("apps.mcp.http_app.init_telemetry", _fake_init_telemetry)
    monkeypatch.setattr(
        "apps.mcp.http_app.get_tracer",
        lambda name="hub-mcp": tp.get_tracer(name),
    )
    monkeypatch.setattr(
        "apps.mcp.http_app.get_meter",
        lambda name="hub-mcp": mp.get_meter(name),
    )
    monkeypatch.setattr(
        "opentelemetry.metrics.get_meter",
        lambda name="hub-mcp": mp.get_meter(name),
    )

    def _upstream(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"tier": "enterprise"})

    app = create_app(
        elder_api_url="http://hub-api:5000",
        elder_transport=httpx.MockTransport(_upstream),
    )

    async with app.test_client() as client:
        assert (await client.get("/healthz")).status_code == 200
        resp = await client.post(
            "/mcp",
            headers={"Authorization": "Bearer otel-caller-jwt-secret"},
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {
                    "name": "invoke_api_operation",
                    "arguments": {
                        "service": "licensing",
                        "operation": "get_licensing_status",
                    },
                },
            },
        )
        assert resp.status_code == 200

    tp.force_flush()
    mp.force_flush()
    lp.force_flush()

    spans = span_exporter.get_finished_spans()
    assert len(spans) >= 1
    assert any("mcp.tool.invoke_api_operation" in s.name for s in spans)

    metrics_data = metric_reader.get_metrics_data()
    metric_names = {
        m.name
        for rm in metrics_data.resource_metrics
        for sm in rm.scope_metrics
        for m in sm.metrics
    }
    assert "mcp.requests.total" in metric_names
    assert "mcp.request.duration_ms" in metric_names
    assert "mcp.tool_calls.total" in metric_names
    assert "mcp.tool.duration_ms" in metric_names
    assert "mcp.upstream.duration_ms" in metric_names

    logs = log_exporter.get_finished_logs()
    assert len(logs) >= 1
    for log_item in logs:
        assert "otel-caller-jwt-secret" not in str(log_item.log_record.body)

    # Verify apps/mcp/start.py invokes uvicorn with factory=True
    captured_uvicorn: dict[str, Any] = {}

    def _fake_uvicorn_run(*args: Any, **kwargs: Any) -> None:
        captured_uvicorn["args"] = args
        captured_uvicorn["kwargs"] = kwargs

    fake_uvicorn = types.ModuleType("uvicorn")
    fake_uvicorn.run = _fake_uvicorn_run  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "uvicorn", fake_uvicorn)
    monkeypatch.setenv("MCP_HOST", "127.0.0.1")
    monkeypatch.setenv("MCP_PORT", "9091")
    mcp_start.main()
    assert captured_uvicorn["args"] == ("apps.mcp.http_app:create_app",)
    assert captured_uvicorn["kwargs"]["factory"] is True
    assert captured_uvicorn["kwargs"]["host"] == "127.0.0.1"
    assert captured_uvicorn["kwargs"]["port"] == 9091


# ---------------------------------------------------------------------------
# FastMCP server.py Hosted Mode & ContextVar Isolation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fastmcp_server_hosted_auth_middleware_and_get_client_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """FastMCP CallerAuthMiddleware rejects unauth requests even with ELDER_API_TOKEN set."""
    _install_fake_fastmcp(monkeypatch)
    sys.modules.pop("apps.mcp.server", None)
    import apps.mcp.server as mcp_server

    monkeypatch.setenv("ELDER_API_TOKEN", "env-token-should-be-ignored")
    monkeypatch.setenv("ELDER_API_URL", "http://hub-api:5000")

    # Hosted mode via MCP_TRANSPORT fails closed when no caller context exists.
    monkeypatch.setenv("MCP_TRANSPORT", "streamable-http")
    with pytest.raises(ElderAuthError):
        mcp_server.get_client()

    # Stdio mode uses env client.
    monkeypatch.setenv("MCP_TRANSPORT", "stdio")
    mcp_server._client = None
    stdio_client = mcp_server.get_client()
    assert stdio_client.session.access_token == "env-token-should-be-ignored"
    await stdio_client.close()
    mcp_server._client = None

    # Test CallerAuthMiddleware directly on streamable-http and sse apps.
    monkeypatch.setenv("MCP_TRANSPORT", "streamable-http")
    observed_tokens: list[tuple[str | None, str | None]] = []

    def _upstream(request: httpx.Request) -> httpx.Response:
        observed_tokens.append(
            (request.headers.get("authorization"), request.headers.get("x-api-key"))
        )
        return httpx.Response(200, json={"entities": []})

    async def _echo_app(scope, receive, send):
        if scope["type"] != "http":
            return
        # Execute a tool inside the middleware's context to verify get_client() isolation.
        c = mcp_server.get_client()
        try:
            await resources.resolve_village_id(c, "vid-1")
        finally:
            await c.close()
        body = b'{"ok":true}'
        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [(b"content-type", b"application/json")],
            }
        )
        await send({"type": "http.response.body", "body": body})

    monkeypatch.setattr(mcp_server, "_hosted_transport", httpx.MockTransport(_upstream))
    wrapped = mcp_server.CallerAuthMiddleware(_echo_app)

    # 1. Unauthenticated -> 401
    status, _hdrs, body, _events = await _run_asgi(wrapped, headers=[])
    assert status == 401
    assert json.loads(body) == {"error": "Authentication required"}

    # 2. Lifespan passes through
    status_ls, _h, _b, events_ls = await _run_asgi(wrapped, scope_type="lifespan")
    assert status_ls is None
    assert events_ls == []

    # 3. Unauthenticated websocket -> closes 1008
    _s, _h, _b, events_ws = await _run_asgi(wrapped, scope_type="websocket", headers=[])
    assert events_ws == [{"type": "websocket.close", "code": 1008}]

    # 4. Concurrent Tenant A and Tenant B calls through CallerAuthMiddleware
    await asyncio.gather(
        _run_asgi(wrapped, headers=[(b"authorization", b"Bearer tokA")]),
        _run_asgi(wrapped, headers=[(b"x-api-key", b"keyB")]),
    )
    assert ("Bearer tokA", None) in observed_tokens
    assert (None, "keyB") in observed_tokens
    assert get_caller_credentials() is None

    # 5. build_hosted_asgi_app works for both transports
    http_asgi = mcp_server.build_hosted_asgi_app(
        "streamable-http", elder_transport=httpx.MockTransport(_upstream)
    )
    sse_asgi = mcp_server.build_hosted_asgi_app(
        "sse", mount_path="/", elder_transport=httpx.MockTransport(_upstream)
    )
    st_unauth, _, _, _ = await _run_asgi(http_asgi, headers=[])
    assert st_unauth == 401
    st_sse_unauth, _, _, _ = await _run_asgi(sse_asgi, headers=[])
    assert st_sse_unauth == 401


# ---------------------------------------------------------------------------
# Catalog Parity, Village ID, and Path Traversal / SSRF Guards
# ---------------------------------------------------------------------------


def test_api_catalog_completeness_determinism_and_no_collisions() -> None:
    """api_catalog.json matches deterministic generator, covers key APIs, and blocks internal/non-api."""
    loaded = load_catalog(force_reload=True)
    generated = generate_catalog()
    assert [op.to_dict() for op in loaded] == generated
    assert 680 <= len(loaded) <= 715

    # Zero duplicate (service, operation)
    keys = [(op.service, op.operation) for op in loaded]
    assert len(keys) == len(set(keys))

    # Every cataloged path is non-internal /api/*
    for op in loaded:
        assert op.path.startswith("/api/")
        assert "/internal/" not in op.path
        assert ".." not in op.path

    # Required endpoints explicitly represented
    expected = {
        ("licensing", "get_licensing_status", "GET", "/api/v1/licensing/status"),
        ("id", "lookup_village_id", "GET", "/api/v1/id/{village_id}"),
        ("search", "run_ai_research", "POST", "/api/v1/search/ai-research"),
        (
            "search",
            "get_ai_research_config",
            "GET",
            "/api/v1/search/ai-research/config",
        ),
        ("discovery", "list_discovery_jobs", "GET", "/api/v1/discovery/jobs"),
        ("webhooks", "list_webhooks", "GET", "/api/v1/webhooks"),
        ("streams", "list_webhooks", "GET", "/api/v1/streams/{stream_id}/webhooks"),
    }
    actual = {(op.service, op.operation, op.method, op.path) for op in loaded}
    assert expected <= actual

    # list_services & list_operations filtering
    svcs = list_services(loaded)
    assert any(s["service"] == "licensing" and s["operation_count"] >= 1 for s in svcs)
    webhook_ops = list_operations(service="webhooks", method="POST", catalog=loaded)
    assert webhook_ops and all(
        op.service == "webhooks" and op.method == "POST" for op in webhook_ops
    )
    ai_ops = list_operations(search="ai-research", catalog=loaded)
    assert len(ai_ops) >= 2


@pytest.mark.asyncio
async def test_catalog_tools_encoding_traversal_rejection_and_api_errors() -> None:
    """Catalog tools URL-encode params, block traversal/SSRF/internal paths, and propagate 403/404."""
    seen: list[tuple[str, str, dict[str, str], Any]] = []

    def _handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode()) if request.content else None
        seen.append(
            (
                request.method,
                request.url.raw_path.decode("ascii"),
                dict(request.url.params),
                body,
            )
        )
        if (
            request.url.path == "/api/v1/documents/hello%20world"
            or request.url.path == "/api/v1/documents/hello world"
        ):
            return httpx.Response(200, json={"slug": "hello world"})
        if request.url.path == "/api/v1/entities/999":
            return httpx.Response(404, json={"error": "Entity not found"})
        if request.url.path == "/api/v1/licensing/status":
            return httpx.Response(403, json={"error": "Forbidden"})
        return httpx.Response(200, json={"ok": True})

    session = ElderSession.from_caller(
        "http://hub-api:5000",
        CallerCredentials(access_token="caller-tok-123"),
    )
    async with ElderClient(
        session=session, transport=httpx.MockTransport(_handler)
    ) as client:
        svcs = await api_catalog.list_api_services(client)
        assert svcs["total_operations"] >= 680

        ops = await api_catalog.list_api_operations(
            client, service="licensing", method="get"
        )
        assert ops["count"] >= 1
        assert ops["operations"][0]["operation"] == "get_licensing_status"

        desc = await api_catalog.describe_api_operation(
            client, service="id", operation="lookup_village_id"
        )
        assert desc["method"] == "GET"
        assert desc["path"] == "/api/v1/id/{village_id}"
        assert desc["path_params"] == ["village_id"]

        # URL-encoding of space in path param
        doc = await api_catalog.invoke_api_operation(
            client,
            service="documents",
            operation="get_document",
            path_params={"slug": "hello world"},
            query_params={"include": "versions"},
        )
        assert doc["slug"] == "hello world"
        assert seen[-1][1].startswith("/api/v1/documents/hello%20world")
        assert seen[-1][2] == {"include": "versions"}

        # Reject unknown operations / unregistered URLs
        with pytest.raises(ValueError, match="Unknown API operation"):
            await api_catalog.invoke_api_operation(
                client,
                service="evil",
                operation="http://169.254.169.254/latest/meta-data",
            )

        # Reject missing / unexpected path_params
        with pytest.raises(ValueError, match="Missing required path parameter"):
            await api_catalog.invoke_api_operation(
                client, service="id", operation="lookup_village_id"
            )
        with pytest.raises(ValueError, match="Unexpected path parameter"):
            await api_catalog.invoke_api_operation(
                client,
                service="licensing",
                operation="get_licensing_status",
                path_params={"extra": "x"},
            )

        # Reject body on GET
        with pytest.raises(ValueError, match="does not accept a request body"):
            await api_catalog.invoke_api_operation(
                client,
                service="licensing",
                operation="get_licensing_status",
                body={"x": 1},
            )

        # Reject path traversal and injection vectors in path_params and resolve_village_id
        for bad_val in (
            "../admin",
            "a/../../b",
            "%2e%2e",
            "%252e%252e",
            "foo/bar",
            "foo\\bar",
            "foo?admin=1",
            "foo#frag",
            "foo\r\nX-Inject: 1",
            "foo\x00bar",
            "   ",
        ):
            with pytest.raises(ValueError):
                await api_catalog.invoke_api_operation(
                    client,
                    service="id",
                    operation="lookup_village_id",
                    path_params={"village_id": bad_val},
                )
            with pytest.raises(ValueError):
                await resources.resolve_village_id(client, bad_val)

        # Reject non-/api/ and /internal/ routes even on synthetic APIOperation
        for bad_path in (
            "/internal/secrets",
            "/api/v1/integrations/internal/pending",
            "/etc/passwd",
            "http://evil.local/api/v1/x",
        ):
            bad_op = APIOperation(
                service="bad",
                operation="bad",
                method="GET",
                path=bad_path,
                path_params=(),
                summary="",
                has_body=False,
            )
            with pytest.raises(ValueError):
                resolve_operation_path(bad_op)

        # Propagate 403 and 404 ElderAPIError from hub-api
        with pytest.raises(ElderAPIError) as exc_404:
            await api_catalog.invoke_api_operation(
                client,
                service="entities",
                operation="get_entity",
                path_params={"id": 999},
            )
        assert exc_404.value.status_code == 404

        with pytest.raises(ElderAPIError) as exc_403:
            await execute_tool(
                "call_api_operation",
                client,
                {"service": "licensing", "operation": "get_licensing_status"},
            )
        assert exc_403.value.status_code == 403


def test_standalone_catalog_and_tools_importable() -> None:
    """Standalone apps/mcp imports catalog and api_catalog without repo root on PYTHONPATH."""
    mcp_dir = str(REPO_ROOT / "apps" / "mcp")
    env = dict(os.environ)
    env["PYTHONPATH"] = mcp_dir
    proc = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import catalog, registry, tools; "
                "assert len(catalog.load_catalog()) >= 680; "
                "assert 'invoke_api_operation' in registry.TOOL_BY_NAME; "
                "assert 'call_api_operation' in registry.TOOL_BY_NAME; "
                "assert 'resolve_village_id' in registry.TOOL_BY_NAME"
            ),
        ],
        cwd=mcp_dir,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr


@pytest.mark.asyncio
async def test_http_app_rejects_oversized_batch_and_body_and_allows_preflight() -> None:
    """Oversized batches and bodies are rejected; CORS preflight is not blocked by auth."""
    from apps.mcp import http_app

    app = create_app(
        elder_api_url="http://hub-api:5000",
        elder_transport=httpx.MockTransport(lambda r: httpx.Response(200, json={})),
    )
    headers = {"Authorization": "Bearer valid-caller-jwt"}
    ping = {"jsonrpc": "2.0", "id": 1, "method": "ping"}
    async with app.test_client() as client:
        too_many = await client.post(
            "/mcp", headers=headers, json=[ping] * (http_app.MAX_BATCH_SIZE + 1)
        )
        assert too_many.status_code == 400
        assert (await too_many.get_json())["error"]["code"] == -32600

        huge = await client.post(
            "/mcp", headers=headers, data=b"x" * (http_app.MAX_REQUEST_BYTES + 1)
        )
        assert huge.status_code == 413

        preflight = await client.options(
            "/mcp",
            headers={
                "Origin": "http://localhost:3000",
                "Access-Control-Request-Method": "POST",
            },
        )
        assert preflight.status_code != 401
