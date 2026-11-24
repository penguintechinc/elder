"""Behavioural tests for the vCenter connector (datacenters, clusters, hosts, VMs, storage)."""

import ssl
import sys
from types import ModuleType, SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import tests.unit.conftest_worker_stubs  # noqa: F401
from apps.worker.config.settings import settings
from apps.worker.connectors import vcenter_connector as mod
from apps.worker.connectors.vcenter_connector import VCenterConnector
from tests.unit._cov_f_helpers import (
    assert_not_leaked,
    by_name,
    created_entities,
    fake_secret,
    logged_text,
    make_elder,
    spy_logger,
    updated_entities,
)

VC_HOST = "vcenter.test.invalid"
ROOT_ORG = 10
GIB = 1024**3


class _Managed:
    """Base for fake pyVmomi managed objects (attributes set from kwargs)."""

    def __init__(self, mo_id: str, name: str, **attrs: Any):
        """Store the managed-object id, name and any extra attributes."""
        self._moId = mo_id
        self.name = name
        for key, value in attrs.items():
            setattr(self, key, value)


class _Datacenter(_Managed):
    """Fake ``vim.Datacenter``."""


class _Folder(_Managed):
    """Fake ``vim.Folder`` (sits between a datacenter and a cluster)."""


class _Cluster(_Managed):
    """Fake ``vim.ClusterComputeResource``."""


class _Host(_Managed):
    """Fake ``vim.HostSystem``."""


class _VM(_Managed):
    """Fake ``vim.VirtualMachine``."""


class _Datastore(_Managed):
    """Fake ``vim.Datastore``."""


class _Network(_Managed):
    """Fake ``vim.Network``."""


class _DVPortgroup(_Network):
    """Fake ``vim.dvs.DistributedVirtualPortgroup``."""


VIM = SimpleNamespace(
    Datacenter=_Datacenter,
    ClusterComputeResource=_Cluster,
    HostSystem=_Host,
    VirtualMachine=_VM,
    Datastore=_Datastore,
    Network=_Network,
    dvs=SimpleNamespace(DistributedVirtualPortgroup=_DVPortgroup),
)


class _Content:
    """Fake vCenter ``content`` whose container views filter the inventory by type."""

    def __init__(self, inventory: list[Any]):
        """Hold the inventory and set up the view manager."""
        self.inventory = inventory
        self.rootFolder = object()
        self.about = SimpleNamespace(version="8.0.2")
        self.destroyed = 0
        self.fail_types: set[type] = set()
        self.viewManager = SimpleNamespace(CreateContainerView=self._view)

    def _view(self, root: Any, obj_types: list[type], recursive: bool) -> Any:
        """Return a container view of the requested managed-object types."""
        assert root is self.rootFolder and recursive is True
        if self.fail_types.intersection(obj_types):
            raise RuntimeError("vim.fault.NotAuthenticated")
        view = [o for o in self.inventory if isinstance(o, tuple(obj_types))]
        return SimpleNamespace(view=view, Destroy=self._destroy)

    def _destroy(self) -> None:
        """Count destroyed views so leaks are detectable."""
        self.destroyed += 1


def _host_summary(state: str = "connected", with_config: bool = True) -> Any:
    """Build a host summary as pyVmomi would return it."""
    product = SimpleNamespace(version="8.0.1", build="2200")
    return SimpleNamespace(
        config=SimpleNamespace(product=product) if with_config else None,
        runtime=SimpleNamespace(
            connectionState=state, powerState="poweredOn", inMaintenanceMode=False
        ),
    )


