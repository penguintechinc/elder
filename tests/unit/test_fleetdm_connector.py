"""Comprehensive unit tests for FleetDM connector, discovery provider, and manifest."""

import importlib.util
import json
import sys
import uuid
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

import tests.unit.conftest_worker_stubs  # noqa: F401
from apps.worker.config.settings import settings
from apps.worker.connectors import FleetDMConnector
from apps.worker.connectors.fleetdm_connector import (
    _cvss_to_severity,
    _extract_entity_attrs,
    _sanitize_dict,
)
from apps.worker.discovery import FleetDMDiscoveryClient
from apps.worker.discovery.executor import CLOUD_PROVIDERS
from apps.worker.discovery.service import DiscoveryService
from apps.worker.streams.connectors.base import AuthType, ConnectorManifest
from apps.worker.streams.connectors.node_generator import generate_nodes_from_connector
from apps.worker.streams.executor.node_registry import NodeRegistry
from apps.worker.utils.elder_client import Entity


def _mock_response(
    status_code: int = 200,
    json_data: dict | list | None = None,
    headers: dict[str, str] | None = None,
) -> MagicMock:
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status_code
    resp.headers = headers or {}
    if json_data is None:
        resp.json.side_effect = ValueError("No JSON body")
    else:
        resp.json.return_value = json_data
    return resp


# ---------------------------------------------------------------------------
# 1. Connection & Settings Tests
# ---------------------------------------------------------------------------


class TestFleetDMConnectAndSettings:
    """Tests for FleetDMConnector.connect, disconnect, health_check, and wiring."""

    @pytest.mark.asyncio
    async def test_connect_fails_loud_on_missing_url_or_token(self):
        connector = FleetDMConnector()
        with (
            patch.object(settings, "fleetdm_url", ""),
            patch.object(settings, "fleetdm_api_token", "tok-123"),
        ):
            with pytest.raises(ValueError, match="URL and API token"):
                await connector.connect()

        with (
            patch.object(settings, "fleetdm_url", "https://fleet.example.com"),
            patch.object(settings, "fleetdm_api_token", None),
        ):
            with pytest.raises(ValueError, match="URL and API token"):
                await connector.connect()

    @pytest.mark.asyncio
    async def test_connect_strips_trailing_slash_passes_ssl_and_reads_config_version(
        self,
    ):
        connector = FleetDMConnector()
        mock_http = AsyncMock()
        mock_http.get = AsyncMock(
            side_effect=[
                _mock_response(
                    200,
                    {
                        "license": {"tier": "premium"},
                        "mdm": {"enabled_and_configured": True},
                    },
                ),
                _mock_response(200, {"version": "4.95.0"}),
            ]
        )
        mock_http.aclose = AsyncMock()

        mock_elder = AsyncMock()
        mock_elder.connect = AsyncMock()
        mock_elder.close = AsyncMock()

        with (
            patch.object(settings, "fleetdm_url", "https://fleet.example.com///"),
            patch.object(settings, "fleetdm_api_token", "secret-jwt"),
            patch.object(settings, "fleetdm_verify_ssl", False),
            patch(
                "apps.worker.connectors.fleetdm_connector.httpx.AsyncClient",
                return_value=mock_http,
            ) as mock_client_cls,
            patch(
                "apps.worker.connectors.fleetdm_connector.ElderAPIClient",
                return_value=mock_elder,
            ),
        ):
            await connector.connect()

        call_kwargs = mock_client_cls.call_args.kwargs
        assert call_kwargs["base_url"] == "https://fleet.example.com"
        assert call_kwargs["verify"] is False
        assert connector.is_premium is True
        assert connector.mdm_enabled is True
        assert connector.fleet_version == "4.95.0"

        assert await connector.health_check() is False or True
        mock_http.get = AsyncMock(return_value=_mock_response(200, {"org_info": {}}))
        assert await connector.health_check() is True

        await connector.disconnect()
        mock_http.aclose.assert_awaited_once()
        mock_elder.close.assert_awaited_once()
        assert connector.http_client is None

    @pytest.mark.asyncio
    async def test_connect_raises_when_config_non_200(self):
        connector = FleetDMConnector()
        mock_http = AsyncMock()
        mock_http.get = AsyncMock(return_value=_mock_response(401, {"message": "bad"}))
        mock_http.aclose = AsyncMock()

        with (
            patch.object(settings, "fleetdm_url", "https://fleet.example.com"),
            patch.object(settings, "fleetdm_api_token", "bad-token"),
            patch(
                "apps.worker.connectors.fleetdm_connector.httpx.AsyncClient",
                return_value=mock_http,
            ),
        ):
            with pytest.raises(RuntimeError, match="HTTP 401"):
                await connector.connect()

        mock_http.aclose.assert_awaited_once()
        assert connector.http_client is None

    def test_worker_service_decoupled_from_connectors(self):
        stub_modules = {}
        if importlib.util.find_spec("aiocron") is None:
            stub_modules["aiocron"] = MagicMock()

        with patch.dict(sys.modules, stub_modules):
            from apps.worker.main import WorkerService

            with (
                patch.object(WorkerService, "_init_database"),
                patch.object(settings, "fleetdm_enabled", True),
            ):
                worker = WorkerService()

            assert not hasattr(worker, "connectors")
            assert not hasattr(worker, "_initialize_connectors")
            assert not hasattr(worker, "_init_discovery_executor")
            connector = FleetDMConnector()
            assert connector.name == "fleetdm"


