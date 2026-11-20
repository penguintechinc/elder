"""Hardening regression tests for hosted ``hub-mcp`` (deny list, limits, confirm, error hygiene)."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import random
import string
import subprocess
import sys
from pathlib import Path
from typing import Any

import httpx
import pytest
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader

from apps.mcp.catalog import (
    DEFAULT_CATALOG_PATH,
    DENIED_ROUTES,
    APIOperation,
    generate_catalog,
    get_operation,
    is_route_denied,
    list_operations,
    load_catalog,
    resolve_operation_path,
)
from apps.mcp.client import (
    ElderAPIError,
    ElderClient,
    ElderResponseTooLargeError,
    mask_secrets,
)
from apps.mcp.http_app import create_app
from apps.mcp.tools import api_catalog

REPO_ROOT = Path(__file__).resolve().parents[2]
AUTH = {"Authorization": "Bearer tok-hardening-a"}


def _rpc(method: str, params: dict[str, Any] | None = None, rid: Any = 1) -> dict:
    """Build a JSON-RPC 2.0 request dict."""
    out: dict[str, Any] = {"jsonrpc": "2.0", "id": rid, "method": method}
    if params is not None:
        out["params"] = params
    return out


def _call(name: str, arguments: dict[str, Any], rid: Any = 1) -> dict:
    """Build a tools/call JSON-RPC request."""
    return _rpc("tools/call", {"name": name, "arguments": arguments}, rid)


def _tool_payload(reply: dict[str, Any]) -> dict[str, Any]:
    """Decode the JSON text payload of a tools/call result."""
    return json.loads(reply["result"]["content"][0]["text"])


def _ok_upstream(_request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json={"ok": True})


def _make_app(handler: Any = _ok_upstream) -> Any:
    return create_app(
        elder_api_url="http://hub-api:5000",
        elder_transport=httpx.MockTransport(handler),
    )


# ---------------------------------------------------------------------------
# 0. Catalog determinism
# ---------------------------------------------------------------------------


def test_catalog_generation_is_deterministic_across_fresh_interpreters() -> None:
    """Fresh interpreters with different hash seeds build a byte-identical catalog equal to the committed JSON."""
    code = (
        "import hashlib, json, sys; sys.path.insert(0, '.');"
        "from apps.mcp.catalog import generate_catalog;"
        "print(hashlib.sha256(json.dumps(generate_catalog()).encode()).hexdigest())"
    )
    digests = set()
    for seed in ("0", "1", "4242", "random"):
        env = {**os.environ, "PYTHONHASHSEED": seed}
        out = subprocess.run(
            [sys.executable, "-c", code],
            cwd=REPO_ROOT,
            env=env,
            capture_output=True,
            text=True,
            check=True,
        )
        digests.add(out.stdout.strip())
    assert len(digests) == 1
    committed = json.loads(Path(DEFAULT_CATALOG_PATH).read_text(encoding="utf-8"))
    expected = hashlib.sha256(json.dumps(committed).encode()).hexdigest()
    assert digests == {expected}, "api_catalog.json is stale; run apps/mcp/catalog.py"


# ---------------------------------------------------------------------------
# 1. Deny list
# ---------------------------------------------------------------------------


def test_deny_list_hides_and_blocks_unauthenticated_and_credential_routes() -> None:
    """Denied routes never appear in the catalog and cannot be described or resolved."""
    assert len(DENIED_ROUTES) >= 30
    for must in (
        ("POST", "/api/v1/auth/login"),
        ("POST", "/api/v1/auth/register"),
        ("POST", "/api/v1/auth/refresh"),
        ("GET", "/api/v1/auth/captcha-challenge"),
        ("POST", "/api/v1/portal-auth/mfa/verify"),
        ("POST", "/api/v1/sso/saml/acs/{tenant_id}"),
        ("GET", "/api/v1/sso/oidc/callback"),
        ("GET", "/api/v1/documents/public/{slug}"),
        ("POST", "/api/v1/webhooks/receive/{slug}"),
        ("POST", "/api/v1/hooks/{token}"),
    ):
        assert is_route_denied(*must), must

    for op in load_catalog(force_reload=True):
        assert not is_route_denied(op.method, op.path), (op.method, op.path)
    for entry in generate_catalog():
        assert not is_route_denied(entry["method"], entry["path"])
    # Name-independent matching of path parameters.
    assert is_route_denied("POST", "/api/v1/sso/saml/acs/{anything_else}")
    assert not list_operations(search="/auth/login")

    with pytest.raises(ValueError):
        get_operation("auth", "login")
    with pytest.raises(ValueError):
        get_operation("auth", "register")

    # Direct resolution is rejected even for a hand-built operation object.
    forged = APIOperation(
        service="auth",
        operation="login",
        method="POST",
        path="/api/v1/auth/login",
        path_params=(),
        summary="forged",
        has_body=True,
    )
    with pytest.raises(ValueError, match="not eligible"):
        resolve_operation_path(forged)


@pytest.mark.asyncio
async def test_denied_operation_describe_and_invoke_fail() -> None:
    """describe/invoke tools reject a deny-listed operation and never reach upstream."""
    calls: list[str] = []

    def _upstream(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        return httpx.Response(200, json={"ok": True})

    client = ElderClient(
        base_url="http://hub-api:5000",
        access_token="tok-x",
        transport=httpx.MockTransport(_upstream),
    )
    try:
        for service, operation in (("auth", "login"), ("auth", "register")):
            with pytest.raises(ValueError):
                await api_catalog.describe_api_operation(client, service, operation)
            with pytest.raises(ValueError):
                await api_catalog.invoke_api_operation(
                    client, service, operation, body={"username": "a"}
                )
    finally:
        await client.close()
    assert calls == []


@pytest.mark.asyncio
async def test_denied_operation_over_http_returns_tool_error() -> None:
    """The hosted invoke tool answers isError for a deny-listed route without upstream traffic."""
    calls: list[str] = []

    def _upstream(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        return httpx.Response(200, json={"ok": True})

    app = _make_app(_upstream)
    async with app.test_client() as client:
        resp = await client.post(
            "/mcp",
            headers=AUTH,
            json=_call(
                "invoke_api_operation",
                {"service": "auth", "operation": "login", "body": {"u": "x"}},
            ),
        )
        reply = await resp.get_json()
    assert reply["result"]["isError"] is True
    assert calls == []


# ---------------------------------------------------------------------------
# 2. Metric label cardinality
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_request_metrics_never_label_raw_paths(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """50 random 404 paths collapse to a single ``unmatched`` label value."""
    reader = InMemoryMetricReader()
    provider = MeterProvider(metric_readers=[reader])
    monkeypatch.setattr(
        "apps.mcp.http_app.get_meter", lambda name="hub-mcp": provider.get_meter(name)
    )
    app = _make_app()
    rng = random.Random(7)
    async with app.test_client() as client:
        for _ in range(50):
            suffix = "".join(rng.choices(string.ascii_lowercase, k=12))
            resp = await client.get(f"/no/such/{suffix}", headers=AUTH)
            assert resp.status_code == 404
        await client.get("/healthz")

    paths: set[str] = set()
    for rm in reader.get_metrics_data().resource_metrics:
        for sm in rm.scope_metrics:
            for metric in sm.metrics:
                if metric.name != "mcp.requests.total":
                    continue
                for point in metric.data.data_points:
                    paths.add(point.attributes["path"])
    assert paths == {"unmatched", "/healthz"}


# ---------------------------------------------------------------------------
# 3. Batch / deadline / concurrency limits
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_batch_tool_call_cap_and_existing_caps(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """More than MCP_MAX_TOOL_CALLS_PER_BATCH tools/call entries is rejected; 50-item cap remains."""
    monkeypatch.setenv("MCP_MAX_TOOL_CALLS_PER_BATCH", "5")
    app = _make_app()
    call = _call("list_api_services", {})
    async with app.test_client() as client:
        ok = await client.post("/mcp", headers=AUTH, json=[call] * 5)
        assert ok.status_code == 200
        assert len(await ok.get_json()) == 5

        too_many = await client.post("/mcp", headers=AUTH, json=[call] * 6)
        assert too_many.status_code == 400
        err = (await too_many.get_json())["error"]
        assert err["code"] == -32600
        assert "max 5" in err["message"]

        pings = [_rpc("ping", rid=i) for i in range(51)]
        oversize = await client.post("/mcp", headers=AUTH, json=pings)
        assert oversize.status_code == 400
        many_pings = await client.post("/mcp", headers=AUTH, json=pings[:50])
        assert many_pings.status_code == 200


@pytest.mark.asyncio
async def test_request_deadline_returns_timeout_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A request exceeding MCP_REQUEST_TIMEOUT_S is aborted with a JSON-RPC timeout error."""
    monkeypatch.setenv("MCP_REQUEST_TIMEOUT_S", "0.2")

    async def _slow(_request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(5)
        return httpx.Response(200, json={"ok": True})

    app = _make_app(_slow)
    async with app.test_client() as client:
        started = asyncio.get_running_loop().time()
        resp = await client.post(
            "/mcp",
            headers=AUTH,
            json=_call(
                "invoke_api_operation",
                {"service": "licensing", "operation": "get_licensing_status"},
            ),
        )
        elapsed = asyncio.get_running_loop().time() - started
        assert resp.status_code == 504
        assert (await resp.get_json())["error"]["message"] == "Request timed out"
    assert elapsed < 3


@pytest.mark.asyncio
async def test_per_caller_concurrency_limit_returns_429(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A caller beyond MCP_MAX_CONCURRENT_PER_CALLER in-flight requests is refused; others are unaffected."""
    monkeypatch.setenv("MCP_MAX_CONCURRENT_PER_CALLER", "2")
    release = asyncio.Event()

    async def _gated(_request: httpx.Request) -> httpx.Response:
        await release.wait()
        return httpx.Response(200, json={"ok": True})

    app = _make_app(_gated)
    body = _call(
        "invoke_api_operation",
        {"service": "licensing", "operation": "get_licensing_status"},
    )
    async with app.test_client() as client:
        first = [
            asyncio.create_task(client.post("/mcp", headers=AUTH, json=body))
            for _ in range(2)
        ]
        await asyncio.sleep(0.3)
        refused = await client.post("/mcp", headers=AUTH, json=body)
        assert refused.status_code == 429
        assert "Too many concurrent" in (await refused.get_json())["error"]["message"]

        other = asyncio.create_task(
            client.post(
                "/mcp", headers={"Authorization": "Bearer tok-other"}, json=body
            )
        )
        await asyncio.sleep(0.2)
        release.set()
        statuses = [r.status_code for r in await asyncio.gather(*first, other)]
        assert statuses == [200, 200, 200]

        # Slots are released afterwards.
        again = await client.post("/mcp", headers=AUTH, json=body)
        assert again.status_code == 200


# ---------------------------------------------------------------------------
# 4. Response byte cap
# ---------------------------------------------------------------------------


class _ChunkedStream(httpx.AsyncByteStream):
    """Async stream with no content-length so the cap must be enforced while reading."""

    def __init__(self, chunks: list[bytes]) -> None:
        self._chunks = chunks

    async def __aiter__(self) -> Any:
        for chunk in self._chunks:
            yield chunk


@pytest.mark.asyncio
async def test_upstream_response_byte_cap_declared_and_streamed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Oversized upstream bodies are refused via content-length and via streamed chunks."""
    monkeypatch.setenv("MCP_MAX_RESPONSE_BYTES", "1024")

    def _declared(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"blob": "x" * 5000})

    def _streamed(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, stream=_ChunkedStream([b"[" + b"1," * 400] * 4))

    for handler in (_declared, _streamed):
        client = ElderClient(
            base_url="http://hub-api:5000",
            access_token="tok-x",
            transport=httpx.MockTransport(handler),
        )
        try:
            with pytest.raises(ElderResponseTooLargeError) as exc_info:
                await client.get("/api/v1/entities")
        finally:
            await client.close()
        assert "1024-byte limit" in exc_info.value.message

    small = ElderClient(
        base_url="http://hub-api:5000",
        access_token="tok-x",
        transport=httpx.MockTransport(_ok_upstream),
    )
    try:
        assert await small.get("/api/v1/entities") == {"ok": True}
    finally:
        await small.close()

    app = _make_app(_declared)
    async with app.test_client() as http:
        resp = await http.post(
            "/mcp",
            headers=AUTH,
            json=_call(
                "invoke_api_operation",
                {"service": "licensing", "operation": "get_licensing_status"},
            ),
        )
        reply = await resp.get_json()
    assert reply["result"]["isError"] is True
    assert "limit" in _tool_payload(reply)["error"]


# ---------------------------------------------------------------------------
# 5. Confirm gate + annotations
# ---------------------------------------------------------------------------


def _first_op(method: str) -> APIOperation:
    """Return a cataloged operation for ``method`` with no path parameters if possible."""
    ops = [op for op in load_catalog() if op.method == method]
    assert ops
    return sorted(ops, key=lambda op: (len(op.path_params), op.service, op.operation))[
        0
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["DELETE", "PUT", "PATCH"])
async def test_state_changing_invoke_requires_confirm(method: str) -> None:
    """DELETE/PUT/PATCH need confirm=true; without it upstream is never contacted."""
    op = _first_op(method)
    calls: list[tuple[str, str]] = []

    def _upstream(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.path))
        return httpx.Response(200, json={"ok": True})

    path_params = {name: "1" for name in op.path_params}
    base_args: dict[str, Any] = {
        "service": op.service,
        "operation": op.operation,
        "path_params": path_params,
    }
    app = _make_app(_upstream)
    async with app.test_client() as client:
        for tool in ("invoke_api_operation", "call_api_operation"):
            denied = await (
                await client.post("/mcp", headers=AUTH, json=_call(tool, base_args))
            ).get_json()
            assert denied["result"]["isError"] is True
            assert "confirm=true" in _tool_payload(denied)["error"]
            falsy = await (
                await client.post(
                    "/mcp",
                    headers=AUTH,
                    json=_call(tool, {**base_args, "confirm": False}),
                )
            ).get_json()
            assert falsy["result"]["isError"] is True
        assert calls == []

        granted = await (
            await client.post(
                "/mcp", headers=AUTH, json=_call(tool, {**base_args, "confirm": True})
            )
        ).get_json()
        assert granted["result"]["isError"] is False
    assert [m for m, _ in calls] == [method]


