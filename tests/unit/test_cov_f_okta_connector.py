"""Behavioural tests for the Okta connector's transport, pagination and write-back paths."""

from typing import Any
from unittest.mock import AsyncMock, patch

import httpx
import pytest

import tests.unit.conftest_worker_stubs  # noqa: F401
from apps.worker.config.settings import settings
from apps.worker.connectors import okta_connector as mod
from apps.worker.connectors.base import SyncResult
from apps.worker.connectors.okta_connector import OktaConnector
from apps.worker.utils.elder_client import ElderAPIClient
from tests.unit._cov_f_helpers import (
    HttpxResponse,
    HttpxScript,
    assert_not_leaked,
    fake_secret,
    logged_text,
    spy_logger,
)

BASE = "https://acme.okta.invalid"
PROBE = f"GET {BASE}/api/v1/users?limit=1"
USERS = f'GET {BASE}/api/v1/users?filter=status eq "ACTIVE"'
GROUPS = f"GET {BASE}/api/v1/groups"
MEMBER = f"{BASE}/api/v1/groups/g1/users/u1"


def _connector(routes: dict[str, Any] | None = None) -> OktaConnector:
    """Return a connector attached to a scripted Okta API and an Elder double."""
    c = OktaConnector()
    c.base_url = BASE
    c._http_client = HttpxScript(routes or {})
    c.elder_client = AsyncMock()
    c.elder_client.get_or_create_identity.return_value = {"id": 1}
    spy_logger(c)
    return c


