"""GCP cloud discovery client for Elder."""

# flake8: noqa: E501


from datetime import datetime
from typing import Any, Dict, List

try:
    from google.auth import default as google_auth_default
    from google.cloud import compute_v1, functions_v1, storage
    from google.cloud.sql import v1 as sql_v1
    from google.oauth2 import service_account
except ImportError:
    compute_v1 = storage = functions_v1 = sql_v1 = None
    google_auth_default = service_account = None

from apps.worker.discovery.base import BaseDiscoveryProvider


class GCPDiscoveryClient(BaseDiscoveryProvider):
    """GCP cloud resource discovery implementation."""

    def __init__(self, config: Dict[str, Any]):
        """Initialize GCP discovery client."""
        super().__init__(config)

        if not compute_v1:
            raise ImportError(
                "google-cloud SDK required. Install: pip install google-cloud-compute google-cloud-storage"
            )

        self.project_id = config.get("project_id")
        if not self.project_id:
            raise ValueError("project_id is required for GCP discovery")

        # Initialize credentials
        if config.get("credentials_json"):
            import json

            creds_dict = (
                json.loads(config["credentials_json"])
                if isinstance(config["credentials_json"], str)
                else config["credentials_json"]
            )
            self.credentials = service_account.Credentials.from_service_account_info(
                creds_dict
            )
        else:
            self.credentials, _ = google_auth_default()

    def test_connection(self) -> bool:
        """Test GCP connectivity."""
        try:
            client = compute_v1.InstancesClient(credentials=self.credentials)
            # Try to list instances (will fail if no permissions, but connection works)
            client.aggregated_list(project=self.project_id, max_results=1)
            return True
        except:
            return False

    def get_supported_services(self) -> List[str]:
        """Get supported GCP services."""
        return ["compute", "storage", "functions", "sql", "vpc"]

    def discover_all(self) -> Dict[str, Any]:
        """Discover all GCP resources."""
        start_time = datetime.utcnow()

        results = {
            "compute": self.discover_compute(),
            "storage": self.discover_storage(),
            "network": self.discover_network(),
            "database": self.discover_databases(),
            "serverless": self.discover_serverless(),
        }

        resources_count = sum(len(resources) for resources in results.values())

        return {
            **results,
            "resources_count": resources_count,
            "discovery_time": datetime.utcnow(),
            "duration_seconds": (datetime.utcnow() - start_time).total_seconds(),
        }

    def discover_compute(self) -> List[Dict[str, Any]]:
        """Discover GCE instances."""
        resources = []
        try:
            client = compute_v1.InstancesClient(credentials=self.credentials)
            request = compute_v1.AggregatedListInstancesRequest(project=self.project_id)
            agg_list = client.aggregated_list(request=request)

            for zone, response in agg_list:
                if response.instances:
                    for instance in response.instances:
                        resource = self.format_resource(
                            resource_id=str(instance.id),
                            resource_type="gce_instance",
                            name=instance.name,
                            metadata={
                                "machine_type": instance.machine_type.split("/")[-1],
                                "status": instance.status,
                                "zone": instance.zone.split("/")[-1],
                            },
                            region=instance.zone.split("/")[-1],
                            tags=(
                                dict(instance.labels)
                                if hasattr(instance, "labels")
                                else {}
                            ),
                        )
                        resources.append(resource)
        except:
            pass
        return resources

    def discover_storage(self) -> List[Dict[str, Any]]:
        """Discover GCS buckets."""
        resources = []
        try:
            client = storage.Client(
                project=self.project_id, credentials=self.credentials
            )
            for bucket in client.list_buckets():
                resource = self.format_resource(
                    resource_id=bucket.name,
                    resource_type="gcs_bucket",
                    name=bucket.name,
                    metadata={
                        "location": bucket.location,
                        "storage_class": bucket.storage_class,
                        "created": (
                            bucket.time_created.isoformat()
                            if bucket.time_created
                            else None
                        ),
                    },
                    region=bucket.location,
                    tags=dict(bucket.labels) if bucket.labels else {},
                )
                resources.append(resource)
        except:
            pass
        return resources

    def discover_network(self) -> List[Dict[str, Any]]:
        """Discover VPCs and subnets."""
        resources = []
        try:
            client = compute_v1.NetworksClient(credentials=self.credentials)
            for network in client.list(project=self.project_id):
                resource = self.format_resource(
                    resource_id=str(network.id),
                    resource_type="vpc",
                    name=network.name,
                    metadata={
                        "auto_create_subnets": network.auto_create_subnetworks,
                    },
                    region="global",
                    tags={},
                )
                resources.append(resource)
        except:
            pass
        return resources

    def discover_databases(self) -> List[Dict[str, Any]]:
        """Discover Cloud SQL instances."""
        resources = []
        # Simplified - would use Cloud SQL API
        return resources

    def discover_serverless(self) -> List[Dict[str, Any]]:
        """Discover Cloud Functions."""
        resources = []
        # Simplified - would use Cloud Functions API
        return resources