@pytest.mark.asyncio
async def test_safe_methods_do_not_require_confirm_and_annotations_are_destructive() -> (
    None
):
    """GET/POST invoke without confirm; invoke tools advertise destructiveHint true."""
    app = _make_app()
    async with app.test_client() as client:
        got = await (
            await client.post(
                "/mcp",
                headers=AUTH,
                json=_call(
                    "invoke_api_operation",
                    {"service": "licensing", "operation": "get_licensing_status"},
                ),
            )
        ).get_json()
        assert got["result"]["isError"] is False

        listing = await (
            await client.post("/mcp", headers=AUTH, json=_rpc("tools/list"))
        ).get_json()
    tools = {t["name"]: t for t in listing["result"]["tools"]}
    for name in ("invoke_api_operation", "call_api_operation"):
        ann = tools[name]["annotations"]
        assert ann["destructiveHint"] is True
        assert ann["readOnlyHint"] is False
        assert "confirm" in tools[name]["inputSchema"]["properties"]
    assert tools["list_api_services"]["annotations"] == {"readOnlyHint": True}


# ---------------------------------------------------------------------------
# 6. Error hygiene
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_upstream_error_bodies_and_transport_text_are_not_leaked(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Callers get status + short phrase only; internal URLs and bodies stay server-side and masked."""
    secret = "Bearer " + "s3cr3t" + "-value"

    def _upstream(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/status"):
            raise httpx.ConnectError(
                "connect failed http://10.9.8.7:5000/internal db password=hunter2"
            )
        return httpx.Response(
            500,
            text=f"Traceback in /srv/app/db.py host=10.9.8.7 {secret}",
        )

    app = _make_app(_upstream)
    body_500 = _call(
        "invoke_api_operation",
        {"service": "licensing", "operation": "get_licensing_status"},
    )
    caplog.set_level("DEBUG")
    async with app.test_client() as client:
        replies = []
        for _ in range(2):
            resp = await client.post("/mcp", headers=AUTH, json=body_500)
            replies.append(await resp.get_json())
        other = await (
            await client.post(
                "/mcp",
                headers=AUTH,
                json=_call(
                    "invoke_api_operation",
                    {"service": "licensing", "operation": "list_licensing_features"}
                    if any(
                        op.operation == "list_licensing_features"
                        for op in load_catalog()
                    )
                    else {
                        "service": "id",
                        "operation": "lookup_village_id",
                        "path_params": {"village_id": "v1"},
                    },
                ),
            )
        ).get_json()
    for reply in [*replies, other]:
        payload = _tool_payload(reply)
        flat = json.dumps(payload)
        assert reply["result"]["isError"] is True
        assert "10.9.8.7" not in flat and "Traceback" not in flat
        assert "hunter2" not in flat and "s3cr3t" not in flat
        assert "detail" not in payload
    assert _tool_payload(other)["error"] == "Internal Server Error"
    assert "s3cr3t" not in caplog.text
    assert "hunter2" not in caplog.text


@pytest.mark.asyncio
async def test_transport_error_message_is_generic() -> None:
    """ElderClient transport failures expose no URL/exception text and mask logs."""

    def _boom(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused http://internal-host:5000/x token=abc123")

    client = ElderClient(
        base_url="http://hub-api:5000",
        access_token="tok-x",
        transport=httpx.MockTransport(_boom),
    )
    try:
        with pytest.raises(ElderAPIError) as exc_info:
            await client.get("/api/v1/entities")
    finally:
        await client.close()
    assert exc_info.value.status_code == 0
    assert exc_info.value.message == "Upstream request failed"
    assert "internal-host" not in str(exc_info.value)
    assert "internal-host" not in repr(exc_info.value.__cause__)


def test_mask_secrets_redacts_credentials() -> None:
    """mask_secrets removes bearer tokens and credential-like query values."""
    text = mask_secrets("Authorization: Bearer abc.def password=hunter2&x=1")
    assert "abc.def" not in text and "hunter2" not in text


# ---------------------------------------------------------------------------
# 7. Probes
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_probes_expose_no_inventory() -> None:
    """/healthz and /readyz carry no version, tool or operation counts."""
    app = _make_app()
    async with app.test_client() as client:
        health = await (await client.get("/healthz")).get_json()
        ready = await (await client.get("/readyz")).get_json()
    assert health == {"status": "ok"}
    assert ready == {"status": "ok", "ready": True}
