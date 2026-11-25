"""Coverage tests for the small discovery providers (base, executor, GCP,
Azure, Vultr, Kubernetes).

Cloud SDKs are replaced by fakes at the module boundary; the DAL is a
MagicMock because the executor only receives a session-like object.
"""

import tests.unit.conftest_worker_stubs  # noqa: F401  isort: skip

import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace as NS
from unittest.mock import MagicMock, patch

import pytest

from apps.worker.discovery import (
    azure_discovery,
    base,
    executor,
    gcp_discovery,
    k8s_discovery,
    vultr_discovery,
)

# ---------------------------------------------------------------- base


class _Concrete(base.BaseDiscoveryProvider):
    """Minimal concrete provider used to exercise base-class helpers."""

    def discover_all(self):
        """Return an empty result set."""
        return {}

    def discover_compute(self):
        """Return no compute resources."""
        return []

    def discover_storage(self):
        """Return no storage resources."""
        return []

    def discover_network(self):
        """Return no network resources."""
        return []

    def discover_databases(self):
        """Return no database resources."""
        return []

    def discover_serverless(self):
        """Return no serverless resources."""
        return []

    def test_connection(self):
        """Report a healthy connection."""
        return True

    def get_supported_services(self):
        """List a single supported service."""
        return ["svc"]


class TestBaseProvider:
    """Base-class defaults, formatting, tag normalisation and validation."""

    def test_init_defaults(self):
        """Missing provider_type and name fall back to defaults."""
        p = _Concrete({})
        assert p.provider_type == "unknown"
        assert p.name == "unknown Discovery"

    def test_init_explicit(self):
        """Explicit provider_type and name are honoured."""
        p = _Concrete({"provider_type": "x", "name": "Mine"})
        assert (p.provider_type, p.name) == ("x", "Mine")

    def test_format_resource_defaults(self):
        """Optional args default to empty containers/None."""
        p = _Concrete({"provider_type": "x"})
        r = p.format_resource("id", "t", "n", {"a": 1})
        assert r["tags"] == {} and r["relationships"] == []
        assert r["region"] is None and r["external_id"] is None
        assert r["provider"] == "x"
        datetime.fromisoformat(r["discovered_at"])

    def test_format_resource_full(self):
        """All optional args are passed through."""
        p = _Concrete({"provider_type": "x"})
        r = p.format_resource(
            "id",
            "t",
            "n",
            {},
            region="r",
            tags={"k": "v"},
            external_id="e",
            relationships=[{"a": 1}],
        )
        assert r["region"] == "r" and r["tags"] == {"k": "v"}
        assert r["external_id"] == "e" and r["relationships"] == [{"a": 1}]

    @pytest.mark.parametrize(
        "tags,expected",
        [
            (None, {}),
            ([], {}),
            ([{"Key": "a", "Value": "b"}, {}], {"a": "b", "": ""}),
            ({"a": "b"}, {"a": "b"}),
            ("string", {}),
            (5, {}),
        ],
    )
    def test_normalize_tags(self, tags, expected):
        """Tags from AWS lists, dicts and junk normalise to a flat dict."""
        assert _Concrete({})._normalize_tags(tags) == expected

    @pytest.mark.parametrize(
        "cfg,expected",
        [
            ({"region": "r1"}, "r1"),
            ({"zone": "z1"}, "z1"),
            ({"region": "", "zone": "z1"}, "z1"),
            ({}, None),
        ],
    )
    def test_extract_region(self, cfg, expected):
        """Region comes from config region, then zone."""
        assert _Concrete(cfg)._extract_region({}) == expected

    def test_get_provider_info(self):
        """Provider info lists name, type and services."""
        info = _Concrete({"provider_type": "x", "name": "N"}).get_provider_info()
        assert info == {
            "name": "N",
            "provider_type": "x",
            "supported_services": ["svc"],
        }

    def test_validate_config(self):
        """Empty config and unknown provider_type are rejected."""
        with pytest.raises(ValueError, match="Configuration is required"):
            _Concrete({}).validate_config()
        with pytest.raises(ValueError, match="provider_type"):
            _Concrete({"name": "n"}).validate_config()
        assert _Concrete({"provider_type": "x"}).validate_config() is True

    def test_abstract_cannot_instantiate(self):
        """The ABC cannot be instantiated directly."""
        with pytest.raises(TypeError):
            base.BaseDiscoveryProvider({})  # type: ignore[abstract]

    def test_abstract_bodies_are_noops(self):
        """Abstract method bodies (docstring only) return None when invoked."""
        p = _Concrete({})
        bp = base.BaseDiscoveryProvider
        for name in (
            "discover_all",
            "discover_compute",
            "discover_storage",
            "discover_network",
            "discover_databases",
            "discover_serverless",
            "test_connection",
            "get_supported_services",
        ):
            assert getattr(bp, name)(p) is None


# ------------------------------------------------------------ executor


def _make_executor():
    """Build a DiscoveryExecutor with a mocked DAL and DiscoveryService."""
    db = MagicMock()
    with patch.object(executor, "DiscoveryService") as svc_cls:
        ex = executor.DiscoveryExecutor(db)
    return ex, db, svc_cls.return_value


