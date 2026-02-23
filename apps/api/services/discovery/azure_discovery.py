# DEPRECATED: Discovery providers have moved to apps/worker/discovery/
# This file is retained for legacy API fallback (?legacy=true). Sunset target: v4.0.0
"""Azure cloud discovery client for Elder - IaaS focus."""

# flake8: noqa: E501


from datetime import datetime
from typing import Any, Dict, List

try:
    from azure.identity import ClientSecretCredential, DefaultAzureCredential
    from azure.mgmt.compute import ComputeManagementClient
    from azure.mgmt.network import NetworkManagementClient
    from azure.mgmt.storage import StorageManagementClient
except ImportError:
    ClientSecretCredential = DefaultAzureCredential = None
    ComputeManagementClient = StorageManagementClient = NetworkManagementClient = None

from apps.api.services.discovery.base import BaseDiscoveryProvider


class AzureDiscoveryClient(BaseDiscoveryProvider):
    """Azure cloud resource discovery implementation - IaaS services."""

    def __init__(self, config: Dict[str, Any]):
        """Initialize Azure discovery client."""
        super().__init__(config)

        if not ComputeManagementClient:
            raise ImportError(
                "azure-mgmt SDKs required. Install: pip install azure-mgmt-compute azure-mgmt-storage azure-mgmt-network"
            )

        self.subscription_id = config.get("subscription_id")
        if not self.subscription_id:
            raise ValueError("subscription_id is required for Azure discovery")

        # Initialize credentials
        if (
            config.get("tenant_id")
            and config.get("client_id")
            and config.get("client_secret")
        ):
            self.credential = ClientSecretCredential(
                tenant_id=config["tenant_id"],
                client_id=config["client_id"],
                client_secret=config["client_secret"],
            )
        else:
            self.credential = DefaultAzureCredential()

        # Initialize clients
        self.compute_client = ComputeManagementClient(
            self.credential, self.subscription_id
        )
        self.storage_client = StorageManagementClient(
            self.credential, self.subscription_id
        )
        self.network_client = NetworkManagementClient(
            self.credential, self.subscription_id
        )

    def test_connection(self) -> bool:
        """Test Azure connectivity."""
        try:
            list(self.compute_client.virtual_machines.list_all())
            return True
        except:
            return False

    def get_supported_services(self) -> List[str]:
        """Get supported Azure services."""
        return ["compute", "storage", "network"]

    def discover_all(self) -> Dict[str, Any]:
        """Discover all Azure IaaS resources."""
        start_time = datetime.utcnow()

        results = {
            "compute": self.discover_compute(),
            "storage": self.discover_storage(),
            "network": self.discover_network(),
            "database": [],  # Not implementing PaaS for now
            "serverless": [],  # Not implementing PaaS for now
        }

        resources_count = sum(len(resources) for resources in results.values())

        return {
            **results,
            "resources_count": resources_count,
            "discovery_time": datetime.utcnow(),
            "duration_seconds": (datetime.utcnow() - start_time).total_seconds(),
        }

    def discover_compute(self) -> List[Dict[str, Any]]:
        """Discover Azure VMs."""
        resources = []
        try:
            for vm in self.compute_client.virtual_machines.list_all():
                resource = self.format_resource(
                    resource_id=vm.id,
                    resource_type="azure_vm",
                    name=vm.name,
                    metadata={
                        "vm_size": (
                            vm.hardware_profile.vm_size if vm.hardware_profile else None
                        ),
                        "os_type": (
                            vm.storage_profile.os_disk.os_type
                            if vm.storage_profile and vm.storage_profile.os_disk
                            else None
                        ),
                        "location": vm.location,
                        "provisioning_state": vm.provisioning_state,
                    },
                    region=vm.location,
                    tags=vm.tags or {},
                )
                resources.append(resource)
        except:
            pass
        return resources

    def discover_storage(self) -> List[Dict[str, Any]]:
        """Discover Azure Storage Accounts."""
        resources = []
        try:
            for account in self.storage_client.storage_accounts.list():
                resource = self.format_resource(
                    resource_id=account.id,
                    resource_type="azure_storage_account",
                    name=account.name,
                    metadata={
                        "kind": account.kind.value if account.kind else None,
                        "sku_name": account.sku.name.value if account.sku else None,
                        "location": account.location,
                        "provisioning_state": (
                            account.provisioning_state.value
                            if account.provisioning_state
                            else None
                        ),
                    },
                    region=account.location,
                    tags=account.tags or {},
                )
                resources.append(resource)
        except:
            pass
        return resources

    def discover_network(self) -> List[Dict[str, Any]]:
        """Discover Azure Virtual Networks."""
        resources = []
        try:
            for vnet in self.network_client.virtual_networks.list_all():
                resource = self.format_resource(
                    resource_id=vnet.id,
                    resource_type="azure_vnet",
                    name=vnet.name,
                    metadata={
                        "address_space": (
                            vnet.address_space.address_prefixes
                            if vnet.address_space
                            else []
                        ),
                        "location": vnet.location,
                        "provisioning_state": vnet.provisioning_state,
                        "subnets_count": len(vnet.subnets) if vnet.subnets else 0,
                    },
                    region=vnet.location,
                    tags=vnet.tags or {},
                )
                resources.append(resource)
        except:
            pass
        return resources

    def discover_databases(self) -> List[Dict[str, Any]]:
        """Not implemented - focusing on IaaS."""
        return []

    def discover_serverless(self) -> List[Dict[str, Any]]:
        """Not implemented - focusing on IaaS."""
        return []
