"""Unit tests for worker utils, settings and the stream node guards.

Covers the multi-destination logger, the Elder API client, the structlog
setup, the settings model, and the SSRF / ReDoS guards used by stream nodes.
Only true external boundaries (HTTP sessions, sockets, DNS) are faked.
"""

import asyncio
import logging
import socket
import sys
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import aiohttp
import pytest
import structlog

import tests.unit.conftest_worker_stubs  # noqa: F401
from apps.worker.config.settings import Settings
from apps.worker.config.settings import settings as global_settings
from apps.worker.streams.nodes import net_guard, regex_guard
from apps.worker.utils import elder_client, multi_logger
from apps.worker.utils import logger as worker_logger
from apps.worker.utils.elder_client import (
    Dependency,
    ElderAPIClient,
    Entity,
    Identity,
    Organization,
)


def fake_key() -> str:
    """Build a credential-looking value at runtime so no literal secret is committed."""
    return "-".join(("cove", uuid4().hex[:8]))


@pytest.fixture
def restore_logging():
    """Save and restore root logger handlers/level and structlog config.

    The logging helpers mutate process-global state; this keeps tests isolated.
    """
    root = logging.getLogger()
    handlers, level = list(root.handlers), root.level
    cfg = structlog.get_config()
    yield root
    root.handlers = handlers
    root.setLevel(level)
    structlog.configure(**cfg)


# ---------------------------------------------------------------------------
# settings
# ---------------------------------------------------------------------------


class TestSettings:
    """Settings model validators and derived properties."""

    def test_defaults_and_regions_list(self):
        """Defaults load and the regions helper strips blanks."""
        s = Settings(_env_file=None, aws_regions=" us-east-1, ,eu-west-1 ,")
        assert s.aws_regions_list == ["us-east-1", "eu-west-1"]
        assert s.elder_api_url

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [("", None), (None, None), (7, 7), ("12", 12)],
    )
    def test_default_org_validator(self, raw, expected):
        """Empty/None map to None; real values are coerced to int."""
        s = Settings(_env_file=None, default_organization_id=raw)
        assert s.default_organization_id == expected

    def test_global_instance_exists(self):
        """The module-level settings singleton is a Settings instance."""
        assert isinstance(global_settings, Settings)


# ---------------------------------------------------------------------------
# logger.py
# ---------------------------------------------------------------------------


class TestWorkerLogger:
    """structlog configuration helper."""

    @pytest.mark.parametrize("fmt", ["json", "text"])
    def test_configure_logging(self, restore_logging, monkeypatch, fmt):
        """Both renderer branches configure structlog and init telemetry."""
        monkeypatch.setattr(worker_logger.settings, "log_format", fmt)
        with patch.object(worker_logger, "init_telemetry") as otel:
            worker_logger.configure_logging()
        otel.assert_called_once_with("svc-worker")
        procs = structlog.get_config()["processors"]
        last = procs[-1]
        expected = (
            structlog.processors.JSONRenderer
            if fmt == "json"
            else structlog.dev.ConsoleRenderer
        )
        assert isinstance(last, expected)

    def test_get_logger_returns_logger(self):
        """get_logger hands back a usable structlog logger."""
        log = worker_logger.get_logger("cov-e")
        assert hasattr(log, "info")


# ---------------------------------------------------------------------------
# multi_logger.py
# ---------------------------------------------------------------------------


def make_handler(**kw):
    """Build a KillKrillHandler with the httpx client replaced by a mock."""
    with patch.object(multi_logger.httpx, "Client") as client_cls:
        client_cls.return_value = MagicMock()
        handler = multi_logger.KillKrillHandler("https://kk.example.com/", **kw)
    return handler


def make_record(msg="hello", args=(), exc_info=None):
    """Create a log record for handler tests."""
    return logging.LogRecord("n", logging.INFO, __file__, 1, msg, args, exc_info)


