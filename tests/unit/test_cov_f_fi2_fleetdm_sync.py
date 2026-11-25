"""Additional FleetDM connector coverage: paging, per-resource syncs and relationships."""

from typing import Any
from unittest.mock import AsyncMock

import pytest

import tests.unit.conftest_worker_stubs  # noqa: F401
from apps.worker.config.settings import settings
from apps.worker.connectors import fleetdm_connector as mod
from apps.worker.connectors.fleetdm_connector import FleetDMConnector
from tests.unit._cov_f_helpers import (
    AsyncRouteClient,
    FakeResponse,
    created_entities,
    make_elder,
    spy_logger,
)

ORG = 300


def _conn() -> FleetDMConnector:
    """Build a connector with Elder and logger doubles and no HTTP client."""
    c = FleetDMConnector()
    c.elder_client = make_elder()
    c.elder_client.get_entity_by_external_id.return_value = None
    ids = iter(range(5000, 6000))
    c.elder_client.create_entity.side_effect = lambda _e: {"id": next(ids)}
    spy_logger(c)
    return c


def _route(c: FleetDMConnector, table: dict[str, Any]) -> list[tuple[str, Any]]:
    """Patch _fleet_get to serve canned (status, data) by path; unknown is 404."""
    calls: list[tuple[str, Any]] = []

    async def fake(path, params=None, fallback_paths=None):
        """Return the canned outcome, calling it with params when callable."""
        calls.append((path, params))
        out = table.get(path, (404, None))
        if callable(out):
            out = out(params)
        if isinstance(out, Exception):
            raise out
        return out

    c._fleet_get = fake
    return calls


class TestGetAndPaginate:
    """Low-level request and paging branches."""

    @pytest.mark.asyncio
    async def test_204_and_429_retry_after(self, monkeypatch) -> None:
        """204 returns no body; 429 honours the parsed retry delay."""

        async def _nosleep(_d):
            """Skip real waiting."""

        monkeypatch.setattr(mod.asyncio, "sleep", _nosleep)
        c = _conn()
        c.http_client = AsyncRouteClient(
            {
                "/a": FakeResponse(None, 204),
                "/b": [FakeResponse({}, 429), FakeResponse({"x": 1})],
            }
        )
        assert await c._fleet_get("/a") == (204, None)
        assert await c._fleet_get("/b") == (200, {"x": 1})

    @pytest.mark.asyncio
    async def test_fallback_path_used(self) -> None:
        """A 404 on the primary path falls through to the fallback."""
        c = _conn()
        c.http_client = AsyncRouteClient(
            {"/new": FakeResponse({}, 404), "/old": {"ok": 1}}
        )
        assert await c._fleet_get("/new", fallback_paths=["/old"]) == (200, {"ok": 1})

    @pytest.mark.asyncio
    async def test_paginate_status_branches(self, monkeypatch) -> None:
        """Pagination handles license, missing, failing, meta and short pages."""
        monkeypatch.setattr(settings, "fleetdm_per_page", 2)
        c = _conn()
        for status in (403, 404, 500):
            _route(c, {"/p": (status, {})})
            st, items = await c._fleet_paginate_with_status("/p", "k")
            assert st == status and items == []
        pages = {
            0: (200, {"k": [{"id": 1}, {"id": 2}], "meta": {"has_next_results": True}}),
            1: (200, {"k": [{"id": 3}], "meta": {"has_next_results": False}}),
        }
        _route(c, {"/p": lambda p: pages[p["page"]]})
        assert [i["id"] for i in await c._fleet_paginate("/p", "k")] == [1, 2, 3]
        short = {
            0: (
                200,
                {"alt": [{"id": 1}, {"id": 2}], "meta": {"has_next_results": None}},
            ),
            1: (200, {"alt": [{"id": 3}]}),
        }
        _route(c, {"/p": lambda p: short[p["page"]]})
        got = await c._fleet_paginate("/p", "k", fallback_result_key="alt")
        assert len(got) == 3
        _route(c, {"/p": (200, {"k": []})})
        assert await c._fleet_paginate("/p", "k") == []


