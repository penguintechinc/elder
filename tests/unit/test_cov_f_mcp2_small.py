"""Coverage for the small hub-mcp modules: env parsing, limiters, entrypoints and import fallbacks."""

import importlib.util
import ipaddress
import logging
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from apps.mcp import auth, envutil, limits, start
from apps.mcp.catalog import APIOperation
from apps.mcp.tools import api_catalog
from tests.unit._cov_f_helpers import fake_secret

MCP_DIR = Path(auth.__file__).resolve().parent


def _probe(
    relative: str, blocked: tuple[str, ...], monkeypatch: pytest.MonkeyPatch
) -> Any:
    """Execute a source file under a probe name with the absolute imports blocked.

    Blocking ``apps.mcp.*`` forces the ``except ImportError`` fallback branch
    that imports the sibling modules as top-level names.
    """
    saved = dict(sys.modules)
    monkeypatch.syspath_prepend(str(MCP_DIR))
    for name in blocked:
        monkeypatch.setitem(sys.modules, name, None)
    spec = importlib.util.spec_from_file_location("cov_mcp2_probe", MCP_DIR / relative)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, "cov_mcp2_probe", module)
    try:
        spec.loader.exec_module(module)
    finally:
        for name in set(sys.modules) - set(saved):
            if name in ("auth", "client", "tools", "envutil", "catalog", "registry"):
                del sys.modules[name]
            elif name.startswith("tools."):
                del sys.modules[name]
    return module