class TestKillKrillHandler:
    """Batching HTTP log shipper."""

    def test_init_variants(self):
        """Constructor strips the URL slash and handles api key / http3 flags."""
        key = fake_key()
        h = make_handler(api_key=key, use_http3=False)
        assert h.killkrill_url == "https://kk.example.com"
        assert h.use_http3 is False
        h2 = make_handler()
        assert h2.api_key is None

    def test_emit_batches_without_flush(self):
        """Below the batch size and interval, records only accumulate."""
        h = make_handler(batch_size=10, flush_interval=3600)
        h.emit(make_record())
        assert len(h.batch) == 1
        h.client.post.assert_not_called()

    def test_emit_flushes_on_batch_size_with_extra_and_exc(self):
        """A full batch flushes; extra fields and exceptions are attached."""
        h = make_handler(batch_size=1, flush_interval=3600)
        h.client.post.return_value = MagicMock(status_code=200)
        rec = make_record()
        rec.extra = {"k": "v"}
        try:
            raise ValueError("boom")
        except ValueError:
            rec.exc_info = sys.exc_info()
        h.emit(rec)
        sent = h.client.post.call_args.kwargs["json"]["logs"][0]
        assert sent["k"] == "v"
        assert "ValueError" in sent["exception"]
        assert h.batch == []

    def test_emit_flushes_on_interval(self):
        """A zero flush interval flushes on every record."""
        h = make_handler(batch_size=100, flush_interval=0)
        h.client.post.return_value = MagicMock(status_code=200)
        h.emit(make_record())
        h.client.post.assert_called_once()

    def test_emit_swallows_errors(self, capsys):
        """A broken record never raises; the error goes to stderr."""
        h = make_handler()
        h.emit(make_record("%s %s", args=(1,)))
        assert "KillKrill logging error" in capsys.readouterr().err

    def test_flush_empty_is_noop(self):
        """Flushing an empty batch sends nothing."""
        h = make_handler()
        h.flush()
        h.client.post.assert_not_called()

    def test_flush_http_error_reports(self, capsys):
        """A 4xx/5xx response is reported to stderr but the batch is cleared."""
        h = make_handler()
        h.batch = [{"a": 1}]
        h.client.post.return_value = MagicMock(status_code=500, text="bad")
        h.flush()
        assert "batch upload failed" in capsys.readouterr().err
        assert h.batch == []

    def test_flush_exception_keeps_batch(self, capsys):
        """A transport failure keeps the batch for the next attempt."""
        h = make_handler()
        h.batch = [{"a": 1}]
        h.client.post.side_effect = RuntimeError("down")
        h.flush()
        assert "batch flush error" in capsys.readouterr().err
        assert h.batch == [{"a": 1}]

    def test_close_flushes_and_closes_client(self):
        """close() flushes remaining logs and closes the HTTP client."""
        h = make_handler()
        h.batch = [{"a": 1}]
        h.client.post.return_value = MagicMock(status_code=200)
        h.close()
        h.client.post.assert_called_once()
        h.client.close.assert_called_once()


class TestCorrelation:
    """Correlation id helpers and filter."""

    def test_filter_adds_id_once(self):
        """The filter stamps a correlation id and keeps an existing one."""
        f = multi_logger.CorrelationIDFilter()
        rec = make_record()
        assert f.filter(rec) is True
        first = rec.correlation_id
        f.filter(rec)
        assert rec.correlation_id == first

    def test_create_and_get_logger(self):
        """create_correlation_id yields uuids; get_logger binds when given one."""
        cid = multi_logger.create_correlation_id()
        assert len(cid) == 36
        assert multi_logger.get_logger("x") is not None
        bound = multi_logger.get_logger("x", correlation_id=cid)
        assert bound is not None


