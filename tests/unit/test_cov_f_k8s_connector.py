"""Behavioural tests for the Kubernetes connector (namespaces, secrets, pods, PVCs)."""

import base64
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from kubernetes import client as k8s
from kubernetes.client.rest import ApiException

import tests.unit.conftest_worker_stubs  # noqa: F401
from apps.worker.config.settings import settings
from apps.worker.connectors import k8s_connector as mod
from apps.worker.connectors.k8s_connector import KubernetesConnector
from tests.unit._cov_f_helpers import (
    assert_not_leaked,
    by_name,
    created_entities,
    fake_secret,
    make_elder,
    spy_logger,
    updated_entities,
)

ORG_ID = 88
CLUSTER = "dal2alpha"
CREATED = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)
SECRET_VALUE = fake_secret("k8s", "db", "password")


def _meta(name: str, namespace: str | None = None, **extra: Any) -> k8s.V1ObjectMeta:
    """Build object metadata as the API server returns it."""
    return k8s.V1ObjectMeta(name=name, namespace=namespace, uid=f"uid-{name}", **extra)


def _core_api() -> MagicMock:
    """Return a CoreV1Api double serving real kubernetes model objects."""
    api = MagicMock(name="CoreV1Api")
    api.list_namespace.return_value = k8s.V1NamespaceList(
        items=[
            k8s.V1Namespace(
                metadata=_meta(
                    "elder", creation_timestamp=CREATED, labels={"team": "core"}
                ),
                status=k8s.V1NamespaceStatus(phase="Active"),
            ),
            k8s.V1Namespace(metadata=_meta("dying"), status=None),
        ]
    )

    def quotas(namespace: str) -> k8s.V1ResourceQuotaList:
        """Serve a quota for one namespace and a 403 for the other."""
        if namespace == "dying":
            raise ApiException(status=403, reason="Forbidden")
        return k8s.V1ResourceQuotaList(
            items=[
                k8s.V1ResourceQuota(
                    status=k8s.V1ResourceQuotaStatus(hard={"pods": "10", "cpu": 4})
                )
            ]
        )

    api.list_namespaced_resource_quota.side_effect = quotas
    api.list_secret_for_all_namespaces.return_value = k8s.V1SecretList(
        items=[
            k8s.V1Secret(
                metadata=_meta("db-creds", "elder", creation_timestamp=CREATED),
                type="Opaque",
                data={"password": base64.b64encode(SECRET_VALUE.encode()).decode()},
            ),
            k8s.V1Secret(metadata=_meta("empty", "elder"), type="Opaque", data=None),
        ]
    )
    api.list_pod_for_all_namespaces.return_value = k8s.V1PodList(
        items=[
            k8s.V1Pod(
                metadata=_meta("api-0", "elder", creation_timestamp=CREATED),
                spec=k8s.V1PodSpec(
                    node_name="worker-1",
                    restart_policy="Always",
                    service_account="elder-api",
                    containers=[
                        k8s.V1Container(
                            name="api",
                            image="ghcr.io/example/api:1",
                            ports=[
                                k8s.V1ContainerPort(container_port=8443, protocol="TCP")
                            ],
                        ),
                        k8s.V1Container(name="sidecar", image="ghcr.io/example/s:1"),
                    ],
                ),
                status=k8s.V1PodStatus(
                    phase="Running", pod_ip="10.0.0.9", host_ip="192.0.2.4"
                ),
            ),
            k8s.V1Pod(
                metadata=_meta("job-x", "batch"),
                spec=k8s.V1PodSpec(containers=[k8s.V1Container(name="j")]),
                status=None,
            ),
        ]
    )
    api.list_persistent_volume_claim_for_all_namespaces.return_value = (
        k8s.V1PersistentVolumeClaimList(
            items=[
                k8s.V1PersistentVolumeClaim(
                    metadata=_meta("data", "elder", creation_timestamp=CREATED),
                    spec=k8s.V1PersistentVolumeClaimSpec(
                        volume_name="pv-1",
                        storage_class_name="ceph",
                        access_modes=["ReadWriteOnce"],
                        resources=k8s.V1VolumeResourceRequirements(
                            requests={"storage": "10Gi"}
                        ),
                    ),
                    status=k8s.V1PersistentVolumeClaimStatus(
                        phase="Bound", capacity={"storage": "10Gi"}
                    ),
                ),
                k8s.V1PersistentVolumeClaim(
                    metadata=_meta("pending", "elder"), spec=None, status=None
                ),
            ]
        )
    )
    return api


@pytest.fixture(autouse=True)
def _frozen_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    """Freeze the connector's clock so status timestamps are deterministic."""
    monkeypatch.setattr(mod.time, "time", lambda: 1_700_000_000.5)