class TestExecutor:
    """Job selection and execution paths of DiscoveryExecutor."""

    def test_init_read_defaults_to_write(self):
        """db_read defaults to db_write when not supplied."""
        ex, db, _ = _make_executor()
        assert ex.db_read is db
        with patch.object(executor, "DiscoveryService"):
            other = MagicMock()
            ex2 = executor.DiscoveryExecutor(db, other)
        assert ex2.db_read is other and ex2.db_write is db

    def test_get_pending_jobs_selects_due(self):
        """Never-run and overdue jobs are selected; fresh/no-interval skipped."""
        ex, db, _ = _make_executor()
        now = datetime.now(UTC)
        never = NS(id=1, last_run_at=None, schedule_interval=60)
        overdue_naive = NS(
            id=2,
            last_run_at=(now - timedelta(seconds=500)).replace(tzinfo=None),
            schedule_interval=60,
        )
        overdue_aware = NS(
            id=3, last_run_at=now - timedelta(seconds=500), schedule_interval=60
        )
        fresh = NS(id=4, last_run_at=now, schedule_interval=3600)
        no_interval = NS(id=5, last_run_at=now - timedelta(days=9), schedule_interval=0)
        db.return_value.select.return_value = [
            never,
            overdue_naive,
            overdue_aware,
            fresh,
            no_interval,
        ]
        pending = ex.get_pending_jobs()
        assert [j.id for j in pending] == [1, 2, 3]

    def test_get_pending_jobs_db_error(self):
        """A DB failure yields an empty list rather than raising."""
        ex, db, _ = _make_executor()
        db.side_effect = RuntimeError("db down")
        assert ex.get_pending_jobs() == []

    def test_execute_job_success(self):
        """Successful runs return the service result."""
        ex, _, svc = _make_executor()
        svc.run_discovery.return_value = {"success": True, "resources_discovered": 3}
        assert ex.execute_job(7) == {"success": True, "resources_discovered": 3}
        svc.run_discovery.assert_called_once_with(7)

    def test_execute_job_failure_result(self):
        """A failed (non-exception) result is still returned."""
        ex, _, svc = _make_executor()
        svc.run_discovery.return_value = {"success": False, "error": "nope"}
        assert ex.execute_job(7)["error"] == "nope"

    def test_execute_job_exception(self):
        """Exceptions are swallowed and reported as None."""
        ex, _, svc = _make_executor()
        svc.run_discovery.side_effect = RuntimeError("boom")
        assert ex.execute_job(7) is None

    def test_run_pending_none(self):
        """No pending jobs means zero executed."""
        ex, _, _ = _make_executor()
        with patch.object(ex, "get_pending_jobs", return_value=[]):
            assert ex.run_pending() == 0

    def test_run_pending_runs_each(self):
        """Each pending job is executed and counted."""
        ex, _, _ = _make_executor()
        jobs = [NS(id=1), NS(id=2)]
        with (
            patch.object(ex, "get_pending_jobs", return_value=jobs),
            patch.object(ex, "execute_job") as run,
        ):
            assert ex.run_pending() == 2
        assert [c.args[0] for c in run.call_args_list] == [1, 2]


# ----------------------------------------------------------------- GCP


def _gcp_client(monkeypatch, config=None, **mods):
    """Create a GCPDiscoveryClient with fake SDK modules patched in."""
    compute = mods.get("compute", MagicMock())
    monkeypatch.setattr(gcp_discovery, "compute_v1", compute)
    monkeypatch.setattr(gcp_discovery, "storage", mods.get("storage", MagicMock()))
    monkeypatch.setattr(gcp_discovery, "service_account", mods.get("sa", MagicMock()))
    monkeypatch.setattr(
        gcp_discovery,
        "google_auth_default",
        mods.get("default", MagicMock(return_value=("creds", "proj"))),
    )
    cfg = {"provider_type": "gcp", "project_id": "proj-1"}
    cfg.update(config or {})
    return gcp_discovery.GCPDiscoveryClient(cfg)