class TestConfigureMultiDestination:
    """Root logger wiring across console, syslog and KillKrill."""

    def apply(self, monkeypatch, **overrides):
        """Apply settings overrides then run the configurator."""
        for k, v in overrides.items():
            monkeypatch.setattr(multi_logger.settings, k, v)
        multi_logger.configure_multi_destination_logging()

    @pytest.mark.parametrize("fmt", ["json", "text"])
    def test_console_only(self, restore_logging, monkeypatch, fmt):
        """Console is always attached; format follows settings."""
        self.apply(
            monkeypatch,
            log_format=fmt,
            syslog_enabled=False,
            killkrill_enabled=False,
            log_level="DEBUG",
        )
        assert len(restore_logging.handlers) == 1
        assert restore_logging.level == logging.DEBUG

    def test_bad_level_falls_back(self, restore_logging, monkeypatch):
        """An unknown level name falls back to INFO."""
        self.apply(
            monkeypatch,
            log_level="nonsense",
            syslog_enabled=False,
            killkrill_enabled=False,
        )
        assert restore_logging.level == logging.INFO

    def test_syslog_and_killkrill_enabled(self, restore_logging, monkeypatch):
        """Optional handlers are added when enabled (both protocol labels)."""
        for http3 in (True, False):
            with (
                patch.object(multi_logger, "SysLogHandler") as sys_cls,
                patch.object(multi_logger, "KillKrillHandler") as kk_cls,
            ):
                sys_cls.return_value = logging.NullHandler()
                kk_cls.return_value = logging.NullHandler()
                self.apply(
                    monkeypatch,
                    syslog_enabled=True,
                    killkrill_enabled=True,
                    killkrill_use_http3=http3,
                    killkrill_api_key=fake_key(),
                )
            assert len(restore_logging.handlers) == 3

    def test_optional_handler_failures_are_logged(self, restore_logging, monkeypatch):
        """Constructor failures are caught so logging setup never aborts."""
        with (
            patch.object(multi_logger, "SysLogHandler", side_effect=OSError("no")),
            patch.object(multi_logger, "KillKrillHandler", side_effect=OSError("no")),
        ):
            self.apply(monkeypatch, syslog_enabled=True, killkrill_enabled=True)
        assert len(restore_logging.handlers) == 1


# ---------------------------------------------------------------------------
# elder_client.py
# ---------------------------------------------------------------------------


class FakeResponse:
    """Minimal aiohttp response stand-in usable as an async context manager."""

    def __init__(self, status=200, payload=None, error=None):
        """Store status/payload and an optional raise_for_status error."""
        self.status = status
        self.payload = payload if payload is not None else {"ok": True}
        self.error = error

    def raise_for_status(self):
        """Raise the configured error, mimicking aiohttp."""
        if self.error:
            raise self.error

    async def json(self):
        """Return the canned JSON body."""
        return self.payload

    async def __aenter__(self):
        """Enter the response context."""
        return self

    async def __aexit__(self, *exc):
        """Leave the response context."""
        return False


def http_error(status):
    """Build an aiohttp ClientResponseError with the given status."""
    return aiohttp.ClientResponseError(MagicMock(), (), status=status)


@pytest.fixture
def api():
    """An ElderAPIClient whose session is a MagicMock (set per test)."""
    client = ElderAPIClient(base_url="https://elder.example.com/", api_key=fake_key())
    client.session = MagicMock()
    return client


@pytest.fixture
def no_sleep(monkeypatch):
    """Make backoff retries instantaneous."""
    monkeypatch.setattr(asyncio, "sleep", AsyncMock())


class TestTransientClassifier:
    """_is_transient_client_error decisions."""

    def test_classification(self):
        """5xx and connection errors retry; 4xx do not."""
        f = elder_client._is_transient_client_error
        assert f(http_error(503)) is True
        assert f(http_error(401)) is False
        assert f(aiohttp.ClientConnectionError()) is True


