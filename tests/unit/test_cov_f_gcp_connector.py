"""Behavioural tests for the GCP connector (Compute Engine, VPC, Cloud Storage)."""

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from google.api_core.exceptions import (
    Forbidden,
    ServiceUnavailable,
    TooManyRequests,
    Unauthenticated,
)

import tests.unit.conftest_worker_stubs  # noqa: F401
from apps.worker.config.settings import settings
from apps.worker.connectors import gcp_connector as mod
from apps.worker.connectors.gcp_connector import GCPConnector
from tests.unit._cov_f_helpers import (
    by_name,
    created_entities,
    logged_text,
    make_elder,
    spy_logger,
    updated_entities,
)

PROJECT = "elder-test-project"
ORG_ID = 64
ZONE_URL = f"https://www.googleapis.com/compute/v1/projects/{PROJECT}/zones/"


def _instance(
    iid: int, name: str, status: str, zone: str, nics: list[Any] | None
) -> Any:
    """Build a Compute Engine instance as the SDK exposes it."""
    return SimpleNamespace(
        id=iid,
        name=name,
        status=status,
        zone=f"{ZONE_URL}{zone}",
        machine_type=f"{ZONE_URL}{zone}/machineTypes/e2-medium",
        network_interfaces=nics,
        creation_timestamp="2026-01-02T03:04:05.000-08:00",
    )


def _aggregated() -> list[tuple[str, Any]]:
    """Return an aggregated instance listing spanning three zones (one empty)."""
    public = SimpleNamespace(
        network_i_p="10.128.0.2",
        access_configs=[SimpleNamespace(nat_i_p="203.0.113.9")],
    )
    private = SimpleNamespace(network_i_p="10.128.0.3", access_configs=[])
    return [
        ("zones/us-central1-a", SimpleNamespace(instances=[])),
        (
            "zones/us-central1-b",
            SimpleNamespace(
                instances=[
                    _instance(111, "web", "RUNNING", "us-central1-b", [public]),
                    _instance(222, "db", "TERMINATED", "us-central1-b", [private]),
                ]
            ),
        ),
        (
            "zones/us-east1-c",
            SimpleNamespace(
                instances=[_instance(333, "bare", "RUNNING", "us-east1-c", None)]
            ),
        ),
    ]


@pytest.fixture
def connector(monkeypatch: pytest.MonkeyPatch) -> GCPConnector:
    """Return a connector for the test project with an Elder double."""
    monkeypatch.setattr(settings, "gcp_project_id", PROJECT)
    monkeypatch.setattr(settings, "create_missing_organizations", True)
    c = GCPConnector()
    c.credentials = object()
    c.elder_client = make_elder()
    spy_logger(c)
    return c