def _inventory() -> list[Any]:
    """Return a small but complete vCenter inventory."""
    dc = _Datacenter("dc-1", "DAL2", parent=None)
    folder = _Folder("f-1", "host", parent=dc)
    cluster = _Cluster("c-1", "Prod", parent=folder)
    orphan_cluster = _Cluster("c-2", "Lab", parent=None)
    hardware = SimpleNamespace(
        cpuInfo=SimpleNamespace(hz=2_400_000_000, numCpuCores=16),
        memorySize=64 * GIB,
    )
    esx1 = _Host(
        "h-1", "esx1", parent=cluster, hardware=hardware, summary=_host_summary()
    )
    esx2 = _Host(
        "h-2",
        "esx2",
        parent=folder,
        hardware=None,
        summary=_host_summary("disconnected", with_config=False),
    )
    vm_config = SimpleNamespace(
        template=False,
        annotation="Web tier",
        uuid="4210-aaaa",
        guestFullName="Ubuntu Linux (64-bit)",
        hardware=SimpleNamespace(numCPU=4, memoryMB=8192),
    )
    guest = SimpleNamespace(toolsStatus="toolsOk", ipAddress="10.9.8.7", hostName="web")
    web = _VM(
        "vm-1",
        "web",
        config=vm_config,
        runtime=SimpleNamespace(host=esx1, powerState="poweredOn"),
        summary=SimpleNamespace(guest=guest),
    )
    template = _VM(
        "vm-2",
        "golden",
        config=SimpleNamespace(template=True),
        runtime=None,
        summary=SimpleNamespace(guest=None),
    )
    bare = _VM(
        "vm-3", "bare", config=None, runtime=None, summary=SimpleNamespace(guest=None)
    )
    standalone = _VM(
        "vm-4",
        "standalone",
        config=None,
        runtime=SimpleNamespace(host=esx2, powerState="poweredOff"),
        summary=SimpleNamespace(guest=None),
    )
    ds = _Datastore(
        "ds-1",
        "vsan",
        summary=SimpleNamespace(
            type="vsan",
            capacity=100 * GIB,
            freeSpace=25 * GIB,
            url="ds:///vmfs/volumes/vsan/",
            accessible=True,
            maintenanceMode="normal",
        ),
    )
    net = _Network("n-1", "VM Network", summary=SimpleNamespace(accessible=False))
    dvpg = _DVPortgroup("n-2", "dv-prod", summary=SimpleNamespace())
    return [
        dc,
        folder,
        cluster,
        orphan_cluster,
        esx1,
        esx2,
        web,
        template,
        bare,
        standalone,
        ds,
        net,
        dvpg,
    ]