@pytest.fixture
def connector() -> KubernetesConnector:
    """Return a connector wired to the fake API server and an Elder double."""
    c = KubernetesConnector()
    c.cluster_name = CLUSTER
    c.k8s_core_v1 = _core_api()
    c.elder_client = make_elder()
    spy_logger(c)
    return c


class TestConnect:
    """Cluster configuration discovery."""

    @pytest.mark.asyncio
    async def test_in_cluster_config_preferred(self) -> None:
        """Inside a pod the service-account config is used and no kubeconfig is read."""
        c = KubernetesConnector()
        elder = AsyncMock()
        with (
            patch.object(mod.config, "load_incluster_config") as incluster,
            patch.object(mod.config, "load_kube_config") as kubeconfig,
            patch.object(mod.client, "CoreV1Api") as core,
            patch.object(mod.client, "AppsV1Api") as apps,
            patch.object(mod, "ElderAPIClient", return_value=elder),
        ):
            await c.connect()

        incluster.assert_called_once()
        kubeconfig.assert_not_called()
        assert c.cluster_name == "in-cluster"
        assert c.k8s_core_v1 is core.return_value
        assert c.k8s_apps_v1 is apps.return_value
        core.return_value.get_api_resources.assert_called_once()
        elder.connect.assert_awaited_once()

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("active", "expected"),
        [({"name": "dal2alpha"}, "dal2alpha"), (None, "default")],
    )
    async def test_falls_back_to_kubeconfig_context(
        self, active: dict[str, str] | None, expected: str
    ) -> None:
        """Outside a cluster the active kubeconfig context names the cluster."""
        c = KubernetesConnector()
        with (
            patch.object(
                mod.config,
                "load_incluster_config",
                side_effect=mod.config.ConfigException("not in cluster"),
            ),
            patch.object(mod.config, "load_kube_config") as kubeconfig,
            patch.object(
                mod.config, "list_kube_config_contexts", return_value=([], active)
            ),
            patch.object(mod.client, "CoreV1Api"),
            patch.object(mod.client, "AppsV1Api"),
            patch.object(mod, "ElderAPIClient", return_value=AsyncMock()),
        ):
            await c.connect()
        kubeconfig.assert_called_once()
        assert c.cluster_name == expected

    @pytest.mark.asyncio
    @pytest.mark.parametrize("status", [401, 403, 500])
    async def test_api_probe_failure_raises_before_elder(self, status: int) -> None:
        """An unauthorized or failing API server aborts connect before Elder is used."""
        c = KubernetesConnector()
        logger = spy_logger(c)
        with (
            patch.object(mod.config, "load_incluster_config"),
            patch.object(mod.client, "CoreV1Api") as core,
            patch.object(mod.client, "AppsV1Api"),
            patch.object(mod, "ElderAPIClient") as elder_cls,
        ):
            core.return_value.get_api_resources.side_effect = ApiException(
                status=status
            )
            with pytest.raises(ApiException):
                await c.connect()
        elder_cls.assert_not_called()
        logger.error.assert_called_once()

    @pytest.mark.asyncio
    async def test_disconnect_closes_elder_and_clears_cache(
        self, connector: KubernetesConnector
    ) -> None:
        """Disconnect closes Elder and drops cached organization ids."""
        connector.organization_cache["root:x"] = 3
        await connector.disconnect()
        connector.elder_client.close.assert_awaited_once()
        assert connector.organization_cache == {}

    @pytest.mark.asyncio
    async def test_disconnect_when_never_connected(self) -> None:
        """Disconnecting a fresh connector does not raise."""
        c = KubernetesConnector()
        await c.disconnect()
        assert c.elder_client is None