class TestConnect:
    """Connection validation."""

    @pytest.mark.asyncio
    async def test_missing_credentials(self, monkeypatch) -> None:
        """Blank URL or token is a configuration error."""
        monkeypatch.setattr(settings, "fleetdm_url", "")
        monkeypatch.setattr(settings, "fleetdm_api_token", "")
        with pytest.raises(ValueError, match="required"):
            await FleetDMConnector().connect()

    @pytest.mark.asyncio
    async def test_connect_success_and_bad_status(self, monkeypatch) -> None:
        """A good config sets tier flags and version; a bad status raises."""
        monkeypatch.setattr(settings, "fleetdm_url", "https://f.invalid/")
        monkeypatch.setattr(settings, "fleetdm_api_token", "-".join(["covf", "t"]))
        monkeypatch.setattr(mod, "ElderAPIClient", lambda: AsyncMock())
        c = FleetDMConnector()
        spy_logger(c)

        async def _noop_get(path, params=None, fallback_paths=None):
            """Serve the canned config/version responses."""
            return c._table[path]

        c._table = {
            "/api/v1/fleet/config": (
                200,
                {
                    "license": {"tier": "premium"},
                    "mdm": {"enabled_and_configured": True},
                },
            ),
            "/api/v1/fleet/version": (200, {"version": "4.95.0"}),
        }
        c._fleet_get = _noop_get
        await c.connect()
        assert c.is_premium and c.mdm_enabled and c.fleet_version == "4.95.0"
        await c.http_client.aclose()
        c2 = FleetDMConnector()
        spy_logger(c2)

        async def _bad(path, params=None, fallback_paths=None):
            """Return a failing status."""
            return 500, None

        c2._fleet_get = _bad
        with pytest.raises(RuntimeError, match="HTTP 500"):
            await c2.connect()
        assert c2.http_client is None


class TestHostsAndDetails:
    """Host mapping and per-host details."""

    @pytest.mark.asyncio
    async def test_host_details(self) -> None:
        """Certificate and profile counts are summarised without PII."""
        c = _conn()
        _route(
            c,
            {
                "/api/v1/fleet/hosts/7/certificates": (
                    200,
                    {
                        "certificates": [
                            {"source": "user"},
                            {"source": "system"},
                            {"source": None},
                            "x",
                        ]
                    },
                ),
                "/api/v1/fleet/hosts/7/configuration_profiles": (
                    200,
                    {"profiles": [{"status": "verified"}, {"status": None}, "x"]},
                ),
            },
        )
        d = await c._fetch_host_details(7)
        assert d["certificates_count"] == 4 and d["certificate_sources"] == [
            "system",
            "user",
        ]
        assert d["profiles_status_summary"] == {"verified": 1, "unknown": 1}
        _route(c, {})
        assert await c._fetch_host_details(7) == {}

    @pytest.mark.asyncio
    async def test_sync_hosts_full(self, monkeypatch) -> None:
        """Hosts map battery, mdm, fleet org and optional details; id-less are skipped."""
        monkeypatch.setattr(settings, "fleetdm_sync_host_details", True)
        c = _conn()
        c.elder_client.get_entity_by_external_id.side_effect = [None, {"id": 77}, None]
        c._fetch_host_details = AsyncMock(return_value={"profiles_count": 1})
        hosts = [
            {
                "id": 1,
                "hostname": "h1",
                "memory": 2 * 1024**3,
                "status": "online",
                "battery": {"health": "ok", "cycle_count": 3, "x": 1},
                "fleet_id": 9,
                "fleet_name": "F",
                "platform": "ubuntu",
                "mdm": {"enrollment_status": "On"},
            },
            {"id": 2, "computer_name": "cn", "battery": "bad", "team_id": 8},
            {"id": 3},
            {"hostname": "noid"},
        ]
        _route(c, {"/api/v1/fleet/hosts": (200, {"hosts": hosts})})
        assert await c._sync_hosts(ORG, {9: 900}) == (2, 1)
        ents = created_entities(c.elder_client)
        assert ents[0].organization_id == 900 and ents[0].attributes["memory_gb"] == 2.0
        assert ents[0].attributes["profiles_count"] == 1 and ents[0].is_active
        assert ents[1].name == "Host-3"
        assert set(c.host_entity_cache) == {1, 2, 3}

    @pytest.mark.asyncio
    async def test_sync_hosts_error_logged(self) -> None:
        """A paging failure is logged and returns zero counts."""
        c = _conn()
        c._fleet_paginate = AsyncMock(side_effect=RuntimeError("x"))
        assert await c._sync_hosts(ORG, {}) == (0, 0)
        c.logger.error.assert_called_once()