@pytest.fixture(autouse=True)
def _fake_sdk(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    """Install fake ``pyVmomi``/``pyVim`` modules (the SDK is the external boundary)."""
    pyvmomi = ModuleType("pyVmomi")
    pyvmomi.vim = VIM
    pyvim = ModuleType("pyVim")
    connect = ModuleType("pyVim.connect")
    connect.SmartConnect = MagicMock(name="SmartConnect")
    connect.Disconnect = MagicMock(name="Disconnect")
    pyvim.connect = connect
    monkeypatch.setitem(sys.modules, "pyVmomi", pyvmomi)
    monkeypatch.setitem(sys.modules, "pyVim", pyvim)
    monkeypatch.setitem(sys.modules, "pyVim.connect", connect)
    monkeypatch.setattr(settings, "vcenter_host", VC_HOST)
    monkeypatch.setattr(settings, "create_missing_organizations", True)
    return SimpleNamespace(connect=connect)


@pytest.fixture
def connector() -> VCenterConnector:
    """Return a connector attached to the fake inventory and an Elder double."""
    c = VCenterConnector()
    c.content = _Content(_inventory())
    c.si = MagicMock(name="ServiceInstance")
    c.elder_client = make_elder()
    spy_logger(c)
    return c


class TestConnect:
    """Connection lifecycle."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("verify", [True, False])
    async def test_connect_passes_credentials_and_tls_mode(
        self, monkeypatch: pytest.MonkeyPatch, _fake_sdk: SimpleNamespace, verify: bool
    ) -> None:
        """Credentials reach the SDK only; TLS verification follows the setting."""
        password = fake_secret("vc", "password")
        monkeypatch.setattr(settings, "vcenter_username", "svc-elder")
        monkeypatch.setattr(settings, "vcenter_password", password)
        monkeypatch.setattr(settings, "vcenter_port", 443)
        monkeypatch.setattr(settings, "vcenter_verify_ssl", verify)
        content = _Content([])
        _fake_sdk.connect.SmartConnect.return_value.RetrieveContent.return_value = (
            content
        )
        c = VCenterConnector()
        logger = spy_logger(c)
        elder = AsyncMock()
        with patch.object(mod, "ElderAPIClient", return_value=elder):
            await c.connect()

        kwargs = _fake_sdk.connect.SmartConnect.call_args.kwargs
        assert (kwargs["host"], kwargs["user"], kwargs["pwd"], kwargs["port"]) == (
            VC_HOST,
            "svc-elder",
            password,
            443,
        )
        if verify:
            assert kwargs["sslContext"] is None
        else:
            assert kwargs["sslContext"].verify_mode == ssl.CERT_NONE
            assert kwargs["sslContext"].check_hostname is False
        assert c.content is content
        elder.connect.assert_awaited_once()
        assert "8.0.2" in logged_text(logger)
        assert_not_leaked(password, logged_text(logger))

    @pytest.mark.asyncio
    async def test_connect_auth_failure_raises_without_leaking_password(
        self, monkeypatch: pytest.MonkeyPatch, _fake_sdk: SimpleNamespace
    ) -> None:
        """A login failure re-raises, skips Elder and keeps the password out of logs."""
        password = fake_secret("vc", "wrong")
        monkeypatch.setattr(settings, "vcenter_password", password)
        _fake_sdk.connect.SmartConnect.side_effect = RuntimeError("InvalidLogin")
        c = VCenterConnector()
        logger = spy_logger(c)
        with (
            patch.object(mod, "ElderAPIClient") as elder_cls,
            pytest.raises(RuntimeError, match="InvalidLogin"),
        ):
            await c.connect()
        elder_cls.assert_not_called()
        logger.error.assert_called_once()
        assert_not_leaked(password, logged_text(logger))

    @pytest.mark.asyncio
    async def test_disconnect_logs_out_and_clears_state(
        self, connector: VCenterConnector, _fake_sdk: SimpleNamespace
    ) -> None:
        """Disconnect ends the vCenter session, closes Elder and clears caches."""
        si, elder = connector.si, connector.elder_client
        connector.organization_cache["root:x"] = 1
        await connector.disconnect()
        _fake_sdk.connect.Disconnect.assert_called_once_with(si)
        assert connector.si is None and connector.content is None
        elder.close.assert_awaited_once()
        assert connector.organization_cache == {}

    @pytest.mark.asyncio
    async def test_disconnect_when_never_connected(
        self, _fake_sdk: SimpleNamespace
    ) -> None:
        """Disconnecting a fresh connector never calls the SDK."""
        await VCenterConnector().disconnect()
        _fake_sdk.connect.Disconnect.assert_not_called()


class TestInventoryView:
    """Container-view helper."""

    def test_get_all_objs_filters_and_destroys_view(
        self, connector: VCenterConnector
    ) -> None:
        """Only requested types are returned and the server-side view is destroyed."""
        hosts = connector._get_all_objs([_Host])
        assert [h.name for h in hosts] == ["esx1", "esx2"]
        assert connector.content.destroyed == 1


class TestOrganizationsFromTopology:
    """Datacenters and clusters become organizations."""

    @pytest.mark.asyncio
    async def test_datacenters_become_child_orgs(
        self, connector: VCenterConnector
    ) -> None:
        """Each datacenter is created as an organization under the vCenter root."""
        created, updated, dc_map = await connector._sync_datacenters(ROOT_ORG)
        assert (created, updated, dc_map) == (1, 0, {"DAL2": 501})
        org = connector.elder_client.create_organization.call_args.args[0]
        assert (org.name, org.parent_id) == ("DC: DAL2", ROOT_ORG)

    @pytest.mark.asyncio
    async def test_clusters_nest_under_their_datacenter(
        self, connector: VCenterConnector
    ) -> None:
        """A cluster walks up to its datacenter; one without falls back to the root."""
        ids = iter([601, 602])
        connector.elder_client.create_organization.side_effect = lambda _o: {
            "id": next(ids)
        }
        created, _, cluster_map = await connector._sync_clusters(ROOT_ORG, {"DAL2": 55})
        assert created == 2 and cluster_map == {"Prod": 601, "Lab": 602}
        parents = {
            c.args[0].name: c.args[0].parent_id
            for c in connector.elder_client.create_organization.call_args_list
        }
        assert parents == {"Cluster: Prod": 55, "Cluster: Lab": ROOT_ORG}

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("method", "args", "failing"),
        [
            ("_sync_datacenters", (ROOT_ORG,), _Datacenter),
            ("_sync_clusters", (ROOT_ORG, {}), _Cluster),
        ],
    )
    async def test_topology_failure_returns_empty_map(
        self, connector: VCenterConnector, method: str, args: tuple, failing: type
    ) -> None:
        """An SDK fault is logged and yields no organizations rather than raising."""
        connector.content.fail_types = {failing}
        assert await getattr(connector, method)(*args) == (0, 0, {})
        connector.logger.error.assert_called_once()


class TestHosts:
    """ESXi host mapping."""

    @pytest.mark.asyncio
    async def test_hosts_mapped_into_cluster_or_root(
        self, connector: VCenterConnector
    ) -> None:
        """Clustered hosts land in the cluster org; standalone hosts in the root org."""
        assert await connector._sync_hosts(ROOT_ORG, {"Prod": 60}) == (2, 0)
        got = by_name(created_entities(connector.elder_client))
        esx1 = got["esx1"]
        assert (esx1.organization_id, esx1.entity_type) == (60, "compute")
        assert esx1.attributes["vcenter_host_id"] == "h-1"
        assert esx1.attributes["version"] == "8.0.1"
        assert esx1.attributes["cpu_cores"] == 16
        assert esx1.attributes["memory_gb"] == 64.0
        assert esx1.attributes["vcenter_host"] == VC_HOST
        assert esx1.is_active is True
        esx2 = got["esx2"]
        assert esx2.organization_id == ROOT_ORG and esx2.is_active is False
        assert esx2.attributes["version"] is None
        assert esx2.attributes["memory_gb"] is None

    @pytest.mark.xfail(
        strict=True,
        reason="bug: vcenter_connector.py:243 stores cpuInfo.hz (clock speed) under "
        "the 'cpu_model' attribute instead of the CPU model string",
    )
    @pytest.mark.asyncio
    async def test_cpu_model_is_not_the_clock_speed(
        self, connector: VCenterConnector
    ) -> None:
        """The cpu_model attribute must describe the model, not the frequency in Hz."""
        await connector._sync_hosts(ROOT_ORG, {"Prod": 60})
        esx1 = by_name(created_entities(connector.elder_client))["esx1"]
        assert esx1.attributes["cpu_model"] != 2_400_000_000

    @pytest.mark.asyncio
    async def test_existing_host_updated(self, connector: VCenterConnector) -> None:
        """A host already known by managed-object id is updated, not duplicated."""
        connector.elder_client.list_entities.return_value = {
            "items": [{"id": 1}, {"id": 2, "metadata": {"vcenter_host_id": "h-2"}}]
        }
        assert await connector._sync_hosts(ROOT_ORG, {}) == (1, 1)
        ((entity_id, entity),) = updated_entities(connector.elder_client)
        assert (entity_id, entity.name) == (2, "esx2")


class TestVMs:
    """Virtual machine mapping."""

    @pytest.mark.asyncio
    async def test_vms_mapped_and_templates_skipped(
        self, connector: VCenterConnector
    ) -> None:
        """Templates are skipped; VMs carry guest details and follow their cluster."""
        assert await connector._sync_vms(ROOT_ORG, {"Prod": 60}) == (3, 0)
        got = by_name(created_entities(connector.elder_client))
        assert "golden" not in got
        web = got["web"]
        assert web.organization_id == 60
        assert web.description == "Web tier"
        assert web.attributes["memory_gb"] == 8.0
        assert web.attributes["ip_address"] == "10.9.8.7"
        assert web.attributes["tools_status"] == "toolsOk"
        assert web.is_active is True
        bare = got["bare"]
        assert bare.organization_id == ROOT_ORG and bare.is_active is False
        assert bare.description == "VM: bare"
        assert bare.attributes["power_state"] is None
        assert bare.attributes["ip_address"] is None
        standalone = got["standalone"]
        assert standalone.organization_id == ROOT_ORG
        assert standalone.attributes["power_state"] == "poweredOff"

    @pytest.mark.asyncio
    async def test_existing_vm_updated(self, connector: VCenterConnector) -> None:
        """A VM already known by managed-object id is updated in place."""
        connector.elder_client.list_entities.return_value = {
            "items": [{"id": 5, "metadata": {"vcenter_vm_id": "vm-1"}}]
        }
        assert await connector._sync_vms(ROOT_ORG, {}) == (2, 1)
        assert updated_entities(connector.elder_client)[0][0] == 5


class TestDatastoresAndNetworks:
    """Datastore and network mapping."""

    @pytest.mark.asyncio
    async def test_datastore_capacity_maths(self, connector: VCenterConnector) -> None:
        """Capacity, free and used space are converted to GiB correctly."""
        assert await connector._sync_datastores(ROOT_ORG) == (1, 0)
        (ds,) = created_entities(connector.elder_client)
        assert (ds.name, ds.entity_type, ds.organization_id) == (
            "vsan",
            "storage",
            ROOT_ORG,
        )
        assert ds.attributes["capacity_gb"] == 100.0
        assert ds.attributes["free_space_gb"] == 25.0
        assert ds.attributes["used_gb"] == 75.0
        assert ds.is_active is True

    @pytest.mark.asyncio
    async def test_existing_datastore_updated(
        self, connector: VCenterConnector
    ) -> None:
        """A known datastore is updated rather than recreated."""
        connector.elder_client.list_entities.return_value = {
            "items": [{"id": 4, "metadata": {"vcenter_datastore_id": "ds-1"}}]
        }
        assert await connector._sync_datastores(ROOT_ORG) == (0, 1)
        connector.elder_client.create_entity.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_networks_typed_by_class(self, connector: VCenterConnector) -> None:
        """Distributed and standard port groups are distinguished by SDK type."""
        connector.elder_client.list_entities.return_value = {
            "items": [{"id": 6, "metadata": {"vcenter_network_id": "n-2"}}]
        }
        assert await connector._sync_networks(ROOT_ORG) == (1, 1)
        (std,) = created_entities(connector.elder_client)
        assert std.attributes["network_type"] == "standard_portgroup"
        assert std.attributes["accessible"] is False
        ((entity_id, dv),) = updated_entities(connector.elder_client)
        assert entity_id == 6
        assert dv.attributes["network_type"] == "distributed_portgroup"
        assert dv.attributes["accessible"] is True
        assert dv.tags == ["vcenter", "network", "distributed_portgroup"]

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("method", "args", "failing"),
        [
            ("_sync_hosts", (ROOT_ORG, {}), _Host),
            ("_sync_vms", (ROOT_ORG, {}), _VM),
            ("_sync_datastores", (ROOT_ORG,), _Datastore),
            ("_sync_networks", (ROOT_ORG,), _Network),
        ],
    )
    async def test_sdk_fault_is_contained(
        self, connector: VCenterConnector, method: str, args: tuple, failing: type
    ) -> None:
        """A session fault while listing a type is logged and yields no writes."""
        connector.content.fail_types = {failing}
        assert await getattr(connector, method)(*args) == (0, 0)
        connector.logger.error.assert_called_once()
        connector.elder_client.create_entity.assert_not_awaited()


class TestSyncAndHealth:
    """End-to-end sync and health check."""

    @pytest.mark.asyncio
    async def test_full_sync_counts(self, connector: VCenterConnector) -> None:
        """A full sync reports organizations and entities for the whole inventory."""
        ids = iter(range(700, 800))
        connector.elder_client.create_organization.side_effect = lambda _o: {
            "id": next(ids)
        }
        result = await connector.sync()
        assert result.errors == []
        assert result.organizations_created == 4
        assert (result.entities_created, result.entities_updated) == (8, 0)
        root = connector.elder_client.create_organization.call_args_list[0].args[0]
        assert root.name == f"vCenter: {VC_HOST}"
        orgs = {e.organization_id for e in created_entities(connector.elder_client)}
        assert orgs <= {700, 701, 702, 703}

    @pytest.mark.asyncio
    async def test_sync_reports_org_failure(self, connector: VCenterConnector) -> None:
        """An Elder failure is captured in the result's errors."""
        connector.elder_client.list_organizations.side_effect = RuntimeError("down")
        result = await connector.sync()
        assert result.errors == ["vCenter sync failed: down"]

    @pytest.mark.asyncio
    async def test_health_check_states(self, connector: VCenterConnector) -> None:
        """Health is True with a live session, False on fault or when disconnected."""
        assert await connector.health_check() is True
        connector.si.CurrentTime.side_effect = RuntimeError("session expired")
        assert await connector.health_check() is False
        connector.si = None
        assert await connector.health_check() is False