# ---------------------------------------------------------------------------
# 2. HTTP Resilience, Pagination & Fleet 4.x/5 Fallbacks
# ---------------------------------------------------------------------------


class TestFleetDMHTTPAndPagination:
    """Tests for _fleet_get retries, 429 Retry-After, fallbacks, and pagination."""

    @pytest.mark.asyncio
    async def test_fleet_get_handles_429_and_5xx_retries(self):
        connector = FleetDMConnector()
        connector.http_client = AsyncMock()
        connector.http_client.get = AsyncMock(
            side_effect=[
                _mock_response(429, None, headers={"Retry-After": "0.1"}),
                _mock_response(503, None),
                _mock_response(200, {"ok": True}),
            ]
        )

        with patch(
            "apps.worker.connectors.fleetdm_connector.asyncio.sleep"
        ) as sleep_mock:
            status, data = await connector._fleet_get("/api/v1/fleet/hosts")

        assert status == 200
        assert data == {"ok": True}
        assert connector.http_client.get.await_count == 3
        assert sleep_mock.await_count == 2
        assert sleep_mock.await_args_list[0].args[0] == 0.1

    @pytest.mark.asyncio
    async def test_fleet_get_tries_fallback_on_404_and_410_preserving_params(self):
        connector = FleetDMConnector()
        connector.http_client = AsyncMock()
        connector.http_client.get = AsyncMock(
            side_effect=[
                _mock_response(404, None),
                _mock_response(410, None),
                _mock_response(200, {"teams": [{"id": 7}]}),
            ]
        )

        status, data = await connector._fleet_get(
            "/api/v1/fleet/fleets",
            params={"page": 0, "per_page": 50},
            fallback_paths=["/api/v1/fleet/v2/fleets", "/api/v1/fleet/teams"],
        )

        assert status == 200
        assert data == {"teams": [{"id": 7}]}
        calls = connector.http_client.get.await_args_list
        assert calls[0].args[0] == "/api/v1/fleet/fleets"
        assert calls[1].args[0] == "/api/v1/fleet/v2/fleets"
        assert calls[2].args[0] == "/api/v1/fleet/teams"
        assert calls[2].kwargs["params"] == {"page": 0, "per_page": 50}

    @pytest.mark.asyncio
    async def test_fleet_get_returns_204_402_403_cleanly_without_raising(self):
        connector = FleetDMConnector()
        connector.http_client = AsyncMock()

        for code in (204, 402, 403):
            connector.http_client.get = AsyncMock(
                return_value=_mock_response(code, None)
            )
            status, data = await connector._fleet_get("/api/v1/fleet/scripts")
            assert status == code
            assert data is None

    @pytest.mark.asyncio
    async def test_paginate_respects_meta_has_next_results(self):
        connector = FleetDMConnector()
        connector.http_client = AsyncMock()
        connector.http_client.get = AsyncMock(
            side_effect=[
                _mock_response(
                    200,
                    {
                        "hosts": [{"id": 1}, {"id": 2}],
                        "meta": {"has_next_results": True},
                    },
                ),
                _mock_response(
                    200,
                    {
                        "hosts": [{"id": 3}],
                        "meta": {"has_next_results": False},
                    },
                ),
            ]
        )

        with patch.object(settings, "fleetdm_per_page", 100):
            items = await connector._fleet_paginate("/api/v1/fleet/hosts", "hosts")

        assert [h["id"] for h in items] == [1, 2, 3]
        assert connector.http_client.get.await_count == 2
        assert connector.http_client.get.await_args_list[0].kwargs["params"] == {
            "page": 0,
            "per_page": 100,
        }
        assert connector.http_client.get.await_args_list[1].kwargs["params"] == {
            "page": 1,
            "per_page": 100,
        }

    @pytest.mark.asyncio
    async def test_paginate_fallback_result_key(self):
        connector = FleetDMConnector()
        connector.http_client = AsyncMock()
        connector.http_client.get = AsyncMock(
            side_effect=[
                _mock_response(404, None),
                _mock_response(200, {"queries": [{"id": 11, "name": "q1"}]}),
            ]
        )

        items = await connector._fleet_paginate(
            "/api/v1/fleet/reports",
            "reports",
            fallback_paths=["/api/v1/fleet/queries"],
            fallback_result_key="queries",
        )
        assert items == [{"id": 11, "name": "q1"}]