class TestElderClientLifecycle:
    """Construction, session lifecycle and the request core."""

    def test_init_defaults_from_settings(self, monkeypatch):
        """Missing args fall back to settings; trailing slash is stripped."""
        monkeypatch.setattr(
            elder_client.settings, "elder_api_url", "http://x.example.com/"
        )
        monkeypatch.setattr(elder_client.settings, "elder_api_key", None)
        c = ElderAPIClient()
        assert c.base_url == "http://x.example.com"
        assert c.api_key is None

    @pytest.mark.asyncio
    async def test_connect_close_with_and_without_key(self):
        """connect() builds a session with auth header only when a key exists."""
        for key in (fake_key(), None):
            c = ElderAPIClient(base_url="http://x.example.com", api_key=key)
            c.api_key = key
            async with c as entered:
                assert entered.session is not None
                has_auth = "Authorization" in entered.session.headers
                assert has_auth == bool(key)
        await c.close()  # closing an already-closed session is tolerated

    @pytest.mark.asyncio
    async def test_close_without_session(self):
        """close() is a no-op when no session was ever created."""
        c = ElderAPIClient(base_url="http://x.example.com", api_key="k")
        await c.close()

    @pytest.mark.asyncio
    async def test_request_connects_lazily_and_handles_204(self):
        """_request opens a session on demand and maps 204 to an empty dict."""
        c = ElderAPIClient(base_url="http://x.example.com", api_key="k")
        session = MagicMock()
        session.request.return_value = FakeResponse(status=204)

        async def fake_connect():
            """Install the fake session like connect() would."""
            c.session = session

        with patch.object(c, "connect", side_effect=fake_connect):
            assert await c._request("GET", "/x", params={"a": 1}) == {}

    @pytest.mark.asyncio
    async def test_request_returns_json(self, api):
        """A 200 response returns the decoded body."""
        api.session.request.return_value = FakeResponse(payload={"id": 1})
        assert await api._request("GET", "/x", json={"a": 1}) == {"id": 1}

    @pytest.mark.asyncio
    async def test_request_retries_then_succeeds(self, api, no_sleep):
        """Transient failures are retried until success."""
        api.session.request.side_effect = [
            FakeResponse(error=http_error(503)),
            aiohttp.ClientConnectionError(),
            FakeResponse(payload={"ok": 1}),
        ]
        assert await api._request("GET", "/x") == {"ok": 1}
        assert api.session.request.call_count == 3

    @pytest.mark.asyncio
    async def test_request_gives_up_on_terminal_status(self, api, no_sleep):
        """A 401 is raised immediately without retrying."""
        api.session.request.return_value = FakeResponse(error=http_error(401))
        with pytest.raises(aiohttp.ClientResponseError):
            await api._request("GET", "/x")
        assert api.session.request.call_count == 1


