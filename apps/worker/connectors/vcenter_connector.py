"""VMware vCenter connector for syncing to Elder."""

# flake8: noqa: E501


from typing import Any, Dict, List, Optional

from apps.worker.config.settings import settings
from apps.worker.connectors.base import BaseConnector, SyncResult
from apps.worker.utils.elder_client import ElderAPIClient, Entity, Organization


class VCenterConnector(BaseConnector):
    """Connector for VMware vCenter infrastructure.

    Syncs VMs, hosts, datastores, clusters, and networks from vCenter.
    This is a read-only connector for discovery purposes.
    """

    def __init__(self):
        """Initialize vCenter connector."""
        super().__init__("vcenter")
        self.elder_client: Optional[ElderAPIClient] = None
        self.si: Optional[Any] = None  # ServiceInstance
        self.content: Optional[Any] = None
        self.organization_cache: Dict[str, int] = {}

    async def connect(self) -> None:
        """Establish connection to vCenter and Elder API."""
        self.logger.info("Connecting to vCenter", host=settings.vcenter_host)

        try:
            import ssl

            from pyVim.connect import SmartConnect

            # Disable SSL verification if configured
            context = None
            if not settings.vcenter_verify_ssl:
                context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
                context.check_hostname = False
                context.verify_mode = ssl.CERT_NONE

            # Connect to vCenter
            self.si = SmartConnect(
                host=settings.vcenter_host,
                user=settings.vcenter_username,
                pwd=settings.vcenter_password,
                port=settings.vcenter_port,
                sslContext=context,
            )

            self.content = self.si.RetrieveContent()

            self.logger.info(
                "vCenter connection established",
                host=settings.vcenter_host,
                version=self.content.about.version,
            )

        except Exception as e:
            self.logger.error("Failed to connect to vCenter", error=str(e))
            raise

        # Initialize Elder API client
        self.elder_client = ElderAPIClient()
        await self.elder_client.connect()

        self.logger.info("vCenter connector connected successfully")

    async def disconnect(self) -> None:
        """Disconnect from vCenter and Elder API."""
        if self.si:
            from pyVim.connect import Disconnect

            Disconnect(self.si)
            self.si = None
            self.content = None

        if self.elder_client:
            await self.elder_client.close()

        self.organization_cache.clear()
        self.logger.info("vCenter connector disconnected")

    async def _get_or_create_organization(
        self,
        name: str,
        description: str,
        parent_id: Optional[int] = None,
    ) -> int:
        """Get or create an organization in Elder."""
        cache_key = f"{parent_id or 'root'}:{name}"
        if cache_key in self.organization_cache:
            return self.organization_cache[cache_key]

        response = await self.elder_client.list_organizations(per_page=1000)
        for org in response.get("items", []):
            if org["name"] == name and org.get("parent_id") == parent_id:
                self.organization_cache[cache_key] = org["id"]
                return org["id"]

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

    def _get_all_objs(self, obj_type: List[str]) -> List[Any]:
        """Get all objects of specified types from vCenter.

        Args:
            obj_type: List of vimtypes to retrieve

        Returns:
            List of managed objects
        """
        container = self.content.viewManager.CreateContainerView(
            self.content.rootFolder, obj_type, True
        )
        objects = list(container.view)
        container.Destroy()
        return objects

    async def _sync_datacenters(
        self, vcenter_org_id: int
    ) -> tuple[int, int, Dict[str, int]]:
        """Sync vCenter datacenters as sub-organizations.

        Returns:
            Tuple of (created, updated, datacenter_id_map)
        """
        from pyVmomi import vim

        created = 0
        dc_map = {}

        try:
            datacenters = self._get_all_objs([vim.Datacenter])

            for dc in datacenters:
                dc_org_id = await self._get_or_create_organization(
                    name=f"DC: {dc.name}",
                    description=f"vCenter Datacenter: {dc.name}",
                    parent_id=vcenter_org_id,
                )
                dc_map[dc.name] = dc_org_id
                created += 1

        except Exception as e:
            self.logger.error("Failed to sync datacenters", error=str(e))

        return created, 0, dc_map

    async def _sync_clusters(
        self, vcenter_org_id: int, dc_map: Dict[str, int]
    ) -> tuple[int, int, Dict[str, int]]:
        """Sync vCenter clusters as sub-organizations.

        Returns:
            Tuple of (created, updated, cluster_id_map)
        """
        from pyVmomi import vim

        created = 0
        cluster_map = {}

        try:
            clusters = self._get_all_objs([vim.ClusterComputeResource])

            for cluster in clusters:
                # Get parent datacenter
                parent = cluster.parent
                while parent and not isinstance(parent, vim.Datacenter):
                    parent = parent.parent

                dc_name = parent.name if parent else None
                parent_org_id = dc_map.get(dc_name, vcenter_org_id)

                cluster_org_id = await self._get_or_create_organization(
                    name=f"Cluster: {cluster.name}",
                    description=f"vCenter Cluster: {cluster.name}",
                    parent_id=parent_org_id,
                )
                cluster_map[cluster.name] = cluster_org_id
                created += 1

        except Exception as e:
            self.logger.error("Failed to sync clusters", error=str(e))

        return created, 0, cluster_map

    async def _sync_hosts(
        self, vcenter_org_id: int, cluster_map: Dict[str, int]
    ) -> tuple[int, int]:
        """Sync ESXi hosts to Elder."""
        from pyVmomi import vim

        created = 0
        updated = 0

        try:
            hosts = self._get_all_objs([vim.HostSystem])

            for host in hosts:
                # Get parent cluster
                cluster_name = None
                if hasattr(host, "parent") and isinstance(
                    host.parent, vim.ClusterComputeResource
                ):
                    cluster_name = host.parent.name

                org_id = cluster_map.get(cluster_name, vcenter_org_id)

                # Get hardware info
                hardware = host.hardware
                summary = host.summary

                entity = Entity(
                    name=host.name,
                    entity_type="compute",
                    organization_id=org_id,
                    description=f"ESXi Host: {host.name}",
                    attributes={
                        "vcenter_host_id": str(host._moId),
                        "host_type": "esxi",
                        "version": (
                            summary.config.product.version if summary.config else None
                        ),
                        "build": (
                            summary.config.product.build if summary.config else None
                        ),
                        "cpu_model": hardware.cpuInfo.hz if hardware else None,
                        "cpu_cores": hardware.cpuInfo.numCpuCores if hardware else None,
                        "memory_bytes": hardware.memorySize if hardware else None,
                        "memory_gb": (
                            round(hardware.memorySize / (1024**3), 2)
                            if hardware
                            else None
                        ),
                        "connection_state": str(summary.runtime.connectionState),
                        "power_state": str(summary.runtime.powerState),
                        "maintenance_mode": summary.runtime.inMaintenanceMode,
                        "provider": "vcenter",
                        "vcenter_host": settings.vcenter_host,
                    },
                    tags=["vcenter", "esxi", "host", "compute"],
                    is_active=str(summary.runtime.connectionState) == "connected",
                )

                # Check if entity exists
                existing = await self.elder_client.list_entities(
                    organization_id=org_id,
                    entity_type="compute",
                )

                found = None
                for item in existing.get("items", []):
                    if item.get("attributes", {}).get("vcenter_host_id") == str(
                        host._moId
                    ):
                        found = item
                        break

                if found:
                    await self.elder_client.update_entity(found["id"], entity)
                    updated += 1
                else:
                    await self.elder_client.create_entity(entity)
                    created += 1

        except Exception as e:
            self.logger.error("Failed to sync hosts", error=str(e))

        return created, updated

    async def _sync_vms(
        self, vcenter_org_id: int, cluster_map: Dict[str, int]
    ) -> tuple[int, int]:
        """Sync virtual machines to Elder."""
        from pyVmomi import vim

        created = 0
        updated = 0

        try:
            vms = self._get_all_objs([vim.VirtualMachine])

            for vm in vms:
                # Skip templates
                if vm.config and vm.config.template:
                    continue

                # Get parent cluster
                cluster_name = None
                host = vm.runtime.host if vm.runtime else None
                if host and hasattr(host, "parent"):
                    if isinstance(host.parent, vim.ClusterComputeResource):
                        cluster_name = host.parent.name

                org_id = cluster_map.get(cluster_name, vcenter_org_id)

                config = vm.config
                summary = vm.summary

                entity = Entity(
                    name=vm.name,
                    entity_type="compute",
                    organization_id=org_id,
                    description=config.annotation if config else f"VM: {vm.name}",
                    attributes={
                        "vcenter_vm_id": str(vm._moId),
                        "vm_type": "virtual_machine",
                        "uuid": config.uuid if config else None,
                        "guest_os": config.guestFullName if config else None,
                        "cpu_count": config.hardware.numCPU if config else None,
                        "memory_mb": config.hardware.memoryMB if config else None,
                        "memory_gb": (
                            round(config.hardware.memoryMB / 1024, 2)
                            if config
                            else None
                        ),
                        "power_state": (
                            str(vm.runtime.powerState) if vm.runtime else None
                        ),
                        "tools_status": (
                            str(summary.guest.toolsStatus) if summary.guest else None
                        ),
                        "ip_address": (
                            summary.guest.ipAddress if summary.guest else None
                        ),
                        "host_name": summary.guest.hostName if summary.guest else None,
                        "provider": "vcenter",
                        "vcenter_host": settings.vcenter_host,
                    },
                    tags=["vcenter", "vm", "virtual_machine", "compute"],
                    is_active=(
                        str(vm.runtime.powerState) == "poweredOn"
                        if vm.runtime
                        else False
                    ),
                )

                # Check if entity exists
                existing = await self.elder_client.list_entities(
                    organization_id=org_id,
                    entity_type="compute",
                )

                found = None
                for item in existing.get("items", []):
                    if item.get("attributes", {}).get("vcenter_vm_id") == str(vm._moId):
                        found = item
                        break

                if found:
                    await self.elder_client.update_entity(found["id"], entity)
                    updated += 1
                else:
                    await self.elder_client.create_entity(entity)
                    created += 1

        except Exception as e:
            self.logger.error("Failed to sync VMs", error=str(e))

        return created, updated

    async def _sync_datastores(self, vcenter_org_id: int) -> tuple[int, int]:
        """Sync datastores to Elder."""
        from pyVmomi import vim

        created = 0
        updated = 0

        try:
            datastores = self._get_all_objs([vim.Datastore])

            for ds in datastores:
                summary = ds.summary

                entity = Entity(
                    name=ds.name,
                    entity_type="storage",
                    organization_id=vcenter_org_id,
                    description=f"vCenter Datastore: {ds.name}",
                    attributes={
                        "vcenter_datastore_id": str(ds._moId),
                        "datastore_type": summary.type,
                        "capacity_bytes": summary.capacity,
                        "capacity_gb": round(summary.capacity / (1024**3), 2),
                        "free_space_bytes": summary.freeSpace,
                        "free_space_gb": round(summary.freeSpace / (1024**3), 2),
                        "used_gb": round(
                            (summary.capacity - summary.freeSpace) / (1024**3), 2
                        ),
                        "url": summary.url,
                        "accessible": summary.accessible,
                        "maintenance_mode": summary.maintenanceMode,
                        "provider": "vcenter",
                        "vcenter_host": settings.vcenter_host,
                    },
                    tags=["vcenter", "datastore", "storage"],
                    is_active=summary.accessible,
                )

                # Check if entity exists
                existing = await self.elder_client.list_entities(
                    organization_id=vcenter_org_id,
                    entity_type="storage",
                )

                found = None
                for item in existing.get("items", []):
                    if item.get("attributes", {}).get("vcenter_datastore_id") == str(
                        ds._moId
                    ):
                        found = item
                        break

                if found:
                    await self.elder_client.update_entity(found["id"], entity)
                    updated += 1
                else:
                    await self.elder_client.create_entity(entity)
                    created += 1

        except Exception as e:
            self.logger.error("Failed to sync datastores", error=str(e))

        return created, updated

    async def _sync_networks(self, vcenter_org_id: int) -> tuple[int, int]:
        """Sync networks and port groups to Elder."""
        from pyVmomi import vim

        created = 0
        updated = 0

        try:
            networks = self._get_all_objs([vim.Network])

            for net in networks:
                # Determine network type
                net_type = "network"
                if isinstance(net, vim.dvs.DistributedVirtualPortgroup):
                    net_type = "distributed_portgroup"
                elif isinstance(net, vim.Network):
                    net_type = "standard_portgroup"

                entity = Entity(
                    name=net.name,
                    entity_type="network",
                    organization_id=vcenter_org_id,
                    description=f"vCenter Network: {net.name}",
                    attributes={
                        "vcenter_network_id": str(net._moId),
                        "network_type": net_type,
                        "accessible": (
                            net.summary.accessible
                            if hasattr(net.summary, "accessible")
                            else True
                        ),
                        "provider": "vcenter",
                        "vcenter_host": settings.vcenter_host,
                    },
                    tags=["vcenter", "network", net_type],
                    is_active=True,
                )

                # Check if entity exists
                existing = await self.elder_client.list_entities(
                    organization_id=vcenter_org_id,
                    entity_type="network",
                )

                found = None
                for item in existing.get("items", []):
                    if item.get("attributes", {}).get("vcenter_network_id") == str(
                        net._moId
                    ):
                        found = item
                        break

                if found:
                    await self.elder_client.update_entity(found["id"], entity)
                    updated += 1
                else:
                    await self.elder_client.create_entity(entity)
                    created += 1

        except Exception as e:
            self.logger.error("Failed to sync networks", error=str(e))

        return created, updated

    async def sync(self) -> SyncResult:
        """Synchronize vCenter resources to Elder.

        Returns:
            SyncResult with statistics
        """
        result = SyncResult(connector_name=self.name)
        self.logger.info("Starting vCenter sync", host=settings.vcenter_host)

        try:
            # Create vCenter root organization
            vcenter_org_id = await self._get_or_create_organization(
                f"vCenter: {settings.vcenter_host}",
                f"VMware vCenter: {settings.vcenter_host}",
            )
            result.organizations_created += 1

            # Sync datacenters (as sub-organizations)
            dc_created, _, dc_map = await self._sync_datacenters(vcenter_org_id)
            result.organizations_created += dc_created

            # Sync clusters (as sub-organizations)
            cluster_created, _, cluster_map = await self._sync_clusters(
                vcenter_org_id, dc_map
            )
            result.organizations_created += cluster_created

            # Sync ESXi hosts
            hosts_created, hosts_updated = await self._sync_hosts(
                vcenter_org_id, cluster_map
            )
            result.entities_created += hosts_created
            result.entities_updated += hosts_updated

            # Sync VMs
            vms_created, vms_updated = await self._sync_vms(vcenter_org_id, cluster_map)
            result.entities_created += vms_created
            result.entities_updated += vms_updated

            # Sync datastores
            ds_created, ds_updated = await self._sync_datastores(vcenter_org_id)
            result.entities_created += ds_created
            result.entities_updated += ds_updated

            # Sync networks
            net_created, net_updated = await self._sync_networks(vcenter_org_id)
            result.entities_created += net_created
            result.entities_updated += net_updated

            self.logger.info(
                "vCenter sync completed",
                total_ops=result.total_operations,
                orgs_created=result.organizations_created,
                entities_created=result.entities_created,
                entities_updated=result.entities_updated,
            )

        except Exception as e:
            error_msg = f"vCenter sync failed: {str(e)}"
            self.logger.error(error_msg, exc_info=True)
            result.errors.append(error_msg)

        return result

    async def health_check(self) -> bool:
        """Check vCenter connectivity.

        Returns:
            True if healthy, False otherwise
        """
        try:
            if not self.si or not self.content:
                return False

            # Try to get server time as health check
            self.si.CurrentTime()
            return True
        except Exception as e:
            self.logger.warning("vCenter health check failed", error=str(e))
            return False