class TestConnect:
    """Connection lifecycle."""

    @pytest.mark.asyncio
    async def test_connect_uses_ssws_token_and_never_logs_it(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The API token is sent as an SSWS header and kept out of the logs."""
        token = fake_secret("okta", "api", "token")
        monkeypatch.setattr(settings, "okta_domain", "acme.okta.invalid")
        monkeypatch.setattr(settings, "okta_api_token", token)
        c = OktaConnector()
        logger = spy_logger(c)
        client = HttpxScript({PROBE: []})
        client.routes[PROBE] = [HttpxResponse([])]
        elder = AsyncMock()
        with (
            patch.object(mod.httpx, "AsyncClient", return_value=client) as ctor,
            patch.object(mod, "ElderAPIClient", return_value=elder),
        ):
            await c.connect()

        assert c.base_url == BASE
        assert ctor.call_args.kwargs["headers"]["Authorization"] == f"SSWS {token}"
        assert client.keys() == [PROBE]
        elder.connect.assert_awaited_once()
        assert_not_leaked(token, logged_text(logger))

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("domain", "token"), [(None, "t"), ("acme.okta.invalid", None), ("", "")]
    )
    async def test_connect_requires_domain_and_token(
        self, monkeypatch: pytest.MonkeyPatch, domain: Any, token: Any
    ) -> None:
        """Missing configuration fails fast without creating an HTTP client."""
        monkeypatch.setattr(settings, "okta_domain", domain)
        monkeypatch.setattr(settings, "okta_api_token", token)
        c = OktaConnector()
        with (
            patch.object(mod.httpx, "AsyncClient") as ctor,
            pytest.raises(ValueError, match="domain and API token are required"),
        ):
            await c.connect()
        ctor.assert_not_called()

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "probe",
        [
            HttpxResponse({}, 401),
            HttpxResponse({}, 429),
            HttpxResponse({}, 503),
            httpx.ConnectTimeout("timed out"),
        ],
        ids=["unauthorized", "rate-limited", "unavailable", "timeout"],
    )
    async def test_connect_probe_failure_raises_before_elder(
        self, monkeypatch: pytest.MonkeyPatch, probe: Any
    ) -> None:
        """A failing verification request aborts connect before Elder is opened."""
        token = fake_secret("okta", "bad")
        monkeypatch.setattr(settings, "okta_domain", "acme.okta.invalid")
        monkeypatch.setattr(settings, "okta_api_token", token)
        c = OktaConnector()
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
        assert_not_leaked(token, logged_text(logger))

    @pytest.mark.asyncio
    async def test_disconnect_closes_http_client(self) -> None:
        """Disconnect closes the HTTP client and forgets it."""
        c = _connector()
        http = c._http_client
        await c.disconnect()
        assert http.closed is True and c._http_client is None
        fresh = OktaConnector()
        fresh.elder_client = None
        await fresh.disconnect()
        assert fresh._http_client is None

    @pytest.mark.xfail(
        strict=True,
        reason="bug: okta_connector.py:88 calls ElderAPIClient.disconnect(), which "
        "does not exist (the client exposes close()), so disconnect raises",
    )
    @pytest.mark.asyncio
    async def test_disconnect_closes_real_elder_client_api(self) -> None:
        """Disconnect must work against the real Elder client interface."""
        c = _connector()
        c.elder_client = AsyncMock(spec=ElderAPIClient)
        await c.disconnect()
        c.elder_client.close.assert_awaited_once()


class TestPagination:
    """Link-header pagination."""

    @pytest.mark.asyncio
    async def test_follows_next_links_until_exhausted(self) -> None:
        """Every page is fetched in order and the results concatenated."""
        page2 = f"{BASE}/api/v1/groups?after=abc"
        c = _connector(
            {
                GROUPS: HttpxResponse(
                    [{"id": "g1"}],
                    headers={
                        "Link": f'<{BASE}/api/v1/groups>; rel="self", <{page2}>; rel="next"'
                    },
                ),
                f"GET {page2}": HttpxResponse(
                    {"id": "g2"}, headers={"Link": f'<{page2}>; rel="self"'}
                ),
            }
        )
        assert await c._paginate("/api/v1/groups") == [{"id": "g1"}, {"id": "g2"}]
        assert c._http_client.keys() == [GROUPS, f"GET {page2}"]

    @pytest.mark.asyncio
    @pytest.mark.parametrize("status", [401, 429, 500])
    async def test_error_page_raises(self, status: int) -> None:
        """An auth failure, rate limit or 5xx on any page propagates."""
        c = _connector({GROUPS: HttpxResponse({}, status)})
        with pytest.raises(httpx.HTTPStatusError):
            await c._paginate("/api/v1/groups")

    @pytest.mark.parametrize(
        ("header", "expected"),
        [
            (None, None),
            ("", None),
            ('<https://x/next>; rel="next"', "https://x/next"),
            ('<https://x/self>; rel="self"', None),
            ('https://x/next; rel="next"', None),
            ('<https://x/a>; rel="next"; extra', None),
            ('garbage, <https://x/n>; rel="next"', "https://x/n"),
        ],
    )
    def test_next_link_parsing(self, header: str | None, expected: str | None) -> None:
        """Only a well-formed rel="next" link is followed."""
        assert OktaConnector()._get_next_link(header) == expected


class TestSync:
    """User and group sync orchestration."""

    @pytest.mark.asyncio
    async def test_sync_creates_identities_and_skips_app_groups(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Users and OKTA_GROUP groups become identities; directory groups are skipped."""
        monkeypatch.setattr(settings, "okta_sync_profile_url", False)
        c = _connector(
            {
                USERS: [
                    HttpxResponse(
                        [
                            {
                                "id": "u1",
                                "profile": {
                                    "login": "jane@corp.invalid",
                                    "email": "jane@corp.invalid",
                                    "firstName": "Jane",
                                    "lastName": "Doe",
                                },
                            },
                            {"id": "u2"},
                        ]
                    )
                ],
                GROUPS: [
                    HttpxResponse(
                        [
                            {
                                "id": "g1",
                                "type": "OKTA_GROUP",
                                "profile": {"name": "Eng"},
                            },
                            {
                                "id": "g2",
                                "type": "APP_GROUP",
                                "profile": {"name": "AD"},
                            },
                            {"id": "g3", "type": "OKTA_GROUP"},
                        ]
                    )
                ],
            }
        )
        result = await c.sync()

        assert result.errors == [] and result.entities_created == 4
        identities = {
            call.args[0].auth_provider_id: call.args[0]
            for call in c.elder_client.get_or_create_identity.call_args_list
        }
        assert set(identities) == {"u1", "u2", "g1", "g3"}
        assert identities["u1"].full_name == "Jane Doe"
        assert identities["u1"].identity_type == "human"
        assert identities["u2"].full_name is None and identities["u2"].username == ""
        assert identities["g1"].identity_type == "service_account"
        assert identities["g1"].username == "Eng"
        assert {i.auth_provider for i in identities.values()} == {"okta"}

    @pytest.mark.asyncio
    async def test_one_bad_record_does_not_stop_the_rest(self) -> None:
        """A failing user or group is recorded as an error while others still sync."""
        c = _connector(
            {
                USERS: [HttpxResponse([{"id": "u1"}, {"id": "u2"}])],
                GROUPS: [
                    HttpxResponse(
                        [
                            {"id": "g1", "type": "OKTA_GROUP"},
                            {"id": "g2", "type": "OKTA_GROUP"},
                        ]
                    )
                ],
            }
        )
        c.elder_client.get_or_create_identity.side_effect = [
            RuntimeError("elder 500"),
            {"id": 2},
            RuntimeError("elder 500"),
            {"id": 3},
        ]
        result = SyncResult(connector_name="okta")
        await c._sync_users(result)
        await c._sync_groups(result)
        assert result.entities_created == 2
        assert result.errors == [
            "Failed to sync Okta user u1: elder 500",
            "Failed to sync Okta group g1: elder 500",
        ]

    @pytest.mark.asyncio
    async def test_sync_reports_provider_outage(self) -> None:
        """A provider outage during listing is reported in the result's errors."""
        c = _connector({USERS: HttpxResponse({}, 503)})
        result = await c.sync()
        assert len(result.errors) == 1
        assert result.errors[0].startswith("Okta sync failed: ")
        c.elder_client.get_or_create_identity.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_health_check_states(self) -> None:
        """Health mirrors the probe status and tolerates transport errors."""
        c = _connector({PROBE: [HttpxResponse([])]})
        assert await c.health_check() is True
        c._http_client.routes[PROBE] = [HttpxResponse({}, 401)]
        assert await c.health_check() is False
        c._http_client.routes[PROBE] = httpx.ReadTimeout("slow")
        assert await c.health_check() is False
        c._http_client = None
        assert await c.health_check() is False


class TestGroupWriteBack:
    """Group membership write-back."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("method", "verb", "operation"),
        [
            ("add_group_member", "PUT", "add"),
            ("remove_group_member", "DELETE", "remove"),
        ],
    )
    async def test_success_on_204(self, method: str, verb: str, operation: str) -> None:
        """A 204 from Okta is reported as a successful membership change."""
        c = _connector({f"{verb} {MEMBER}": HttpxResponse(None, 204)})
        result = await getattr(c, method)("g1", "u1")
        assert (result.success, result.operation, result.error) == (
            True,
            operation,
            None,
        )
        assert c._http_client.keys() == [f"{verb} {MEMBER}"]

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("method", "verb"),
        [("add_group_member", "PUT"), ("remove_group_member", "DELETE")],
    )
    @pytest.mark.parametrize(
        ("response", "expected"),
        [
            (
                HttpxResponse({"errorSummary": "Not allowed on APP_GROUP"}, 403),
                "Not allowed on APP_GROUP",
            ),
            (HttpxResponse({}, 429, text="slow down"), "slow down"),
            (
                HttpxResponse(ValueError("no json"), 502, text="Bad Gateway"),
                "HTTP 502: Bad Gateway",
            ),
        ],
        ids=["forbidden", "rate-limited", "non-json-5xx"],
    )
    async def test_provider_rejection_reported(
        self, method: str, verb: str, response: HttpxResponse, expected: str
    ) -> None:
        """Rejections surface Okta's summary, falling back to status and body."""
        c = _connector({f"{verb} {MEMBER}": [response]})
        result = await getattr(c, method)("g1", "u1")
        assert (result.success, result.error) == (False, expected)

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("method", "verb"),
        [("add_group_member", "PUT"), ("remove_group_member", "DELETE")],
    )
    async def test_transport_error_and_disconnected(
        self, method: str, verb: str
    ) -> None:
        """Timeouts and a missing client produce failed results, never exceptions."""
        c = _connector({f"{verb} {MEMBER}": httpx.ConnectTimeout("timed out")})
        result = await getattr(c, method)("g1", "u1")
        assert result.success is False and "timed out" in result.error
        c._http_client = None
        result = await getattr(c, method)("g1", "u1")
        assert result.error == "Okta client not connected"

    @pytest.mark.asyncio
    async def test_get_group_members(self) -> None:
        """Member ids are collected across pages; failures yield an empty list."""
        url = f"GET {BASE}/api/v1/groups/g1/users"
        c = _connector({url: [HttpxResponse([{"id": "u1"}, {"login": "no-id"}])]})
        assert await c.get_group_members("g1") == ["u1"]
        c._http_client.routes[url] = [HttpxResponse({}, 404)]
        assert await c.get_group_members("g1") == []
        c.logger.error.assert_called_once()
        c._http_client = None
        assert await c.get_group_members("g1") == []
        c.logger.warning.assert_called_once()


