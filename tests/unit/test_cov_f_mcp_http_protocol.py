"""Behavioural tests for the hosted MCP JSON-RPC surface and tool execution gating."""

import json
from typing import Any
from unittest.mock import patch

import httpx
import pytest

from apps.mcp import http_app as mod
from apps.mcp.client import ElderClient
from apps.mcp.http_app import (
    DEFAULT_PROTOCOL_VERSION,
    SUPPORTED_PROTOCOL_VERSIONS,
    create_app,
)
from apps.mcp.registry import TOOL_BY_NAME, _coerce_arg_value, execute_tool, invoke_tool
from tests.unit._cov_f_helpers import fake_secret

TOKEN = fake_secret("mcp", "caller", "token")
AUTH = {"Authorization": f"Bearer {TOKEN}"}


class _Upstream:
    """Records the requests hub-api would receive and replies with canned JSON."""

    def __init__(self, status: int = 200, body: Any = None):
        """Store the canned reply."""
        self.status = status
        self.body = {"id": 7, "name": "web"} if body is None else body
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        """Record and answer one request."""
        self.requests.append(request)
        return httpx.Response(self.status, json=self.body)


def _app(upstream: _Upstream | None = None) -> Any:
    """Build the hosted MCP app against a stubbed hub-api."""
    return create_app(
        elder_api_url="http://hub-api:5000",
        elder_transport=httpx.MockTransport(upstream or _Upstream()),
    )


async def _post(app: Any, payload: Any, headers: dict[str, str] | None = None) -> Any:
    """POST a JSON-RPC payload to /mcp as an authenticated caller."""
    client = app.test_client()
    return await client.post("/mcp", json=payload, headers=headers or AUTH)


def _rpc(method: Any, params: Any = None, **extra: Any) -> dict[str, Any]:
    """Build a JSON-RPC request with id 1 unless overridden."""
    message: dict[str, Any] = {"jsonrpc": "2.0", "id": 1, "method": method, **extra}
    if params is not None:
        message["params"] = params
    return message


def _tool_text(reply: dict[str, Any]) -> dict[str, Any]:
    """Decode the JSON text content of a tools/call result."""
    return json.loads(reply["result"]["content"][0]["text"])


