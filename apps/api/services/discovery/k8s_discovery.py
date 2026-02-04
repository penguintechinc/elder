"""Kubernetes cluster discovery client for Elder - IaaS focus."""

# flake8: noqa: E501


from datetime import datetime
from typing import Any, Dict, List

try:
    from kubernetes import client, config
except ImportError:
    client = config = None

from apps.api.services.discovery.base import BaseDiscoveryProvider


class KubernetesDiscoveryClient(BaseDiscoveryProvider):
    """Kubernetes cluster resource discovery implementation."""

    def __init__(self, config_dict: Dict[str, Any]):
        """Initialize Kubernetes discovery client."""
        super().__init__(config_dict)

        if not client:
            raise ImportError("kubernetes required. Install: pip install kubernetes")

        # Load kubeconfig
        if config_dict.get("in_cluster"):
            config.load_incluster_config()
        else:
            kubeconfig_path = config_dict.get("kubeconfig_path")
            context = config_dict.get("context")
            config.load_kube_config(config_file=kubeconfig_path, context=context)

        self.core_v1 = client.CoreV1Api()
        self.apps_v1 = client.AppsV1Api()
        self.namespace = config_dict.get("namespace", "default")

    def test_connection(self) -> bool:
        """Test Kubernetes connectivity."""
        try:
            self.core_v1.list_namespace()
            return True
        except:
            return False

    def get_supported_services(self) -> List[str]:
        """Get supported Kubernetes resources."""
        return ["nodes", "pods", "services", "deployments", "persistent_volumes", "service_accounts"]

    def discover_all(self) -> Dict[str, Any]:
        """Discover all Kubernetes resources."""
        start_time = datetime.utcnow()

        results = {
            "compute": self.discover_compute(),
            "storage": self.discover_storage(),
            "network": self.discover_network(),
            "iam": self._discover_service_accounts(),
            "database": [],
            "serverless": [],
        }

        resources_count = sum(len(resources) for resources in results.values())

        return {
            **results,
            "resources_count": resources_count,
            "discovery_time": datetime.utcnow(),
            "duration_seconds": (datetime.utcnow() - start_time).total_seconds(),
        }

    def discover_compute(self) -> List[Dict[str, Any]]:
        """Discover Kubernetes nodes and pods."""
        resources = []

        # Discover nodes
        try:
            nodes = self.core_v1.list_node()
            for node in nodes.items:
                resource = self.format_resource(
                    resource_id=node.metadata.uid,
                    resource_type="k8s_node",
                    name=node.metadata.name,
                    metadata={
                        "capacity_cpu": (
                            node.status.capacity.get("cpu")
                            if node.status.capacity
                            else None
                        ),
                        "capacity_memory": (
                            node.status.capacity.get("memory")
                            if node.status.capacity
                            else None
                        ),
                        "kubelet_version": (
                            node.status.node_info.kubelet_version
                            if node.status.node_info
                            else None
                        ),
                        "os_image": (
                            node.status.node_info.os_image
                            if node.status.node_info
                            else None
                        ),
                        "conditions": (
                            [c.type for c in node.status.conditions]
                            if node.status.conditions
                            else []
                        ),
                    },
                    region="N/A",
                    tags=node.metadata.labels or {},
                )
                resources.append(resource)
        except:
            pass

        # Discover pods
        try:
            pods = self.core_v1.list_pod_for_all_namespaces()
            for pod in pods.items:
                resource = self.format_resource(
                    resource_id=pod.metadata.uid,
                    resource_type="k8s_pod",
                    name=pod.metadata.name,
                    metadata={
                        "namespace": pod.metadata.namespace,
                        "node_name": pod.spec.node_name if pod.spec else None,
                        "phase": pod.status.phase if pod.status else None,
                        "pod_ip": pod.status.pod_ip if pod.status else None,
                        "containers_count": (
                            len(pod.spec.containers)
                            if pod.spec and pod.spec.containers
                            else 0
                        ),
                        "containers": (
                            [
                                {
                                    "name": c.name,
                                    "image": c.image,
                                }
                                for c in pod.spec.containers
                            ]
                            if pod.spec and pod.spec.containers
                            else []
                        ),
                    },
                    region="N/A",
                    tags=pod.metadata.labels or {},
                )
                resources.append(resource)
        except:
            pass

        return resources

    def discover_storage(self) -> List[Dict[str, Any]]:
        """Discover Persistent Volumes."""
        resources = []

        try:
            pvs = self.core_v1.list_persistent_volume()
            for pv in pvs.items:
                resource = self.format_resource(
                    resource_id=pv.metadata.uid,
                    resource_type="k8s_persistent_volume",
                    name=pv.metadata.name,
                    metadata={
                        "capacity": (
                            pv.spec.capacity.get("storage")
                            if pv.spec and pv.spec.capacity
                            else None
                        ),
                        "access_modes": pv.spec.access_modes if pv.spec else [],
                        "storage_class": (
                            pv.spec.storage_class_name if pv.spec else None
                        ),
                        "phase": pv.status.phase if pv.status else None,
                    },
                    region="N/A",
                    tags=pv.metadata.labels or {},
                )
                resources.append(resource)
        except:
            pass

        return resources

    def discover_network(self) -> List[Dict[str, Any]]:
        """Discover Kubernetes services."""
        resources = []

        try:
            services = self.core_v1.list_service_for_all_namespaces()
            for svc in services.items:
                resource = self.format_resource(
                    resource_id=svc.metadata.uid,
                    resource_type="k8s_service",
                    name=svc.metadata.name,
                    metadata={
                        "namespace": svc.metadata.namespace,
                        "type": svc.spec.type if svc.spec else None,
                        "cluster_ip": svc.spec.cluster_ip if svc.spec else None,
                        "ports": (
                            [
                                {"port": p.port, "protocol": p.protocol}
                                for p in svc.spec.ports
                            ]
                            if svc.spec and svc.spec.ports
                            else []
                        ),
                    },
                    region="N/A",
                    tags=svc.metadata.labels or {},
                )
                resources.append(resource)
        except:
            pass

        return resources

    def _discover_service_accounts(self) -> List[Dict[str, Any]]:
        """Discover Kubernetes service accounts."""
        resources = []

        try:
            service_accounts = self.core_v1.list_service_account_for_all_namespaces()
            for sa in service_accounts.items:
                resource = self.format_resource(
                    resource_id=sa.metadata.uid,
                    resource_type="k8s_service_account",
                    name=sa.metadata.name,
                    metadata={
                        "namespace": sa.metadata.namespace,
                        "automount_token": (
                            sa.automount_service_account_token
                            if hasattr(sa, "automount_service_account_token")
                            else None
                        ),
                        "secrets": (
                            [s.name for s in sa.secrets]
                            if sa.secrets
                            else []
                        ),
                    },
                    region="N/A",
                    tags=sa.metadata.labels or {},
                )
                resources.append(resource)
        except Exception:
            pass

        return resources

    def discover_databases(self) -> List[Dict[str, Any]]:
        """Not applicable for Kubernetes."""
        return []

    def discover_serverless(self) -> List[Dict[str, Any]]:
        """Not applicable for Kubernetes."""
        return []
