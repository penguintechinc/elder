"""Cloud Auto-Discovery service for Elder - automated cloud resource discovery."""

# flake8: noqa: E501


from apps.worker.discovery.aws_discovery import AWSDiscoveryClient
from apps.worker.discovery.azure_discovery import AzureDiscoveryClient
from apps.worker.discovery.base import BaseDiscoveryProvider
from apps.worker.discovery.gcp_discovery import GCPDiscoveryClient
from apps.worker.discovery.k8s_discovery import KubernetesDiscoveryClient
from apps.worker.discovery.service import DiscoveryService

__all__ = [
    "BaseDiscoveryProvider",
    "AWSDiscoveryClient",
    "GCPDiscoveryClient",
    "AzureDiscoveryClient",
    "KubernetesDiscoveryClient",
    "DiscoveryService",
]