class TestPostureFleets:
    """Posture and fleets."""

    @pytest.mark.asyncio
    async def test_posture_new_updated_and_error(self) -> None:
        """Posture creates or updates one entity and survives errors."""
        c = _conn()
        _route(
            c,
            {
                "/api/v1/fleet/host_summary": (200, {"total": 1}),
                "/api/v1/fleet/disk_encryption": (200, {"a": 1}),
            },
        )
        assert await c._sync_posture(ORG) == (1, 0)
        c.elder_client.get_entity_by_external_id.return_value = {"id": 4}
        assert await c._sync_posture(ORG) == (0, 1)
        c._fleet_get = AsyncMock(side_effect=RuntimeError("x"))
        assert await c._sync_posture(ORG) == (0, 0)

    @pytest.mark.asyncio
    async def test_fleets_mapped(self) -> None:
        """Fleets become sub-organisations keyed by fleet id."""
        c = _conn()
        c._fleet_paginate_with_status = AsyncMock(
            return_value=(200, [{"id": 1, "name": "A"}, {"id": 2}, {"name": "noid"}])
        )
        c._get_or_create_organization = AsyncMock(side_effect=[11, 12])
        assert await c._sync_fleets(ORG) == {1: 11, 2: 12}


class TestSoftware:
    """Software titles and versions."""

    @pytest.mark.asyncio
    async def test_titles_map(self) -> None:
        """Title metadata is indexed by title and version id."""
        c = _conn()
        _route(
            c,
            {
                "/api/v1/fleet/software/titles": (
                    200,
                    {
                        "software_titles": [
                            {
                                "id": 1,
                                "software_package": {"hash_sha256": "h"},
                                "app_store_app": {},
                                "versions": [{"id": 10}, {"id": None}, "x"],
                                "ai_tool": True,
                            },
                            {"id": None, "hash_sha256": "z"},
                        ]
                    },
                )
            },
        )
        vm, tm = await c._fetch_software_titles_map()
        assert vm[10]["hash_sha256"] == "h" and tm[1]["has_installer_package"]
        c._fleet_paginate = AsyncMock(side_effect=RuntimeError("x"))
        assert await c._fetch_software_titles_map() == ({}, {})

    @pytest.mark.asyncio
    async def test_sync_software(self) -> None:
        """Software merges title metadata and counts created/updated."""
        c = _conn()
        c.elder_client.get_entity_by_external_id.side_effect = [
            None,
            {"id": 5},
            None,
            None,
        ]
        _route(
            c,
            {
                "/api/v1/fleet/software/versions": (
                    200,
                    {
                        "software": [
                            {"id": 1, "name": "a", "version": "1", "hosts_count": 2},
                            {"id": 2, "title_id": 7, "vulnerabilities": [1, 2]},
                            {"id": 3, "title_id": 99, "ai_tool": False},
                            {"name": "noid"},
                        ]
                    },
                )
            },
        )
        c._fetch_software_titles_map = AsyncMock(
            return_value=(
                {1: {"bundle_identifier": "b", "ai_tool": True}},
                {7: {"software_title_id": 7, "has_app_store_app": True}},
            )
        )
        assert await c._sync_software(ORG) == (2, 1)
        assert c.software_entity_cache[2] == 5
        c._fleet_paginate = AsyncMock(side_effect=RuntimeError("x"))
        assert await c._sync_software(ORG) == (0, 0)