class TestEnvelopeValidation:
    """JSON-RPC envelope handling."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "payload",
        [
            {"id": 1, "method": "tools/list"},
            {"jsonrpc": "1.0", "id": 1, "method": "tools/list"},
            {"jsonrpc": "2.0", "id": 1},
            {"jsonrpc": "2.0", "id": 1, "method": "   "},
            {"jsonrpc": "2.0", "id": 1, "method": 5},
        ],
        ids=["no-version", "wrong-version", "no-method", "blank-method", "non-string"],
    )
    async def test_malformed_envelopes_are_invalid_requests(
        self, payload: dict
    ) -> None:
        """Malformed envelopes get a -32600 error and never reach hub-api."""
        upstream = _Upstream()
        response = await _post(_app(upstream), payload)
        body = await response.get_json()
        assert response.status_code == 200
        assert body["error"] == {"code": -32600, "message": "Invalid Request"}
        assert upstream.requests == []

    @pytest.mark.asyncio
    @pytest.mark.parametrize("payload", ["just a string", 42, None])
    async def test_non_object_payloads_are_rejected(self, payload: Any) -> None:
        """A JSON body that is neither object nor batch is refused with HTTP 400."""
        response = await _post(_app(), payload)
        assert response.status_code == 400
        assert (await response.get_json())["error"]["code"] == -32600

    @pytest.mark.asyncio
    async def test_unparseable_body_is_a_parse_error(self) -> None:
        """Invalid JSON yields a -32700 parse error."""
        client = _app().test_client()
        response = await client.post(
            "/mcp",
            data=b"{not json",
            headers={**AUTH, "Content-Type": "application/json"},
        )
        assert response.status_code == 400
        assert (await response.get_json())["error"]["code"] == -32700

    @pytest.mark.asyncio
    async def test_notifications_and_idless_requests_get_no_body(self) -> None:
        """Notifications (and any request without an id) are accepted with 202, no body."""
        app = _app()
        for payload in (
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "method": "notifications/cancelled"},
            {"jsonrpc": "2.0", "method": "tools/list"},
        ):
            response = await _post(app, payload)
            assert response.status_code == 202
            assert await response.get_data() == b""

    @pytest.mark.asyncio
    async def test_notification_with_id_gets_empty_result(self) -> None:
        """A notification sent with an id is answered with an empty result."""
        response = await _post(_app(), _rpc("notifications/initialized"))
        assert await response.get_json() == {"jsonrpc": "2.0", "id": 1, "result": {}}

    @pytest.mark.asyncio
    async def test_unknown_method_and_resource(self) -> None:
        """Unknown methods and resources return the standard JSON-RPC error codes."""
        app = _app()
        unknown = await (await _post(app, _rpc("bogus/method"))).get_json()
        assert unknown["error"] == {
            "code": -32601,
            "message": "Method not found: bogus/method",
        }
        for params in ({"uri": "elder://nope"}, ["not", "an", "object"]):
            missing = await (
                await _post(app, _rpc("resources/read", params))
            ).get_json()
            assert missing["error"]["code"] == -32002
        prompts = await (await _post(app, _rpc("prompts/list"))).get_json()
        assert prompts["result"] == {"prompts": []}

    @pytest.mark.asyncio
    async def test_catalog_resource_is_listed_and_readable(self) -> None:
        """The API catalog resource is advertised and returns a services summary."""
        app = _app()
        listed = await (await _post(app, _rpc("resources/list"))).get_json()
        assert [r["uri"] for r in listed["result"]["resources"]] == [
            "elder://api/catalog"
        ]
        read = await (
            await _post(app, _rpc("resources/read", {"uri": "elder://api/catalog"}))
        ).get_json()
        catalog = json.loads(read["result"]["contents"][0]["text"])
        assert catalog["total_operations"] > 0
        assert catalog["total_services"] == len(catalog["services"]) > 0

    @pytest.mark.asyncio
    async def test_protocol_version_negotiated_from_header(self) -> None:
        """A supported MCP-Protocol-Version header is echoed; unknown ones fall back."""
        app = _app()
        supported = sorted(SUPPORTED_PROTOCOL_VERSIONS)[0]
        ok = await _post(
            app, _rpc("prompts/list"), {**AUTH, "MCP-Protocol-Version": supported}
        )
        assert ok.headers["MCP-Protocol-Version"] == supported
        fallback = await _post(
            app, _rpc("prompts/list"), {**AUTH, "MCP-Protocol-Version": "1999-01-01"}
        )
        assert fallback.headers["MCP-Protocol-Version"] == DEFAULT_PROTOCOL_VERSION


class TestBatches:
    """Batch handling."""

    @pytest.mark.asyncio
    async def test_empty_batch_is_invalid(self) -> None:
        """An empty batch array is an invalid request."""
        response = await _post(_app(), [])
        assert response.status_code == 400
        assert (await response.get_json())["error"]["code"] == -32600

    @pytest.mark.asyncio
    async def test_batch_of_only_notifications_returns_202(self) -> None:
        """A batch with nothing to answer returns 202 with no body."""
        response = await _post(
            _app(), [{"jsonrpc": "2.0", "method": "notifications/initialized"}]
        )
        assert response.status_code == 202 and await response.get_data() == b""

    @pytest.mark.asyncio
    async def test_mixed_batch_answers_only_requests(self) -> None:
        """Replies are returned only for entries with ids, in order."""
        response = await _post(
            _app(),
            [
                _rpc("prompts/list", id="a"),
                {"jsonrpc": "2.0", "method": "notifications/initialized"},
                "garbage",
                _rpc("nope", id="b"),
            ],
        )
        replies = await response.get_json()
        assert [r.get("id") for r in replies] == ["a", None, "b"]
        assert "result" in replies[0]
        assert replies[1]["error"]["code"] == -32600
        assert replies[2]["error"]["code"] == -32601


class TestToolCalls:
    """tools/call validation, tenant credential forwarding and error hygiene."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("params", "message"),
        [
            (["get_entity"], "Tool call params must be an object"),
            ({"arguments": {}}, "Tool name must be a non-empty string"),
            ({"name": "  "}, "Tool name must be a non-empty string"),
            ({"name": 7}, "Tool name must be a non-empty string"),
            (
                {"name": "get_entity", "arguments": [1]},
                "Tool arguments must be an object",
            ),
        ],
    )
    async def test_invalid_params_are_rejected(self, params: Any, message: str) -> None:
        """Malformed tools/call params are -32602 errors and never reach hub-api."""
        upstream = _Upstream()
        reply = await (
            await _post(_app(upstream), _rpc("tools/call", params))
        ).get_json()
        assert reply["error"] == {"code": -32602, "message": message}
        assert upstream.requests == []

    @pytest.mark.asyncio
    async def test_successful_call_forwards_caller_token(self) -> None:
        """A tool call runs with the caller's own bearer token, not a service identity."""
        upstream = _Upstream()
        reply = await (
            await _post(
                _app(upstream),
                _rpc(
                    "tools/call",
                    {"name": " get_entity ", "arguments": {"entity_id": "7"}},
                ),
            )
        ).get_json()
        assert reply["result"]["isError"] is False
        assert reply["result"]["structuredContent"] == {"id": 7, "name": "web"}
        (request,) = upstream.requests
        assert request.url.path == "/api/v1/entities/7"
        assert request.headers["Authorization"] == f"Bearer {TOKEN}"
        assert TOKEN not in json.dumps(reply)

    @pytest.mark.asyncio
    async def test_unknown_tool_and_missing_argument(self) -> None:
        """Unknown tools and missing required arguments are tool errors, not crashes."""
        app = _app()
        unknown = await (
            await _post(app, _rpc("tools/call", {"name": "no_such_tool"}))
        ).get_json()
        assert unknown["result"]["isError"] is True
        assert "Unknown MCP tool" in _tool_text(unknown)["error"]
        missing = await (
            await _post(
                app, _rpc("tools/call", {"name": "get_entity", "arguments": None})
            )
        ).get_json()
        assert missing["result"]["isError"] is True
        assert "requires argument 'entity_id'" in _tool_text(missing)["error"]

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("status", "phrase"),
        [
            (403, "Forbidden"),
            (404, "Not Found"),
            (429, "Too Many Requests"),
            (500, "Internal Server Error"),
        ],
    )
    async def test_upstream_errors_expose_only_the_reason_phrase(
        self, status: int, phrase: str
    ) -> None:
        """Upstream error bodies are replaced by the bare HTTP reason phrase."""
        secret = fake_secret("internal", "detail")
        upstream = _Upstream(status, {"message": f"stack trace token={secret}"})
        reply = await (
            await _post(
                _app(upstream),
                _rpc(
                    "tools/call", {"name": "get_entity", "arguments": {"entity_id": 7}}
                ),
            )
        ).get_json()
        assert reply["result"]["isError"] is True
        assert _tool_text(reply) == {"error": phrase, "status_code": status}
        assert secret not in json.dumps(reply)

    @pytest.mark.asyncio
    async def test_unexpected_tool_failure_is_generic(self) -> None:
        """An unexpected exception inside a tool yields a generic error, no details."""
        secret = fake_secret("boom", "detail")
        with patch.object(mod, "execute_tool", side_effect=RuntimeError(secret)):
            reply = await (
                await _post(
                    _app(),
                    _rpc(
                        "tools/call",
                        {"name": "get_entity", "arguments": {"entity_id": 7}},
                    ),
                )
            ).get_json()
        assert _tool_text(reply) == {"error": "Internal tool execution error"}
        assert secret not in json.dumps(reply)

    @pytest.mark.asyncio
    async def test_unauthenticated_calls_never_reach_tools(self) -> None:
        """Without credentials /mcp is refused; probes stay open and reveal nothing."""
        upstream = _Upstream()
        app = _app(upstream)
        client = app.test_client()
        refused = await client.post("/mcp", json=_rpc("tools/list"))
        assert refused.status_code == 401
        assert upstream.requests == []
        health = await client.get("/healthz")
        ready = await client.get("/readyz")
        assert await health.get_json() == {"status": "ok"}
        assert await ready.get_json() == {"status": "ok", "ready": True}
        assert (await client.get("/mcp", headers=AUTH)).status_code == 405
        assert (await client.delete("/mcp", headers=AUTH)).status_code == 204


