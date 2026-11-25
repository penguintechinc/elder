"""Behavioural tests for the Authentik connector's transport, pagination and write-back."""

from typing import Any
from unittest.mock import AsyncMock, patch

import httpx
import pytest

import tests.unit.conftest_worker_stubs  # noqa: F401
from apps.worker.config.settings import settings
from apps.worker.connectors import authentik_connector as mod
from apps.worker.connectors.authentik_connector import AuthentikConnector
from apps.worker.utils.elder_client import ElderAPIClient
from tests.unit._cov_f_helpers import (
    HttpxResponse,
    HttpxScript,
    assert_not_leaked,
    fake_secret,
    logged_text,
    spy_logger,
)

BASE = "https://auth.corp.invalid/api/v3"
PROBE = f"GET {BASE}/core/users/?page_size=1"
ADD = f"POST {BASE}/core/groups/g-1/add_user/"
REMOVE = f"POST {BASE}/core/groups/g-1/remove_user/"
GROUP = f"GET {BASE}/core/groups/g-1/"


def _connector(routes: dict[str, Any] | None = None) -> AuthentikConnector:
    """Return a connector attached to a scripted Authentik API and an Elder double."""
    c = AuthentikConnector()
    c.base_url = BASE
    c._http_client = HttpxScript(routes or {})
    c.elder_client = AsyncMock()
    c.elder_client.get_or_create_identity.return_value = {"id": 1}
    spy_logger(c)
    return c