class TestElderClientOperations:
    """Public API wrappers: each builds the right request."""

    @pytest.fixture(autouse=True)
    def _req(self, api):
        """Replace _request with a recording AsyncMock."""
        api._request = AsyncMock(return_value={"items": []})
        self.api = api

    @pytest.mark.asyncio
    async def test_organizations(self):
        """List/get/create/update organizations."""
        a = self.api
        await a.list_organizations(parent_id=3)
        assert a._request.call_args.kwargs["params"]["parent_id"] == 3
        await a.list_organizations()
        assert "parent_id" not in a._request.call_args.kwargs["params"]
        await a.get_organization(5)
        a._request.assert_called_with("GET", "/organizations/5")
        org = Organization(name="o", description="d", ldap_dn="dn")
        await a.create_organization(org)
        body = a._request.call_args.kwargs["json"]
        assert body == {"name": "o", "description": "d", "ldap_dn": "dn"}
        await a.update_organization(5, org)
        assert a._request.call_args.args[:2] == ("PATCH", "/organizations/5")

    @pytest.mark.asyncio
    async def test_entities(self):
        """List/get/create/update entities and external-id lookup."""
        a = self.api
        await a.list_entities(
            organization_id=1, entity_type="vm", external_id="e1", page=2
        )
        p = a._request.call_args.kwargs["params"]
        assert p["organization_id"] == 1 and p["entity_type"] == "vm"
        assert p["external_id"] == "e1"
        await a.list_entities()
        assert set(a._request.call_args.kwargs["params"]) == {"page", "per_page"}
        await a.get_entity(4)
        a._request.assert_called_with("GET", "/entities/4")
        ent = Entity(name="n", entity_type="vm", organization_id=1, tags=["t"])
        await a.create_entity(ent)
        assert a._request.call_args.kwargs["json"]["tags"] == ["t"]
        await a.update_entity(4, ent)
        assert a._request.call_args.args[:2] == ("PATCH", "/entities/4")

    @pytest.mark.asyncio
    async def test_get_entity_by_external_id(self):
        """Returns the first hit or None."""
        a = self.api
        a._request.return_value = {"items": [{"id": 9}]}
        assert await a.get_entity_by_external_id("x", organization_id=1) == {"id": 9}
        a._request.return_value = {"items": []}
        assert await a.get_entity_by_external_id("x") is None

    @pytest.mark.asyncio
    async def test_dependencies(self):
        """List, create and get-or-create dependency paths."""
        a = self.api
        await a.list_dependencies(source_entity_id=1, target_entity_id=2)
        p = a._request.call_args.kwargs["params"]
        assert p["source_entity_id"] == 1 and p["target_entity_id"] == 2
        await a.list_dependencies()
        await a.create_dependency(Dependency(1, 2, "uses"))
        assert a._request.call_args.kwargs["json"]["dependency_type"] == "uses"

        a._request.reset_mock()
        a._request.return_value = {"items": [{"dependency_type": "uses", "id": 7}]}
        assert (await a.get_or_create_dependency(1, 2, "uses"))["id"] == 7
        assert a._request.call_count == 1

        a._request.reset_mock()
        a._request.side_effect = [
            {"items": [{"dependency_type": "other"}]},
            {"id": 8},
        ]
        assert await a.get_or_create_dependency(1, 2, "uses", "d", {"a": 1}) == {
            "id": 8
        }

    @pytest.mark.asyncio
    async def test_identities(self):
        """List, create and get-or-create identity paths."""
        a = self.api
        await a.list_identities(auth_provider="ldap", auth_provider_id="u1")
        p = a._request.call_args.kwargs["params"]
        assert p["auth_provider"] == "ldap" and p["auth_provider_id"] == "u1"
        await a.list_identities()
        ident = Identity(username="u", identity_type="human", auth_provider="ldap")
        await a.create_identity(ident)
        assert "email" not in a._request.call_args.kwargs["json"]

        a._request.reset_mock()
        a._request.return_value = {"items": [{"id": 3}]}
        with_id = Identity("u", "human", "ldap", auth_provider_id="u1")
        assert await a.get_or_create_identity(with_id) == {"id": 3}

        a._request.reset_mock()
        a._request.side_effect = [{"items": []}, {"id": 4}]
        assert await a.get_or_create_identity(with_id) == {"id": 4}

        a._request.reset_mock(side_effect=True)
        a._request.return_value = {"id": 5}
        assert await a.get_or_create_identity(ident) == {"id": 5}

    @pytest.mark.asyncio
    async def test_health_check(self):
        """health_check maps success to True and any failure to False."""
        a = self.api
        assert await a.health_check() is True
        a._request.side_effect = RuntimeError("down")
        assert await a.health_check() is False


# ---------------------------------------------------------------------------
# net_guard.py
# ---------------------------------------------------------------------------