class TestVulnerabilities:
    """Vulnerability detail enrichment and sync."""

    @pytest.mark.asyncio
    async def test_details_variants(self) -> None:
        """Detail parsing covers dict/list/invalid software and os shapes."""
        c = _conn()
        _route(
            c,
            {
                "/api/v1/fleet/vulnerabilities/CVE-1": (
                    200,
                    {
                        "vulnerability": {
                            "software": {
                                "id": 4,
                                "name": "n",
                                "resolved_in_version": "2",
                            },
                            "os_versions": [
                                {"os_version_id": 8, "resolved_in_version": "9"},
                                {"id": 9},
                                {},
                                "x",
                            ],
                        }
                    },
                )
            },
        )
        d = await c._fetch_vulnerability_details("CVE-1", {})
        assert d["affected_software_ids"] == [4] and d["affected_os_version_ids"] == [
            8,
            9,
        ]
        assert d["resolved_in_versions"] == ["2", "9"] and d["software_names"] == ["n"]
        _route(
            c, {"/api/v1/fleet/vulnerabilities/CVE-2": (200, {"vulnerability": "str"})}
        )
        d = await c._fetch_vulnerability_details(
            "CVE-2", {"software": [{"id": 1}, "x"], "os_versions": "bad"}
        )
        assert d["affected_software_ids"] == [1] and d["affected_os_version_ids"] == []
        d = await c._fetch_vulnerability_details("CVE-3", {"software": "bad"})
        assert d["affected_software_ids"] == []
        _route(c, {"/api/v1/fleet/vulnerabilities/CVE-4": RuntimeError("x")})
        assert (await c._fetch_vulnerability_details("CVE-4", {}))["detail"] == {}

    @pytest.mark.asyncio
    async def test_sync_vulnerabilities(self) -> None:
        """Vulnerabilities fall back to detail values; late items skip enrichment."""
        c = _conn()
        c.elder_client.get_entity_by_external_id.side_effect = [None, {"id": 3}] + [
            None
        ] * 600
        vulns = [
            {"cve": "CVE-A", "cvss_score": 9.5, "hosts_count": 2},
            {"cve": "CVE-B"},
            {"nocve": 1},
        ]
        vulns += [{"cve": f"CVE-X{i}"} for i in range(500)]
        c._fleet_paginate = AsyncMock(return_value=vulns)
        c._fetch_vulnerability_details = AsyncMock(
            return_value={
                "detail": {
                    "cvss_score": 5.0,
                    "hosts_count": 1,
                    "cve_description": "d",
                    "epss_probability": 0.1,
                    "cisa_known_exploit": True,
                },
                "affected_software_ids": [],
                "affected_os_version_ids": [],
                "resolved_in_versions": [],
                "software_names": [],
            }
        )
        created, updated = await c._sync_vulnerabilities(ORG)
        assert updated == 1 and created == 501
        assert c._fetch_vulnerability_details.await_count == 499
        c._fleet_paginate = AsyncMock(side_effect=RuntimeError("x"))
        assert await c._sync_vulnerabilities(ORG) == (0, 0)


