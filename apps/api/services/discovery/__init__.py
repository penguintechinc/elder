"""Cloud Auto-Discovery service for Elder - automated cloud resource discovery."""

# flake8: noqa: E501

from apps.api.services.discovery.aws_discovery import AWSDiscoveryClient
from apps.api.services.discovery.azure_discovery import AzureDiscoveryClient
from apps.api.services.discovery.base import BaseDiscoveryProvider
from apps.api.services.discovery.gcp_discovery import GCPDiscoveryClient
from apps.api.services.discovery.k8s_discovery import KubernetesDiscoveryClient
from apps.api.services.discovery.service import DiscoveryService

__all__ = [
    "BaseDiscoveryProvider",
    "AWSDiscoveryClient",
    "GCPDiscoveryClient",
    "AzureDiscoveryClient",
    "KubernetesDiscoveryClient",
    "DiscoveryService",
]
