"""Behavioural tests for FleetDM connector failure paths, retries and secondary resources."""

import inspect
from typing import Any
from unittest.mock import AsyncMock, patch

import httpx
import pytest

import tests.unit.conftest_worker_stubs  # noqa: F401
from apps.worker.config.settings import settings
from apps.worker.connectors import fleetdm_connector as mod
from apps.worker.connectors.fleetdm_connector import (
    FleetDMConnector,
    _resolve_fleet_org_id,
)
from tests.unit._cov_f_helpers import (
    AsyncRouteClient,
    FakeResponse,
    assert_not_leaked,
    by_name,
    created_entities,
    fake_secret,
    logged_text,
    make_elder,
    spy_logger,
)

ORG_ID = 300
CONFIG = "/api/v1/fleet/config"
VERSION = "/api/v1/fleet/version"
STAGES = [
    "_sync_posture",
    "_sync_hosts",
    "_sync_os_versions",
    "_sync_software",
    "_sync_vulnerabilities",
    "_sync_policies",
    "_sync_labels",
    "_sync_mdm_profiles",
    "_sync_scripts_and_reports",
]


@pytest.fixture(autouse=True)
def _no_backoff(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """Replace the retry sleep with a recorder so tests never wait."""
    delays: list[float] = []

    async def _sleep(delay: float) -> None:
        """Record the requested backoff instead of sleeping."""
        delays.append(delay)

    monkeypatch.setattr(mod.asyncio, "sleep", _sleep)
    monkeypatch.setattr(settings, "create_missing_organizations", True)
    return delays


def _connector(routes: dict[str, Any] | None = None) -> FleetDMConnector:
    """Return a connector attached to a routed fake Fleet API and an Elder double."""
    c = FleetDMConnector()
    c.http_client = AsyncRouteClient(routes or {})
    c.elder_client = make_elder()
    c.elder_client.get_entity_by_external_id.return_value = None
    ids = iter(range(2000, 2500))
    c.elder_client.create_entity.side_effect = lambda _e: {"id": next(ids)}
    spy_logger(c)
    return c


def _stage_args(connector: FleetDMConnector, name: str) -> tuple:
    """Return the positional args a sync stage expects (org id, optional fleet map)."""
    params = inspect.signature(getattr(connector, name)).parameters
    return (ORG_ID, {}) if len(params) == 2 else (ORG_ID,)


class TestFleetGetRetries:
    """Transport-level retry and fallback behaviour."""

    @pytest.mark.asyncio
    async def test_transport_error_retries_with_backoff_then_raises(
        self, _no_backoff: list[float]
    ) -> None:
        """A dead connection is retried twice with growing backoff, then re-raised."""
        c = _connector({CONFIG: httpx.ConnectError("connection refused")})
        with pytest.raises(httpx.ConnectError):
            await c._fleet_get(CONFIG)
        assert c.http_client.paths() == [f"GET {CONFIG}"] * 3
        assert _no_backoff == [0.5, 1.0]
        c.logger.error.assert_called_once()

    @pytest.mark.asyncio
    async def test_transient_transport_error_recovers(
        self, _no_backoff: list[float]
    ) -> None:
        """A single timeout followed by success returns the payload."""
        c = _connector(
            {CONFIG: [httpx.ReadTimeout("slow"), FakeResponse({"license": {}})]}
        )
        assert await c._fleet_get(CONFIG) == (200, {"license": {}})
        assert _no_backoff == [0.5]

    @pytest.mark.asyncio
    @pytest.mark.parametrize("status", [429, 500, 503])
    async def test_persistent_throttle_or_5xx_returns_status(
        self, _no_backoff: list[float], status: int
    ) -> None:
        """After three attempts the final 429/5xx status is returned, not raised."""
        c = _connector({CONFIG: FakeResponse({"message": "busy"}, status)})
        assert await c._fleet_get(CONFIG) == (status, {"message": "busy"})
        assert len(c.http_client.calls) == 3
        assert len(_no_backoff) == 2

    @pytest.mark.asyncio
    async def test_exhausted_fallbacks_report_last_status(self) -> None:
        """When the primary and every fallback route are gone, the last status is reported."""
        c = _connector(
            {"/new": FakeResponse({}, 404), "/old": FakeResponse(ValueError("x"), 410)}
        )
        status, data = await c._fleet_get("/new", fallback_paths=["/old"])
        assert status == 410 and data is None
        assert c.http_client.paths() == ["GET /new", "GET /old"]

    @pytest.mark.asyncio
    async def test_requires_connected_client(self) -> None:
        """Calling the API before connect fails loudly."""
        c = _connector()
        c.http_client = None
        with pytest.raises(RuntimeError, match="not connected"):
            await c._fleet_get(CONFIG)


class TestConnect:
    """Connection lifecycle edge cases."""

    def _settings(self, monkeypatch: pytest.MonkeyPatch, token: str) -> None:
        """Point the connector at a fake Fleet server."""
        monkeypatch.setattr(settings, "fleetdm_url", "https://fleet.test.invalid/")
        monkeypatch.setattr(settings, "fleetdm_api_token", token)

    @pytest.mark.asyncio
    async def test_version_lookup_failure_is_tolerated(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A failing version endpoint does not block connect; the token is not logged."""
        token = fake_secret("fleet", "token")
        self._settings(monkeypatch, token)
        client = AsyncRouteClient(
            {
                CONFIG: {
                    "license": {"tier": "premium"},
                    "mdm": {"enabled_and_configured": True},
                },
                VERSION: httpx.ConnectError("refused"),
            }
        )
        c = FleetDMConnector()
        logger = spy_logger(c)
        elder = AsyncMock()
        with (
            patch.object(mod.httpx, "AsyncClient", return_value=client) as ctor,
            patch.object(mod, "ElderAPIClient", return_value=elder),
        ):
            await c.connect()

        assert ctor.call_args.kwargs["base_url"] == "https://fleet.test.invalid"
        assert ctor.call_args.kwargs["headers"]["Authorization"] == f"Bearer {token}"
        assert (c.is_premium, c.mdm_enabled, c.fleet_version) == (True, True, None)
        elder.connect.assert_awaited_once()
        assert_not_leaked(token, logged_text(logger))

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "config",
        [
            FakeResponse({}, 401),
            FakeResponse(["not", "a", "dict"]),
            FakeResponse({}, 503),
        ],
        ids=["unauthorized", "malformed", "unavailable"],
    )
    async def test_failed_connect_closes_http_client(
        self, monkeypatch: pytest.MonkeyPatch, config: FakeResponse
    ) -> None:
        """A rejected or malformed config response closes the client and raises."""
        self._settings(monkeypatch, fake_secret("fleet", "bad"))
        client = AsyncRouteClient({CONFIG: config})
        c = FleetDMConnector()
        spy_logger(c)
        with (
            patch.object(mod.httpx, "AsyncClient", return_value=client),
            patch.object(mod, "ElderAPIClient") as elder_cls,
            pytest.raises(RuntimeError, match="FleetDM API connection failed"),
        ):
            await c.connect()
        assert client.closed is True and c.http_client is None
        elder_cls.assert_not_called()

    @pytest.mark.asyncio
    async def test_disconnect_releases_clients_and_caches(self) -> None:
        """Disconnect closes both clients and clears every cache."""
        c = _connector()
        http, elder = c.http_client, c.elder_client
        c.organization_cache["root:FleetDM"] = 1
        c.host_entity_cache[1] = 2
        c.vuln_details_cache["CVE-1"] = {}
        await c.disconnect()
        assert http.closed is True
        elder.close.assert_awaited_once()
        assert c.http_client is None and c.elder_client is None
        assert c.organization_cache == {} and c.host_entity_cache == {}
        assert c.vuln_details_cache == {}
        await c.disconnect()

    @pytest.mark.asyncio
    async def test_health_check_states(self) -> None:
        """Health reflects the config endpoint and tolerates transport failure."""
        c = _connector({CONFIG: {"license": {}}})
        assert await c.health_check() is True
        c.http_client.routes[CONFIG] = FakeResponse({}, 401)
        assert await c.health_check() is False
        c.http_client.routes[CONFIG] = httpx.ConnectError("down")
        assert await c.health_check() is False
        c.logger.warning.assert_called_once()
        c.http_client = None
        assert await c.health_check() is False


class TestElderGuards:
    """Elder client guards and organization fallbacks."""

    @pytest.mark.asyncio
    async def test_operations_require_elder_client(self) -> None:
        """Using Elder before connect fails loudly rather than with AttributeError."""
        c = _connector()
        c.elder_client = None
        with pytest.raises(RuntimeError, match="Elder API client is not connected"):
            await c._get_or_create_organization("FleetDM", "d")

    @pytest.mark.asyncio
    async def test_org_default_and_error_fallbacks(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Existing orgs are reused; else the default id; else a loud error."""
        c = _connector()
        c.elder_client.list_organizations.return_value = {
            "items": [{"id": 5, "name": "FleetDM", "parent_id": None}]
        }
        assert await c._get_or_create_organization("FleetDM", "d") == 5
        assert await c._get_or_create_organization("FleetDM", "d") == 5
        c.elder_client.list_organizations.assert_awaited_once()
        monkeypatch.setattr(settings, "create_missing_organizations", False)
        monkeypatch.setattr(settings, "default_organization_id", 42)
        assert await c._get_or_create_organization("Other", "d") == 42
        monkeypatch.setattr(settings, "default_organization_id", None)
        with pytest.raises(ValueError, match="auto-creation disabled"):
            await c._get_or_create_organization("Other", "d")

    @pytest.mark.asyncio
    async def test_upsert_returns_entity_id(self) -> None:
        """Upsert returns the Elder id for both new and existing entities."""
        c = _connector()
        entity = mod.Entity(
            name="x", entity_type="compute", organization_id=ORG_ID, external_id="e:1"
        )
        assert await c._upsert_entity(entity) == 2000
        c.elder_client.get_entity_by_external_id.return_value = {"id": 77}
        assert await c._upsert_entity(entity) == 77
        c.elder_client.update_entity.assert_awaited_once_with(77, entity)

    @pytest.mark.parametrize(
        ("item", "expected"),
        [
            ({"fleet_id": 3}, (3, 30)),
            ({"team_id": "4"}, ("4", 40)),
            ({"fleet_id": None, "team_id": 9}, (9, ORG_ID)),
            ({}, (None, ORG_ID)),
        ],
    )
    def test_fleet_scoped_items_resolve_to_their_org(
        self, item: dict[str, Any], expected: tuple
    ) -> None:
        """Fleet 5 and Fleet 4 scoping keys both resolve; unknown fleets use the root."""
        assert _resolve_fleet_org_id(item, ORG_ID, {3: 30, 4: 40}) == expected


class TestStageFailureContainment:
    """Every sync stage survives a provider failure."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("stage", STAGES)
    @pytest.mark.parametrize(
        "error",
        [httpx.ConnectError("refused"), ValueError("malformed payload")],
        ids=["transport", "malformed"],
    )
    async def test_stage_failure_is_logged_and_writes_nothing(
        self, stage: str, error: Exception
    ) -> None:
        """A stage whose API calls fail returns zero counts and makes no Elder writes."""
        c = _connector()
        c._fleet_get = AsyncMock(side_effect=error)
        assert await getattr(c, stage)(*_stage_args(c, stage)) == (0, 0)
        assert c.logger.error.called
        c.elder_client.create_entity.assert_not_awaited()
        c.elder_client.update_entity.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_fleets_and_relationships_failures(self) -> None:
        """Fleet listing and relationship failures degrade to empty results."""
        c = _connector()
        c._fleet_get = AsyncMock(side_effect=httpx.ConnectError("refused"))
        assert await c._sync_fleets(ORG_ID) == {}
        c.host_entity_cache[1] = 10
        c.elder_client.get_or_create_dependency.side_effect = RuntimeError("elder")
        assert await c._sync_relationships() == 0
        assert c.logger.error.called

    @pytest.mark.asyncio
    @pytest.mark.parametrize("status", [402, 403])
    async def test_fleets_require_premium(self, status: int) -> None:
        """A licence/permission refusal for fleets is treated as 'no fleets'."""
        c = _connector()
        c._fleet_get = AsyncMock(return_value=(status, None))
        assert await c._sync_fleets(ORG_ID) == {}
        c.elder_client.create_organization.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_fleets_skip_entries_without_id(self) -> None:
        """Fleets lacking an id are skipped; unnamed ones get an id-based name."""
        c = _connector()
        c._fleet_get = AsyncMock(
            return_value=(200, {"fleets": [{"name": "ghost"}, {"id": 7}]})
        )
        assert await c._sync_fleets(ORG_ID) == {7: 501}
        org = c.elder_client.create_organization.call_args.args[0]
        assert (org.name, org.parent_id) == ("Fleet: Fleet 7", ORG_ID)

    @pytest.mark.asyncio
    async def test_empty_posture_writes_nothing(self) -> None:
        """When no posture endpoint answers, no posture entity is written."""
        c = _connector()
        c._fleet_get = AsyncMock(return_value=(404, None))
        assert await c._sync_posture(ORG_ID) == (0, 0)
        c.elder_client.create_entity.assert_not_awaited()


class TestSecondaryResources:
    """OS versions, scripts and custom vitals."""

    @pytest.mark.asyncio
    async def test_os_versions_mapped_and_updated(self) -> None:
        """OS versions carry CVE lists; entries without an id are skipped."""
        c = _connector()
        c._fleet_get = AsyncMock(
            return_value=(
                200,
                {
                    "os_versions": [
                        {
                            "os_version_id": 11,
                            "name_only": "Ubuntu",
                            "version": "24.04",
                            "hosts_count": 3,
                            "vulnerabilities": [
                                {"cve": "CVE-2026-0001"},
                                "CVE-2026-0002",
                                {},
                            ],
                        },
                        {"id": 12, "name": "macOS 15", "vulnerabilities_count": 4},
                        {"name": "no id"},
                    ]
                },
            )
        )
        c.elder_client.get_entity_by_external_id.side_effect = [None, {"id": 90}]
        assert await c._sync_os_versions(ORG_ID) == (1, 1)
        (ubuntu,) = created_entities(c.elder_client)
        assert ubuntu.name == "Ubuntu 24.04" and ubuntu.is_active is True
        assert ubuntu.organization_id == ORG_ID
        assert "CVE-2026-0001" in repr(ubuntu.attributes)
        assert "CVE-2026-0002" in repr(ubuntu.attributes)
        assert c.os_entity_cache == {11: 2000, 12: 90}

    @pytest.mark.asyncio
    async def test_scripts_deduplicated_across_fleets_and_vitals_mapped(self) -> None:
        """Global and per-fleet scripts are merged without duplicates; vitals are synced."""
        scripts_global = {"scripts": [{"id": 1, "name": "rotate"}, {"name": "no-id"}]}
        scripts_fleet = {
            "scripts": [{"id": 1, "name": "rotate"}, {"id": 2, "fleet_id": 5}]
        }

        async def fleet_get(path: str, params: Any = None, **_kw: Any) -> tuple:
            """Serve scripts (global and fleet-scoped) and custom vitals only."""
            if path == "/api/v1/fleet/scripts":
                scoped = bool(params and params.get("fleet_id"))
                return 200, scripts_fleet if scoped else scripts_global
            if path == "/api/v1/fleet/custom_host_vitals":
                return 200, {"vitals": [{"id": 8, "query": "SELECT 1"}, {"name": "x"}]}
            return 404, None

        c = _connector()
        c._fleet_get = AsyncMock(side_effect=fleet_get)
        created, updated = await c._sync_scripts_and_reports(ORG_ID, {5: 55})

        got = by_name(created_entities(c.elder_client))
        assert (created, updated) == (len(got), 0)
        assert got["Script: rotate"].organization_id == ORG_ID
        assert got["Script: rotate"].external_id == "fleetdm:script:1"
        assert got["Script: Script-2"].organization_id == 55
        assert got["Script: Script-2"].attributes["fleet_id"] == 5
        vital = got["Custom Vital: Vital-8"]
        assert vital.sub_type == "fleet_custom_vital"
        assert vital.attributes["query"] == "SELECT 1"
        assert sum(name.startswith("Script:") for name in got) == 2


class TestSyncOrchestration:
    """Feature toggles and top-level failure handling."""

    @pytest.mark.asyncio
    async def test_optional_stages_follow_settings(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Disabled optional stages are not run; counts aggregate the rest."""
        for name in (
            "fleetdm_sync_os_versions",
            "fleetdm_sync_labels",
            "fleetdm_sync_mdm",
            "fleetdm_sync_scripts_reports",
        ):
            monkeypatch.setattr(settings, name, False)
        c = _connector()
        ran: list[str] = []
        for stage in STAGES:

            async def _stage(*_a: Any, _name: str = stage) -> tuple[int, int]:
                """Record that the stage ran and report one create and one update."""
                ran.append(_name)
                return 1, 1

            setattr(c, stage, _stage)
        c._sync_fleets = AsyncMock(return_value={1: 11, 2: 12})
        c._sync_relationships = AsyncMock(return_value=4)

        result = await c.sync()

        assert ran == [
            "_sync_posture",
            "_sync_hosts",
            "_sync_software",
            "_sync_vulnerabilities",
            "_sync_policies",
        ]
        assert result.errors == []
        assert result.organizations_created == 3
        assert (result.entities_created, result.entities_updated) == (5, 5)

    @pytest.mark.asyncio
    async def test_sync_reports_failure(self) -> None:
        """An Elder outage is reported in the result's errors."""
        c = _connector()
        c.elder_client.list_organizations.side_effect = RuntimeError("down")
        result = await c.sync()
        assert result.errors == ["FleetDM sync failed: down"]