class TestEnvUtil:
    """Environment parsing never raises and warns once per bad variable."""

    @pytest.fixture(autouse=True)
    def _reset_warned(self) -> None:
        """Clear the warn-once registry so each test observes its own warning."""
        envutil._warned.clear()

    def test_env_int_variants(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Unset, valid, junk and non-positive values map to value or default."""
        monkeypatch.delenv("COVF_INT", raising=False)
        assert envutil.env_int("COVF_INT", 7) == 7
        monkeypatch.setenv("COVF_INT", " 12 ")
        assert envutil.env_int("COVF_INT", 7) == 12
        monkeypatch.setenv("COVF_INT", "abc")
        assert envutil.env_int("COVF_INT", 7) == 7
        monkeypatch.setenv("COVF_INT", "-3")
        assert envutil.env_int("COVF_INT", 7) == 7

    def test_env_float_variants(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Floats reject junk, zero, negatives and infinity but accept positives."""
        monkeypatch.delenv("COVF_FLT", raising=False)
        assert envutil.env_float("COVF_FLT", 1.5) == 1.5
        monkeypatch.setenv("COVF_FLT", "2.25")
        assert envutil.env_float("COVF_FLT", 1.5) == 2.25
        for bad in ("nope", "0", "-1", "inf"):
            monkeypatch.setenv("COVF_FLT", bad)
            assert envutil.env_float("COVF_FLT", 1.5) == 1.5

    def test_warning_logged_once_and_value_not_leaked(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        """A rejected value warns exactly once and the raw value is never logged."""
        secret = fake_secret("env", "value")
        monkeypatch.setenv("COVF_ONCE", secret)
        with caplog.at_level(logging.WARNING, logger="hub-mcp.env"):
            envutil.env_int("COVF_ONCE", 3)
            envutil.env_int("COVF_ONCE", 3)
        assert len(caplog.records) == 1
        assert secret not in caplog.text


class TestLimits:
    """Admission control primitives fail closed and clean up after themselves."""

    def test_ipv6_prefix_len(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Valid values are honoured, anything else falls back to the default."""
        monkeypatch.delenv("MCP_IPV6_PREFIX_LEN", raising=False)
        assert limits.ipv6_prefix_len() == 64
        monkeypatch.setenv("MCP_IPV6_PREFIX_LEN", "48")
        assert limits.ipv6_prefix_len() == 48
        for bad in ("x", "8", "200"):
            monkeypatch.setenv("MCP_IPV6_PREFIX_LEN", bad)
            assert limits.ipv6_prefix_len() == 64

    @pytest.mark.parametrize(
        ("addr", "expected"),
        [
            (None, "unknown"),
            ("", "unknown"),
            ("not-an-ip", "unknown"),
            ("10.0.0.9", "10.0.0.9"),
            ("::ffff:10.0.0.9", "10.0.0.9"),
            ("2001:db8::1%eth0", "2001:db8::/64"),
        ],
    )
    def test_normalize_ip_key(self, addr: str | None, expected: str) -> None:
        """Addresses collapse to their limiter bucket key."""
        assert limits.normalize_ip_key(addr) == expected

    def test_concurrency_limiter_refusals_and_release(self) -> None:
        """Each cap yields its own refusal reason and release drops empty entries."""
        lim = limits.ConcurrencyLimiter()
        caps = {
            "max_total": 3,
            "max_per_caller": 2,
            "max_per_ip": 2,
            "max_tracked_callers": 2,
        }
        assert lim.try_acquire("a", "ip1", **caps) is None
        assert lim.try_acquire("a", "ip1", **caps) is None
        assert lim.try_acquire("a", "ip2", **caps) == "caller"
        assert lim.try_acquire("b", "ip1", **caps) == "ip"
        assert lim.try_acquire("b", "ip2", **caps) is None
        assert lim.try_acquire("c", "ip3", **caps) == "total"
        lim.total = 0
        assert lim.try_acquire("c", "ip3", **caps) == "tracked"
        lim.release("a", "ip1")
        assert lim.callers["a"] == 1
        lim.release("a", "ip1")
        assert "a" not in lim.callers and "ip1" not in lim.ips
        lim.release("zzz", "zzz")
        assert lim.total == 0

    def test_body_read_limiter(self) -> None:
        """Body reads are capped globally and per IP with a bounded table."""
        lim = limits.BodyReadLimiter()
        assert lim.try_acquire("ip1", max_total=2, max_per_ip=1) is None
        assert lim.try_acquire("ip1", max_total=2, max_per_ip=1) == "ip"
        assert lim.try_acquire("ip2", max_total=2, max_per_ip=1) is None
        assert lim.try_acquire("ip3", max_total=2, max_per_ip=1) == "total"
        lim.total = 0
        assert lim.try_acquire("ip3", max_total=2, max_per_ip=1) == "tracked"
        lim.ips["ip1"] = 2
        lim.release("ip1")
        assert lim.ips["ip1"] == 1
        lim.release("ip1")
        assert "ip1" not in lim.ips

    def test_parse_trusted_proxies_skips_invalid(self) -> None:
        """Invalid and blank entries are ignored; valid CIDRs and hosts parse."""
        nets = limits.parse_trusted_proxies("10.0.0.0/8, bogus, ,192.168.1.1")
        assert [str(n) for n in nets] == ["10.0.0.0/8", "192.168.1.1/32"]
        assert limits.parse_trusted_proxies(None) == []

    def test_in_helper(self) -> None:
        """Membership is False for unparsable addresses."""
        nets = [ipaddress.ip_network("10.0.0.0/8")]
        assert limits._in("10.1.2.3", nets) is True
        assert limits._in("junk", nets) is False

    def test_client_ip_resolution(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """XFF is honoured only from a trusted peer and the right-most untrusted hop wins."""
        monkeypatch.delenv("MCP_TRUSTED_PROXIES", raising=False)
        assert limits.client_ip("1.2.3.4", {"X-Forwarded-For": "9.9.9.9"}) == "1.2.3.4"
        assert limits.client_ip(None, {}) == "unknown"
        monkeypatch.setenv("MCP_TRUSTED_PROXIES", "10.0.0.0/8")
        assert limits.client_ip("1.2.3.4", {"X-Forwarded-For": "9.9.9.9"}) == "1.2.3.4"
        hdr = {"X-Forwarded-For": "7.7.7.7, 8.8.8.8, 10.0.0.2"}
        assert limits.client_ip("10.0.0.1", hdr) == "8.8.8.8"
        assert (
            limits.client_ip("10.0.0.1", {"X-Forwarded-For": "bad, 10.0.0.2"})
            == "10.0.0.1"
        )
        assert (
            limits.client_ip("10.0.0.1", {"X-Forwarded-For": "10.0.0.5, 10.0.0.2"})
            == "10.0.0.5"
        )
        assert limits.client_ip("10.0.0.1", {}) == "10.0.0.1"


class TestStart:
    """The uvicorn entrypoint wires env configuration into ``uvicorn.run``."""

    def test_main_passes_env_to_uvicorn(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Host and port come from the environment and the app is a factory."""
        runner = MagicMock()
        monkeypatch.setattr("uvicorn.run", runner)
        monkeypatch.setenv("MCP_HOST", "127.0.0.1")
        monkeypatch.setenv("MCP_PORT", "9191")
        start.main()
        args, kwargs = runner.call_args
        assert args == ("apps.mcp.http_app:create_app",)
        assert kwargs["factory"] is True
        assert kwargs["host"] == "127.0.0.1" and kwargs["port"] == 9191

    def test_fallback_import_path(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """When run from apps/mcp the sibling ``envutil`` module is used."""
        module = _probe("start.py", ("apps.mcp.envutil",), monkeypatch)
        assert callable(module.main)


class TestImportFallbacks:
    """Modules fall back to top-level sibling imports when run from ``apps/mcp``."""

    def test_package_init(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The package init resolves auth/client via the bare module names."""
        module = _probe(
            "__init__.py", ("apps.mcp.auth", "apps.mcp.client"), monkeypatch
        )
        assert "ElderClient" in module.__all__

    def test_tools_init(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The tools package init resolves its submodules via the bare name."""
        module = _probe("tools/__init__.py", ("apps.mcp.tools",), monkeypatch)
        assert "entities" in module.__all__

    def test_registry(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The registry resolves client/tools via the bare module names."""
        module = _probe(
            "registry.py", ("apps.mcp.client", "apps.mcp.tools"), monkeypatch
        )
        assert module.TOOL_REGISTRY


class TestAuthGaps:
    """Remaining validation branches of the credential helpers."""

    def test_mask_and_sanitize(self) -> None:
        """Empty values mask to empty and blank headers sanitize to ``None``."""
        assert auth.mask_secret("") == ""
        assert auth._sanitize_header_value(None) is None
        assert auth._sanitize_header_value("  ") is None
        assert auth._sanitize_header_value("a\nb") is None

    def test_hosted_mode_detection(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Hosted mode comes from the context var or the transport env var."""
        monkeypatch.delenv("MCP_TRANSPORT", raising=False)
        assert auth.is_hosted_mode() is False
        monkeypatch.setenv("MCP_TRANSPORT", "SSE")
        assert auth.is_hosted_mode() is True
        monkeypatch.setenv("MCP_TRANSPORT", "stdio")
        tokens = auth.set_caller_context(None, hosted=True)
        try:
            assert auth.is_hosted_mode() is True
        finally:
            auth.reset_caller_context(tokens)

    @pytest.mark.parametrize(
        "headers",
        [
            {"authorization": " "},
            {"authorization": "Basic abc"},
            {"authorization": "Bearer"},
            {"x-api-key": " "},
            {"x-api-key": "two words"},
            {},
        ],
    )
    def test_extract_rejects_malformed(self, headers: dict[str, str]) -> None:
        """Malformed or missing credentials fail closed."""
        with pytest.raises(auth.ElderAuthError):
            auth.extract_caller_credentials(headers)

    def test_extract_accepts_api_key(self) -> None:
        """A single-token X-API-Key header yields API-key credentials."""
        key = fake_secret("mcp2", "key")
        creds = auth.extract_caller_credentials({"X-API-Key": key})
        assert creds.api_key == key and creds.access_token is None

    def test_make_http_client_with_and_without_transport(self) -> None:
        """A custom transport is forwarded only when configured."""
        import httpx

        plain = auth.ElderSession("http://hub:5000")
        assert plain._make_http_client().base_url == "http://hub:5000"
        custom = auth.ElderSession(
            "http://hub:5000",
            transport=httpx.MockTransport(lambda r: httpx.Response(200)),
        )
        assert custom._make_http_client()._transport is custom._transport


def _op(
    method: str = "GET", path: str = "/api/v1/widgets/{wid}", **kw: Any
) -> APIOperation:
    """Build a synthetic catalog operation for the catalog-backed tools."""
    return APIOperation(
        service="widgets",
        operation="thing",
        method=method,
        path=path,
        path_params=("wid",),
        summary="s",
        has_body=False,
        **kw,
    )


class TestApiCatalogTools:
    """Catalog-backed tools validate input before touching the upstream client."""

    @pytest.mark.asyncio
    async def test_discovery_tools(self) -> None:
        """Service listing and operation filtering report counts and normalised method."""
        client = MagicMock()
        services = await api_catalog.list_api_services(client)
        assert services["total_services"] == len(services["services"])
        found = await api_catalog.list_api_operations(client, method=" get ")
        assert found["method"].strip() == "GET" and found["count"] == len(
            found["operations"]
        )
        blank = await api_catalog.list_api_operations(client, method="  ")
        assert blank["method"] is None
        described = await api_catalog.describe_api_operation(
            client,
            found["operations"][0]["service"],
            found["operations"][0]["operation"],
        )
        assert described == found["operations"][0]

    @pytest.mark.asyncio
    async def test_invoke_guards_and_call(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Destructive calls need confirm, GET refuses a body, and valid calls proxy upstream."""
        client = MagicMock()
        client.request = AsyncMock(return_value={"ok": True})
        current = {"op": _op("DELETE")}
        monkeypatch.setattr(api_catalog, "get_operation", lambda s, o: current["op"])

        with pytest.raises(ValueError, match="confirm=true"):
            await api_catalog.invoke_api_operation(
                client, "widgets", "thing", path_params={"wid": 1}
            )
        client.request.assert_not_called()

        out = await api_catalog.invoke_api_operation(
            client, "widgets", "thing", path_params={"wid": 1}, confirm=True
        )
        assert out == {"ok": True}
        assert client.request.call_args.args == ("DELETE", "/api/v1/widgets/1")

        current["op"] = _op("GET")
        with pytest.raises(ValueError, match="JSON object"):
            await api_catalog.invoke_api_operation(
                client,
                "widgets",
                "thing",
                path_params={"wid": 1},
                query_params=[1],  # type: ignore[arg-type]
            )
        with pytest.raises(ValueError, match="request body"):
            await api_catalog.invoke_api_operation(
                client, "widgets", "thing", path_params={"wid": 1}, body={"a": 1}
            )
        client.request.reset_mock()
        await api_catalog.call_api_operation(
            client, "widgets", "thing", path_params={"wid": 2}, query_params={"q": "x"}
        )
        assert client.request.call_args.kwargs == {"params": {"q": "x"}, "json": None}

    def test_namespace_unused(self) -> None:
        """Keep a reference to ``SimpleNamespace`` import used by sibling tests."""
        assert SimpleNamespace(a=1).a == 1


def test_start_main_guard(monkeypatch: pytest.MonkeyPatch) -> None:
    """Running ``start.py`` as a script invokes ``main`` through the module guard."""
    import runpy

    runner = MagicMock()
    monkeypatch.setattr("uvicorn.run", runner)
    runpy.run_path(str(MCP_DIR / "start.py"), run_name="__main__")
    runner.assert_called_once()