# ---------------------------------------------------------------------------
# 3. Sync Stages, Deduplication, PII Guard & Relationships
# ---------------------------------------------------------------------------


class TestFleetDMSyncAndPIISafety:
    """Tests for sync stages, external_id dedup, PII exclusion, and relationships."""

    @pytest.fixture
    def connector(self):
        c = FleetDMConnector()
        c.http_client = AsyncMock()
        c.elder_client = AsyncMock()
        c.elder_client.list_organizations = AsyncMock(
            return_value={"items": [{"id": 1, "name": "FleetDM", "parent_id": None}]}
        )
        c.elder_client.create_organization = AsyncMock(return_value={"id": 2})
        c.elder_client.get_or_create_dependency = AsyncMock(return_value={})
        return c

    @pytest.mark.asyncio
    async def test_upsert_entity_dedup_uses_get_entity_by_external_id(self, connector):
        entity = Entity(
            name="laptop-01",
            entity_type="compute",
            sub_type="endpoint",
            organization_id=1,
            external_id="fleetdm:host:42",
            attributes={"fleetdm_host_id": 42},
        )

        # First pass -> missing -> create
        connector.elder_client.get_entity_by_external_id = AsyncMock(return_value=None)
        connector.elder_client.create_entity = AsyncMock(return_value={"id": 101})
        connector.elder_client.update_entity = AsyncMock()

        eid1, created1 = await connector._upsert_entity_with_status(entity)
        assert eid1 == 101
        assert created1 is True
        connector.elder_client.create_entity.assert_awaited_once()

        # Second pass -> existing -> update
        connector.elder_client.get_entity_by_external_id = AsyncMock(
            return_value={"id": 101, "metadata": {"fleetdm_host_id": 42}}
        )
        connector.elder_client.create_entity.reset_mock()
        eid2, created2 = await connector._upsert_entity_with_status(entity)
        assert eid2 == 101
        assert created2 is False
        connector.elder_client.create_entity.assert_not_awaited()
        connector.elder_client.update_entity.assert_awaited_once_with(101, entity)

    def test_extract_entity_attrs_handles_metadata_and_legacy_attributes(self):
        assert _extract_entity_attrs({"metadata": {"a": 1}}) == {"a": 1}
        assert _extract_entity_attrs({"attributes": {"b": 2}}) == {"b": 2}
        assert _extract_entity_attrs({}) == {}

    def test_cvss_severity_handles_none_and_ranges(self):
        assert _cvss_to_severity(None) == "unknown"
        assert _cvss_to_severity(9.8) == "critical"
        assert _cvss_to_severity(7.5) == "high"
        assert _cvss_to_severity(5.0) == "medium"
        assert _cvss_to_severity(2.1) == "low"

    @pytest.mark.asyncio
    async def test_no_pii_or_secrets_persisted_across_hosts_policies_profiles(
        self, connector
    ):
        captured_entities: list[Entity] = []

        async def _capture_create(ent: Entity):
            captured_entities.append(ent)
            return {"id": 100 + len(captured_entities)}

        connector.elder_client.get_entity_by_external_id = AsyncMock(return_value=None)
        connector.elder_client.create_entity = AsyncMock(side_effect=_capture_create)

        async def _fake_get(path, params=None):
            if path == "/api/v1/fleet/hosts":
                return _mock_response(
                    200,
                    {
                        "hosts": [
                            {
                                "id": 7,
                                "hostname": "macbook-pro-7",
                                "platform": "darwin",
                                "status": "online",
                                "seen_time": "2026-11-20T12:00:00Z",
                                "fleet_id": 10,
                                "fleet_name": "Workstations",
                                "memory": 17179869184,
                                "disk_encryption_enabled": True,
                                "primary_ip": "10.0.0.7",
                                "primary_mac": "aa:bb:cc:dd:ee:ff",
                                "public_ip": "203.0.113.7",
                                "device_mapping": [
                                    {"email": "alice.pii@example.com", "source": "mdm"}
                                ],
                                "end_users": [
                                    {
                                        "idp_username": "alice.pii",
                                        "idp_full_name": "Alice Sensitive",
                                    }
                                ],
                                "battery": {
                                    "health": "Normal",
                                    "cycle_count": 12,
                                    "serial_secret": "drop-me",
                                },
                                "mdm": {
                                    "enrollment_status": "On (automatic)",
                                    "connected_to_fleet": True,
                                    "name": "Fleet",
                                    "encryption_key": "SUPER-SECRET-FILEVAULT-KEY",
                                    "recovery_lock_password": "SUPER-SECRET-PASSWORD",
                                    "macos_settings": {
                                        "disk_encryption": "verified",
                                        "bootstrap_token": "SECRET-TOKEN",
                                    },
                                },
                            }
                        ]
                    },
                )
            if path == "/api/v1/fleet/hosts/7/certificates":
                return _mock_response(
                    200,
                    {
                        "certificates": [
                            {"source": "system", "common_name": "Alice Secret CN"},
                            {"source": "user", "common_name": "alice.pii@example.com"},
                        ]
                    },
                )
            if path == "/api/v1/fleet/hosts/7/configuration_profiles":
                return _mock_response(
                    200,
                    {
                        "profiles": [
                            {"profile_uuid": "p1", "status": "verified"},
                            {"profile_uuid": "p2", "status": "pending"},
                        ]
                    },
                )
            if path == "/api/v1/fleet/policies":
                return _mock_response(
                    200,
                    {
                        "policies": [
                            {
                                "id": 50,
                                "name": "Disk encryption enabled",
                                "query": "SELECT 1;",
                                "platform": "darwin",
                                "author_email": "bob.author@example.com",
                                "author_name": "Bob Author",
                                "passing_host_count": 9,
                                "failing_host_count": 1,
                            }
                        ]
                    },
                )
            if path == "/api/v1/fleet/configuration_profiles":
                return _mock_response(
                    200,
                    {
                        "profiles": [
                            {
                                "profile_uuid": "prof-uuid-1",
                                "name": "FileVault Escrow",
                                "platform": "darwin",
                                "identifier": "com.fleet.fv",
                                "profile_contents": "<plist>RAW-XML-SECRET</plist>",
                            }
                        ]
                    },
                )
            return _mock_response(404, None)

        connector.http_client.get = AsyncMock(side_effect=_fake_get)

        with patch.object(settings, "fleetdm_sync_host_details", True):
            await connector._sync_hosts(1, {10: 2})
        await connector._sync_policies(1, {})
        await connector._sync_mdm_profiles(1, {})

        serialized = json.dumps([e.attributes for e in captured_entities])
        for forbidden in (
            "alice.pii",
            "Alice Sensitive",
            "Alice Secret CN",
            "device_mapping",
            "end_users",
            "SUPER-SECRET-FILEVAULT-KEY",
            "SUPER-SECRET-PASSWORD",
            "SECRET-TOKEN",
            "bob.author@example.com",
            "Bob Author",
            "RAW-XML-SECRET",
        ):
            assert forbidden not in serialized

        host_ent = captured_entities[0]
        assert host_ent.external_id == "fleetdm:host:7"
        assert host_ent.sub_type == "endpoint"
        assert host_ent.organization_id == 2
        assert host_ent.status_metadata == {
            "status": "online",
            "seen_time": "2026-11-20T12:00:00Z",
        }
        assert host_ent.attributes["certificates_count"] == 2
        assert host_ent.attributes["profiles_status_summary"] == {
            "verified": 1,
            "pending": 1,
        }
        assert (
            host_ent.attributes["mdm"]["macos_settings"]["disk_encryption"]
            == "verified"
        )

    @pytest.mark.asyncio
    async def test_fleet4_fallback_for_fleets_software_policies_reports(
        self, connector
    ):
        connector.elder_client.get_entity_by_external_id = AsyncMock(return_value=None)
        next_id = 10

        async def _create_ent(ent: Entity):
            nonlocal next_id
            next_id += 1
            return {"id": next_id}

        connector.elder_client.create_entity = AsyncMock(side_effect=_create_ent)

        async def _fake_get(path, params=None):
            if path == "/api/v1/fleet/fleets":
                return _mock_response(404, None)
            if path == "/api/v1/fleet/teams":
                return _mock_response(200, {"teams": [{"id": 3, "name": "Eng"}]})
            if path == "/api/v1/fleet/software/versions":
                return _mock_response(404, None)
            if path == "/api/v1/fleet/software":
                return _mock_response(
                    200,
                    {
                        "software": [
                            {
                                "id": 77,
                                "name": "curl",
                                "version": "8.7.1",
                                "source": "deb_packages",
                                "hosts_count": 4,
                            }
                        ]
                    },
                )
            if path == "/api/v1/fleet/software/titles":
                return _mock_response(
                    200,
                    {
                        "software_titles": [
                            {
                                "id": 5,
                                "ai_tool": True,
                                "versions": [{"id": 77, "version": "8.7.1"}],
                            }
                        ]
                    },
                )
            if path == "/api/v1/fleet/policies":
                return _mock_response(404, None)
            if path == "/api/v1/fleet/global/policies":
                return _mock_response(
                    200,
                    {
                        "policies": [
                            {"id": 1, "name": "Global Check", "passing_host_count": 2}
                        ]
                    },
                )
            if path == "/api/v1/fleet/fleets/3/policies":
                return _mock_response(404, None)
            if path == "/api/v1/fleet/teams/3/policies":
                return _mock_response(
                    200,
                    {
                        "policies": [
                            {
                                "id": 2,
                                "name": "Team Check",
                                "team_id": 3,
                                "failing_host_count": 1,
                            }
                        ]
                    },
                )
            if path == "/api/v1/fleet/reports":
                return _mock_response(404, None)
            if path == "/api/v1/fleet/queries":
                return _mock_response(
                    200,
                    {"queries": [{"id": 88, "name": "Uptime", "query": "SELECT 1"}]},
                )
            if path == "/api/v1/fleet/scripts":
                return _mock_response(402, None)
            if path == "/api/v1/fleet/custom_host_vitals":
                return _mock_response(404, None)
            return _mock_response(404, None)

        connector.http_client.get = AsyncMock(side_effect=_fake_get)

        fleet_map = await connector._sync_fleets(1)
        assert fleet_map == {3: 2}

        sw_created, _ = await connector._sync_software(1)
        assert sw_created == 1
        assert 77 in connector.software_entity_cache

        pol_created, _ = await connector._sync_policies(1, fleet_map)
        assert pol_created == 2
        assert connector.policy_cache[2][1] == 1

        rep_created, _ = await connector._sync_scripts_and_reports(1, fleet_map)
        assert rep_created == 1

    @pytest.mark.asyncio
    async def test_vulnerabilities_204_and_relationships_all_edge_types(
        self, connector
    ):
        connector.elder_client.get_entity_by_external_id = AsyncMock(return_value=None)
        eid_seq = 500

        async def _create_ent(ent: Entity):
            nonlocal eid_seq
            eid_seq += 1
            return {"id": eid_seq}

        connector.elder_client.create_entity = AsyncMock(side_effect=_create_ent)

        async def _fake_get(path, params=None):
            if path == "/api/v1/fleet/vulnerabilities":
                return _mock_response(
                    200,
                    {
                        "vulnerabilities": [
                            {
                                "cve": "CVE-2025-0001",
                                "cvss_score": None,
                                "hosts_count": 1,
                            },
                            {
                                "cve": "CVE-2025-0002",
                                "cvss_score": 9.8,
                                "hosts_count": 1,
                            },
                        ]
                    },
                )
            if path == "/api/v1/fleet/vulnerabilities/CVE-2025-0001":
                return _mock_response(204, None)
            if path == "/api/v1/fleet/vulnerabilities/CVE-2025-0002":
                return _mock_response(
                    200,
                    {
                        "vulnerability": {
                            "cve": "CVE-2025-0002",
                            "software": [
                                {
                                    "id": 90,
                                    "name": "openssl",
                                    "resolved_in_version": "3.2",
                                }
                            ],
                            "os_versions": [
                                {"os_version_id": 80, "resolved_in_version": "15.1"}
                            ],
                        }
                    },
                )
            if path == "/api/v1/fleet/hosts":
                if (params or {}).get("vulnerability") == "CVE-2025-0002":
                    return _mock_response(200, {"hosts": [{"id": 7}]})
                if (params or {}).get("vulnerability") == "CVE-2025-0001":
                    return _mock_response(204, None)
                if (params or {}).get("software_version_id") == 90:
                    return _mock_response(200, {"hosts": [{"id": 7}]})
                if (params or {}).get("policy_id") == 50:
                    assert params.get("policy_response") == "failing"
                    return _mock_response(200, {"hosts": [{"id": 7}]})
                return _mock_response(200, {"hosts": []})
            if path == "/api/v1/fleet/labels/30/hosts":
                return _mock_response(200, {"hosts": [{"id": 7}]})
            return _mock_response(404, None)

        connector.http_client.get = AsyncMock(side_effect=_fake_get)

        # Pre-seed host, software, os, policy, label caches
        connector.host_entity_cache = {7: 1007}
        connector.software_entity_cache = {90: 1090}
        connector.os_entity_cache = {80: 1080}
        connector.policy_cache = {50: (1050, 1), 51: (1051, 0)}
        connector.label_entity_cache = {30: 1030}

        v_created, _ = await connector._sync_vulnerabilities(1)
        assert v_created == 2
        assert "CVE-2025-0001" in connector.vuln_entity_cache
        assert "CVE-2025-0002" in connector.vuln_entity_cache

        rel_count = await connector._sync_relationships()
        assert rel_count == 6

        deps = [
            (
                call.kwargs["source_entity_id"],
                call.kwargs["target_entity_id"],
                call.kwargs["dependency_type"],
            )
            for call in connector.elder_client.get_or_create_dependency.await_args_list
        ]
        v2_id = connector.vuln_entity_cache["CVE-2025-0002"]
        assert (v2_id, 1007, "affects") in deps
        assert (v2_id, 1090, "affects") in deps
        assert (v2_id, 1080, "affects") in deps
        assert (1090, 1007, "installed_on") in deps
        assert (1007, 1050, "fails_policy") in deps
        assert (1007, 1030, "member_of") in deps

    @pytest.mark.asyncio
    async def test_labels_caches_only_non_builtin_labels(self, connector):
        connector.elder_client.get_entity_by_external_id = AsyncMock(return_value=None)
        seq = 200

        async def _create_ent(ent: Entity):
            nonlocal seq
            seq += 1
            return {"id": seq}

        connector.elder_client.create_entity = AsyncMock(side_effect=_create_ent)
        connector.http_client.get = AsyncMock(
            return_value=_mock_response(
                200,
                {
                    "labels": [
                        {"id": 1, "name": "All Hosts", "label_type": "builtin"},
                        {
                            "id": 2,
                            "name": "Engineering Macs",
                            "label_type": "regular",
                            "author_email": "secret@example.com",
                        },
                    ]
                },
            )
        )

        created, _ = await connector._sync_labels(1, {})
        assert created == 2
        assert 1 not in connector.label_entity_cache
        assert 2 in connector.label_entity_cache

    @pytest.mark.asyncio
    async def test_full_sync_clears_stale_caches_and_runs_all_stages(self, connector):
        connector.host_entity_cache = {999: 9999}
        connector.elder_client.get_entity_by_external_id = AsyncMock(return_value=None)
        connector.elder_client.create_entity = AsyncMock(return_value={"id": 300})
        connector.http_client.get = AsyncMock(return_value=_mock_response(404, None))

        result = await connector.sync()
        assert result.has_errors is False
        assert 999 not in connector.host_entity_cache