class TestConnect:
    """Connection lifecycle."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("verify", [True, False])
    async def test_connect_uses_bearer_token_and_tls_setting(
        self, monkeypatch: pytest.MonkeyPatch, verify: bool
    ) -> None:
        """The token is sent as a Bearer header, TLS follows config, nothing is leaked."""
        token = fake_secret("authentik", "token")
        monkeypatch.setattr(settings, "authentik_domain", "auth.corp.invalid")
        monkeypatch.setattr(settings, "authentik_api_token", token)
        monkeypatch.setattr(settings, "authentik_verify_ssl", verify)
        c = AuthentikConnector()
        logger = spy_logger(c)
        client = HttpxScript({PROBE: [HttpxResponse({"results": []})]})
        elder = AsyncMock()
        with (
            patch.object(mod.httpx, "AsyncClient", return_value=client) as ctor,
            patch.object(mod, "ElderAPIClient", return_value=elder),
        ):
            await c.connect()

        assert c.base_url == BASE
        assert ctor.call_args.kwargs["headers"]["Authorization"] == f"Bearer {token}"
        assert ctor.call_args.kwargs["verify"] is verify
        assert client.keys() == [PROBE]
        elder.connect.assert_awaited_once()
        assert_not_leaked(token, logged_text(logger))

    @pytest.mark.asyncio
    @pytest.mark.parametrize(("domain", "token"), [(None, "t"), ("d", None)])
    async def test_connect_requires_domain_and_token(
        self, monkeypatch: pytest.MonkeyPatch, domain: Any, token: Any
    ) -> None:
        """Missing configuration fails fast without creating an HTTP client."""
        monkeypatch.setattr(settings, "authentik_domain", domain)
        monkeypatch.setattr(settings, "authentik_api_token", token)
        with (
            patch.object(mod.httpx, "AsyncClient") as ctor,
            pytest.raises(ValueError, match="domain and API token are required"),
        ):
            await AuthentikConnector().connect()
        ctor.assert_not_called()

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "probe",
        [HttpxResponse({}, 403), HttpxResponse({}, 500), httpx.ConnectError("refused")],
        ids=["forbidden", "server-error", "unreachable"],
    )
    async def test_connect_probe_failure_raises_before_elder(
        self, monkeypatch: pytest.MonkeyPatch, probe: Any
    ) -> None:
        """A failing verification request aborts connect before Elder is opened."""
        monkeypatch.setattr(settings, "authentik_domain", "auth.corp.invalid")
        monkeypatch.setattr(settings, "authentik_api_token", fake_secret("t"))
        c = AuthentikConnector()
        logger = spy_logger(c)
        with (
            patch.object(
                mod.httpx, "AsyncClient", return_value=HttpxScript({PROBE: probe})
            ),
            patch.object(mod, "ElderAPIClient") as elder_cls,
            pytest.raises(httpx.HTTPError),
        ):
            await c.connect()
        elder_cls.assert_not_called()
        logger.error.assert_called_once()

    @pytest.mark.asyncio
    async def test_disconnect_closes_http_client(self) -> None:
        """Disconnect closes the HTTP client and forgets it."""
        c = _connector()
        http = c._http_client
        await c.disconnect()
        assert http.closed is True and c._http_client is None
        fresh = AuthentikConnector()
        await fresh.disconnect()
        assert fresh._http_client is None

    @pytest.mark.xfail(
        strict=True,
        reason="bug: authentik_connector.py:92 calls ElderAPIClient.disconnect(), "
        "which does not exist (the client exposes close()), so disconnect raises",
    )
    @pytest.mark.asyncio
    async def test_disconnect_closes_real_elder_client_api(self) -> None:
        """Disconnect must work against the real Elder client interface."""
        c = _connector()
        c.elder_client = AsyncMock(spec=ElderAPIClient)
        await c.disconnect()
        c.elder_client.close.assert_awaited_once()


class TestPagination:
    """Authentik page handling."""

    @pytest.mark.asyncio
    async def test_follows_relative_and_absolute_next_links(self) -> None:
        """Relative, slash-less and absolute next links are all followed in order."""
        first = f"GET {BASE}/core/users/?page_size=100"
        c = _connector(
            {
                first: HttpxResponse(
                    {
                        "results": [{"pk": 1}],
                        "pagination": {"next": "/core/users/?page=2"},
                    }
                ),
                f"GET {BASE}/core/users/?page=2": HttpxResponse(
                    {
                        "results": [{"pk": 2}],
                        "pagination": {"next": "core/users/?page=3"},
                    }
                ),
                f"GET {BASE}/core/users/?page=3": HttpxResponse(
                    {
                        "results": [{"pk": 3}],
                        "pagination": {"next": "https://other.invalid/p4"},
                    }
                ),
                "GET https://other.invalid/p4": HttpxResponse(
                    {"results": [], "pagination": {}}
                ),
            }
        )
        assert await c._paginate("/core/users/") == [{"pk": 1}, {"pk": 2}, {"pk": 3}]
        assert len(c._http_client.calls) == 4

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("body", "expected"),
        [([{"pk": 1}, {"pk": 2}], [{"pk": 1}, {"pk": 2}]), ({"pk": 9}, [{"pk": 9}])],
        ids=["bare-list", "single-object"],
    )
    async def test_unpaginated_bodies(self, body: Any, expected: list) -> None:
        """Bodies without a results envelope are returned as-is, with the query appended."""
        url = f"GET {BASE}/core/groups/?search=x&page_size=100"
        c = _connector({url: [HttpxResponse(body)]})
        assert await c._paginate("/core/groups/?search=x") == expected
        assert c._http_client.keys() == [url]

    @pytest.mark.asyncio
    @pytest.mark.parametrize("status", [401, 429, 502])
    async def test_error_page_raises(self, status: int) -> None:
        """Auth failures, rate limits and gateway errors propagate to the caller."""
        c = _connector(
            {f"GET {BASE}/core/users/?page_size=100": HttpxResponse({}, status)}
        )
        with pytest.raises(httpx.HTTPStatusError):
            await c._paginate("/core/users/")


class TestSync:
    """Sync orchestration flags and failures."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("users", "groups", "expected"),
        [
            (True, True, ["users", "groups"]),
            (True, False, ["users"]),
            (False, False, []),
        ],
    )
    async def test_sync_honours_enable_flags(
        self, monkeypatch: pytest.MonkeyPatch, users: bool, groups: bool, expected: list
    ) -> None:
        """Only the enabled resource types are synced."""
        monkeypatch.setattr(settings, "authentik_sync_users", users)
        monkeypatch.setattr(settings, "authentik_sync_groups", groups)
        c = _connector()
        ran: list[str] = []
        c._sync_users = AsyncMock(side_effect=lambda _r: ran.append("users"))
        c._sync_groups = AsyncMock(side_effect=lambda _r: ran.append("groups"))
        result = await c.sync()
        assert ran == expected and result.errors == []

    @pytest.mark.asyncio
    async def test_sync_reports_provider_outage(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A provider outage is captured in the result's errors."""
        monkeypatch.setattr(settings, "authentik_sync_users", True)
        c = _connector(
            {f"GET {BASE}/core/users/?page_size=100": HttpxResponse({}, 503)}
        )
        result = await c.sync()
        assert len(result.errors) == 1
        assert result.errors[0].startswith("Authentik sync failed: ")
        c.elder_client.get_or_create_identity.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_group_failure_isolated(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """One group failing in Elder is recorded while the next still syncs."""
        monkeypatch.setattr(settings, "authentik_sync_users", False)
        monkeypatch.setattr(settings, "authentik_sync_groups", True)
        c = _connector(
            {
                f"GET {BASE}/core/groups/?page_size=100": HttpxResponse(
                    {
                        "results": [
                            {"pk": "g-1", "name": "a"},
                            {"pk": "g-2", "name": "b"},
                        ]
                    }
                )
            }
        )
        c.elder_client.get_or_create_identity.side_effect = [RuntimeError("x"), {}]
        result = await c.sync()
        assert result.entities_created == 1
        assert result.errors == ["Failed to sync Authentik group g-1: x"]

    @pytest.mark.asyncio
    async def test_health_check_states(self) -> None:
        """Health mirrors the probe status and tolerates transport errors."""
        c = _connector({PROBE: [HttpxResponse({})]})
        assert await c.health_check() is True
        c._http_client.routes[PROBE] = [HttpxResponse({}, 403)]
        assert await c.health_check() is False
        c._http_client.routes[PROBE] = httpx.ReadTimeout("slow")
        assert await c.health_check() is False
        c._http_client = None
        assert await c.health_check() is False


class TestGroupWriteBack:
    """Group membership write-back."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("method", "route", "operation"),
        [("add_group_member", ADD, "add"), ("remove_group_member", REMOVE, "remove")],
    )
    @pytest.mark.parametrize("status", [200, 204])
    async def test_success_posts_numeric_pk(
        self, method: str, route: str, operation: str, status: int
    ) -> None:
        """The user pk is sent as an integer and 200/204 count as success."""
        c = _connector({route: [HttpxResponse({}, status)]})
        result = await getattr(c, method)("g-1", "42")
        assert (result.success, result.operation, result.error) == (
            True,
            operation,
            None,
        )
        assert c._http_client.calls == [(route, {"json": {"pk": 42}})]

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("method", "route"),
        [("add_group_member", ADD), ("remove_group_member", REMOVE)],
    )
    @pytest.mark.parametrize(
        ("response", "expected"),
        [
            (HttpxResponse({"detail": "User not found"}, 404), "User not found"),
            (HttpxResponse({}, 403, text="forbidden"), "forbidden"),
            (
                HttpxResponse(ValueError("no json"), 500, text="boom"),
                "HTTP 500: boom",
            ),
        ],
        ids=["not-found", "forbidden", "non-json-500"],
    )
    async def test_provider_rejection_reported(
        self, method: str, route: str, response: HttpxResponse, expected: str
    ) -> None:
        """Rejections surface Authentik's detail, falling back to status and body."""
        c = _connector({route: [response]})
        result = await getattr(c, method)("g-1", "42")
        assert (result.success, result.error) == (False, expected)

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("method", "route"),
        [("add_group_member", ADD), ("remove_group_member", REMOVE)],
    )
    async def test_transport_error_and_disconnected(
        self, method: str, route: str
    ) -> None:
        """Timeouts and a missing client produce failed results, never exceptions."""
        c = _connector({route: httpx.ConnectTimeout("timed out")})
        result = await getattr(c, method)("g-1", "42")
        assert result.success is False and "timed out" in result.error
        c.logger.error.assert_called_once()
        c._http_client = None
        result = await getattr(c, method)("g-1", "42")
        assert result.error == "Authentik client not connected"

    @pytest.mark.asyncio
    async def test_get_group_members(self) -> None:
        """Member pks come back as strings; errors and no client yield []."""
        c = _connector(
            {
                GROUP: [
                    HttpxResponse({"users_obj": [{"pk": 7}, {"name": "x"}, {"pk": 9}]})
                ]
            }
        )
        assert await c.get_group_members("g-1") == ["7", "9"]
        c._http_client.routes[GROUP] = [HttpxResponse({})]
        assert await c.get_group_members("g-1") == []
        c._http_client.routes[GROUP] = [HttpxResponse({}, 404)]
        assert await c.get_group_members("g-1") == []
        c.logger.error.assert_called_once()
        c._http_client = None
        assert await c.get_group_members("g-1") == []
        c.logger.warning.assert_called_once()