class TestNetGuard:
    """SSRF guard IP classification and URL validation."""

    @pytest.mark.parametrize(
        ("ip", "blocked"),
        [
            ("127.0.0.1", True),
            ("10.1.2.3", True),
            ("169.254.169.254", True),
            ("100.64.0.1", True),
            ("224.0.0.1", True),
            ("0.0.0.0", True),  # nosec B104 - classification input only
            ("::ffff:127.0.0.1", True),
            ("not-an-ip", True),
            ("93.184.216.34", False),
            ("2606:4700:4700::1111", False),
        ],
    )
    def test_is_blocked_ip(self, ip, blocked):
        """Internal/reserved/unparseable addresses are blocked."""
        assert net_guard.is_blocked_ip(ip) is blocked

    @pytest.mark.parametrize(
        "url", ["ftp://example.com/x", "file:///etc/hosts", "http:///nohost"]
    )
    def test_guard_rejects_bad_scheme_or_host(self, url):
        """Non-http(s) schemes and host-less URLs raise ValueError."""
        with pytest.raises(ValueError):
            net_guard.guard_ssrf(url)

    def test_guard_unresolvable_host(self):
        """DNS failures are surfaced as ValueError."""
        with patch.object(
            net_guard.socket, "getaddrinfo", side_effect=socket.gaierror("x")
        ):
            with pytest.raises(ValueError, match="Cannot resolve"):
                net_guard.guard_ssrf("https://nope.example.com/")

    def test_guard_blocks_internal_resolution(self):
        """A host resolving to a private address is blocked."""
        infos = [(2, 1, 6, "", ("10.0.0.5", 80))]
        with patch.object(net_guard.socket, "getaddrinfo", return_value=infos):
            with pytest.raises(ValueError, match="internal/reserved"):
                net_guard.guard_ssrf("http://svc.example.com/")

    @pytest.mark.parametrize(
        "url", ["http://a.example.com/", "https://a.example.com:8443/"]
    )
    def test_guard_allows_public(self, url):
        """Public resolution passes for both schemes and explicit ports."""
        infos = [(2, 1, 6, "", ("93.184.216.34", 443))]
        with patch.object(net_guard.socket, "getaddrinfo", return_value=infos):
            assert net_guard.guard_ssrf(url) is None


# ---------------------------------------------------------------------------
# regex_guard.py
# ---------------------------------------------------------------------------


class TestRegexGuard:
    """ReDoS guard bounds and timeout behaviour."""

    @pytest.mark.asyncio
    async def test_matches(self):
        """A safe pattern matches / does not match."""
        assert await regex_guard.safe_regex_search(r"ab+", "xabbx") is True
        assert await regex_guard.safe_regex_search(r"zz", "abc") is False

    @pytest.mark.asyncio
    async def test_rejections(self):
        """Non-str, oversize and nested-quantifier patterns are rejected."""
        with pytest.raises(TypeError):
            await regex_guard.safe_regex_search(123, "x")  # type: ignore[arg-type]
        with pytest.raises(regex_guard.UnsafeRegexError):
            await regex_guard.safe_regex_search("a" * 501, "x")
        with pytest.raises(regex_guard.UnsafeRegexError):
            await regex_guard.safe_regex_search("a", "x" * 10_001)
        with pytest.raises(regex_guard.UnsafeRegexError):
            await regex_guard.safe_regex_search(r"(a+)+$", "aaa")

    @pytest.mark.asyncio
    async def test_invalid_pattern_raises_re_error(self):
        """Uncompilable patterns raise re.error before any thread work."""
        import re

        with pytest.raises(re.error):
            await regex_guard.safe_regex_search("(", "x")

    @pytest.mark.asyncio
    async def test_timeout_propagates(self, monkeypatch):
        """A match exceeding the timeout raises TimeoutError."""
        import time

        monkeypatch.setattr(regex_guard, "MATCH_TIMEOUT_S", 0.05)
        monkeypatch.setattr(
            regex_guard, "_bounded_search", lambda p, t: time.sleep(0.3) or True
        )
        with pytest.raises(TimeoutError):
            await regex_guard.safe_regex_search("a", "a")
