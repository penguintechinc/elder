"""Base discovery provider for cloud resource discovery."""

# flake8: noqa: E501


from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any, Dict, List, Optional


class BaseDiscoveryProvider(ABC):
    """
    Abstract base class for cloud discovery providers.

    Supports AWS, GCP, Azure, and Kubernetes resource discovery.
    """

    def __init__(self, config: Dict[str, Any]):
        """
        Initialize discovery provider.

        Args:
            config: Provider configuration dict containing credentials and settings
        """
        self.config = config
        self.provider_type = config.get("provider_type", "unknown")
        self.name = config.get("name", f"{self.provider_type} Discovery")

    # Core Discovery Methods

    @abstractmethod
    def discover_all(self) -> Dict[str, Any]:
        """
        Discover all resources from the cloud provider.

        Returns:
            Dictionary with discovered resources by type:
            {
                "compute": [...],
                "storage": [...],
                "network": [...],
                "database": [...],
                "serverless": [...],
                "resources_count": int,
                "discovery_time": datetime
            }
        """

    @abstractmethod
    def discover_compute(self) -> List[Dict[str, Any]]:
        """
        Discover compute resources (VMs, instances, containers, etc.).

        Returns:
            List of compute resources
        """

    @abstractmethod
    def discover_storage(self) -> List[Dict[str, Any]]:
        """
        Discover storage resources (buckets, disks, volumes, etc.).

        Returns:
            List of storage resources
        """

    @abstractmethod
    def discover_network(self) -> List[Dict[str, Any]]:
        """
        Discover network resources (VPCs, subnets, load balancers, etc.).

        Returns:
            List of network resources
        """

    @abstractmethod
    def discover_databases(self) -> List[Dict[str, Any]]:
        """
        Discover database resources (RDS, Cloud SQL, Cosmos DB, etc.).

        Returns:
            List of database resources
        """

    @abstractmethod
    def discover_serverless(self) -> List[Dict[str, Any]]:
        """
        Discover serverless resources (Lambda, Cloud Functions, Azure Functions, etc.).

        Returns:
            List of serverless resources
        """

    # Utility Methods

    @abstractmethod
    def test_connection(self) -> bool:
        """
        Test connectivity to cloud provider.

        Returns:
            True if connection successful, False otherwise
        """

    @abstractmethod
    def get_supported_services(self) -> List[str]:
        """
        Get list of supported services for discovery.

        Returns:
            List of service names
        """

    def format_resource(
        self,
        resource_id: str,
        resource_type: str,
        name: str,
        metadata: Dict[str, Any],
        region: Optional[str] = None,
        tags: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        """
        Format discovered resource into standard Elder format.

        Args:
            resource_id: Unique resource identifier
            resource_type: Type of resource (e.g., ec2_instance, gcs_bucket)
            name: Resource name
            metadata: Additional resource metadata
            region: Cloud region/zone
            tags: Resource tags/labels

        Returns:
            Standardized resource dictionary
        """
        return {
            "resource_id": resource_id,
            "resource_type": resource_type,
            "name": name,
            "provider": self.provider_type,
            "region": region,
            "tags": tags or {},
            "metadata": metadata,
            "discovered_at": datetime.utcnow().isoformat(),
        }

    def _normalize_tags(self, tags: Any) -> Dict[str, str]:
        """
        Normalize tags from various cloud provider formats.

        Args:
            tags: Tags in provider-specific format

        Returns:
            Normalized tags dictionary
        """
        if not tags:
            return {}

        # AWS format: list of dicts with Key/Value
        if isinstance(tags, list):
            return {tag.get("Key", ""): tag.get("Value", "") for tag in tags}

        # GCP/Azure format: dict
        if isinstance(tags, dict):
            return tags

        return {}

    def _extract_region(self, resource_data: Dict[str, Any]) -> Optional[str]:
        """
        Extract region from resource data (provider-specific implementation).

        Args:
            resource_data: Raw resource data from provider

        Returns:
            Region string or None
        """
        # Override in provider-specific implementations
        return self.config.get("region") or self.config.get("zone")

    # Helper Methods

    def get_provider_info(self) -> Dict[str, Any]:
        """
        Get provider information.

        Returns:
            Provider info dictionary
        """
        return {
            "name": self.name,
            "provider_type": self.provider_type,
            "supported_services": self.get_supported_services(),
        }

    def validate_config(self) -> bool:
        """
        Validate provider configuration.

        Returns:
            True if configuration is valid

        Raises:
            ValueError: If configuration is invalid
        """
        if not self.config:
            raise ValueError("Configuration is required")

        if not self.provider_type or self.provider_type == "unknown":
            raise ValueError("provider_type is required in configuration")

        return True
