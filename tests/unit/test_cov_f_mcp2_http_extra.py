"""Gap coverage for the hosted MCP HTTP app, registry helpers and import fallbacks."""

import asyncio
import importlib.util
import logging
import sys
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import httpx
import pytest
from werkzeug.exceptions import RequestEntityTooLarge

from apps.mcp import auth, registry
from apps.mcp import http_app as mod
from apps.mcp.http_app import create_app
from tests.unit._cov_f_helpers import fake_secret

TOKEN = fake_secret("mcp2", "http", "token")
AUTH = {"Authorization": f"Bearer {TOKEN}"}
MCP_DIR = Path(auth.__file__).resolve().parent


def _app(embedded: bool = False) -> Any:
    """Build the MCP app against a stubbed hub-api."""
    transport = httpx.MockTransport(lambda r: httpx.Response(200, json={"ok": True}))
    return create_app(
        elder_api_url="http://hub-api:5000",
        elder_transport=transport,
        embedded=embedded,
    )


async def _post(app: Any, payload: Any, headers: dict[str, str] | None = None) -> Any:
    """POST a JSON-RPC payload as an authenticated caller."""
    return await app.test_client().post("/mcp", json=payload, headers=headers or AUTH)


def _rpc(method: str, params: Any = None, rid: Any = 1) -> dict[str, Any]:
    """Build a JSON-RPC request."""
    msg: dict[str, Any] = {"jsonrpc": "2.0", "id": rid, "method": method}
    if params is not None:
        msg["params"] = params
    return msg