class TestGCP:
    """GCP discovery with a faked google SDK."""

    def test_import_error_without_sdk(self, monkeypatch):
        """Missing SDK raises ImportError."""
        monkeypatch.setattr(gcp_discovery, "compute_v1", None)
        with pytest.raises(ImportError):
            gcp_discovery.GCPDiscoveryClient({"project_id": "p"})

    def test_requires_project(self, monkeypatch):
        """project_id is mandatory."""
        monkeypatch.setattr(gcp_discovery, "compute_v1", MagicMock())
        with pytest.raises(ValueError, match="project_id"):
            gcp_discovery.GCPDiscoveryClient({})

    def test_default_credentials(self, monkeypatch):
        """Without credentials_json, ADC is used."""
        c = _gcp_client(monkeypatch)
        assert c.credentials == "creds"

    @pytest.mark.parametrize("as_str", [True, False])
    def test_service_account_credentials(self, monkeypatch, as_str):
        """credentials_json may be a JSON string or a dict."""
        sa = MagicMock()
        info = {"type": "service_account"}
        c = _gcp_client(
            monkeypatch,
            {"credentials_json": json.dumps(info) if as_str else info},
            sa=sa,
        )
        sa.Credentials.from_service_account_info.assert_called_once_with(info)
        assert c.credentials is sa.Credentials.from_service_account_info.return_value

    def test_connection_ok_and_fail(self, monkeypatch):
        """test_connection reflects whether the aggregated list call works."""
        compute = MagicMock()
        c = _gcp_client(monkeypatch, compute=compute)
        assert c.test_connection() is True
        compute.InstancesClient.return_value.aggregated_list.side_effect = Exception()
        assert c.test_connection() is False

    def test_supported_services(self, monkeypatch):
        """Supported services list includes compute and storage."""
        c = _gcp_client(monkeypatch)
        assert "compute" in c.get_supported_services()

    def test_compute(self, monkeypatch):
        """Instances across zones are formatted; empty zones skipped."""
        compute = MagicMock()
        inst = NS(
            id=11,
            name="vm1",
            machine_type="zones/z/machineTypes/e2-small",
            status="RUNNING",
            zone="projects/p/zones/us-central1-a",
            labels={"env": "x"},
        )
        inst_nolabels = NS(
            id=12,
            name="vm2",
            machine_type="e2",
            status="STOPPED",
            zone="a/b",
        )
        compute.InstancesClient.return_value.aggregated_list.return_value = [
            ("zone-a", NS(instances=[inst, inst_nolabels])),
            ("zone-b", NS(instances=[])),
        ]
        c = _gcp_client(monkeypatch, compute=compute)
        out = c.discover_compute()
        assert [r["name"] for r in out] == ["vm1", "vm2"]
        assert out[0]["metadata"]["machine_type"] == "e2-small"
        assert out[0]["region"] == "us-central1-a"
        assert out[0]["tags"] == {"env": "x"}
        assert out[1]["tags"] == {}

    def test_compute_error(self, monkeypatch):
        """SDK errors are swallowed to an empty list."""
        compute = MagicMock()
        compute.InstancesClient.side_effect = Exception("x")
        c = _gcp_client(monkeypatch, compute=compute)
        assert c.discover_compute() == []

    def test_storage(self, monkeypatch):
        """Buckets are formatted with and without created time / labels."""
        st = MagicMock()
        created = datetime(2024, 1, 2, tzinfo=UTC)
        b1 = NS(
            name="b1",
            location="US",
            storage_class="STANDARD",
            time_created=created,
            labels={"a": "b"},
        )
        b2 = NS(
            name="b2",
            location="EU",
            storage_class="NEARLINE",
            time_created=None,
            labels=None,
        )
        st.Client.return_value.list_buckets.return_value = [b1, b2]
        c = _gcp_client(monkeypatch, storage=st)
        out = c.discover_storage()
        assert out[0]["metadata"]["created"] == created.isoformat()
        assert out[1]["metadata"]["created"] is None and out[1]["tags"] == {}

    def test_storage_error(self, monkeypatch):
        """Storage errors yield an empty list."""
        st = MagicMock()
        st.Client.side_effect = Exception("x")
        assert _gcp_client(monkeypatch, storage=st).discover_storage() == []

    def test_network(self, monkeypatch):
        """VPC networks are formatted as global resources."""
        compute = MagicMock()
        compute.NetworksClient.return_value.list.return_value = [
            NS(id=5, name="default", auto_create_subnetworks=True)
        ]
        out = _gcp_client(monkeypatch, compute=compute).discover_network()
        assert out[0]["resource_id"] == "5" and out[0]["region"] == "global"

    def test_network_error(self, monkeypatch):
        """Network errors yield an empty list."""
        compute = MagicMock()
        compute.NetworksClient.side_effect = Exception("x")
        assert _gcp_client(monkeypatch, compute=compute).discover_network() == []

    def test_db_and_serverless_empty(self, monkeypatch):
        """Unimplemented categories return empty lists."""
        c = _gcp_client(monkeypatch)
        assert c.discover_databases() == [] and c.discover_serverless() == []

    def test_discover_all(self, monkeypatch):
        """discover_all sums the category counts."""
        c = _gcp_client(monkeypatch)
        with (
            patch.object(c, "discover_compute", return_value=[1, 2]),
            patch.object(c, "discover_storage", return_value=[3]),
            patch.object(c, "discover_network", return_value=[]),
        ):
            out = c.discover_all()
        assert out["resources_count"] == 3
        assert out["duration_seconds"] >= 0


# --------------------------------------------------------------- Azure


def _azure_client(monkeypatch, config=None):
    """Create an AzureDiscoveryClient with faked SDK classes."""
    mocks = {
        "ClientSecretCredential": MagicMock(),
        "DefaultAzureCredential": MagicMock(),
        "ComputeManagementClient": MagicMock(),
        "StorageManagementClient": MagicMock(),
        "NetworkManagementClient": MagicMock(),
    }
    for k, v in mocks.items():
        monkeypatch.setattr(azure_discovery, k, v)
    cfg = {"provider_type": "azure", "subscription_id": "sub-1"}
    cfg.update(config or {})
    return azure_discovery.AzureDiscoveryClient(cfg), mocks


