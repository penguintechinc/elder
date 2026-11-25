"""Coverage for the FastMCP stdio/hosted server wrapper in ``apps.mcp.server``."""

import importlib.util
import inspect
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from apps.mcp import auth
from apps.mcp.client import ElderClient
from tests.unit._cov_f_helpers import fake_secret

TOKEN = fake_secret("mcp2", "server", "token")
SERVER_PATH = Path(auth.__file__).resolve().parent / "server.py"
server: Any = None


class _FakeFastMCP:
    """Minimal FastMCP stand-in (the ``mcp`` SDK is not installed in the unit image)."""

    def __init__(self, name: str, host: str = "", port: int = 0) -> None:
        """Record settings and registered tools."""
        self.settings = SimpleNamespace(host=host, port=port, log_level="INFO")
        self.tools: dict[str, Any] = {}

    def tool(self, name: str, description: str, annotations: Any = None):
        """Return a decorator registering a tool by name."""

        def deco(fn: Any) -> Any:
            """Store the wrapped function."""
            self.tools[name] = (fn, annotations)
            return fn

        return deco

    def sse_app(self, mount_path: Any = None) -> str:
        """Return a sentinel ASGI app."""
        return "sse-app"

    def streamable_http_app(self) -> str:
        """Return a sentinel ASGI app."""
        return "http-app"

    def run(self, transport: str) -> None:
        """Pretend to run the stdio server."""

    def get_context(self) -> Any:
        """Return an empty context."""
        return SimpleNamespace(request_context=None)


@pytest.fixture(autouse=True)
def _load_server(monkeypatch: pytest.MonkeyPatch) -> Any:
    """Import ``apps/mcp/server.py`` against a stubbed ``mcp`` SDK for each test."""
    global server
    stubs = {
        "mcp": SimpleNamespace(),
        "mcp.server": SimpleNamespace(),
        "mcp.server.fastmcp": SimpleNamespace(FastMCP=_FakeFastMCP),
        "mcp.types": SimpleNamespace(
            ToolAnnotations=lambda **kw: SimpleNamespace(**kw)
        ),
        "mcp.server.auth": SimpleNamespace(),
        "mcp.server.auth.middleware": SimpleNamespace(),
        "mcp.server.auth.middleware.bearer_auth": SimpleNamespace(
            AuthenticatedUser=lambda token: SimpleNamespace(token=token)
        ),
        "mcp.server.auth.provider": SimpleNamespace(
            AccessToken=lambda **kw: SimpleNamespace(**kw)
        ),
    }
    for name, mod in stubs.items():
        monkeypatch.setitem(sys.modules, name, mod)
    spec = importlib.util.spec_from_file_location("cov_mcp2_server", SERVER_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, "cov_mcp2_server", module)
    spec.loader.exec_module(module)
    server = module
    return module


class _Sink:
    """Collects ASGI messages sent by the middleware under test."""

    def __init__(self) -> None:
        """Start with no recorded messages."""
        self.messages: list[dict[str, Any]] = []

    async def __call__(self, message: dict[str, Any]) -> None:
        """Record one outgoing ASGI message."""
        self.messages.append(message)


async def _receive() -> dict[str, Any]:
    """Return an empty ASGI receive event."""
    return {"type": "http.request", "body": b""}