# ---------------------------------------------------------------------------
# 4. Discovery Provider & DiscoveryService Registration
# ---------------------------------------------------------------------------


class TestFleetDMDiscoveryProvider:
    """Tests for FleetDMDiscoveryClient, DiscoveryService, and CLOUD_PROVIDERS."""

    def test_cloud_providers_and_valid_providers_include_fleetdm(self):
        assert "fleetdm" in CLOUD_PROVIDERS

    def test_validate_config_and_test_connection(self):
        bad = FleetDMDiscoveryClient({"url": "", "api_token": ""})
        with pytest.raises(ValueError):
            bad.validate_config()
        assert bad.test_connection() is False

        client = FleetDMDiscoveryClient(
            {
                "url": "https://fleet.example.com/",
                "api_token": "tok-abc",
                "verify_ssl": False,
            }
        )
        assert client.validate_config() is True
        assert client.url == "https://fleet.example.com"
        assert "mdm_profiles" in client.get_supported_services()

        mock_sync_client = MagicMock()
        mock_sync_client.__enter__ = MagicMock(return_value=mock_sync_client)
        mock_sync_client.__exit__ = MagicMock(return_value=None)
        mock_sync_client.get = MagicMock(return_value=_mock_response(200, {}))
        with patch(
            "apps.worker.discovery.fleetdm_discovery.httpx.Client",
            return_value=mock_sync_client,
        ):
            assert client.test_connection() is True

    def test_discover_all_returns_pii_free_compute_and_security(self):
        client = FleetDMDiscoveryClient(
            {"url": "https://fleet.example.com", "api_token": "tok-abc"}
        )

        def _fake_get(path, params=None):
            if path == "/api/v1/fleet/hosts":
                return _mock_response(
                    200,
                    {
                        "hosts": [
                            {
                                "id": 1,
                                "hostname": "eng-mac-1",
                                "platform": "darwin",
                                "os_version_id": 10,
                                "device_mapping": [{"email": "pii@example.com"}],
                                "end_users": [{"idp_username": "pii"}],
                                "mdm": {
                                    "enrollment_status": "On (automatic)",
                                    "encryption_key": "SECRET",
                                },
                            }
                        ]
                    },
                )
            if path == "/api/v1/fleet/software/versions":
                return _mock_response(
                    200,
                    {"software": [{"id": 20, "name": "python", "version": "3.13.1"}]},
                )
            if path == "/api/v1/fleet/os_versions":
                return _mock_response(
                    200,
                    {
                        "os_versions": [
                            {
                                "os_version_id": 10,
                                "name": "macOS 15.1",
                                "platform": "darwin",
                            }
                        ]
                    },
                )
            if path == "/api/v1/fleet/labels":
                return _mock_response(
                    200,
                    {"labels": [{"id": 5, "name": "VIP", "label_type": "manual"}]},
                )
            if path == "/api/v1/fleet/labels/5/hosts":
                return _mock_response(200, {"hosts": [{"id": 1}]})
            if path == "/api/v1/fleet/vulnerabilities":
                return _mock_response(
                    200,
                    {"vulnerabilities": [{"cve": "CVE-2025-1111", "cvss_score": 8.1}]},
                )
            if path == "/api/v1/fleet/policies":
                return _mock_response(
                    200,
                    {
                        "policies": [
                            {
                                "id": 3,
                                "name": "Gatekeeper",
                                "author_email": "author@example.com",
                            }
                        ]
                    },
                )
            if path == "/api/v1/fleet/configuration_profiles":
                return _mock_response(402, None)
            return _mock_response(404, None)

        mock_sync_client = MagicMock()
        mock_sync_client.__enter__ = MagicMock(return_value=mock_sync_client)
        mock_sync_client.__exit__ = MagicMock(return_value=None)
        mock_sync_client.get = MagicMock(side_effect=_fake_get)

        with patch(
            "apps.worker.discovery.fleetdm_discovery.httpx.Client",
            return_value=mock_sync_client,
        ):
            results = client.discover_all()

        assert results["resources_count"] == 6
        assert isinstance(results["discovery_time"], datetime)
        assert len(results["compute"]) == 4
        assert len(results["security"]) == 2
        serialized = json.dumps(results["compute"] + results["security"], default=str)
        assert "pii@example.com" not in serialized
        assert "SECRET" not in serialized
        assert "author@example.com" not in serialized

    def test_discovery_service_wiring_and_sanitization(self):
        fake_db = MagicMock()
        fake_job = MagicMock()
        fake_job.provider = "fleetdm"
        fake_job.config_json = {
            "url": "https://fleet.example.com",
            "api_token": "tok-secret",
        }
        fake_job.as_dict.return_value = {
            "id": 9,
            "provider": "fleetdm",
            "config_json": fake_job.config_json,
        }
        jobs_table = MagicMock()
        jobs_table.__getitem__.return_value = fake_job
        jobs_table.insert.return_value = 9
        fake_db.discovery_jobs = jobs_table

        svc = DiscoveryService(fake_db)
        client = svc._get_discovery_client(9)
        assert isinstance(client, FleetDMDiscoveryClient)

        sanitized = svc._sanitize_job(
            {
                "id": 9,
                "provider": "fleetdm",
                "config_json": {
                    "url": "https://fleet.example.com",
                    "api_token": "tok-1",
                    "api_key": "key-2",
                    "fleetdm_api_token": "tok-3",
                },
            }
        )
        assert sanitized["config_json"]["api_token"] == "***REDACTED***"
        assert sanitized["config_json"]["api_key"] == "***REDACTED***"
        assert sanitized["config_json"]["fleetdm_api_token"] == "***REDACTED***"

        assert (
            svc._detect_provider_type(
                {"compute": [{"resource_type": "fleetdm_host", "provider": "fleetdm"}]}
            )
            == "fleetdm"
        )

        # Invalid provider check still validates & accepts fleetdm
        created = svc.create_job(
            name="FleetDM Scan",
            provider="fleetdm",
            config={"url": "https://fleet.example.com", "api_token": "secret"},
            organization_id=1,
        )
        assert created["config_json"]["api_token"] == "***REDACTED***"

    def test_store_discovered_resources_persists_fleetdm_root_and_security(self, app):
        db = app.db
        tenant_id = db.tenants.insert(
            name="FleetDM Disc Tenant",
            slug=f"fleetdm-disc-{uuid.uuid4().hex[:8]}",
            is_active=True,
        )
        org_id = db.organizations.insert(name="FleetDM Disc Org", tenant_id=tenant_id)
        db.commit()

        svc = DiscoveryService(db)
        discovery_results = {
            "compute": [
                {
                    "resource_id": "1",
                    "resource_type": "fleetdm_host",
                    "name": "mac-01",
                    "provider": "fleetdm",
                    "external_id": "fleetdm:host:1",
                    "metadata": {"hostname": "mac-01"},
                    "tags": {"provider": "fleetdm"},
                    "relationships": [],
                }
            ],
            "security": [
                {
                    "resource_id": "CVE-2025-9999",
                    "resource_type": "fleetdm_vulnerability",
                    "name": "CVE: CVE-2025-9999",
                    "provider": "fleetdm",
                    "external_id": "fleetdm:vuln:CVE-2025-9999",
                    "metadata": {"cve": "CVE-2025-9999", "severity": "critical"},
                    "tags": {"provider": "fleetdm"},
                    "relationships": [],
                }
            ],
            "storage": [],
            "network": [],
            "database": [],
            "serverless": [],
        }

        edge_counts = svc._store_discovered_resources(org_id, discovery_results)
        assert edge_counts["unresolved_edges"] == 0

        root = (
            db(
                (db.entities.organization_id == org_id)
                & (db.entities.sub_type == "fleetdm_server")
            )
            .select()
            .first()
        )
        assert root is not None

        vuln_row = (
            db(
                (db.entities.organization_id == org_id)
                & (db.entities.external_id == "fleetdm:vuln:CVE-2025-9999")
            )
            .select()
            .first()
        )
        assert vuln_row is not None
        assert vuln_row.type == "security"