class TestAzure:
    """Azure discovery with faked management clients."""

    def test_import_error(self, monkeypatch):
        """Missing SDK raises ImportError."""
        monkeypatch.setattr(azure_discovery, "ComputeManagementClient", None)
        with pytest.raises(ImportError):
            azure_discovery.AzureDiscoveryClient({"subscription_id": "s"})

    def test_requires_subscription(self, monkeypatch):
        """subscription_id is mandatory."""
        monkeypatch.setattr(azure_discovery, "ComputeManagementClient", MagicMock())
        with pytest.raises(ValueError, match="subscription_id"):
            azure_discovery.AzureDiscoveryClient({})

    def test_default_credential(self, monkeypatch):
        """Without a full service principal, DefaultAzureCredential is used."""
        c, m = _azure_client(monkeypatch, {"tenant_id": "t"})
        assert c.credential is m["DefaultAzureCredential"].return_value
        m["ClientSecretCredential"].assert_not_called()

    def test_service_principal_credential(self, monkeypatch):
        """tenant, client id and secret select ClientSecretCredential."""
        secret = "-".join(("cove", "x1"))
        c, m = _azure_client(
            monkeypatch,
            {"tenant_id": "t", "client_id": "c", "client_secret": secret},
        )
        assert c.credential is m["ClientSecretCredential"].return_value

    def test_connection(self, monkeypatch):
        """test_connection reflects the VM listing result."""
        c, _ = _azure_client(monkeypatch)
        c.compute_client.virtual_machines.list_all.return_value = []
        assert c.test_connection() is True
        c.compute_client.virtual_machines.list_all.side_effect = Exception()
        assert c.test_connection() is False

    def test_supported(self, monkeypatch):
        """Supported services are IaaS only."""
        c, _ = _azure_client(monkeypatch)
        assert c.get_supported_services() == ["compute", "storage", "network"]

    def test_compute_full_and_sparse(self, monkeypatch):
        """VMs with and without profiles are formatted."""
        c, _ = _azure_client(monkeypatch)
        full = NS(
            id="/vm/1",
            name="vm1",
            hardware_profile=NS(vm_size="D2"),
            storage_profile=NS(os_disk=NS(os_type="Linux")),
            location="eastus",
            provisioning_state="Succeeded",
            tags={"a": "b"},
        )
        sparse = NS(
            id="/vm/2",
            name="vm2",
            hardware_profile=None,
            storage_profile=NS(os_disk=None),
            location="westus",
            provisioning_state="Failed",
            tags=None,
        )
        no_sp = NS(
            id="/vm/3",
            name="vm3",
            hardware_profile=None,
            storage_profile=None,
            location="x",
            provisioning_state=None,
            tags=None,
        )
        c.compute_client.virtual_machines.list_all.return_value = [full, sparse, no_sp]
        out = c.discover_compute()
        assert out[0]["metadata"]["vm_size"] == "D2"
        assert out[0]["metadata"]["os_type"] == "Linux"
        assert out[1]["metadata"]["vm_size"] is None
        assert out[1]["metadata"]["os_type"] is None
        assert out[2]["metadata"]["os_type"] is None and out[2]["tags"] == {}

    def test_compute_error(self, monkeypatch):
        """Errors yield an empty list."""
        c, _ = _azure_client(monkeypatch)
        c.compute_client.virtual_machines.list_all.side_effect = Exception()
        assert c.discover_compute() == []

    def test_storage(self, monkeypatch):
        """Storage accounts with and without enum fields are formatted."""
        c, _ = _azure_client(monkeypatch)
        full = NS(
            id="/sa/1",
            name="sa1",
            kind=NS(value="StorageV2"),
            sku=NS(name=NS(value="Standard_LRS")),
            location="eastus",
            provisioning_state=NS(value="Succeeded"),
            tags={"k": "v"},
        )
        bare = NS(
            id="/sa/2",
            name="sa2",
            kind=None,
            sku=None,
            location="x",
            provisioning_state=None,
            tags=None,
        )
        c.storage_client.storage_accounts.list.return_value = [full, bare]
        out = c.discover_storage()
        assert out[0]["metadata"]["kind"] == "StorageV2"
        assert out[0]["metadata"]["sku_name"] == "Standard_LRS"
        assert out[1]["metadata"]["kind"] is None
        assert out[1]["metadata"]["provisioning_state"] is None

    def test_storage_error(self, monkeypatch):
        """Errors yield an empty list."""
        c, _ = _azure_client(monkeypatch)
        c.storage_client.storage_accounts.list.side_effect = Exception()
        assert c.discover_storage() == []

    def test_network(self, monkeypatch):
        """VNets with and without address space/subnets are formatted."""
        c, _ = _azure_client(monkeypatch)
        full = NS(
            id="/vn/1",
            name="vn1",
            address_space=NS(address_prefixes=["10.0.0.0/16"]),
            location="eastus",
            provisioning_state="Succeeded",
            subnets=[1, 2],
            tags={"a": "b"},
        )
        bare = NS(
            id="/vn/2",
            name="vn2",
            address_space=None,
            location="x",
            provisioning_state=None,
            subnets=None,
            tags=None,
        )
        c.network_client.virtual_networks.list_all.return_value = [full, bare]
        out = c.discover_network()
        assert out[0]["metadata"]["subnets_count"] == 2
        assert out[1]["metadata"]["address_space"] == []
        assert out[1]["metadata"]["subnets_count"] == 0

    def test_network_error(self, monkeypatch):
        """Errors yield an empty list."""
        c, _ = _azure_client(monkeypatch)
        c.network_client.virtual_networks.list_all.side_effect = Exception()
        assert c.discover_network() == []

    def test_unimplemented(self, monkeypatch):
        """PaaS categories are empty."""
        c, _ = _azure_client(monkeypatch)
        assert c.discover_databases() == [] and c.discover_serverless() == []

    def test_discover_all(self, monkeypatch):
        """discover_all aggregates IaaS counts."""
        c, _ = _azure_client(monkeypatch)
        c.compute_client.virtual_machines.list_all.return_value = []
        c.storage_client.storage_accounts.list.return_value = []
        c.network_client.virtual_networks.list_all.return_value = []
        out = c.discover_all()
        assert out["resources_count"] == 0 and out["database"] == []


# --------------------------------------------------------------- Vultr


class _VultrConcrete(vultr_discovery.VultrDiscoveryClient):
    """Vultr client plus the abstract method the source class omits."""

    def get_supported_services(self):
        """List the supported Vultr services."""
        return ["compute"]