class TestRequestHeaders:
    """Locating the live FastMCP request headers."""

    def test_no_get_context(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A server without ``get_context`` yields no headers."""
        monkeypatch.setattr(server.mcp, "get_context", None, raising=False)
        assert server._extract_active_mcp_request_headers() is None

    def test_context_error_is_swallowed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A failing ``get_context`` is treated as no active request."""
        monkeypatch.setattr(
            server.mcp, "get_context", MagicMock(side_effect=RuntimeError("x"))
        )
        assert server._extract_active_mcp_request_headers() is None

    def test_context_variants(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Headers are read from ``request_context.request`` when present."""
        headers = {"authorization": f"Bearer {TOKEN}"}
        ctx = SimpleNamespace(
            request_context=SimpleNamespace(request=SimpleNamespace(headers=headers))
        )
        monkeypatch.setattr(server.mcp, "get_context", lambda: ctx)
        assert server._extract_active_mcp_request_headers() == headers
        for bare in (
            SimpleNamespace(request_context=None),
            SimpleNamespace(request_context=SimpleNamespace(request=None)),
        ):
            monkeypatch.setattr(server.mcp, "get_context", lambda bare=bare: bare)
            assert server._extract_active_mcp_request_headers() is None


class TestGetClient:
    """Client selection is caller-scoped when hosted and shared in stdio."""

    def test_hosted_headers_build_scoped_client(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Live request headers yield a per-caller client, never the shared one."""
        monkeypatch.setenv("ELDER_URL", "http://hub-api:5000")
        monkeypatch.setattr(
            server,
            "_extract_active_mcp_request_headers",
            lambda: {"authorization": f"Bearer {TOKEN}"},
        )
        client = server.get_client()
        assert isinstance(client, ElderClient)
        assert client.session.access_token == TOKEN
        assert client is not server._client

    def test_hosted_without_headers_fails_closed(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Hosted mode with no locatable headers never falls back to ambient creds."""
        monkeypatch.setattr(server, "_extract_active_mcp_request_headers", lambda: None)
        monkeypatch.setenv("MCP_TRANSPORT", "sse")
        with pytest.raises(auth.ElderAuthError):
            server.get_client()

    def test_stdio_shares_one_client(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Stdio mode lazily creates and then reuses a single env-configured client."""
        monkeypatch.setattr(server, "_extract_active_mcp_request_headers", lambda: None)
        monkeypatch.delenv("MCP_TRANSPORT", raising=False)
        monkeypatch.setattr(server, "_client", None)
        monkeypatch.setenv("ELDER_API_KEY", TOKEN)
        first = server.get_client()
        assert server.get_client() is first


class TestCallerAuthMiddleware:
    """The ASGI wrapper enforces credentials before delegating."""

    @pytest.mark.asyncio
    async def test_non_http_scopes_pass_through(self) -> None:
        """Lifespan and unknown scope types bypass auth entirely."""
        inner = AsyncMock()
        mw = server.CallerAuthMiddleware(inner)
        for scope_type in ("lifespan", "other"):
            await mw({"type": scope_type}, _receive, _Sink())
        assert inner.await_count == 2

    @pytest.mark.asyncio
    async def test_missing_credentials_http_401(self) -> None:
        """An unauthenticated HTTP request is answered 401 and never reaches the app."""
        inner = AsyncMock()
        sink = _Sink()
        await server.CallerAuthMiddleware(inner)(
            {"type": "http", "headers": []}, _receive, sink
        )
        inner.assert_not_called()
        assert sink.messages[0]["status"] == 401
        assert b"Authentication required" in sink.messages[1]["body"]

    @pytest.mark.asyncio
    async def test_missing_credentials_websocket_close(self) -> None:
        """An unauthenticated websocket is closed with policy-violation code."""
        sink = _Sink()
        await server.CallerAuthMiddleware(AsyncMock())(
            {"type": "websocket"}, _receive, sink
        )
        assert sink.messages == [{"type": "websocket.close", "code": 1008}]

    @pytest.mark.asyncio
    @pytest.mark.parametrize("with_user", [True, False])
    async def test_authenticated_request_sets_and_resets_context(
        self, monkeypatch: pytest.MonkeyPatch, with_user: bool
    ) -> None:
        """The inner app sees caller context (and a user when available), then it is reset."""
        seen: dict[str, Any] = {}

        async def inner(scope: dict, receive: Any, send: Any) -> None:
            """Capture the caller context visible during the request."""
            seen["creds"] = auth.get_caller_credentials()
            seen["scope"] = scope

        if not with_user:
            monkeypatch.setitem(
                sys.modules, "mcp.server.auth.middleware.bearer_auth", None
            )
        headers = [(b"authorization", f"Bearer {TOKEN}".encode())]
        await server.CallerAuthMiddleware(inner)(
            {"type": "http", "headers": headers}, _receive, _Sink()
        )
        assert seen["creds"].access_token == TOKEN
        assert ("user" in seen["scope"]) is with_user
        assert auth.get_caller_credentials() is None


class TestHostedApp:
    """Hosted ASGI app construction for each transport."""

    @pytest.mark.parametrize("transport", ["sse", "streamable-http"])
    def test_builds_wrapped_app(self, transport: str) -> None:
        """Supported transports are wrapped in the auth middleware."""
        app = server.build_hosted_asgi_app(transport, elder_transport=None)  # type: ignore[arg-type]
        assert isinstance(app, server.CallerAuthMiddleware)

    def test_rejects_unknown_transport(self) -> None:
        """An unsupported transport name raises instead of silently defaulting."""
        with pytest.raises(ValueError, match="Unsupported"):
            server.build_hosted_asgi_app("carrier-pigeon")  # type: ignore[arg-type]


class TestToolWrapper:
    """Tool registration and the generated FastMCP wrapper."""

    @staticmethod
    def _definition(handler: Any) -> Any:
        """Build a minimal ToolDefinition stand-in around a handler."""
        return SimpleNamespace(
            name="demo_tool",
            description="Demo.",
            handler=handler,
            read_only=True,
            destructive=False,
        )

    @pytest.mark.asyncio
    async def test_wrapper_runs_and_closes_scoped_client(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The wrapper drops ``client`` from the signature and closes per-request clients."""

        async def handler(client: Any, name: str = "x"):  # no return annotation
            """Demo handler."""
            return name

        scoped = ElderClient(base_url="http://hub-api:5000", access_token=TOKEN)
        scoped.close = AsyncMock()  # type: ignore[method-assign]
        execute = AsyncMock(return_value={"ok": 1})
        monkeypatch.setattr(server, "get_client", lambda: scoped)
        monkeypatch.setattr(server, "execute_tool", execute)
        wrapper = server._build_mcp_wrapper(self._definition(handler))
        assert list(inspect.signature(wrapper).parameters) == ["name"]
        assert "return" not in wrapper.__annotations__
        assert await wrapper(name="y") == {"ok": 1}
        execute.assert_awaited_once()
        scoped.close.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_wrapper_keeps_shared_client_open(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The shared stdio client is never closed by the wrapper."""

        async def handler(client: Any) -> dict:
            """Demo handler."""
            return {}

        shared = ElderClient(base_url="http://hub-api:5000", access_token=TOKEN)
        shared.close = AsyncMock()  # type: ignore[method-assign]
        monkeypatch.setattr(server, "_client", shared)
        monkeypatch.setattr(server, "get_client", lambda: shared)
        monkeypatch.setattr(server, "execute_tool", AsyncMock(return_value={}))
        wrapper = server._build_mcp_wrapper(self._definition(handler))
        assert wrapper.__annotations__["return"] is dict
        await wrapper()
        shared.close.assert_not_awaited()

    def test_register_without_annotation_support(self) -> None:
        """Servers whose ``tool`` lacks ``annotations`` still register every tool."""
        calls: list[str] = []

        class Plain:
            """FastMCP stand-in with an old-style ``tool`` signature."""

            def tool(self, name: str, description: str):
                """Return a decorator that records the registered name."""
                calls.append(name)
                return lambda fn: fn

        names = server.register_all_tools(Plain())  # type: ignore[arg-type]
        assert names == calls and names

    def test_registered_tools_cover_registry(self) -> None:
        """Import-time registration exposed every registry tool."""
        assert server.REGISTERED_TOOLS


class TestMain:
    """Transport selection in ``main``."""

    def test_stdio(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The default transport runs FastMCP over stdio."""
        run = MagicMock()
        monkeypatch.setattr(server.mcp, "run", run)
        monkeypatch.delenv("MCP_TRANSPORT", raising=False)
        server.main()
        run.assert_called_once_with(transport="stdio")

    def test_invalid_transport(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """An unknown transport name is rejected."""
        monkeypatch.setenv("MCP_TRANSPORT", "bogus")
        with pytest.raises(ValueError, match="Unsupported MCP_TRANSPORT"):
            server.main()

    def test_hosted_transport_runs_uvicorn(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Hosted transports serve the wrapped app through uvicorn."""
        runner = MagicMock()
        monkeypatch.setattr("uvicorn.run", runner)
        monkeypatch.setenv("MCP_TRANSPORT", "streamable-http")
        server.main()
        assert isinstance(runner.call_args.args[0], server.CallerAuthMiddleware)


class TestServerImportFallbacks:
    """``server.py`` resolves siblings by bare name when run from ``apps/mcp``."""

    def test_fallback_imports_and_main_guard(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Blocking ``apps.mcp.*`` exercises the bare imports; ``__main__`` runs ``main``."""
        import runpy

        saved = dict(sys.modules)
        monkeypatch.syspath_prepend(str(SERVER_PATH.parent))
        for name in (
            "apps.mcp.envutil",
            "apps.mcp.auth",
            "apps.mcp.client",
            "apps.mcp.registry",
        ):
            monkeypatch.setitem(sys.modules, name, None)
        monkeypatch.setattr(_FakeFastMCP, "run", lambda self, transport: None)
        monkeypatch.delenv("MCP_TRANSPORT", raising=False)
        try:
            ns = runpy.run_path(str(SERVER_PATH), run_name="__main__")
            assert ns["REGISTERED_TOOLS"]
        finally:
            for name in set(sys.modules) - set(saved):
                if name in (
                    "auth",
                    "client",
                    "envutil",
                    "registry",
                    "tools",
                ) or name.startswith("tools."):
                    del sys.modules[name]
