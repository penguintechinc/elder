"""GCP connector for syncing Google Cloud Platform resources to Elder."""

# flake8: noqa: E501


from typing import Dict, Optional

from google.api_core.exceptions import GoogleAPIError
from google.auth import load_credentials_from_file
from google.cloud import compute_v1, storage

from apps.worker.config.settings import settings
from apps.worker.connectors.base import BaseConnector, SyncResult
from apps.worker.utils.elder_client import ElderAPIClient, Entity, Organization


class GCPConnector(BaseConnector):
    """Connector for GCP resources."""

    def __init__(self):
        """Initialize GCP connector."""
        super().__init__("gcp")
        self.elder_client: Optional[ElderAPIClient] = None
        self.credentials = None
        self.project_id = settings.gcp_project_id
        self.organization_cache: Dict[str, int] = {}

    async def connect(self) -> None:
        """Establish connection to GCP and Elder API."""
        self.logger.info("Connecting to GCP services")

        # Load GCP credentials
        if settings.gcp_credentials_path:
            try:
                self.credentials, _ = load_credentials_from_file(
                    settings.gcp_credentials_path
                )
                self.logger.info(
                    "GCP credentials loaded",
                    path=settings.gcp_credentials_path,
                )
            except Exception as e:
                self.logger.error(
                    "Failed to load GCP credentials",
                    path=settings.gcp_credentials_path,
                    error=str(e),
                )
                raise
        else:
            # Use default credentials
            from google.auth import default

            self.credentials, _ = default()
            self.logger.info("Using GCP default credentials")

        # Initialize Elder API client
        self.elder_client = ElderAPIClient()
        await self.elder_client.connect()

        self.logger.info("GCP connector connected successfully")

    async def disconnect(self) -> None:
        """Disconnect from GCP and Elder API."""
        if self.elder_client:
            await self.elder_client.close()
        self.organization_cache.clear()
        self.logger.info("GCP connector disconnected")

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

    async def _sync_compute_instances(self, project_org_id: int) -> tuple[int, int]:
        """
        Sync GCP Compute Engine instances.

        Args:
            project_org_id: Elder organization ID for the project

        Returns:
            Tuple of (created_count, updated_count)
        """
        created = 0
        updated = 0

        try:
            instance_client = compute_v1.InstancesClient(credentials=self.credentials)

            # List instances across all zones
            aggregated_list = instance_client.aggregated_list(project=self.project_id)

            for zone, response in aggregated_list:
                if not response.instances:
                    continue

                for instance in response.instances:
                    # Extract zone name from full path
                    zone_name = instance.zone.split("/")[-1]

                    # Get first network interface for IP
                    network_interface = (
                        instance.network_interfaces[0]
                        if instance.network_interfaces
                        else None
                    )
                    internal_ip = (
                        network_interface.network_i_p if network_interface else None
                    )
                    external_ip = None
                    if network_interface and network_interface.access_configs:
                        external_ip = network_interface.access_configs[0].nat_i_p

                    entity = Entity(
                        name=f"GCE: {instance.name}",
                        entity_type="compute",
                        organization_id=project_org_id,
                        description=f"GCP Compute Engine instance in {zone_name}",
                        attributes={
                            "instance_id": str(instance.id),
                            "instance_name": instance.name,
                            "machine_type": instance.machine_type.split("/")[-1],
                            "status": instance.status,
                            "zone": zone_name,
                            "internal_ip": internal_ip,
                            "external_ip": external_ip,
                            "provider": "gcp",
                            "project_id": self.project_id,
                            "creation_timestamp": instance.creation_timestamp,
                        },
                        tags=["gcp", "compute", "gce", zone_name],
                        is_active=instance.status == "RUNNING",
                    )

                    # Check if entity already exists
                    existing = await self.elder_client.list_entities(
                        organization_id=project_org_id,
                        entity_type="compute",
                    )

                    found = None
                    for item in existing.get("items", []):
                        if item.get("attributes", {}).get("instance_id") == str(
                            instance.id
                        ):
                            found = item
                            break

                    if found:
                        await self.elder_client.update_entity(found["id"], entity)
                        updated += 1
                    else:
                        await self.elder_client.create_entity(entity)
                        created += 1

        except GoogleAPIError as e:
            self.logger.error("Failed to sync GCP compute instances", error=str(e))

        return created, updated

    async def _sync_vpc_networks(self, project_org_id: int) -> tuple[int, int]:
        """
        Sync GCP VPC networks.

        Args:
            project_org_id: Elder organization ID for the project

        Returns:
            Tuple of (created_count, updated_count)
        """
        created = 0
        updated = 0

        try:
            networks_client = compute_v1.NetworksClient(credentials=self.credentials)
            networks = networks_client.list(project=self.project_id)

            for network in networks:
                entity = Entity(
                    name=f"VPC: {network.name}",
                    entity_type="vpc",
                    organization_id=project_org_id,
                    description=f"GCP VPC network",
                    attributes={
                        "network_id": str(network.id),
                        "network_name": network.name,
                        "auto_create_subnetworks": network.auto_create_subnetworks,
                        "routing_mode": (
                            network.routing_config.routing_mode
                            if network.routing_config
                            else None
                        ),
                        "provider": "gcp",
                        "project_id": self.project_id,
                        "creation_timestamp": network.creation_timestamp,
                    },
                    tags=["gcp", "vpc", "network"],
                )

                # Check if entity already exists
                existing = await self.elder_client.list_entities(
                    organization_id=project_org_id,
                    entity_type="vpc",
                )

                found = None
                for item in existing.get("items", []):
                    if item.get("attributes", {}).get("network_id") == str(network.id):
                        found = item
                        break

                if found:
                    await self.elder_client.update_entity(found["id"], entity)
                    updated += 1
                else:
                    await self.elder_client.create_entity(entity)
                    created += 1

        except GoogleAPIError as e:
            self.logger.error("Failed to sync GCP VPC networks", error=str(e))

        return created, updated

    async def _sync_storage_buckets(self, project_org_id: int) -> tuple[int, int]:
        """
        Sync GCP Cloud Storage buckets.

        Args:
            project_org_id: Elder organization ID for the project

        Returns:
            Tuple of (created_count, updated_count)
        """
        created = 0
        updated = 0

        try:
            storage_client = storage.Client(
                credentials=self.credentials,
                project=self.project_id,
            )
            buckets = storage_client.list_buckets()

            for bucket in buckets:
                entity = Entity(
                    name=f"GCS: {bucket.name}",
                    entity_type="network",  # Storage is networked
                    organization_id=project_org_id,
                    description=f"GCP Cloud Storage bucket",
                    attributes={
                        "bucket_name": bucket.name,
                        "location": bucket.location,
                        "storage_class": bucket.storage_class,
                        "provider": "gcp",
                        "service": "gcs",
                        "project_id": self.project_id,
                        "time_created": (
                            bucket.time_created.isoformat()
                            if bucket.time_created
                            else None
                        ),
                    },
                    tags=["gcp", "gcs", "storage", bucket.location.lower()],
                )

                # Check if entity already exists
                existing = await self.elder_client.list_entities(
                    organization_id=project_org_id,
                    entity_type="network",
                )

                found = None
                for item in existing.get("items", []):
                    if item.get("attributes", {}).get("bucket_name") == bucket.name:
                        found = item
                        break

                if found:
                    await self.elder_client.update_entity(found["id"], entity)
                    updated += 1
                else:
                    await self.elder_client.create_entity(entity)
                    created += 1

        except GoogleAPIError as e:
            self.logger.error("Failed to sync GCP storage buckets", error=str(e))

        return created, updated

    async def sync(self) -> SyncResult:
        """
        Synchronize GCP resources to Elder.

        Returns:
            SyncResult with statistics
        """
        result = SyncResult(connector_name=self.name)
        self.logger.info("Starting GCP sync", project_id=self.project_id)

        try:
            # Create GCP root organization
            gcp_org_id = await self._get_or_create_organization(
                "GCP",
                "Google Cloud Platform",
            )
            result.organizations_created += 1

            # Create project organization
            project_org_id = await self._get_or_create_organization(
                f"GCP Project: {self.project_id}",
                f"GCP project {self.project_id}",
                parent_id=gcp_org_id,
            )
            result.organizations_created += 1

            # Sync compute instances
            compute_created, compute_updated = await self._sync_compute_instances(
                project_org_id
            )
            result.entities_created += compute_created
            result.entities_updated += compute_updated

            # Sync VPC networks
            vpc_created, vpc_updated = await self._sync_vpc_networks(project_org_id)
            result.entities_created += vpc_created
            result.entities_updated += vpc_updated

            # Sync storage buckets
            storage_created, storage_updated = await self._sync_storage_buckets(
                project_org_id
            )
            result.entities_created += storage_created
            result.entities_updated += storage_updated

            self.logger.info(
                "GCP sync completed",
                total_ops=result.total_operations,
                orgs_created=result.organizations_created,
                entities_created=result.entities_created,
                entities_updated=result.entities_updated,
            )

        except Exception as e:
            error_msg = f"GCP sync failed: {str(e)}"
            self.logger.error(error_msg, exc_info=True)
            result.errors.append(error_msg)

        return result

    async def health_check(self) -> bool:
        """Check GCP connectivity and credentials."""
        try:
            # Try to list compute zones as a health check
            zones_client = compute_v1.ZonesClient(credentials=self.credentials)
            list(zones_client.list(project=self.project_id, max_results=1))
            return True
        except Exception as e:
            self.logger.warning("GCP health check failed", error=str(e))
            return False
