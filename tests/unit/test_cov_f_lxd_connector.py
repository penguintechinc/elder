"""Behavioural tests for the LXD connector (instances, pools, networks, cluster)."""

import os
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
import requests
from requests.exceptions import ConnectionError as ReqConnectionError
from requests.exceptions import Timeout

import tests.unit.conftest_worker_stubs  # noqa: F401
from apps.worker.config.settings import settings
from apps.worker.connectors import lxd_connector as mod
from apps.worker.connectors.lxd_connector import LXDConnector
from tests.unit._cov_f_helpers import (
    FakeResponse,
    RouteSession,
    assert_not_leaked,
    by_name,
    created_entities,
    fake_secret,
    logged_text,
    make_elder,
    spy_logger,
    updated_entities,
)

LXD_URL = "https://lxd.test.invalid:8443"
ORG_ID = 77


def _meta(payload: Any) -> dict[str, Any]:
    """Wrap a payload in the LXD ``{"metadata": ...}`` envelope."""
    return {"type": "sync", "status_code": 200, "metadata": payload}


def _routes() -> dict[str, Any]:
    """Return a realistic LXD API surface: 2 instances, 1 pool, 1 network, 1 node."""
    return {
        "/1.0": _meta({"api_version": "1.0", "environment": {"server": "lxd"}}),
        "/1.0/instances": _meta(["/1.0/instances/web01", "/1.0/instances/vm01/"]),
        "/1.0/instances/web01": _meta(
            {
                "type": "container",
                "status": "Stopped",
                "architecture": "x86_64",
                "config": {
                    "image.description": "Ubuntu noble amd64",
                    "image.os": "Ubuntu",
                    "image.release": "noble",
                    "limits.memory": "2GiB",
                    "limits.cpu": "2",
                },
                "profiles": ["default"],
                "devices": {
                    "root": {"type": "disk", "source": "/dev/sda", "path": "/"},
                    "eth0": {"type": "nic", "network": "lxdbr0"},
                    "junk": "not-a-dict",
                },
            }
        ),
        "/1.0/instances/web01/state": _meta(
            {
                "status": "Running",
                "network": {
                    "eth0": {
                        "addresses": [
                            {"family": "inet", "address": "10.1.2.3"},
                            {"family": "inet6", "address": "fd42::1"},
                        ]
                    },
                    "lo": "garbage",
                },
            }
        ),
        "/1.0/instances/vm01": _meta({"type": "virtual-machine", "status": "Stopped"}),
        "/1.0/instances/vm01/state": Timeout("state timed out"),
        "/1.0/storage-pools": _meta(["/1.0/storage-pools/default"]),
        "/1.0/storage-pools/default": _meta(
            {"driver": "zfs", "status": "Created", "config": {"size": "30GiB"}}
        ),
        "/1.0/storage-pools/default/resources": _meta(
            {"space": {"used": 1024, "total": 4096}}
        ),
        "/1.0/networks": _meta(["/1.0/networks/lxdbr0"]),
        "/1.0/networks/lxdbr0": _meta(
            {
                "type": "bridge",
                "status": "Created",
                "managed": True,
                "config": {"ipv4.address": "10.1.2.1/24", "ipv6.address": "none"},
            }
        ),
        "/1.0/cluster": _meta({"enabled": True}),
        "/1.0/cluster/members": _meta(["/1.0/cluster/members/node-a"]),
        "/1.0/cluster/members/node-a": _meta(
            {
                "status": "Online",
                "url": "https://10.0.0.5:8443",
                "database": True,
                "roles": ["database-leader"],
                "architecture": "x86_64",
            }
        ),
    }


@pytest.fixture
def connector() -> LXDConnector:
    """Return a connector wired to a routed fake LXD session and Elder double."""
    c = LXDConnector()
    c.lxd_url = LXD_URL
    c.session = RouteSession(LXD_URL, _routes())
    c.elder_client = make_elder()
    spy_logger(c)
    return c