class TestVultr:
    """Vultr discovery against a faked httpx boundary."""

    @pytest.mark.xfail(
        strict=True,
        reason="bug: vultr_discovery.py:24 VultrDiscoveryClient lacks "
        "get_supported_services so it is abstract and cannot be instantiated",
    )
    def test_source_class_instantiable(self):
        """The shipped client should be instantiable (currently abstract)."""
        vultr_discovery.VultrDiscoveryClient({})

    def _client(self, monkeypatch, **cfg):
        """Build a client with a deterministic api key."""
        monkeypatch.delenv("VULTR_API_KEY", raising=False)
        return _VultrConcrete(cfg)

    def test_api_key_from_config_and_env(self, monkeypatch):
        """api_key comes from config first, then the environment."""
        key = "-".join(("cove", "k1"))
        assert self._client(monkeypatch, api_key=key).api_key == key
        monkeypatch.setenv("VULTR_API_KEY", key)
        assert _VultrConcrete({}).api_key == key

    def test_validate_config(self, monkeypatch):
        """validate_config is true only with an api key."""
        assert self._client(monkeypatch).validate_config() is False
        assert self._client(monkeypatch, api_key="k").validate_config() is True

    def test_get_httpx_unavailable(self, monkeypatch):
        """Without httpx, _get returns an empty dict."""
        monkeypatch.setattr(vultr_discovery, "_HTTPX_AVAILABLE", False)
        assert self._client(monkeypatch)._get("/x") == {}

    def test_get_success_and_error(self, monkeypatch):
        """_get returns JSON on success and {} on any failure."""
        c = self._client(monkeypatch, api_key="k")
        httpx_mod = MagicMock()
        monkeypatch.setattr(vultr_discovery, "httpx", httpx_mod, raising=False)
        monkeypatch.setattr(vultr_discovery, "_HTTPX_AVAILABLE", True)
        http = httpx_mod.Client.return_value.__enter__.return_value
        http.get.return_value.json.return_value = {"a": 1}
        assert c._get("/p") == {"a": 1}
        assert http.get.call_args.args[0].endswith("/p")
        http.get.return_value.raise_for_status.side_effect = RuntimeError("401")
        assert c._get("/p") == {}

    def test_test_connection(self, monkeypatch):
        """test_connection requires an account object in the response."""
        c = self._client(monkeypatch)
        with patch.object(c, "_get", return_value={"account": {"n": 1}}):
            assert c.test_connection() is True
        with patch.object(c, "_get", return_value={}):
            assert c.test_connection() is False

    def test_discover_methods(self, monkeypatch):
        """Each discover_* maps API payloads and falls back to id for name."""
        c = self._client(monkeypatch)
        data = {
            "/instances": {"instances": [{"id": "i1", "label": "web"}, {"id": "i2"}]},
            "/bare-metals": {"bare_metals": [{"id": "b1"}]},
            "/blocks": {"blocks": [{"id": "k1", "label": "disk"}]},
            "/object-storage": {"object_storages": [{"id": "o1"}]},
            "/vpcs": {"vpcs": [{"id": "v1", "description": "net"}, {"id": "v2"}]},
            "/reserved-ips": {"reserved_ips": [{"id": "r1"}]},
            "/databases": {"databases": [{"id": "d1", "label": "db"}]},
        }
        with patch.object(c, "_get", side_effect=lambda p: data.get(p, {})):
            compute = c.discover_compute()
            storage = c.discover_storage()
            network = c.discover_network()
            dbs = c.discover_databases()
            assert c.discover_serverless() == []
            full = c.discover_all()
        assert [r["name"] for r in compute] == ["web", "i2", "b1"]
        assert {r["type"] for r in compute} == {"vultr_instance", "vultr_bare_metal"}
        assert [r["type"] for r in storage] == [
            "vultr_block_storage",
            "vultr_object_storage",
        ]
        assert [r["name"] for r in network] == ["net", "v2", "r1"]
        assert dbs[0]["type"] == "vultr_managed_database"
        assert full["resources_count"] == 3 + 2 + 3 + 1
        assert full["provider"] == "vultr"

    def test_discover_empty(self, monkeypatch):
        """Empty API responses produce empty lists."""
        c = self._client(monkeypatch)
        with patch.object(c, "_get", return_value={}):
            assert c.discover_all()["resources_count"] == 0


# ----------------------------------------------------------------- K8s


@pytest.fixture
def kube(monkeypatch):
    """Patch the kubernetes SDK handles and return (module-config, client)."""
    cfg_mod = MagicMock()
    cl_mod = MagicMock()
    monkeypatch.setattr(k8s_discovery, "config", cfg_mod)
    monkeypatch.setattr(k8s_discovery, "client", cl_mod)
    return cfg_mod, cl_mod


def _k8s(kube, **cfg):
    """Instantiate the Kubernetes discovery client."""
    return k8s_discovery.KubernetesDiscoveryClient({"provider_type": "k8s", **cfg})


def _meta(name="n", uid="u", ns="ns", labels=None, annotations=None):
    """Build a fake object metadata namespace."""
    return NS(name=name, uid=uid, namespace=ns, labels=labels, annotations=annotations)


class TestK8sInit:
    """Construction and static parsing helpers."""

    def test_import_error(self, monkeypatch):
        """Missing SDK raises ImportError."""
        monkeypatch.setattr(k8s_discovery, "client", None)
        with pytest.raises(ImportError):
            k8s_discovery.KubernetesDiscoveryClient({})

    def test_in_cluster(self, kube):
        """in_cluster loads the in-cluster configuration."""
        c = _k8s(kube, in_cluster=True)
        kube[0].load_incluster_config.assert_called_once()
        assert c.namespace == "default"

    def test_kubeconfig(self, kube):
        """Otherwise kubeconfig path and context are forwarded."""
        _k8s(kube, kubeconfig_path="/p", context="ctx", namespace="x")
        kube[0].load_kube_config.assert_called_once_with(
            config_file="/p", context="ctx"
        )

    @pytest.mark.parametrize(
        "raw,expected",
        [("", 0.0), (None, 0.0), ("500m", 0.5), ("2", 2.0), (1, 1.0)],
    )
    def test_parse_cpu(self, raw, expected):
        """CPU strings parse to cores."""
        assert k8s_discovery.KubernetesDiscoveryClient._parse_cpu(raw) == expected

    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("", 0),
            (None, 0),
            ("1Ki", 1024),
            ("512Mi", 512 * 1024**2),
            ("1Gi", 1024**3),
            ("1Ti", 1024**4),
            ("2k", 2000),
            ("3M", 3 * 1000**2),
            ("4G", 4 * 1000**3),
            ("1024", 1024),
        ],
    )
    def test_parse_memory(self, raw, expected):
        """Memory strings parse to bytes."""
        assert k8s_discovery.KubernetesDiscoveryClient._parse_memory(raw) == expected

    def test_connection_and_services(self, kube):
        """test_connection reflects namespace listing; services list is static."""
        c = _k8s(kube)
        assert c.test_connection() is True
        c.core_v1.list_namespace.side_effect = Exception()
        assert c.test_connection() is False
        assert "pods" in c.get_supported_services()
        assert c.discover_databases() == [] and c.discover_serverless() == []