class TestProfileUrlWriteBack:
    """Elder profile URL write-back."""

    URL = f"POST {BASE}/api/v1/users/u1"

    @pytest.fixture(autouse=True)
    def _enabled(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Enable profile URL sync against a known Elder web URL."""
        monkeypatch.setattr(settings, "okta_sync_profile_url", True)
        monkeypatch.setattr(settings, "elder_web_url", "https://elder.test.invalid")

    @pytest.mark.asyncio
    @pytest.mark.parametrize("status", [200, 204])
    async def test_profile_url_posted(self, status: int) -> None:
        """The Elder profile link for the village id is written to the Okta user."""
        c = _connector({self.URL: [HttpxResponse({}, status)]})
        assert await c.update_user_profile_url("u1", "vil-1") is True
        assert c._http_client.calls[0][1] == {
            "json": {
                "profile": {"profileUrl": "https://elder.test.invalid/profile/vil-1"}
            }
        }

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "outcome",
        [
            [HttpxResponse({"errorSummary": "denied"}, 403)],
            [HttpxResponse(ValueError("x"), 500, text="oops")],
            httpx.ReadTimeout("slow"),
            RuntimeError("unexpected"),
        ],
        ids=["forbidden", "non-json-500", "timeout", "unexpected"],
    )
    async def test_failures_return_false(self, outcome: Any) -> None:
        """Rejections and transport errors return False instead of raising."""
        c = _connector({self.URL: outcome})
        assert await c.update_user_profile_url("u1", "vil-1") is False

    @pytest.mark.asyncio
    async def test_disabled_or_disconnected_sends_nothing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """With the feature off or no client, no request is made."""
        c = _connector()
        monkeypatch.setattr(settings, "okta_sync_profile_url", False)
        assert await c.update_user_profile_url("u1", "vil-1") is False
        assert c._http_client.calls == []
        c._http_client = None
        assert await c.update_user_profile_url("u1", "vil-1") is False

    @pytest.mark.asyncio
    async def test_user_sync_triggers_write_back_when_village_id_present(self) -> None:
        """A synced user with a village id gets its profile URL written back."""
        c = _connector(
            {USERS: [HttpxResponse([{"id": "u1"}])], self.URL: [HttpxResponse({}, 200)]}
        )
        c.elder_client.get_or_create_identity.return_value = {"village_id": "vil-9"}
        result = SyncResult(connector_name="okta")
        await c._sync_users(result)
        assert self.URL in c._http_client.keys()
        assert result.errors == []