@pytest.fixture(autouse=True)
def _frozen_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    """Freeze the connector's clock so status timestamps are deterministic."""
    monkeypatch.setattr(mod.time, "time", lambda: 1_700_000_000.9)


class TestSession:
    """Session construction and the raw GET helper."""

    def test_client_cert_auth_writes_pem_files_and_cleans_up(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Cert+key PEMs land in a temp dir used as the session cert tuple."""
        cert, key = fake_secret("cert", "pem"), fake_secret("key", "pem")
        monkeypatch.setattr(settings, "lxd_cert", cert)
        monkeypatch.setattr(settings, "lxd_key", key)
        monkeypatch.setattr(settings, "lxd_trust_token", fake_secret("unused"))
        monkeypatch.setattr(settings, "lxd_verify_cert", True)
        c = LXDConnector()
        logger = spy_logger(c)

        session = c._build_session()

        assert session.verify is True
        assert session.cert == (c._cert_file, c._key_file)
        with open(c._cert_file) as fh:
            assert fh.read() == cert
        with open(c._key_file) as fh:
            assert fh.read() == key
        assert "Authorization" not in session.headers
        assert_not_leaked(key, logged_text(logger))
        tempdir = c._cert_tempdir.name
        c._cert_tempdir.cleanup()
        assert not os.path.exists(tempdir)

    def test_trust_token_auth_sets_bearer_and_never_logs_token(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A trust token becomes a Bearer header and stays out of the logs."""
        token = fake_secret("trust", "token")
        monkeypatch.setattr(settings, "lxd_cert", None)
        monkeypatch.setattr(settings, "lxd_key", None)
        monkeypatch.setattr(settings, "lxd_trust_token", token)
        monkeypatch.setattr(settings, "lxd_verify_cert", False)
        c = LXDConnector()
        logger = spy_logger(c)

        session = c._build_session()

        assert session.headers["Authorization"] == f"Bearer {token}"
        assert session.verify is False
        assert session.cert is None
        assert_not_leaked(token, logged_text(logger))

    def test_no_auth_configured_warns(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """With neither cert nor token the session is anonymous and a warning is logged."""
        for name in ("lxd_cert", "lxd_key", "lxd_trust_token"):
            monkeypatch.setattr(settings, name, None)
        c = LXDConnector()
        logger = spy_logger(c)

        session = c._build_session()

        assert "Authorization" not in session.headers
        assert session.cert is None
        logger.warning.assert_called_once()

    def test_lxd_get_unwraps_metadata_and_sets_timeout(
        self, connector: LXDConnector
    ) -> None:
        """The GET helper returns the ``metadata`` field and always passes a timeout."""
        assert connector._lxd_get("/1.0")["api_version"] == "1.0"
        assert connector.session.calls[0] == ("GET /1.0", {"timeout": 30})

    def test_lxd_get_returns_raw_body_without_envelope(
        self, connector: LXDConnector
    ) -> None:
        """A body lacking ``metadata`` is returned unchanged."""
        connector.session.routes["/1.0/raw"] = {"plain": 1}
        assert connector._lxd_get("/1.0/raw") == {"plain": 1}

    @pytest.mark.parametrize("status", [401, 403, 429, 500, 503])
    def test_lxd_get_raises_on_http_error(
        self, connector: LXDConnector, status: int
    ) -> None:
        """Auth failures, throttling and server errors surface as HTTPError."""
        connector.session.routes["/1.0/x"] = FakeResponse({}, status_code=status)
        with pytest.raises(requests.HTTPError):
            connector._lxd_get("/1.0/x")


class TestConnectDisconnect:
    """Connection lifecycle."""

    @pytest.mark.asyncio
    async def test_connect_probes_api_and_connects_elder(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Connect probes ``/1.0`` and then opens the Elder client."""
        monkeypatch.setattr(settings, "lxd_url", LXD_URL)
        c = LXDConnector()
        session = RouteSession(LXD_URL, _routes())
        elder = AsyncMock()
        with (
            patch.object(c, "_build_session", return_value=session),
            patch.object(mod, "ElderAPIClient", return_value=elder),
        ):
            await c.connect()

        assert c.lxd_url == LXD_URL
        assert session.paths() == ["GET /1.0"]
        elder.connect.assert_awaited_once()
        assert c.elder_client is elder

    @pytest.mark.asyncio
    async def test_connect_tolerates_non_dict_server_info(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A malformed (list) server-info body still connects with version 'unknown'."""
        monkeypatch.setattr(settings, "lxd_url", LXD_URL)
        c = LXDConnector()
        logger = spy_logger(c)
        session = RouteSession(LXD_URL, {"/1.0": _meta(["unexpected"])})
        with (
            patch.object(c, "_build_session", return_value=session),
            patch.object(mod, "ElderAPIClient", return_value=AsyncMock()),
        ):
            await c.connect()

        assert "api_version='unknown'" in logged_text(logger)

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "failure",
        [
            ReqConnectionError("connection refused"),
            Timeout("read timed out"),
            FakeResponse({}, status_code=403),
        ],
        ids=["unreachable", "timeout", "forbidden"],
    )
    async def test_connect_failure_propagates_and_skips_elder(
        self, monkeypatch: pytest.MonkeyPatch, failure: Any
    ) -> None:
        """Unreachable/timeout/auth failures re-raise and never touch Elder."""
        monkeypatch.setattr(settings, "lxd_url", LXD_URL)
        c = LXDConnector()
        logger = spy_logger(c)
        session = RouteSession(LXD_URL, {"/1.0": failure})
        with (
            patch.object(c, "_build_session", return_value=session),
            patch.object(mod, "ElderAPIClient") as elder_cls,
            pytest.raises(requests.RequestException),
        ):
            await c.connect()

        elder_cls.assert_not_called()
        logger.error.assert_called_once()

    @pytest.mark.xfail(
        strict=True,
        reason="bug: lxd_connector.py:115 getattr default never applies because "
        "settings.lxd_url exists with default None, so the URL becomes 'None/1.0'",
    )
    @pytest.mark.asyncio
    async def test_connect_defaults_to_localhost_when_url_unset(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """With no lxd_url configured the documented localhost default is used."""
        monkeypatch.setattr(settings, "lxd_url", None)
        c = LXDConnector()
        session = RouteSession("https://localhost:8443", _routes())
        with (
            patch.object(c, "_build_session", return_value=session),
            patch.object(mod, "ElderAPIClient", return_value=AsyncMock()),
        ):
            await c.connect()

        assert c.lxd_url == "https://localhost:8443"

    @pytest.mark.asyncio
    async def test_disconnect_releases_everything(
        self, connector: LXDConnector, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Disconnect closes the session, deletes key material and clears caches."""
        monkeypatch.setattr(settings, "lxd_cert", fake_secret("c"))
        monkeypatch.setattr(settings, "lxd_key", fake_secret("k"))
        connector._build_session()
        tempdir = connector._cert_tempdir.name
        session, elder = connector.session, connector.elder_client
        connector.organization_cache["root:x"] = 1

        await connector.disconnect()

        assert session.closed is True
        assert connector.session is None
        assert not os.path.exists(tempdir)
        assert connector._cert_file is None and connector._key_file is None
        elder.close.assert_awaited_once()
        assert connector.organization_cache == {}

    @pytest.mark.asyncio
    async def test_disconnect_is_safe_when_never_connected(self) -> None:
        """Disconnecting a fresh connector is a no-op that does not raise."""
        c = LXDConnector()
        await c.disconnect()
        assert c.session is None and c.elder_client is None


class TestOrganization:
    """Organization lookup/creation rules."""

    @pytest.mark.asyncio
    async def test_existing_org_is_reused_and_cached(
        self, connector: LXDConnector
    ) -> None:
        """A name+parent match is returned and cached so Elder is queried once."""
        connector.elder_client = make_elder(
            orgs=[
                {"id": 1, "name": "LXD", "parent_id": 9},
                {"id": 2, "name": "LXD", "parent_id": None},
            ]
        )
        assert await connector._get_or_create_organization("LXD", "d") == 2
        assert await connector._get_or_create_organization("LXD", "d") == 2
        connector.elder_client.list_organizations.assert_awaited_once_with(
            per_page=1000
        )
        connector.elder_client.create_organization.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_missing_org_is_created_with_parent(
        self, connector: LXDConnector, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When auto-create is on a missing org is created under the given parent."""
        monkeypatch.setattr(settings, "create_missing_organizations", True)
        org_id = await connector._get_or_create_organization("New", "desc", parent_id=4)
        payload = connector.elder_client.create_organization.call_args.args[0]
        assert org_id == 501
        assert (payload.name, payload.description, payload.parent_id) == (
            "New",
            "desc",
            4,
        )
        assert connector.organization_cache == {"4:New": 501}

    @pytest.mark.asyncio
    async def test_falls_back_to_default_org(
        self, connector: LXDConnector, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """With auto-create off the configured default organization is used."""
        monkeypatch.setattr(settings, "create_missing_organizations", False)
        monkeypatch.setattr(settings, "default_organization_id", 42)
        assert await connector._get_or_create_organization("New", "d") == 42
        connector.elder_client.create_organization.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_raises_when_no_org_and_no_default(
        self, connector: LXDConnector, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """With auto-create off and no default the lookup fails loudly."""
        monkeypatch.setattr(settings, "create_missing_organizations", False)
        monkeypatch.setattr(settings, "default_organization_id", None)
        with pytest.raises(ValueError, match="auto-creation disabled"):
            await connector._get_or_create_organization("New", "d")


class TestInstances:
    """Instance mapping."""

    @pytest.mark.asyncio
    async def test_maps_containers_and_vms(self, connector: LXDConnector) -> None:
        """Containers and VMs map to distinct sub-types with live state merged in."""
        assert await connector._sync_instances(ORG_ID) == (2, 0)
        got = by_name(created_entities(connector.elder_client))

        web = got["LXD: web01"]
        assert (web.entity_type, web.sub_type, web.organization_id) == (
            "compute",
            "lxd_container",
            ORG_ID,
        )
        assert web.is_active is True
        assert web.description == "Ubuntu noble amd64"
        assert web.attributes["status"] == "Running"
        assert web.attributes["ipv4_addresses"] == ["10.1.2.3"]
        assert web.attributes["disk_devices"] == {"root": "/dev/sda"}
        assert web.attributes["config_memory_limit"] == "2GiB"
        assert web.attributes["lxd_url"] == LXD_URL
        assert web.tags == ["lxd", "container", "running"]
        assert web.status_metadata == {"status": "Running", "timestamp": 1_700_000_000}

        vm = got["LXD: vm01"]
        assert vm.sub_type == "lxd_vm"
        assert vm.is_active is False
        assert vm.description == "LXD virtual-machine instance: vm01"
        assert vm.attributes["ipv4_addresses"] == []

    @pytest.mark.asyncio
    async def test_existing_instance_is_updated_only_for_same_host(
        self, connector: LXDConnector
    ) -> None:
        """Only an entity with the same name AND lxd_url is updated; others are created."""
        connector.elder_client = make_elder(
            entities=[
                {
                    "id": 5,
                    "metadata": {
                        "instance_name": "web01",
                        "lxd_url": "https://other:8443",
                    },
                },
                {"id": 6, "metadata": {"instance_name": "web01", "lxd_url": LXD_URL}},
            ]
        )
        assert await connector._sync_instances(ORG_ID) == (1, 1)
        ((entity_id, entity),) = updated_entities(connector.elder_client)
        assert entity_id == 6 and entity.name == "LXD: web01"
        connector.elder_client.list_entities.assert_awaited_with(
            organization_id=ORG_ID, entity_type="compute"
        )

    @pytest.mark.asyncio
    async def test_instance_detail_failure_skips_only_that_instance(
        self, connector: LXDConnector
    ) -> None:
        """A 500 on one instance is logged and the remaining instances still sync."""
        connector.session.routes["/1.0/instances/web01"] = FakeResponse({}, 500)
        assert await connector._sync_instances(ORG_ID) == (1, 0)
        assert [e.name for e in created_entities(connector.elder_client)] == [
            "LXD: vm01"
        ]
        connector.logger.warning.assert_called_once()

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "listing",
        [_meta([]), _meta(None), ReqConnectionError("down"), FakeResponse({}, 429)],
        ids=["empty", "null", "unreachable", "throttled"],
    )
    async def test_instance_listing_empty_or_failing_creates_nothing(
        self, connector: LXDConnector, listing: Any
    ) -> None:
        """Empty/null listings and listing failures yield zero writes without raising."""
        connector.session.routes["/1.0/instances"] = listing
        assert await connector._sync_instances(ORG_ID) == (0, 0)
        connector.elder_client.create_entity.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_non_dict_state_keeps_instance_status(
        self, connector: LXDConnector
    ) -> None:
        """A malformed state body leaves the instance's own status in place."""
        connector.session.routes["/1.0/instances/web01/state"] = _meta(["bad"])
        connector.session.routes["/1.0/instances"] = _meta(["/1.0/instances/web01"])
        await connector._sync_instances(ORG_ID)
        (web,) = created_entities(connector.elder_client)
        assert web.attributes["status"] == "Stopped" and web.is_active is False

    @pytest.mark.asyncio
    async def test_state_without_status_keeps_instance_status(
        self, connector: LXDConnector
    ) -> None:
        """A state body with no status still contributes addresses only."""
        connector.session.routes["/1.0/instances/web01/state"] = _meta(
            {"network": {"eth0": {"addresses": [{"family": "inet", "address": "x"}]}}}
        )
        connector.session.routes["/1.0/instances"] = _meta(["/1.0/instances/web01"])
        await connector._sync_instances(ORG_ID)
        (web,) = created_entities(connector.elder_client)
        assert web.attributes["status"] == "Stopped"
        assert web.attributes["ipv4_addresses"] == ["x"]


class TestStoragePools:
    """Storage pool mapping."""

    @pytest.mark.asyncio
    async def test_pool_mapped_with_capacity(self, connector: LXDConnector) -> None:
        """A pool becomes a storage entity carrying driver and capacity."""
        assert await connector._sync_storage_pools(ORG_ID) == (1, 0)
        (pool,) = created_entities(connector.elder_client)
        assert (pool.name, pool.entity_type, pool.sub_type) == (
            "LXD Storage: default",
            "storage",
            "virtual_disk",
        )
        assert pool.attributes["used_bytes"] == 1024
        assert pool.attributes["total_bytes"] == 4096
        assert pool.attributes["config"] == {"size": "30GiB"}
        assert pool.tags == ["lxd", "storage", "zfs"]
        assert pool.is_active is True and pool.organization_id == ORG_ID

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "resources", [Timeout("slow"), _meta(["bad"])], ids=["timeout", "malformed"]
    )
    async def test_pool_capacity_unavailable_defaults_to_zero(
        self, connector: LXDConnector, resources: Any
    ) -> None:
        """Capacity lookups that fail or are malformed fall back to zero bytes."""
        connector.session.routes["/1.0/storage-pools/default/resources"] = resources
        await connector._sync_storage_pools(ORG_ID)
        (pool,) = created_entities(connector.elder_client)
        assert (pool.attributes["used_bytes"], pool.attributes["total_bytes"]) == (0, 0)

    @pytest.mark.asyncio
    async def test_existing_pool_updated_and_inactive_status(
        self, connector: LXDConnector
    ) -> None:
        """An existing pool is updated; a non-ready status marks it inactive."""
        connector.session.routes["/1.0/storage-pools/default"] = _meta(
            {"status": "Errored"}
        )
        connector.elder_client = make_elder(
            entities=[
                {"id": 1, "metadata": {"pool_name": "other", "lxd_url": LXD_URL}},
                {"id": 8, "metadata": {"pool_name": "default", "lxd_url": LXD_URL}},
            ]
        )
        assert await connector._sync_storage_pools(ORG_ID) == (0, 1)
        ((entity_id, pool),) = updated_entities(connector.elder_client)
        assert entity_id == 8
        assert pool.is_active is False and pool.attributes["driver"] == "unknown"

    @pytest.mark.asyncio
    async def test_pool_detail_and_listing_failures(
        self, connector: LXDConnector
    ) -> None:
        """Detail failure skips the pool; listing failure aborts the pool sync cleanly."""
        connector.session.routes["/1.0/storage-pools/default"] = FakeResponse({}, 503)
        assert await connector._sync_storage_pools(ORG_ID) == (0, 0)
        connector.logger.warning.assert_called_once()

        connector.session.routes["/1.0/storage-pools"] = ReqConnectionError("down")
        assert await connector._sync_storage_pools(ORG_ID) == (0, 0)
        connector.logger.error.assert_called_once()
        connector.elder_client.create_entity.assert_not_awaited()


class TestNetworks:
    """Network mapping."""

    @pytest.mark.asyncio
    async def test_network_mapped(self, connector: LXDConnector) -> None:
        """A bridge becomes a network/subnet entity with its addresses."""
        assert await connector._sync_networks(ORG_ID) == (1, 0)
        (net,) = created_entities(connector.elder_client)
        assert (net.name, net.entity_type, net.sub_type) == (
            "LXD Network: lxdbr0",
            "network",
            "subnet",
        )
        assert net.attributes["ipv4_address"] == "10.1.2.1/24"
        assert net.attributes["managed"] is True
        assert net.tags == ["lxd", "network", "bridge"]
        assert net.is_active is True

    @pytest.mark.asyncio
    async def test_existing_network_updated_with_sparse_payload(
        self, connector: LXDConnector
    ) -> None:
        """A sparse network body uses safe defaults and updates the matching entity."""
        connector.session.routes["/1.0/networks/lxdbr0"] = _meta({})
        connector.elder_client = make_elder(
            entities=[
                {"id": 2, "metadata": {"network_name": "lxdbr0", "lxd_url": "x"}},
                {"id": 3, "metadata": {"network_name": "lxdbr0", "lxd_url": LXD_URL}},
            ]
        )
        assert await connector._sync_networks(ORG_ID) == (0, 1)
        ((entity_id, net),) = updated_entities(connector.elder_client)
        assert entity_id == 3
        assert net.attributes["network_type"] == "unknown"
        assert net.is_active is False

    @pytest.mark.asyncio
    async def test_network_detail_and_listing_failures(
        self, connector: LXDConnector
    ) -> None:
        """Detail failure skips the network; listing failure is logged, not raised."""
        connector.session.routes["/1.0/networks/lxdbr0"] = Timeout("slow")
        assert await connector._sync_networks(ORG_ID) == (0, 0)
        connector.logger.warning.assert_called_once()

        connector.session.routes["/1.0/networks"] = FakeResponse({}, 500)
        assert await connector._sync_networks(ORG_ID) == (0, 0)
        connector.logger.error.assert_called_once()


class TestClusterMembers:
    """Cluster member mapping."""

    @pytest.mark.asyncio
    async def test_member_mapped(self, connector: LXDConnector) -> None:
        """An online member becomes an active server entity."""
        assert await connector._sync_cluster_members(ORG_ID) == (1, 0)
        (node,) = created_entities(connector.elder_client)
        assert (node.name, node.sub_type) == ("LXD Node: node-a", "server")
        assert node.description == "LXD cluster node: node-a"
        assert node.attributes["failure_domain"] == "default"
        assert node.attributes["roles"] == ["database-leader"]
        assert node.attributes["lxd_resource"] == "cluster_member"
        assert node.tags == ["lxd", "cluster", "node", "online"]
        assert node.is_active is True

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "cluster", [_meta({"enabled": False}), _meta(["x"])], ids=["off", "malformed"]
    )
    async def test_non_clustered_server_is_skipped(
        self, connector: LXDConnector, cluster: Any
    ) -> None:
        """Standalone servers never list members and write nothing."""
        connector.session.routes["/1.0/cluster"] = cluster
        assert await connector._sync_cluster_members(ORG_ID) == (0, 0)
        assert "GET /1.0/cluster/members" not in connector.session.paths()

    @pytest.mark.asyncio
    async def test_existing_member_requires_cluster_marker(
        self, connector: LXDConnector
    ) -> None:
        """An instance sharing the node's name is not mistaken for the cluster member."""
        connector.elder_client = make_elder(
            entities=[
                {"id": 1, "metadata": {"member_name": "node-a", "lxd_url": LXD_URL}},
                {
                    "id": 2,
                    "metadata": {
                        "member_name": "node-a",
                        "lxd_resource": "cluster_member",
                        "lxd_url": LXD_URL,
                    },
                },
            ]
        )
        assert await connector._sync_cluster_members(ORG_ID) == (0, 1)
        assert updated_entities(connector.elder_client)[0][0] == 2

    @pytest.mark.asyncio
    async def test_member_detail_and_listing_failures(
        self, connector: LXDConnector
    ) -> None:
        """Detail failure skips the member; a cluster probe failure is logged."""
        connector.session.routes["/1.0/cluster/members/node-a"] = FakeResponse({}, 500)
        assert await connector._sync_cluster_members(ORG_ID) == (0, 0)
        connector.logger.warning.assert_called_once()

        connector.session.routes["/1.0/cluster"] = ReqConnectionError("down")
        assert await connector._sync_cluster_members(ORG_ID) == (0, 0)
        connector.logger.error.assert_called_once()


class TestSyncAndHealth:
    """End-to-end sync and health check."""

    @pytest.mark.asyncio
    async def test_full_sync_counts_and_scopes_to_lxd_org(
        self, connector: LXDConnector, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A full sync creates one org and five entities, all inside that org."""
        monkeypatch.setattr(settings, "create_missing_organizations", True)
        result = await connector.sync()

        assert result.errors == []
        assert result.organizations_created == 1
        assert (result.entities_created, result.entities_updated) == (5, 0)
        org = connector.elder_client.create_organization.call_args.args[0]
        assert org.name == f"LXD: {LXD_URL}"
        entities = created_entities(connector.elder_client)
        assert {e.organization_id for e in entities} == {501}
        assert {e.attributes["provider"] for e in entities} == {"lxd"}

    @pytest.mark.asyncio
    async def test_sync_reports_error_instead_of_raising(
        self, connector: LXDConnector
    ) -> None:
        """An Elder failure is captured in the result's errors, not raised."""
        connector.elder_client.list_organizations.side_effect = RuntimeError("boom")
        result = await connector.sync()
        assert result.has_errors is True
        assert result.errors == ["LXD sync failed: boom"]
        assert result.total_operations == 0

    @pytest.mark.asyncio
    async def test_health_check_states(self, connector: LXDConnector) -> None:
        """Health is True when reachable, False when erroring or never connected."""
        assert await connector.health_check() is True
        connector.session.routes["/1.0"] = FakeResponse({}, 500)
        assert await connector.health_check() is False
        connector.session = None
        assert await connector.health_check() is False