class TestLifecycleAndConfig:
    """App lifecycle and configuration helpers."""

    @pytest.mark.asyncio
    async def test_pooled_client_created_on_startup_and_closed_on_shutdown(
        self,
    ) -> None:
        """The shared upstream pool exists while serving and is closed afterwards."""
        app = _app()
        await app.startup()
        pool = app.extensions["mcp_http_client"]
        assert isinstance(pool, httpx.AsyncClient) and pool.is_closed is False
        await app.shutdown()
        assert pool.is_closed is True
        assert app.extensions["mcp_http_client"] is None

    @pytest.mark.parametrize(
        ("env", "expected"),
        [("4.2.1", "4.2.1"), (" 9.9.9 ", "9.9.9")],
    )
    def test_app_version_prefers_real_env_version(
        self, monkeypatch: pytest.MonkeyPatch, env: str, expected: str
    ) -> None:
        """A real APP_VERSION wins over the version file."""
        monkeypatch.setenv("APP_VERSION", env)
        assert mod._resolve_app_version() == expected

    def test_app_version_placeholder_falls_back(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Any
    ) -> None:
        """Placeholder versions defer to the version file, then to the placeholder."""
        monkeypatch.setenv("APP_VERSION", "0.1.0")
        (tmp_path / ".version").write_text("7.7.7\n", encoding="utf-8")
        monkeypatch.setattr(mod, "Path", lambda _p: tmp_path / "no-app-version")
        monkeypatch.setattr(mod, "_REPO_ROOT", tmp_path)
        assert mod._resolve_app_version() == "7.7.7"
        monkeypatch.setattr(mod, "Path", lambda _p: tmp_path / "missing")
        monkeypatch.setattr(mod, "_REPO_ROOT", tmp_path / "also-missing")
        assert mod._resolve_app_version() == "0.1.0"
        monkeypatch.delenv("APP_VERSION")
        assert mod._resolve_app_version() == "0.0.0"

    def test_cors_origins_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Configured origins are split and trimmed; MCP-specific config wins."""
        monkeypatch.setenv("CORS_ORIGINS", "https://general.invalid")
        monkeypatch.setenv(
            "MCP_CORS_ORIGINS", " https://a.invalid , ,https://b.invalid"
        )
        assert mod._cors_origins() == ["https://a.invalid", "https://b.invalid"]
        monkeypatch.delenv("MCP_CORS_ORIGINS")
        assert mod._cors_origins() == ["https://general.invalid"]

    def test_jsonrpc_error_carries_optional_data(self) -> None:
        """Error objects include a data member only when one is provided."""
        assert mod._jsonrpc_error(3, -1, "x") == {
            "jsonrpc": "2.0",
            "id": 3,
            "error": {"code": -1, "message": "x"},
        }
        assert mod._jsonrpc_error(3, -1, "x", {"k": 1})["error"]["data"] == {"k": 1}

    def test_destructive_tools_are_never_annotated_read_only(self) -> None:
        """Destructive tools advertise destructiveHint and are not read-only."""
        for tool in TOOL_BY_NAME.values():
            annotations = mod._tool_annotations(tool)
            if getattr(tool, "destructive", False):
                assert annotations == {"readOnlyHint": False, "destructiveHint": True}
            else:
                assert annotations == {"readOnlyHint": bool(tool.read_only)}


class TestToolExecutionGating:
    """Registry-level read-only and module gates."""

    def _client(self, upstream: _Upstream) -> ElderClient:
        """Return an Elder client wired to the stub upstream."""
        return ElderClient(
            base_url="http://hub-api:5000",
            access_token=TOKEN,
            transport=httpx.MockTransport(upstream),
        )

    @pytest.mark.asyncio
    async def test_write_tools_blocked_in_read_only_mode(self) -> None:
        """invoke_tool defaults to read-only and refuses write tools before any request."""
        upstream = _Upstream()
        async with self._client(upstream) as client:
            with pytest.raises(PermissionError, match="write operation"):
                await invoke_tool("delete_relationship", client, {"dependency_id": 3})
            assert upstream.requests == []
            await execute_tool("delete_relationship", client, {"dependency_id": 3})
        assert [r.method for r in upstream.requests] == ["DELETE"]

    @pytest.mark.asyncio
    async def test_disabled_module_blocks_its_tools(self) -> None:
        """A tool whose module is disabled for the tenant is refused."""
        upstream = _Upstream()
        async with self._client(upstream) as client:
            with pytest.raises(
                PermissionError, match="requires module 'infrastructure'"
            ):
                await invoke_tool(
                    "get_entity", client, {"entity_id": 1}, allowed_modules=["security"]
                )
            assert upstream.requests == []
            await invoke_tool(
                "get_entity",
                client,
                {"entity_id": 1},
                allowed_modules=["infrastructure"],
            )
        assert len(upstream.requests) == 1

    @pytest.mark.asyncio
    async def test_argument_validation(self) -> None:
        """Unknown tools, non-object args and missing required args are rejected."""
        upstream = _Upstream()
        async with self._client(upstream) as client:
            with pytest.raises(ValueError, match="Unknown MCP tool"):
                await invoke_tool("nope", client, {})
            with pytest.raises(ValueError, match="must be a JSON object"):
                await invoke_tool("get_entity", client, ["x"])
            with pytest.raises(ValueError, match="requires argument 'entity_id'"):
                await invoke_tool("get_entity", client, {"entity_id": None, "junk": 1})
        assert upstream.requests == []

    @pytest.mark.parametrize(
        ("value", "schema", "expected"),
        [
            ("42", {"type": "integer"}, 42),
            (" -7 ", {"type": "integer"}, -7),
            ("4.5", {"type": "integer"}, "4.5"),
            ("TRUE", {"type": "boolean"}, True),
            (" false", {"type": "boolean"}, False),
            ("maybe", {"type": "boolean"}, "maybe"),
            ("x", {"type": "string"}, "x"),
            (5, {"type": "integer"}, 5),
            ("5", None, "5"),
            (None, {"type": "integer"}, None),
        ],
    )
    def test_llm_argument_coercion(
        self, value: Any, schema: Any, expected: Any
    ) -> None:
        """Stringified ints/bools from LLMs are coerced; everything else is untouched."""
        result = _coerce_arg_value(value, schema)
        assert result == expected and type(result) is type(expected)