class TestNamespaces:
    """Namespace mapping."""

    @pytest.mark.asyncio
    async def test_namespaces_mapped_with_quota(
        self, connector: KubernetesConnector
    ) -> None:
        """Namespaces carry labels, stringified quotas and phase-driven activity."""
        assert await connector._sync_namespaces(ORG_ID) == (2, 0)
        got = by_name(created_entities(connector.elder_client))
        elder_ns = got["Namespace: elder"]
        assert (elder_ns.entity_type, elder_ns.sub_type) == ("network", "namespace")
        assert elder_ns.organization_id == ORG_ID and elder_ns.is_active is True
        assert elder_ns.attributes["resource_quota"] == {"pods": "10", "cpu": "4"}
        assert elder_ns.attributes["labels"] == {"team": "core"}
        assert elder_ns.attributes["creation_timestamp"] == CREATED.isoformat()
        assert elder_ns.attributes["cluster"] == CLUSTER
        assert elder_ns.status_metadata == {
            "status": "Active",
            "timestamp": 1_700_000_000,
        }
        dying = got["Namespace: dying"]
        assert dying.is_active is False
        assert dying.attributes["resource_quota"] == {}
        assert dying.attributes["creation_timestamp"] is None
        assert dying.attributes["annotations"] == {}

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "quota_list",
        [
            k8s.V1ResourceQuotaList(items=[]),
            k8s.V1ResourceQuotaList(items=[k8s.V1ResourceQuota(status=None)]),
        ],
        ids=["no-quota", "quota-without-status"],
    )
    async def test_missing_quota_yields_empty_mapping(
        self, connector: KubernetesConnector, quota_list: Any
    ) -> None:
        """Namespaces without a usable quota report an empty quota mapping."""
        connector.k8s_core_v1.list_namespaced_resource_quota.side_effect = None
        connector.k8s_core_v1.list_namespaced_resource_quota.return_value = quota_list
        await connector._sync_namespaces(ORG_ID)
        for entity in created_entities(connector.elder_client):
            assert entity.attributes["resource_quota"] == {}

    @pytest.mark.asyncio
    async def test_existing_namespace_matched_per_cluster(
        self, connector: KubernetesConnector
    ) -> None:
        """A same-named namespace from another cluster is never overwritten."""
        connector.elder_client.list_entities.return_value = {
            "items": [
                {"id": 1, "metadata": {"namespace": "elder", "cluster": "other"}},
                {"id": 2, "metadata": {"namespace": "elder", "cluster": CLUSTER}},
            ]
        }
        assert await connector._sync_namespaces(ORG_ID) == (1, 1)
        ((entity_id, entity),) = updated_entities(connector.elder_client)
        assert (entity_id, entity.name) == (2, "Namespace: elder")


class TestSecrets:
    """Secret metadata mapping."""

    @pytest.mark.asyncio
    async def test_secret_values_never_leave_the_cluster(
        self, connector: KubernetesConnector
    ) -> None:
        """Only key names are synced; neither raw nor encoded values reach Elder or logs."""
        assert await connector._sync_secrets(ORG_ID) == (2, 0)
        entities = created_entities(connector.elder_client)
        got = by_name(entities)
        creds = got["Secret: elder/db-creds"]
        assert (creds.entity_type, creds.sub_type) == ("security", "config")
        assert creds.attributes["secret_keys"] == ["password"]
        assert creds.attributes["secret_key_count"] == 1
        assert creds.attributes["type"] == "Opaque"
        assert creds.tags == ["kubernetes", "secret", "elder", CLUSTER]
        assert got["Secret: elder/empty"].attributes["secret_keys"] == []
        encoded = base64.b64encode(SECRET_VALUE.encode()).decode()
        for entity in entities:
            assert_not_leaked(SECRET_VALUE, entity)
            assert_not_leaked(encoded, entity)
        assert_not_leaked(SECRET_VALUE, connector.logger.mock_calls)

    @pytest.mark.asyncio
    async def test_existing_secret_updated(
        self, connector: KubernetesConnector
    ) -> None:
        """A secret is matched on name, namespace and cluster together."""
        connector.elder_client.list_entities.return_value = {
            "items": [
                {
                    "id": 3,
                    "metadata": {
                        "secret_name": "db-creds",
                        "namespace": "other",
                        "cluster": CLUSTER,
                    },
                },
                {
                    "id": 4,
                    "metadata": {
                        "secret_name": "db-creds",
                        "namespace": "elder",
                        "cluster": CLUSTER,
                    },
                },
            ]
        }
        assert await connector._sync_secrets(ORG_ID) == (1, 1)
        assert updated_entities(connector.elder_client)[0][0] == 4


class TestPods:
    """Pod mapping."""

    @pytest.mark.asyncio
    async def test_pods_mapped_with_containers(
        self, connector: KubernetesConnector
    ) -> None:
        """Pods carry container images/ports and are active only when Running."""
        assert await connector._sync_pods(ORG_ID) == (2, 0)
        got = by_name(created_entities(connector.elder_client))
        api = got["Pod: elder/api-0"]
        assert (api.entity_type, api.sub_type) == ("compute", "kubernetes_node")
        assert api.is_active is True
        assert api.attributes["containers"] == [
            {
                "name": "api",
                "image": "ghcr.io/example/api:1",
                "ports": [{"containerPort": 8443, "protocol": "TCP"}],
            },
            {"name": "sidecar", "image": "ghcr.io/example/s:1", "ports": []},
        ]
        assert api.attributes["node_name"] == "worker-1"
        assert api.attributes["pod_ip"] == "10.0.0.9"
        assert api.attributes["service_account"] == "elder-api"
        job = got["Pod: batch/job-x"]
        assert job.is_active is False
        assert job.attributes["phase"] == "Unknown"
        assert job.attributes["pod_ip"] is None

    @pytest.mark.asyncio
    async def test_existing_pod_updated(self, connector: KubernetesConnector) -> None:
        """A known pod is updated rather than duplicated."""
        connector.elder_client.list_entities.return_value = {
            "items": [
                {
                    "id": 7,
                    "metadata": {
                        "pod_name": "job-x",
                        "namespace": "batch",
                        "cluster": CLUSTER,
                    },
                }
            ]
        }
        assert await connector._sync_pods(ORG_ID) == (1, 1)
        assert updated_entities(connector.elder_client)[0][0] == 7