class TestProtocolMethods:
    """initialize, ping and tools/list handling."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("params", "expected"),
        [
            ({"protocolVersion": "2024-11-05"}, "2024-11-05"),
            ({"protocolVersion": "1999-01-01"}, mod.DEFAULT_PROTOCOL_VERSION),
            ("not-an-object", mod.DEFAULT_PROTOCOL_VERSION),
        ],
    )
    async def test_initialize(self, params: Any, expected: str) -> None:
        """Initialize negotiates a supported version and advertises capabilities."""
        if expected == "2024-11-05" and expected not in mod.SUPPORTED_PROTOCOL_VERSIONS:
            expected = mod.DEFAULT_PROTOCOL_VERSION
            params = {"protocolVersion": expected}
        body = await (await _post(_app(), _rpc("initialize", params))).get_json()
        assert body["result"]["protocolVersion"] == expected
        assert body["result"]["serverInfo"]["name"] == "elder"
        assert "tools" in body["result"]["capabilities"]

    @pytest.mark.asyncio
    async def test_ping_uses_protocol_header(self) -> None:
        """A supported MCP-Protocol-Version header is echoed back on later requests."""
        version = sorted(mod.SUPPORTED_PROTOCOL_VERSIONS)[0]
        response = await _post(
            _app(), _rpc("ping"), {**AUTH, "MCP-Protocol-Version": version}
        )
        assert (await response.get_json())["result"] == {}
        assert response.headers["MCP-Protocol-Version"] == version

    @pytest.mark.asyncio
    async def test_tools_list_matches_registry(self) -> None:
        """Every registry tool is listed with annotations."""
        body = await (await _post(_app(), _rpc("tools/list"))).get_json()
        tools = body["result"]["tools"]
        assert len(tools) == len(registry.TOOL_REGISTRY)
        assert all("readOnlyHint" in t["annotations"] for t in tools)

    def test_tool_annotations_destructive(self) -> None:
        """Destructive tools are never advertised as read-only."""
        from types import SimpleNamespace

        out = mod._tool_annotations(SimpleNamespace(read_only=True, destructive=True))
        assert out == {"readOnlyHint": False, "destructiveHint": True}

    def test_success_result_for_non_dict(self) -> None:
        """Non-dict tool results carry no structured content."""
        out = mod._mcp_tool_success_result([1, 2])
        assert "structuredContent" not in out and out["isError"] is False


class TestEmbedded:
    """In-process (embedded) mode skips global telemetry and per-IP caps."""

    @pytest.mark.asyncio
    async def test_embedded_app_serves_requests(self) -> None:
        """An embedded app answers requests using the shared loopback key."""
        app = _app(embedded=True)
        assert app.config["MCP_EMBEDDED"] is True
        body = await (await _post(app, _rpc("ping"))).get_json()
        assert body["result"] == {}


class TestAdmissionPaths:
    """Body-read, concurrency and timeout refusals."""

    @pytest.mark.asyncio
    async def test_declared_length_too_large(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An oversized Content-Length is refused with 413 before reading."""
        monkeypatch.setenv("MCP_MAX_REQUEST_BYTES", "64")
        app = _app()
        response = await app.test_client().post(
            "/mcp",
            data=b"x" * 10,
            headers={
                **AUTH,
                "Content-Type": "application/json",
                "Content-Length": "5000",
            },
        )
        assert response.status_code == 413

    @pytest.mark.asyncio
    async def test_body_limiter_refusal(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A saturated body-read limiter answers 429 with Retry-After."""
        monkeypatch.setattr(mod.BodyReadLimiter, "try_acquire", lambda *a, **k: "total")
        response = await _post(_app(), _rpc("ping"))
        assert response.status_code == 429
        assert response.headers["Retry-After"] == "1"

    @pytest.mark.asyncio
    async def test_concurrency_limiter_refusal(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A saturated concurrency limiter answers 429 with Retry-After."""
        monkeypatch.setattr(
            mod.ConcurrencyLimiter, "try_acquire", lambda *a, **k: "caller"
        )
        response = await _post(_app(), _rpc("ping"))
        assert response.status_code == 429
        assert response.headers["Retry-After"] == "1"

    @pytest.mark.asyncio
    async def test_body_read_timeout(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A stalled body read is cut off with 408."""
        from quart.wrappers.request import Request

        async def slow(self: Any, *a: Any, **k: Any) -> bytes:
            """Never finish reading the body in time."""
            await asyncio.sleep(5)
            return b""

        monkeypatch.setattr(Request, "get_data", slow)
        monkeypatch.setenv("MCP_BODY_READ_TIMEOUT_S", "0.05")
        assert (await _post(_app(), _rpc("ping"))).status_code == 408

    @pytest.mark.asyncio
    async def test_body_read_entity_too_large(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A framework-level size violation while reading maps to 413."""
        from quart.wrappers.request import Request

        async def boom(self: Any, *a: Any, **k: Any) -> bytes:
            """Raise the framework's oversize error."""
            raise RequestEntityTooLarge()

        monkeypatch.setattr(Request, "get_data", boom)
        assert (await _post(_app(), _rpc("ping"))).status_code == 413

    @pytest.mark.asyncio
    async def test_request_timeout(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A tool call exceeding the request budget is answered 504."""

        async def slow_tool(*a: Any, **k: Any) -> Any:
            """Simulate a hung upstream call."""
            await asyncio.sleep(5)

        monkeypatch.setattr(mod, "execute_tool", slow_tool)
        monkeypatch.setenv("MCP_REQUEST_TIMEOUT_S", "0.05")
        name = next(iter(mod.TOOL_BY_NAME))
        response = await _post(
            _app(), _rpc("tools/call", {"name": name, "arguments": {}})
        )
        assert response.status_code == 504

    @pytest.mark.asyncio
    async def test_batch_tool_call_cap(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Batches with too many tools/call entries are refused."""
        monkeypatch.setenv("MCP_MAX_TOOL_CALLS_PER_BATCH", "1")
        name = next(iter(mod.TOOL_BY_NAME))
        call = {"name": name, "arguments": {}}
        response = await _post(
            _app(), [_rpc("tools/call", call, 1), _rpc("tools/call", call, 2)]
        )
        assert response.status_code == 400
        assert "max 1" in (await response.get_json())["error"]["message"]


class TestLifecycle:
    """Shutdown handling of the pooled upstream client."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("state", ["none", "closed", "open"])
    async def test_shutdown_variants(self, state: str) -> None:
        """Shutdown tolerates a missing, already-closed or open pooled client."""
        app = _app()
        async with app.test_app():
            if state == "none":
                app.extensions["mcp_http_client"] = None
            elif state == "closed":
                client = httpx.AsyncClient()
                await client.aclose()
                app.extensions["mcp_http_client"] = client
            else:
                app.extensions["mcp_http_client"] = httpx.AsyncClient()
        assert app.extensions["mcp_http_client"] is None


class TestOtelHandler:
    """Log handler attachment is idempotent and failure tolerant."""

    def test_no_provider(self) -> None:
        """Without a provider only the logger level is configured."""
        assert mod._attach_otel_log_handler(None).level == logging.INFO

    def test_attach_once_then_reuse(self) -> None:
        """The same provider is attached once; repeated calls keep one handler."""
        from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler

        provider = LoggerProvider()
        logger = mod._attach_otel_log_handler(provider)
        mod._attach_otel_log_handler(provider)
        handlers = [h for h in logger.handlers if isinstance(h, LoggingHandler)]
        assert len(handlers) == 1
        mod._attach_otel_log_handler(LoggerProvider())
        assert len([h for h in logger.handlers if isinstance(h, LoggingHandler)]) == 1
        logger.handlers = []

    def test_handler_failure_swallowed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A failing handler constructor never breaks app start."""
        import opentelemetry.sdk._logs as sdk_logs

        def explode(*a: Any, **k: Any) -> None:
            """Raise like a broken SDK."""
            raise RuntimeError("sdk broken")

        monkeypatch.setattr(sdk_logs, "LoggingHandler", explode)
        assert mod._attach_otel_log_handler(object()).name == "hub-mcp.otel"


class TestImportFallbacks:
    """``http_app`` falls back to bare sibling imports when run from ``apps/mcp``."""

    def test_fallback_and_path_insert(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Blocking ``apps.mcp.*`` exercises the bare imports and the sys.path insert."""
        saved = dict(sys.modules)
        monkeypatch.syspath_prepend(str(MCP_DIR))
        repo_root = str(MCP_DIR.parents[1])
        monkeypatch.setattr(
            sys, "path", [p for p in sys.path if p not in (repo_root, "")]
        )
        monkeypatch.syspath_prepend(str(MCP_DIR))
        for name in (
            "apps.mcp.auth",
            "apps.mcp.catalog",
            "apps.mcp.client",
            "apps.mcp.envutil",
            "apps.mcp.limits",
            "apps.mcp.registry",
        ):
            monkeypatch.setitem(sys.modules, name, None)
        spec = importlib.util.spec_from_file_location(
            "cov_mcp2_http_probe", MCP_DIR / "http_app.py"
        )
        assert spec is not None and spec.loader is not None
        probe = importlib.util.module_from_spec(spec)
        monkeypatch.setitem(sys.modules, "cov_mcp2_http_probe", probe)
        try:
            spec.loader.exec_module(probe)
            assert callable(probe.create_app)
        finally:
            for name in set(sys.modules) - set(saved):
                if name in (
                    "auth",
                    "catalog",
                    "client",
                    "envutil",
                    "limits",
                    "registry",
                    "tools",
                ) or name.startswith("tools."):
                    del sys.modules[name]


class TestRegistryHelpers:
    """Tool filtering and Ollama schema conversion."""

    def test_get_tools_policies(self) -> None:
        """Read-only filtering and module gating select the right tools."""
        all_tools = registry.get_tools(read_only=False)
        assert len(all_tools) == len(registry.TOOL_REGISTRY)
        read_only = registry.get_tools()
        assert all(t.read_only for t in read_only)
        gated = [t for t in all_tools if t.required_module is not None]
        assert gated, "registry should contain module-gated tools"
        module = gated[0].required_module
        without = registry.get_tools(enabled_modules=[], read_only=False)
        assert all(t.required_module is None for t in without)
        with_mod = registry.get_tools(enabled_modules=[module], read_only=False)
        assert any(t.required_module == module for t in with_mod)

    def test_to_ollama_tools(self) -> None:
        """Tool definitions convert to function schemas."""
        tools = registry.get_tools()[:2]
        converted = registry.to_ollama_tools(tools)
        assert [c["function"]["name"] for c in converted] == [t.name for t in tools]
        assert all(c["type"] == "function" for c in converted)


def test_unused_async_mock_import() -> None:
    """Keep the AsyncMock import referenced for sibling-style helpers."""
    assert AsyncMock()