class TestPoliciesLabelsProfiles:
    """Policies, labels and MDM profiles."""

    @pytest.mark.asyncio
    async def test_policies_fleet5(self) -> None:
        """Fleet 5 layout merges global and fleet policies, de-duplicated."""
        c = _conn()
        c._fleet_paginate_with_status = AsyncMock(
            side_effect=[
                (
                    200,
                    [
                        {"id": 1, "passing_host_count": 3, "failing_host_count": 1},
                        {"id": 1},
                        {"name": "noid"},
                    ],
                ),
                (200, [{"id": 2, "fleet_id": 9}]),
            ]
        )
        assert await c._sync_policies(ORG, {9: 90}) == (2, 0)
        assert c.policy_cache[1][1] == 1
        c._fleet_paginate_with_status = AsyncMock(side_effect=RuntimeError("x"))
        assert await c._sync_policies(ORG, {}) == (0, 0)

    @pytest.mark.asyncio
    async def test_policies_fleet4_fallback(self) -> None:
        """A 404 on the new route switches to the legacy global/team routes."""
        c = _conn()
        c._fleet_paginate_with_status = AsyncMock(
            side_effect=[(404, []), (200, [{"id": 5}]), (200, [{"id": 6}])]
        )
        assert await c._sync_policies(ORG, {9: 90}) == (2, 0)

    @pytest.mark.asyncio
    async def test_labels(self) -> None:
        """Builtin labels are not cached for membership; custom ones are."""
        c = _conn()
        c._fleet_paginate = AsyncMock(
            return_value=[
                {"id": 1, "label_type": "builtin"},
                {"id": 2, "name": "x", "fleet_id": 9},
                {"name": "noid"},
            ]
        )
        c.elder_client.get_entity_by_external_id.side_effect = [None, {"id": 8}]
        assert await c._sync_labels(ORG, {9: 90}) == (1, 1)
        assert list(c.label_entity_cache) == [2]
        c._fleet_paginate = AsyncMock(side_effect=RuntimeError("x"))
        assert await c._sync_labels(ORG, {}) == (0, 0)

    @pytest.mark.asyncio
    async def test_mdm_profiles(self) -> None:
        """Profiles de-duplicate by uuid and skip unavailable endpoints."""
        c = _conn()
        c._fleet_paginate_with_status = AsyncMock(return_value=(403, []))
        assert await c._sync_mdm_profiles(ORG, {}) == (0, 0)
        c._fleet_paginate_with_status = AsyncMock(
            side_effect=[
                (
                    200,
                    [{"profile_uuid": "a", "name": "A"}, {"uuid": "a"}, {"id": 5}, {}],
                ),
                (200, [{"uuid": "b", "team_id": 1, "fleet_id": 9}]),
            ]
        )
        c.elder_client.get_entity_by_external_id.side_effect = [None, {"id": 2}, None]
        assert await c._sync_mdm_profiles(ORG, {9: 90}) == (2, 1)
        c._fleet_paginate_with_status = AsyncMock(side_effect=RuntimeError("x"))
        assert await c._sync_mdm_profiles(ORG, {}) == (0, 0)


class TestScriptsReports:
    """Reports, scripts and vitals."""

    @pytest.mark.asyncio
    async def test_reports_scripts_vitals(self) -> None:
        """All three resource kinds are created; duplicates and id-less skipped."""
        c = _conn()
        c.elder_client.get_entity_by_external_id.side_effect = [None, {"id": 1}] + [
            None
        ] * 10
        c._fleet_paginate_with_status = AsyncMock(
            side_effect=[
                (200, [{"id": 1, "name": "r"}, {"id": 1}, {"name": "x"}]),
                (200, [{"id": 2, "fleet_id": 9}]),
                (200, [{"id": 3}, {"id": 3}, {}]),
                (200, [{"id": 4, "name": "s"}]),
            ]
        )
        c._fleet_paginate = AsyncMock(return_value=[{"id": 5}, {"name": "x"}])
        created, updated = await c._sync_scripts_and_reports(ORG, {9: 90})
        assert (created, updated) == (4, 1)

    @pytest.mark.asyncio
    async def test_scripts_unavailable_and_error(self) -> None:
        """A non-200 scripts endpoint skips fleet scripts; errors are logged."""
        c = _conn()
        c._fleet_paginate_with_status = AsyncMock(side_effect=[(200, []), (403, [])])
        c._fleet_paginate = AsyncMock(return_value=[])
        assert await c._sync_scripts_and_reports(ORG, {}) == (0, 0)
        c._fleet_paginate_with_status = AsyncMock(side_effect=RuntimeError("x"))
        assert await c._sync_scripts_and_reports(ORG, {}) == (0, 0)