def _node(sparse=False):
    """Build a fake node."""
    if sparse:
        return NS(
            metadata=_meta("n2", "nu2"),
            spec=None,
            status=NS(capacity=None, node_info=None, conditions=None),
        )
    return NS(
        metadata=_meta("n1", "nu1", labels={"r": "w"}),
        spec=NS(taints=[NS(key="k", value="v", effect="NoSchedule")]),
        status=NS(
            capacity={"cpu": "4", "memory": "8Gi"},
            node_info=NS(
                kubelet_version="v1",
                os_image="os",
                architecture="amd64",
                kernel_version="6",
                container_runtime_version="crt",
            ),
            conditions=[NS(type="Ready")],
        ),
    )


def _pod(kind="full"):
    """Build a fake pod: 'full', 'noresources', or 'bare'."""
    if kind == "bare":
        return NS(metadata=_meta("p3", "pu3"), spec=None, status=None)
    res = (
        NS(
            requests={"cpu": "500m", "memory": "256Mi"},
            limits={"cpu": "1", "memory": "1Gi"},
        )
        if kind == "full"
        else None
    )
    c1 = NS(name="c1", image="img:1", resources=res)
    c2 = NS(
        name="c2",
        image="img:2",
        resources=NS(requests=None, limits=None) if kind == "full" else None,
    )
    return NS(
        metadata=_meta("p", "pu", labels={"a": "b"}),
        spec=NS(node_name="n1", containers=[c1, c2]),
        status=NS(phase="Running", pod_ip="10.0.0.1"),
    )


class TestK8sCompute:
    """Node, pod and deployment discovery."""

    def test_compute_full(self, kube):
        """Nodes, pods and deployments are formatted, summing resources."""
        c = _k8s(kube)
        c.core_v1.list_node.return_value = NS(items=[_node(), _node(sparse=True)])
        c.core_v1.list_pod_for_all_namespaces.return_value = NS(
            items=[_pod("full"), _pod("noresources"), _pod("bare")]
        )
        dep = NS(
            metadata=_meta("d", "du", labels={"x": "y"}),
            spec=NS(
                replicas=2,
                selector=NS(match_labels={"app": "d"}),
                template=NS(spec=NS(containers=[NS(image="i:1")])),
            ),
            status=NS(available_replicas=2, ready_replicas=1),
        )
        dep_bare = NS(
            metadata=_meta("d2", "du2"),
            spec=NS(replicas=1, selector=NS(match_labels=None), template=None),
            status=None,
        )
        dep_nospec = NS(metadata=_meta("d3", "du3"), spec=None, status=None)
        c.apps_v1.list_deployment_for_all_namespaces.return_value = NS(
            items=[dep, dep_bare, dep_nospec]
        )
        out = c.discover_compute()
        types = [r["resource_type"] for r in out]
        assert types.count("k8s_node") == 2
        assert types.count("k8s_pod") == 3
        assert types.count("k8s_deployment") == 3
        n1 = out[0]["metadata"]
        assert n1["taints"] == [{"key": "k", "value": "v", "effect": "NoSchedule"}]
        assert n1["conditions"] == ["Ready"]
        n2 = out[1]["metadata"]
        assert (
            n2["capacity_cpu"] is None and n2["taints"] == [] and n2["conditions"] == []
        )
        p1 = out[2]["metadata"]
        assert p1["resource_requests"] == {"cpu": 0.5, "memory": 256 * 1024**2}
        assert p1["resource_limits"] == {"cpu": 1.0, "memory": 1024**3}
        p3 = out[4]["metadata"]
        assert p3["containers_count"] == 0 and p3["resource_requests"] == {
            "cpu": 0,
            "memory": 0,
        }
        d = out[5]["metadata"]
        assert d["images"] == ["i:1"] and d["selector"] == {"app": "d"}
        assert (
            out[6]["metadata"]["images"] == [] and out[6]["metadata"]["selector"] == {}
        )
        assert out[7]["metadata"]["replicas"] is None

    def test_pod_without_containers(self, kube):
        """A pod spec with an empty container list counts zero containers."""
        c = _k8s(kube)
        c.core_v1.list_node.side_effect = Exception()
        pod = NS(
            metadata=_meta(),
            spec=NS(node_name=None, containers=[]),
            status=NS(phase="Pending", pod_ip=None),
        )
        c.core_v1.list_pod_for_all_namespaces.return_value = NS(items=[pod])
        c.apps_v1.list_deployment_for_all_namespaces.side_effect = Exception()
        out = c.discover_compute()
        assert len(out) == 1
        assert out[0]["metadata"]["containers"] == []
        assert out[0]["metadata"]["resource_limits"] == {"cpu": 0, "memory": 0}

    def test_pod_error_swallowed(self, kube):
        """Pod and deployment API errors are swallowed."""
        c = _k8s(kube)
        c.core_v1.list_node.return_value = NS(items=[])
        c.core_v1.list_pod_for_all_namespaces.side_effect = Exception()
        c.apps_v1.list_deployment_for_all_namespaces.side_effect = Exception()
        assert c.discover_compute() == []


