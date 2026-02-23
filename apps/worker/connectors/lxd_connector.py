"""LXD connector for syncing LXD container/VM hypervisor resources to Elder."""

# flake8: noqa: E501


import os
import tempfile
import time
from typing import Any, Dict, Optional

import requests
from requests.exceptions import ConnectionError, RequestException

from apps.worker.config.settings import settings
from apps.worker.connectors.base import BaseConnector, SyncResult
from apps.worker.utils.elder_client import ElderAPIClient, Entity, Organization


class LXDConnector(BaseConnector):
    """Connector for LXD container/VM hypervisor resources.

    Syncs instances (containers and VMs), storage pools, networks,
    and cluster members from an LXD REST API endpoint.
    """

    def __init__(self):
        """Initialize LXD connector."""
        super().__init__("lxd")
        self.elder_client: Optional[ElderAPIClient] = None
        self.organization_cache: Dict[str, int] = {}
        self.lxd_url: Optional[str] = None
        self.session: Optional[requests.Session] = None
        self._cert_tempdir: Optional[tempfile.TemporaryDirectory] = None
        self._cert_file: Optional[str] = None
        self._key_file: Optional[str] = None

    def _get_setting(self, name: str, default: Any = None) -> Any:
        """
        Retrieve an LXD setting from the settings object (extra fields).

        Args:
            name: Setting name (e.g., 'lxd_url')
            default: Default value if not set

        Returns:
            Setting value or default
        """
        return getattr(settings, name, default)

    def _build_session(self) -> requests.Session:
        """
        Build a requests.Session configured for LXD API access.

        Supports two auth modes:
          - Client certificate + key (lxd_cert / lxd_key PEM strings)
          - Trust token via Bearer header (lxd_trust_token)

        Returns:
            Configured requests.Session
        """
        lxd_cert_pem: Optional[str] = self._get_setting("lxd_cert")
        lxd_key_pem: Optional[str] = self._get_setting("lxd_key")
        lxd_trust_token: Optional[str] = self._get_setting("lxd_trust_token")
        lxd_verify_cert: bool = self._get_setting("lxd_verify_cert", False)

        session = requests.Session()
        session.verify = lxd_verify_cert

        if lxd_cert_pem and lxd_key_pem:
            # Write cert/key PEM strings to temp files for requests cert tuple
            self._cert_tempdir = tempfile.TemporaryDirectory(prefix="lxd_connector_")
            self._cert_file = os.path.join(self._cert_tempdir.name, "client.crt")
            self._key_file = os.path.join(self._cert_tempdir.name, "client.key")

            with open(self._cert_file, "w") as f:
                f.write(lxd_cert_pem)
            with open(self._key_file, "w") as f:
                f.write(lxd_key_pem)

            session.cert = (self._cert_file, self._key_file)
            self.logger.info("LXD session configured with client certificate auth")

        elif lxd_trust_token:
            session.headers.update({"Authorization": f"Bearer {lxd_trust_token}"})
            self.logger.info("LXD session configured with trust token auth")

        else:
            self.logger.warning(
                "LXD session: no auth configured — relying on server trust (local only)"
            )

        return session

    def _lxd_get(self, path: str) -> Any:
        """
        Perform a GET request against the LXD API.

        Args:
            path: API path starting with /1.0/...

        Returns:
            Parsed JSON response metadata field (list or dict)

        Raises:
            RequestException: On HTTP or connection failure
        """
        url = f"{self.lxd_url}{path}"
        response = self.session.get(url, timeout=30)
        response.raise_for_status()
        data = response.json()
        # LXD API wraps results in {"metadata": ...}
        return data.get("metadata", data)

    async def connect(self) -> None:
        """Establish connection to LXD API and Elder API."""
        self.lxd_url = self._get_setting("lxd_url", "https://localhost:8443")
        self.logger.info("Connecting to LXD API", url=self.lxd_url)

        try:
            self.session = self._build_session()

            # Test connectivity — /1.0 returns server info
            info = self._lxd_get("/1.0")
            api_version = info.get("api_version", "unknown") if isinstance(info, dict) else "unknown"
            self.logger.info(
                "LXD API connection established",
                url=self.lxd_url,
                api_version=api_version,
            )

        except ConnectionError as e:
            self.logger.error("Cannot reach LXD API", url=self.lxd_url, error=str(e))
            raise
        except RequestException as e:
            self.logger.error("LXD API request failed during connect", error=str(e))
            raise

        # Initialize Elder API client
        self.elder_client = ElderAPIClient()
        await self.elder_client.connect()

        self.logger.info("LXD connector connected successfully")

    async def disconnect(self) -> None:
        """Disconnect from LXD and Elder API."""
        if self.session:
            self.session.close()
            self.session = None

        if self._cert_tempdir:
            self._cert_tempdir.cleanup()
            self._cert_tempdir = None
            self._cert_file = None
            self._key_file = None

        if self.elder_client:
            await self.elder_client.close()

        self.organization_cache.clear()
        self.logger.info("LXD connector disconnected")

    async def _get_or_create_organization(
        self,
        name: str,
        description: str,
        parent_id: Optional[int] = None,
    ) -> int:
        """
        Get or create an organization in Elder.

        Args:
            name: Organization name
            description: Organization description
            parent_id: Parent organization ID

        Returns:
            Organization ID
        """
        cache_key = f"{parent_id or 'root'}:{name}"
        if cache_key in self.organization_cache:
            return self.organization_cache[cache_key]

        # Search for existing organization
        response = await self.elder_client.list_organizations(per_page=1000)
        for org in response.get("items", []):
            if org["name"] == name and org.get("parent_id") == parent_id:
                self.organization_cache[cache_key] = org["id"]
                return org["id"]

        # Create new organization
        if settings.create_missing_organizations:
            org_data = Organization(
                name=name,
                description=description,
                parent_id=parent_id,
            )
            created = await self.elder_client.create_organization(org_data)
            org_id = created["id"]
            self.organization_cache[cache_key] = org_id
            return org_id
        elif settings.default_organization_id:
            return settings.default_organization_id
        else:
            raise ValueError(
                f"Organization '{name}' not found and auto-creation disabled"
            )

    async def _sync_instances(self, lxd_org_id: int) -> tuple[int, int]:
        """
        Sync LXD instances (containers and VMs) to Elder entities.

        Containers map to sub_type=lxd_container, VMs to sub_type=lxd_vm.

        Args:
            lxd_org_id: Elder organization ID for LXD resources

        Returns:
            Tuple of (created_count, updated_count)
        """
        created = 0
        updated = 0

        try:
            # Returns list of instance URL paths e.g. ["/1.0/instances/my-container"]
            instance_paths = self._lxd_get("/1.0/instances")

            for path in instance_paths or []:
                instance_name = path.rstrip("/").split("/")[-1]

                try:
                    instance = self._lxd_get(f"/1.0/instances/{instance_name}")
                except RequestException as e:
                    self.logger.warning(
                        "Failed to get LXD instance details",
                        instance=instance_name,
                        error=str(e),
                    )
                    continue

                # Determine type: "virtual-machine" or "container"
                instance_type = instance.get("type", "container")
                sub_type = "lxd_vm" if instance_type == "virtual-machine" else "lxd_container"

                # Instance status from state
                status = instance.get("status", "Unknown")
                is_active = status.lower() == "running"

                config = instance.get("config", {})
                architecture = instance.get("architecture", "")
                description_text = config.get("image.description", "")
                os_name = config.get("image.os", "")
                os_release = config.get("image.release", "")

                # Network addresses from expanded state if available
                networks: Dict[str, Any] = {}
                try:
                    state = self._lxd_get(f"/1.0/instances/{instance_name}/state")
                    if isinstance(state, dict):
                        networks = state.get("network", {})
                        # Override status from live state if available
                        live_status = state.get("status")
                        if live_status:
                            status = live_status
                            is_active = status.lower() == "running"
                except RequestException:
                    pass

                # Collect IPv4 addresses from all interfaces
                ipv4_addresses = []
                for iface_name, iface_data in networks.items():
                    if isinstance(iface_data, dict):
                        for addr in iface_data.get("addresses", []):
                            if addr.get("family") == "inet":
                                ipv4_addresses.append(addr.get("address", ""))

                # Profiles, devices
                profiles = instance.get("profiles", [])
                devices = instance.get("devices", {})
                disk_devices = {
                    k: v for k, v in devices.items()
                    if isinstance(v, dict) and v.get("type") == "disk"
                }

                status_metadata = {"status": status, "timestamp": int(time.time())}

                entity = Entity(
                    name=f"LXD: {instance_name}",
                    entity_type="compute",
                    sub_type=sub_type,
                    organization_id=lxd_org_id,
                    description=(
                        description_text
                        or f"LXD {instance_type} instance: {instance_name}"
                    ),
                    attributes={
                        "instance_name": instance_name,
                        "instance_type": instance_type,
                        "architecture": architecture,
                        "status": status,
                        "os": os_name,
                        "os_release": os_release,
                        "ipv4_addresses": ipv4_addresses,
                        "profiles": profiles,
                        "disk_devices": {k: v.get("source", "") for k, v in disk_devices.items()},
                        "config_memory_limit": config.get("limits.memory", ""),
                        "config_cpu_limit": config.get("limits.cpu", ""),
                        "lxd_url": self.lxd_url,
                        "provider": "lxd",
                    },
                    status_metadata=status_metadata,
                    tags=["lxd", instance_type, status.lower()],
                    is_active=is_active,
                )

                # Check if entity already exists
                existing = await self.elder_client.list_entities(
                    organization_id=lxd_org_id,
                    entity_type="compute",
                )

                found = None
                for item in existing.get("items", []):
                    attrs = item.get("attributes", {})
                    if (
                        attrs.get("instance_name") == instance_name
                        and attrs.get("lxd_url") == self.lxd_url
                    ):
                        found = item
                        break

                if found:
                    await self.elder_client.update_entity(found["id"], entity)
                    updated += 1
                else:
                    await self.elder_client.create_entity(entity)
                    created += 1

        except RequestException as e:
            self.logger.error("Failed to sync LXD instances", error=str(e))

        return created, updated

    async def _sync_storage_pools(self, lxd_org_id: int) -> tuple[int, int]:
        """
        Sync LXD storage pools to Elder data_stores entities.

        Args:
            lxd_org_id: Elder organization ID for LXD resources

        Returns:
            Tuple of (created_count, updated_count)
        """
        created = 0
        updated = 0

        try:
            pool_paths = self._lxd_get("/1.0/storage-pools")

            for path in pool_paths or []:
                pool_name = path.rstrip("/").split("/")[-1]

                try:
                    pool = self._lxd_get(f"/1.0/storage-pools/{pool_name}")
                except RequestException as e:
                    self.logger.warning(
                        "Failed to get LXD storage pool details",
                        pool=pool_name,
                        error=str(e),
                    )
                    continue

                driver = pool.get("driver", "unknown")
                status = pool.get("status", "Unknown")
                pool_config = pool.get("config", {})

                # Capacity info varies by driver
                used_bytes = 0
                total_bytes = 0
                try:
                    resources = self._lxd_get(
                        f"/1.0/storage-pools/{pool_name}/resources"
                    )
                    if isinstance(resources, dict):
                        space = resources.get("space", {})
                        used_bytes = space.get("used", 0)
                        total_bytes = space.get("total", 0)
                except RequestException:
                    pass

                status_metadata = {"status": status, "timestamp": int(time.time())}

                entity = Entity(
                    name=f"LXD Storage: {pool_name}",
                    entity_type="storage",
                    sub_type="virtual_disk",
                    organization_id=lxd_org_id,
                    description=f"LXD storage pool '{pool_name}' ({driver})",
                    attributes={
                        "pool_name": pool_name,
                        "driver": driver,
                        "status": status,
                        "used_bytes": used_bytes,
                        "total_bytes": total_bytes,
                        "config": pool_config,
                        "lxd_url": self.lxd_url,
                        "provider": "lxd",
                    },
                    status_metadata=status_metadata,
                    tags=["lxd", "storage", driver],
                    is_active=status.lower() in ("created", "ready"),
                )

                # Check if entity already exists
                existing = await self.elder_client.list_entities(
                    organization_id=lxd_org_id,
                    entity_type="storage",
                )

                found = None
                for item in existing.get("items", []):
                    attrs = item.get("attributes", {})
                    if (
                        attrs.get("pool_name") == pool_name
                        and attrs.get("lxd_url") == self.lxd_url
                    ):
                        found = item
                        break

                if found:
                    await self.elder_client.update_entity(found["id"], entity)
                    updated += 1
                else:
                    await self.elder_client.create_entity(entity)
                    created += 1

        except RequestException as e:
            self.logger.error("Failed to sync LXD storage pools", error=str(e))

        return created, updated

    async def _sync_networks(self, lxd_org_id: int) -> tuple[int, int]:
        """
        Sync LXD networks to Elder networking entities.

        Args:
            lxd_org_id: Elder organization ID for LXD resources

        Returns:
            Tuple of (created_count, updated_count)
        """
        created = 0
        updated = 0

        try:
            network_paths = self._lxd_get("/1.0/networks")

            for path in network_paths or []:
                net_name = path.rstrip("/").split("/")[-1]

                try:
                    network = self._lxd_get(f"/1.0/networks/{net_name}")
                except RequestException as e:
                    self.logger.warning(
                        "Failed to get LXD network details",
                        network=net_name,
                        error=str(e),
                    )
                    continue

                net_type = network.get("type", "unknown")
                status = network.get("status", "Unknown")
                managed = network.get("managed", False)
                net_config = network.get("config", {})
                ipv4_address = net_config.get("ipv4.address", "")
                ipv6_address = net_config.get("ipv6.address", "")

                status_metadata = {"status": status, "timestamp": int(time.time())}

                entity = Entity(
                    name=f"LXD Network: {net_name}",
                    entity_type="network",
                    sub_type="subnet",
                    organization_id=lxd_org_id,
                    description=f"LXD network '{net_name}' (type: {net_type})",
                    attributes={
                        "network_name": net_name,
                        "network_type": net_type,
                        "status": status,
                        "managed": managed,
                        "ipv4_address": ipv4_address,
                        "ipv6_address": ipv6_address,
                        "lxd_url": self.lxd_url,
                        "provider": "lxd",
                    },
                    status_metadata=status_metadata,
                    tags=["lxd", "network", net_type],
                    is_active=status.lower() == "created",
                )

                # Check if entity already exists
                existing = await self.elder_client.list_entities(
                    organization_id=lxd_org_id,
                    entity_type="network",
                )

                found = None
                for item in existing.get("items", []):
                    attrs = item.get("attributes", {})
                    if (
                        attrs.get("network_name") == net_name
                        and attrs.get("lxd_url") == self.lxd_url
                    ):
                        found = item
                        break

                if found:
                    await self.elder_client.update_entity(found["id"], entity)
                    updated += 1
                else:
                    await self.elder_client.create_entity(entity)
                    created += 1

        except RequestException as e:
            self.logger.error("Failed to sync LXD networks", error=str(e))

        return created, updated

    async def _sync_cluster_members(self, lxd_org_id: int) -> tuple[int, int]:
        """
        Sync LXD cluster members to Elder entities.

        Only runs when the LXD server is operating in cluster mode.

        Args:
            lxd_org_id: Elder organization ID for LXD resources

        Returns:
            Tuple of (created_count, updated_count)
        """
        created = 0
        updated = 0

        try:
            # Check if clustering is enabled
            cluster_info = self._lxd_get("/1.0/cluster")
            if not isinstance(cluster_info, dict) or not cluster_info.get("enabled"):
                self.logger.info("LXD cluster mode not enabled — skipping cluster members")
                return created, updated

            member_paths = self._lxd_get("/1.0/cluster/members")

            for path in member_paths or []:
                member_name = path.rstrip("/").split("/")[-1]

                try:
                    member = self._lxd_get(f"/1.0/cluster/members/{member_name}")
                except RequestException as e:
                    self.logger.warning(
                        "Failed to get LXD cluster member details",
                        member=member_name,
                        error=str(e),
                    )
                    continue

                status = member.get("status", "Unknown")
                url = member.get("url", "")
                database = member.get("database", False)
                failure_domain = member.get("failure_domain", "default")
                description_text = member.get("description", "")
                roles = member.get("roles", [])
                architecture = member.get("architecture", "")

                is_active = status.lower() == "online"
                status_metadata = {"status": status, "timestamp": int(time.time())}

                entity = Entity(
                    name=f"LXD Node: {member_name}",
                    entity_type="compute",
                    sub_type="server",
                    organization_id=lxd_org_id,
                    description=(
                        description_text
                        or f"LXD cluster node: {member_name}"
                    ),
                    attributes={
                        "member_name": member_name,
                        "url": url,
                        "status": status,
                        "database": database,
                        "failure_domain": failure_domain,
                        "roles": roles,
                        "architecture": architecture,
                        "lxd_url": self.lxd_url,
                        "provider": "lxd",
                        "lxd_resource": "cluster_member",
                    },
                    status_metadata=status_metadata,
                    tags=["lxd", "cluster", "node", status.lower()],
                    is_active=is_active,
                )

                # Check if entity already exists
                existing = await self.elder_client.list_entities(
                    organization_id=lxd_org_id,
                    entity_type="compute",
                )

                found = None
                for item in existing.get("items", []):
                    attrs = item.get("attributes", {})
                    if (
                        attrs.get("member_name") == member_name
                        and attrs.get("lxd_resource") == "cluster_member"
                        and attrs.get("lxd_url") == self.lxd_url
                    ):
                        found = item
                        break

                if found:
                    await self.elder_client.update_entity(found["id"], entity)
                    updated += 1
                else:
                    await self.elder_client.create_entity(entity)
                    created += 1

        except RequestException as e:
            self.logger.error("Failed to sync LXD cluster members", error=str(e))

        return created, updated

    async def sync(self) -> SyncResult:
        """
        Synchronize LXD resources to Elder.

        Returns:
            SyncResult with statistics
        """
        result = SyncResult(connector_name=self.name)
        self.logger.info("Starting LXD sync", url=self.lxd_url)

        try:
            # Create LXD root organization
            # Use the hostname portion of the URL as a label
            lxd_label = self.lxd_url or "lxd"
            lxd_org_id = await self._get_or_create_organization(
                f"LXD: {lxd_label}",
                f"LXD hypervisor at {lxd_label}",
            )
            result.organizations_created += 1

            # Sync instances (containers + VMs)
            inst_created, inst_updated = await self._sync_instances(lxd_org_id)
            result.entities_created += inst_created
            result.entities_updated += inst_updated

            # Sync storage pools
            pool_created, pool_updated = await self._sync_storage_pools(lxd_org_id)
            result.entities_created += pool_created
            result.entities_updated += pool_updated

            # Sync networks
            net_created, net_updated = await self._sync_networks(lxd_org_id)
            result.entities_created += net_created
            result.entities_updated += net_updated

            # Sync cluster members (no-op if not clustered)
            node_created, node_updated = await self._sync_cluster_members(lxd_org_id)
            result.entities_created += node_created
            result.entities_updated += node_updated

            self.logger.info(
                "LXD sync completed",
                url=self.lxd_url,
                total_ops=result.total_operations,
                orgs_created=result.organizations_created,
                entities_created=result.entities_created,
                entities_updated=result.entities_updated,
            )

        except Exception as e:
            error_msg = f"LXD sync failed: {str(e)}"
            self.logger.error(error_msg, exc_info=True)
            result.errors.append(error_msg)

        return result

    async def health_check(self) -> bool:
        """Check LXD API connectivity."""
        try:
            if self.session:
                self._lxd_get("/1.0")
                return True
            return False
        except Exception as e:
            self.logger.warning("LXD health check failed", error=str(e))
            return False