class TestConnect:
    """Credential loading."""

    @pytest.mark.asyncio
    async def test_service_account_file_is_loaded(
        self, connector: GCPConnector, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A configured key file is loaded and the Elder client connected."""
        creds = object()
        monkeypatch.setattr(settings, "gcp_credentials_path", "/run/secrets/gcp.json")
        elder = AsyncMock()
        with (
            patch.object(
                mod, "load_credentials_from_file", return_value=(creds, PROJECT)
            ) as loader,
            patch.object(mod, "ElderAPIClient", return_value=elder),
        ):
            await connector.connect()
        loader.assert_called_once_with("/run/secrets/gcp.json")
        assert connector.credentials is creds
        elder.connect.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_unreadable_key_file_raises_before_elder(
        self, connector: GCPConnector, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A bad key file aborts connect and logs the path, never key material."""
        monkeypatch.setattr(settings, "gcp_credentials_path", "/nope.json")
        with (
            patch.object(
                mod, "load_credentials_from_file", side_effect=ValueError("bad json")
            ),
            patch.object(mod, "ElderAPIClient") as elder_cls,
            pytest.raises(ValueError, match="bad json"),
        ):
            await connector.connect()
        elder_cls.assert_not_called()
        assert "/nope.json" in logged_text(connector.logger)

    @pytest.mark.asyncio
    async def test_application_default_credentials_fallback(
        self, connector: GCPConnector, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Without a key file, application-default credentials are used."""
        creds = object()
        monkeypatch.setattr(settings, "gcp_credentials_path", None)
        with (
            patch("google.auth.default", return_value=(creds, PROJECT)) as default,
            patch.object(mod, "ElderAPIClient", return_value=AsyncMock()),
        ):
            await connector.connect()
        default.assert_called_once_with()
        assert connector.credentials is creds

    @pytest.mark.asyncio
    async def test_disconnect(self, connector: GCPConnector) -> None:
        """Disconnect closes Elder and clears the organization cache."""
        connector.organization_cache["root:GCP"] = 1
        await connector.disconnect()
        connector.elder_client.close.assert_awaited_once()
        assert connector.organization_cache == {}
        connector.elder_client = None
        await connector.disconnect()


class TestCompute:
    """Compute Engine instance mapping."""

    @pytest.mark.asyncio
    async def test_instances_mapped_across_zones(self, connector: GCPConnector) -> None:
        """Instances from every non-empty zone are mapped with addresses and status."""
        with patch.object(mod.compute_v1, "InstancesClient") as client_cls:
            client_cls.return_value.aggregated_list.return_value = _aggregated()
            assert await connector._sync_compute_instances(ORG_ID) == (3, 0)

        client_cls.assert_called_once_with(credentials=connector.credentials)
        client_cls.return_value.aggregated_list.assert_called_once_with(project=PROJECT)
        got = by_name(created_entities(connector.elder_client))
        web = got["GCE: web"]
        assert (web.entity_type, web.organization_id) == ("compute", ORG_ID)
        assert web.is_active is True
        assert web.attributes["instance_id"] == "111"
        assert web.attributes["machine_type"] == "e2-medium"
        assert web.attributes["zone"] == "us-central1-b"
        assert web.attributes["internal_ip"] == "10.128.0.2"
        assert web.attributes["external_ip"] == "203.0.113.9"
        assert web.attributes["project_id"] == PROJECT
        assert web.tags == ["gcp", "compute", "gce", "us-central1-b"]
        db = got["GCE: db"]
        assert db.is_active is False and db.attributes["external_ip"] is None
        bare = got["GCE: bare"]
        assert bare.attributes["internal_ip"] is None
        assert bare.attributes["external_ip"] is None

    @pytest.mark.asyncio
    async def test_existing_instance_updated_by_id(
        self, connector: GCPConnector
    ) -> None:
        """An instance already in Elder (by instance id) is updated, not duplicated."""
        connector.elder_client.list_entities.return_value = {
            "items": [{"id": 1}, {"id": 2, "metadata": {"instance_id": "222"}}]
        }
        with patch.object(mod.compute_v1, "InstancesClient") as client_cls:
            client_cls.return_value.aggregated_list.return_value = _aggregated()
            assert await connector._sync_compute_instances(ORG_ID) == (2, 1)
        ((entity_id, entity),) = updated_entities(connector.elder_client)
        assert (entity_id, entity.name) == (2, "GCE: db")


class TestNetworksAndBuckets:
    """VPC and bucket mapping."""

    @pytest.mark.asyncio
    async def test_vpc_networks_mapped(self, connector: GCPConnector) -> None:
        """VPCs carry their routing mode when present and None otherwise."""
        networks = [
            SimpleNamespace(
                id=9001,
                name="default",
                auto_create_subnetworks=True,
                routing_config=SimpleNamespace(routing_mode="REGIONAL"),
                creation_timestamp="2025-05-05T00:00:00Z",
            ),
            SimpleNamespace(
                id=9002,
                name="legacy",
                auto_create_subnetworks=False,
                routing_config=None,
                creation_timestamp=None,
            ),
        ]
        connector.elder_client.list_entities.return_value = {
            "items": [{"id": 12, "metadata": {"network_id": "9002"}}]
        }
        with patch.object(mod.compute_v1, "NetworksClient") as client_cls:
            client_cls.return_value.list.return_value = networks
            assert await connector._sync_vpc_networks(ORG_ID) == (1, 1)

        client_cls.return_value.list.assert_called_once_with(project=PROJECT)
        (default,) = created_entities(connector.elder_client)
        assert (default.name, default.entity_type) == ("VPC: default", "vpc")
        assert default.attributes["routing_mode"] == "REGIONAL"
        ((entity_id, legacy),) = updated_entities(connector.elder_client)
        assert entity_id == 12 and legacy.attributes["routing_mode"] is None
        connector.elder_client.list_entities.assert_awaited_with(
            organization_id=ORG_ID, entity_type="vpc"
        )

    @pytest.mark.asyncio
    async def test_buckets_mapped(self, connector: GCPConnector) -> None:
        """Buckets carry location/class and tolerate a missing creation time."""
        buckets = [
            SimpleNamespace(
                name="elder-assets",
                location="US-CENTRAL1",
                storage_class="STANDARD",
                time_created=datetime(2025, 3, 4, tzinfo=UTC),
            ),
            SimpleNamespace(
                name="elder-logs",
                location="EU",
                storage_class="NEARLINE",
                time_created=None,
            ),
        ]
        connector.elder_client.list_entities.return_value = {
            "items": [{"id": 13, "metadata": {"bucket_name": "elder-logs"}}]
        }
        with patch.object(mod.storage, "Client") as client_cls:
            client_cls.return_value.list_buckets.return_value = buckets
            assert await connector._sync_storage_buckets(ORG_ID) == (1, 1)

        client_cls.assert_called_once_with(
            credentials=connector.credentials, project=PROJECT
        )
        (assets,) = created_entities(connector.elder_client)
        assert assets.name == "GCS: elder-assets"
        assert assets.attributes["time_created"] == "2025-03-04T00:00:00+00:00"
        assert assets.tags == ["gcp", "gcs", "storage", "us-central1"]
        ((entity_id, logs),) = updated_entities(connector.elder_client)
        assert entity_id == 13 and logs.attributes["time_created"] is None


class TestFailures:
    """Provider error containment."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("method", "target", "call"),
        [
            (
                "_sync_compute_instances",
                "compute_v1.InstancesClient",
                "aggregated_list",
            ),
            ("_sync_vpc_networks", "compute_v1.NetworksClient", "list"),
            ("_sync_storage_buckets", "storage.Client", "list_buckets"),
        ],
    )
    @pytest.mark.parametrize(
        "error",
        [
            Unauthenticated("expired"),
            Forbidden("denied"),
            TooManyRequests("quota"),
            ServiceUnavailable("backend"),
        ],
        ids=["401", "403", "429", "503"],
    )
    async def test_google_api_errors_contained(
        self, connector: GCPConnector, method: str, target: str, call: str, error: Any
    ) -> None:
        """Auth, quota and availability errors are logged and write nothing."""
        owner_name, attr = target.split(".")
        client_cls = MagicMock()
        getattr(client_cls.return_value, call).side_effect = error
        with patch.object(getattr(mod, owner_name), attr, client_cls):
            assert await getattr(connector, method)(ORG_ID) == (0, 0)
        connector.logger.error.assert_called_once()
        connector.elder_client.create_entity.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_empty_project_writes_nothing(self, connector: GCPConnector) -> None:
        """A project with no resources results in zero Elder writes."""
        with (
            patch.object(mod.compute_v1, "InstancesClient") as inst,
            patch.object(mod.compute_v1, "NetworksClient") as nets,
            patch.object(mod.storage, "Client") as stor,
        ):
            inst.return_value.aggregated_list.return_value = []
            nets.return_value.list.return_value = []
            stor.return_value.list_buckets.return_value = []
            result = await connector.sync()
        assert result.errors == []
        assert (result.organizations_created, result.entities_created) == (2, 0)
        connector.elder_client.create_entity.assert_not_awaited()


class TestSyncAndHealth:
    """End-to-end sync and health check."""

    @pytest.mark.asyncio
    async def test_full_sync_nests_project_under_gcp_org(
        self, connector: GCPConnector
    ) -> None:
        """The project org is created under the GCP root and owns every entity."""
        ids = iter([700, 701])
        connector.elder_client.create_organization.side_effect = lambda _o: {
            "id": next(ids)
        }
        with (
            patch.object(mod.compute_v1, "InstancesClient") as inst,
            patch.object(mod.compute_v1, "NetworksClient") as nets,
            patch.object(mod.storage, "Client") as stor,
        ):
            inst.return_value.aggregated_list.return_value = _aggregated()
            nets.return_value.list.return_value = []
            stor.return_value.list_buckets.return_value = []
            result = await connector.sync()

        orgs = [
            c.args[0] for c in connector.elder_client.create_organization.call_args_list
        ]
        assert [(o.name, o.parent_id) for o in orgs] == [
            ("GCP", None),
            (f"GCP Project: {PROJECT}", 700),
        ]
        assert result.entities_created == 3
        assert {
            e.organization_id for e in created_entities(connector.elder_client)
        } == {701}

    @pytest.mark.asyncio
    async def test_sync_reports_elder_failure(self, connector: GCPConnector) -> None:
        """An Elder outage is reported in the result, not raised."""
        connector.elder_client.list_organizations.side_effect = RuntimeError("down")
        result = await connector.sync()
        assert result.errors == ["GCP sync failed: down"]

    @pytest.mark.asyncio
    async def test_health_check_states(self, connector: GCPConnector) -> None:
        """Health lists one zone; any provider failure reports unhealthy."""
        with patch.object(mod.compute_v1, "ZonesClient") as zones:
            zones.return_value.list.return_value = iter([object()])
            assert await connector.health_check() is True
            zones.return_value.list.assert_called_once_with(
                project=PROJECT, max_results=1
            )
            zones.return_value.list.side_effect = Forbidden("denied")
            assert await connector.health_check() is False