class TestRelationships:
    """Relationship linking."""

    @pytest.mark.asyncio
    async def test_link_paginated_hosts(self) -> None:
        """Only hosts known to the cache are linked, in both directions."""
        c = _conn()
        c.host_entity_cache = {1: 100}
        c._fleet_paginate = AsyncMock(return_value=[{"id": 1}, {"id": 2}, {}])
        assert await c._link_paginated_hosts("/p", None, 7, "t", "d") == 1
        assert (
            await c._link_paginated_hosts("/p", None, 7, "t", "d", host_is_source=True)
            == 1
        )
        kw = c.elder_client.get_or_create_dependency.call_args.kwargs
        assert kw["source_entity_id"] == 100 and kw["target_entity_id"] == 7

    @pytest.mark.asyncio
    async def test_sync_relationships(self) -> None:
        """Every relationship family is linked; per-link failures are logged."""
        c = _conn()
        c.vuln_entity_cache = {"CVE-1": 1, "CVE-2": 2}
        c.vuln_details_cache = {
            "CVE-1": {
                "affected_software_ids": [10, 11],
                "affected_os_version_ids": [20, 21],
            },
            "CVE-9": {"affected_software_ids": [10]},
        }
        c.software_entity_cache = {10: 100}
        c.os_entity_cache = {20: 200}
        c.policy_cache = {1: (300, 0), 2: (301, 5)}
        c.label_entity_cache = {7: 400}
        c._link_paginated_hosts = AsyncMock(
            side_effect=[1, RuntimeError("x"), 1, 1, RuntimeError("y")]
        )
        assert await c._sync_relationships() == 5
        assert c.logger.warning.call_count == 2

    @pytest.mark.asyncio
    async def test_relationships_outer_error(self) -> None:
        """An unexpected error in the detail phase is logged."""
        c = _conn()
        c.vuln_entity_cache = {"CVE-1": 1}
        c._link_paginated_hosts = AsyncMock(return_value=0)
        c.vuln_details_cache = {"CVE-1": None}
        assert await c._sync_relationships() == 0
        c.logger.error.assert_called_once()


class TestSyncFlags:
    """sync() stage selection and health check."""

    @pytest.mark.asyncio
    async def test_all_flags_on(self, monkeypatch) -> None:
        """Every optional stage runs when its flag is enabled."""
        for f in (
            "fleetdm_sync_os_versions",
            "fleetdm_sync_labels",
            "fleetdm_sync_mdm",
            "fleetdm_sync_scripts_reports",
        ):
            monkeypatch.setattr(settings, f, True)
        c = _conn()
        c._get_or_create_organization = AsyncMock(return_value=ORG)
        c._sync_fleets = AsyncMock(return_value={1: 2})
        names = [
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
        for n in names:
            setattr(c, n, AsyncMock(return_value=(1, 1)))
        c._sync_relationships = AsyncMock(return_value=0)
        res = await c.sync()
        assert res.entities_created == 9 and res.entities_updated == 9
        assert res.organizations_created == 2

    @pytest.mark.asyncio
    async def test_health(self) -> None:
        """Health reflects connectivity and status, and swallows errors."""
        c = _conn()
        assert await c.health_check() is False
        c.http_client = object()
        c._fleet_get = AsyncMock(return_value=(200, {}))
        assert await c.health_check() is True
        c._fleet_get = AsyncMock(side_effect=RuntimeError("x"))
        assert await c.health_check() is False