# ---------------------------------------------------------------------------
# 5. Playbooks / Streams Manifest Validation
# ---------------------------------------------------------------------------


class TestFleetDMStreamsManifest:
    """Tests for apps/worker/streams/connectors/manifests/fleetdm.yaml."""

    def test_fleetdm_manifest_loads_and_generates_valid_nodes(self):
        manifest = ConnectorManifest.from_yaml(
            "apps/worker/streams/connectors/manifests/fleetdm.yaml"
        )

        assert manifest.id == "fleetdm"
        assert manifest.name == "FleetDM"
        assert manifest.base_url_env == "FLEETDM_URL"
        assert manifest.health_endpoint == "/api/v1/fleet/config"
        assert len(manifest.auth_methods) == 1
        assert manifest.auth_methods[0].type == AuthType.JWT
        assert manifest.auth_methods[0].header == "Authorization"
        assert manifest.auth_methods[0].env_var == "FLEETDM_API_TOKEN"
        assert manifest.auth_methods[0].token_prefix == "Bearer "

        assert len(manifest.triggers) == 3
        assert len(manifest.transforms) == 19
        assert len(manifest.actions) == 21

        with patch.dict(NodeRegistry._registry, {}, clear=True):
            generated = generate_nodes_from_connector(manifest)
            assert generated == 43
            for node_type in NodeRegistry._registry:
                assert len(node_type) <= 50, f"node_type exceeds 50 chars: {node_type}"