class TestPVCs:
    """Persistent volume claim mapping."""

    @pytest.mark.asyncio
    async def test_pvcs_mapped(self, connector: KubernetesConnector) -> None:
        """Bound claims carry capacity and class; pending ones use safe defaults."""
        assert await connector._sync_pvcs(ORG_ID) == (2, 0)
        got = by_name(created_entities(connector.elder_client))
        data = got["PVC: elder/data"]
        assert (data.entity_type, data.sub_type) == ("storage", "virtual_disk")
        assert data.is_active is True
        assert data.attributes["requested_storage"] == "10Gi"
        assert data.attributes["capacity"] == "10Gi"
        assert data.attributes["storage_class"] == "ceph"
        assert data.attributes["access_modes"] == ["ReadWriteOnce"]
        pending = got["PVC: elder/pending"]
        assert pending.is_active is False
        assert pending.attributes["capacity"] is None
        assert pending.attributes["requested_storage"] is None
        assert pending.attributes["access_modes"] == []

    @pytest.mark.asyncio
    async def test_existing_pvc_updated(self, connector: KubernetesConnector) -> None:
        """A known claim is updated in place."""
        connector.elder_client.list_entities.return_value = {
            "items": [
                {
                    "id": 9,
                    "metadata": {
                        "pvc_name": "data",
                        "namespace": "elder",
                        "cluster": CLUSTER,
                    },
                }
            ]
        }
        assert await connector._sync_pvcs(ORG_ID) == (1, 1)
        assert updated_entities(connector.elder_client)[0][0] == 9


class TestFailuresAndSync:
    """Error containment, full sync and health."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("method", "api_call"),
        [
            ("_sync_namespaces", "list_namespace"),
            ("_sync_secrets", "list_secret_for_all_namespaces"),
            ("_sync_pods", "list_pod_for_all_namespaces"),
            ("_sync_pvcs", "list_persistent_volume_claim_for_all_namespaces"),
        ],
    )
    @pytest.mark.parametrize("status", [403, 429, 503])
    async def test_api_errors_are_contained(
        self, connector: KubernetesConnector, method: str, api_call: str, status: int
    ) -> None:
        """RBAC denials, throttling and outages are logged and write nothing."""
        getattr(connector.k8s_core_v1, api_call).side_effect = ApiException(
            status=status
        )
        assert await getattr(connector, method)(ORG_ID) == (0, 0)
        connector.logger.error.assert_called_once()
        connector.elder_client.create_entity.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_full_sync_scoped_to_cluster_org(
        self, connector: KubernetesConnector, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A full sync writes all eight resources under the cluster's organization."""
        monkeypatch.setattr(settings, "create_missing_organizations", True)
        result = await connector.sync()
        assert result.errors == []
        assert result.organizations_created == 1
        assert (result.entities_created, result.entities_updated) == (8, 0)
        org = connector.elder_client.create_organization.call_args.args[0]
        assert org.name == f"Kubernetes: {CLUSTER}"
        entities = created_entities(connector.elder_client)
        assert {e.organization_id for e in entities} == {501}
        assert {e.attributes["cluster"] for e in entities} == {CLUSTER}
        assert result.to_dict()["total_operations"] == 9

    @pytest.mark.asyncio
    async def test_sync_reports_unexpected_failure(
        self, connector: KubernetesConnector
    ) -> None:
        """A non-API failure is captured in the result instead of raising."""
        connector.elder_client.list_organizations.side_effect = RuntimeError("down")
        result = await connector.sync()
        assert result.errors == ["Kubernetes sync failed: down"]
        assert result.has_errors is True

    @pytest.mark.asyncio
    async def test_health_check_states(self, connector: KubernetesConnector) -> None:
        """Health is True when the API answers, False on error or without a client."""
        assert await connector.health_check() is True
        connector.k8s_core_v1.get_api_resources.side_effect = ApiException(status=500)
        assert await connector.health_check() is False
        connector.k8s_core_v1 = None
        assert await connector.health_check() is False