class TestK8sStorageNetwork:
    """PV/PVC, service, ingress and CNI discovery."""

    def test_storage(self, kube):
        """PVs and PVCs are formatted including sparse variants."""
        c = _k8s(kube)
        pv = NS(
            metadata=_meta("pv", "pvu", labels={"a": "b"}),
            spec=NS(
                capacity={"storage": "10Gi"},
                access_modes=["RWO"],
                storage_class_name="fast",
            ),
            status=NS(phase="Bound"),
        )
        pv_bare = NS(metadata=_meta("pv2", "pvu2"), spec=None, status=None)
        pv_nocap = NS(
            metadata=_meta("pv3", "pvu3"),
            spec=NS(capacity=None, access_modes=[], storage_class_name=None),
            status=NS(phase="Available"),
        )
        c.core_v1.list_persistent_volume.return_value = NS(
            items=[pv, pv_bare, pv_nocap]
        )
        pvc = NS(
            metadata=_meta("pvc", "pvcu", labels={"c": "d"}),
            spec=NS(volume_name="pv", storage_class_name="fast", access_modes=["RWO"]),
            status=NS(capacity={"storage": "10Gi"}, phase="Bound"),
        )
        pvc_bare = NS(metadata=_meta("pvc2", "pvcu2"), spec=None, status=None)
        pvc_nocap = NS(
            metadata=_meta("pvc3", "pvcu3"),
            spec=NS(volume_name=None, storage_class_name=None, access_modes=[]),
            status=NS(capacity=None, phase="Pending"),
        )
        c.core_v1.list_persistent_volume_claim_for_all_namespaces.return_value = NS(
            items=[pvc, pvc_bare, pvc_nocap]
        )
        out = c.discover_storage()
        assert [r["resource_type"] for r in out] == ["k8s_persistent_volume"] * 3 + [
            "k8s_pvc"
        ] * 3
        assert out[0]["metadata"]["capacity"] == "10Gi"
        assert out[1]["metadata"]["access_modes"] == []
        assert out[2]["metadata"]["capacity"] is None
        assert out[3]["metadata"]["capacity"] == "10Gi"
        assert out[4]["metadata"]["phase"] is None
        assert out[5]["metadata"]["capacity"] is None

    def test_storage_errors(self, kube):
        """PV and PVC errors are swallowed."""
        c = _k8s(kube)
        c.core_v1.list_persistent_volume.side_effect = Exception()
        c.core_v1.list_persistent_volume_claim_for_all_namespaces.side_effect = (
            Exception()
        )
        assert c.discover_storage() == []

    def test_network(self, kube):
        """Services are formatted and ingress/CNI are appended."""
        c = _k8s(kube)
        svc = NS(
            metadata=_meta("s", "su", labels={"a": "b"}),
            spec=NS(
                type="ClusterIP",
                cluster_ip="10.0.0.1",
                ports=[NS(port=80, protocol="TCP")],
            ),
        )
        svc_bare = NS(metadata=_meta("s2", "su2"), spec=None)
        svc_noports = NS(
            metadata=_meta("s3", "su3"),
            spec=NS(type="ExternalName", cluster_ip=None, ports=None),
        )
        c.core_v1.list_service_for_all_namespaces.return_value = NS(
            items=[svc, svc_bare, svc_noports]
        )
        c.networking_v1.list_ingress_for_all_namespaces.side_effect = Exception()
        c.core_v1.list_namespaced_pod.return_value = NS(
            items=[NS(metadata=_meta("cilium-abc"), spec=None)]
        )
        out = c.discover_network()
        assert [r["resource_type"] for r in out] == ["k8s_service"] * 3 + ["k8s_cni"]
        assert out[0]["metadata"]["ports"] == [{"port": 80, "protocol": "TCP"}]
        assert out[1]["metadata"]["type"] is None
        assert out[2]["metadata"]["ports"] == []

    def test_network_service_error(self, kube):
        """Service listing errors are swallowed."""
        c = _k8s(kube)
        c.core_v1.list_service_for_all_namespaces.side_effect = Exception()
        c.networking_v1.list_ingress_for_all_namespaces.side_effect = Exception()
        c.core_v1.list_namespaced_pod.side_effect = Exception()
        assert c.discover_network() == []

    def test_ingress(self, kube):
        """Ingress rules, TLS and backend dedupe are handled."""
        c = _k8s(kube)
        be = lambda name, port: NS(  # noqa: E731
            service=NS(name=name, port=port) if name else None
        )
        paths = [
            NS(
                path="/a",
                path_type="Prefix",
                backend=be("svc-a", NS(number=80, name=None)),
            ),
            NS(
                path=None,
                path_type="Exact",
                backend=be("svc-a", NS(number=None, name="http")),
            ),
            NS(path="/c", path_type="Exact", backend=be("svc-c", None)),
            NS(path="/d", path_type="Exact", backend=be(None, None)),
            NS(path="/e", path_type="Exact", backend=None),
        ]
        ing = NS(
            metadata=_meta("i", "iu", labels={"a": "b"}),
            spec=NS(
                ingress_class_name="nginx",
                tls=[NS(hosts=["a.example.com"]), NS(hosts=None)],
                rules=[
                    NS(host="a.example.com", http=NS(paths=paths)),
                    NS(host=None, http=None),
                    NS(host="b.example.com", http=NS(paths=[])),
                ],
            ),
        )
        ing_bare = NS(metadata=_meta("i2", "iu2"), spec=None)
        ing_empty = NS(
            metadata=_meta("i3", "iu3"),
            spec=NS(ingress_class_name=None, tls=None, rules=None),
        )
        c.networking_v1.list_ingress_for_all_namespaces.return_value = NS(
            items=[ing, ing_bare, ing_empty]
        )
        out = c.discover_ingress()
        m = out[0]["metadata"]
        assert m["tls_enabled"] is True and m["tls_hosts"] == ["a.example.com"]
        assert m["backend_services"] == ["svc-a", "svc-c"]
        assert m["paths"][1]["path"] == "/" and m["paths"][1]["port"] == "http"
        assert m["paths"][2]["port"] is None
        assert out[1]["metadata"]["ingress_class"] is None
        assert out[2]["metadata"]["tls_enabled"] is False

    def test_ingress_error(self, kube):
        """Ingress errors are swallowed."""
        c = _k8s(kube)
        c.networking_v1.list_ingress_for_all_namespaces.side_effect = Exception()
        assert c.discover_ingress() == []

    @pytest.mark.parametrize("cni", ["calico", "flannel", "cilium", "weave", "canal"])
    def test_cni_types(self, kube, cni):
        """Each known CNI is detected from kube-system pod names."""
        c = _k8s(kube)
        pod = NS(
            metadata=_meta(f"{cni}-node-x"),
            spec=NS(containers=[NS(image="reg/x:v1.2"), NS(image="noversion")]),
        )
        c.core_v1.list_namespaced_pod.return_value = NS(
            items=[NS(metadata=_meta("coredns"), spec=None), pod]
        )
        out = c.discover_cni()
        assert out[0]["metadata"] == {"cni_type": cni, "version": "v1.2"}

    def test_cni_without_spec_and_none(self, kube):
        """A CNI pod without a spec has no version; no match gives nothing."""
        c = _k8s(kube)
        c.core_v1.list_namespaced_pod.return_value = NS(
            items=[NS(metadata=_meta("calico-x"), spec=None)]
        )
        assert c.discover_cni()[0]["metadata"]["version"] is None
        c.core_v1.list_namespaced_pod.return_value = NS(
            items=[NS(metadata=_meta("coredns"), spec=None)]
        )
        assert c.discover_cni() == []
        c.core_v1.list_namespaced_pod.side_effect = Exception()
        assert c.discover_cni() == []


class TestK8sSecurityAndAll:
    """Secrets, certificates, service accounts and discover_all."""

    def test_secrets(self, kube):
        """Only key names, never values, are recorded."""
        c = _k8s(kube)
        s1 = NS(
            metadata=_meta("s", "su", labels={"a": "b"}, annotations={"x": "y"}),
            type="Opaque",
            data={"k1": "v", "k2": "v"},
        )
        s2 = NS(metadata=_meta("s2", "su2"), type="Opaque", data=None)
        c.core_v1.list_secret_for_all_namespaces.return_value = NS(items=[s1, s2])
        out = c.discover_secrets()
        assert out[0]["metadata"]["keys"] == ["k1", "k2"]
        assert out[0]["metadata"]["annotations"] == {"x": "y"}
        assert (
            out[1]["metadata"]["keys"] == [] and out[1]["metadata"]["annotations"] == {}
        )
        assert "v" not in json.dumps(out[0]["metadata"])

    def test_secrets_error(self, kube):
        """Secret listing errors are swallowed."""
        c = _k8s(kube)
        c.core_v1.list_secret_for_all_namespaces.side_effect = Exception()
        assert c.discover_secrets() == []

    def test_certs(self, kube):
        """cert-manager certificates map spec and status."""
        c = _k8s(kube)
        c.custom_objects.list_cluster_custom_object.return_value = {
            "items": [
                {
                    "metadata": {
                        "uid": "u1",
                        "name": "c1",
                        "namespace": "ns",
                        "labels": {"a": "b"},
                    },
                    "spec": {
                        "commonName": "cn",
                        "dnsNames": ["a.example.com"],
                        "issuerRef": {"name": "i"},
                        "secretName": "sec",
                    },
                    "status": {
                        "notAfter": "2030",
                        "notBefore": "2020",
                        "conditions": [
                            {"type": "Ready", "status": "True", "reason": "ok"}
                        ],
                    },
                },
                {"metadata": {"name": "c2"}},
            ]
        }
        out = c.discover_cert_manager_certs()
        assert out[0]["metadata"]["conditions"][0]["type"] == "Ready"
        assert out[1]["resource_id"] == "c2" and out[1]["metadata"]["dns_names"] == []

    def test_certs_error(self, kube):
        """Missing CRD (API error) yields empty list."""
        c = _k8s(kube)
        c.custom_objects.list_cluster_custom_object.side_effect = Exception()
        assert c.discover_cert_manager_certs() == []

    def test_service_accounts(self, kube):
        """Service accounts with and without secrets are formatted."""
        c = _k8s(kube)
        sa1 = NS(
            metadata=_meta("sa", "sau", labels={"a": "b"}),
            automount_service_account_token=True,
            secrets=[NS(name="tok")],
        )
        sa2 = NS(metadata=_meta("sa2", "sau2"), secrets=None)  # no automount attr
        c.core_v1.list_service_account_for_all_namespaces.return_value = NS(
            items=[sa1, sa2]
        )
        out = c._discover_service_accounts()
        assert out[0]["metadata"]["secrets"] == ["tok"]
        assert out[1]["metadata"]["automount_token"] is None
        assert out[1]["metadata"]["secrets"] == []

    def test_service_accounts_error(self, kube):
        """Errors are swallowed."""
        c = _k8s(kube)
        c.core_v1.list_service_account_for_all_namespaces.side_effect = Exception()
        assert c._discover_service_accounts() == []

    def test_discover_all(self, kube):
        """discover_all aggregates category counts."""
        c = _k8s(kube)
        with (
            patch.object(c, "discover_compute", return_value=[1]),
            patch.object(c, "discover_storage", return_value=[1, 2]),
            patch.object(c, "discover_network", return_value=[]),
            patch.object(c, "_discover_service_accounts", return_value=[1]),
            patch.object(c, "discover_secrets", return_value=[]),
            patch.object(c, "discover_cert_manager_certs", return_value=[1]),
        ):
            out = c.discover_all()
        assert out["resources_count"] == 5 and out["serverless"] == []
