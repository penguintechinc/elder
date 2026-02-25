"""Kubernetes cluster discovery client for Elder - IaaS focus."""

# flake8: noqa: E501


from datetime import datetime
from typing import Any, Dict, List

try:
    from kubernetes import client, config
except ImportError:
    client = config = None

from apps.worker.discovery.base import BaseDiscoveryProvider


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
        self.networking_v1 = client.NetworkingV1Api()
        self.custom_objects = client.CustomObjectsApi()
        self.namespace = config_dict.get("namespace", "default")

    @staticmethod
    def _parse_cpu(cpu_str: str) -> float:
        """Parse K8s CPU string to float cores. '500m' -> 0.5, '2' -> 2.0"""
        if not cpu_str:
            return 0.0
        cpu_str = str(cpu_str)
        if cpu_str.endswith("m"):
            return float(cpu_str[:-1]) / 1000.0
        return float(cpu_str)

    @staticmethod
    def _parse_memory(mem_str: str) -> int:
        """Parse K8s memory string to bytes. '512Mi' -> 536870912"""
        if not mem_str:
            return 0
        mem_str = str(mem_str)
        units = {"Ki": 1024, "Mi": 1024**2, "Gi": 1024**3, "Ti": 1024**4}
        for suffix, multiplier in units.items():
            if mem_str.endswith(suffix):
                return int(float(mem_str[: -len(suffix)]) * multiplier)
        if mem_str.endswith("k"):
            return int(float(mem_str[:-1]) * 1000)
        if mem_str.endswith("M"):
            return int(float(mem_str[:-1]) * 1000**2)
        if mem_str.endswith("G"):
            return int(float(mem_str[:-1]) * 1000**3)
        return int(mem_str)

    def test_connection(self) -> bool:
        """Test Kubernetes connectivity."""
        try:
            self.core_v1.list_namespace()
            return True
        except:
            return False

    def get_supported_services(self) -> List[str]:
        """Get supported Kubernetes resources."""
        return ["nodes", "pods", "services", "deployments", "persistent_volumes", "service_accounts", "ingress", "pvcs", "secrets", "certificates", "cni"]

    def discover_all(self) -> Dict[str, Any]:
        """Discover all Kubernetes resources."""
        start_time = datetime.utcnow()

        results = {
            "compute": self.discover_compute(),
            "storage": self.discover_storage(),
            "network": self.discover_network(),
            "iam": self._discover_service_accounts(),
            "secrets": self.discover_secrets(),
            "certificates": self.discover_cert_manager_certs(),
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
                        "architecture": (
                            node.status.node_info.architecture
                            if node.status.node_info
                            else None
                        ),
                        "kernel_version": (
                            node.status.node_info.kernel_version
                            if node.status.node_info
                            else None
                        ),
                        "container_runtime": (
                            node.status.node_info.container_runtime_version
                            if node.status.node_info
                            else None
                        ),
                        "taints": (
                            [
                                {"key": t.key, "value": t.value, "effect": t.effect}
                                for t in node.spec.taints
                            ]
                            if node.spec and node.spec.taints
                            else []
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
                        "resource_requests": {
                            "cpu": sum(
                                self._parse_cpu(c.resources.requests.get("cpu", "0"))
                                for c in (pod.spec.containers or [])
                                if c.resources and c.resources.requests
                            ),
                            "memory": sum(
                                self._parse_memory(c.resources.requests.get("memory", "0"))
                                for c in (pod.spec.containers or [])
                                if c.resources and c.resources.requests
                            ),
                        } if pod.spec and pod.spec.containers else {"cpu": 0, "memory": 0},
                        "resource_limits": {
                            "cpu": sum(
                                self._parse_cpu(c.resources.limits.get("cpu", "0"))
                                for c in (pod.spec.containers or [])
                                if c.resources and c.resources.limits
                            ),
                            "memory": sum(
                                self._parse_memory(c.resources.limits.get("memory", "0"))
                                for c in (pod.spec.containers or [])
                                if c.resources and c.resources.limits
                            ),
                        } if pod.spec and pod.spec.containers else {"cpu": 0, "memory": 0},
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

        # Discover PVCs
        resources.extend(self.discover_pvcs())

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

        # Discover Ingress resources
        resources.extend(self.discover_ingress())

        # Discover CNI
        resources.extend(self.discover_cni())

        return resources

    def discover_ingress(self) -> List[Dict[str, Any]]:
        """Discover Kubernetes Ingress resources."""
        resources = []

        try:
            ingresses = self.networking_v1.list_ingress_for_all_namespaces()
            for ing in ingresses.items:
                paths = []
                backend_services = []
                tls_hosts = []

                if ing.spec and ing.spec.tls:
                    for tls in ing.spec.tls:
                        if tls.hosts:
                            tls_hosts.extend(tls.hosts)

                if ing.spec and ing.spec.rules:
                    for rule in ing.spec.rules:
                        host = rule.host or "*"
                        if rule.http and rule.http.paths:
                            for p in rule.http.paths:
                                svc_name = None
                                svc_port = None
                                if p.backend and p.backend.service:
                                    svc_name = p.backend.service.name
                                    if p.backend.service.port:
                                        svc_port = (
                                            p.backend.service.port.number
                                            or p.backend.service.port.name
                                        )
                                paths.append({
                                    "host": host,
                                    "path": p.path or "/",
                                    "path_type": p.path_type,
                                    "service": svc_name,
                                    "port": svc_port,
                                })
                                if svc_name and svc_name not in backend_services:
                                    backend_services.append(svc_name)

                resource = self.format_resource(
                    resource_id=ing.metadata.uid,
                    resource_type="k8s_ingress",
                    name=ing.metadata.name,
                    metadata={
                        "namespace": ing.metadata.namespace,
                        "ingress_class": (
                            ing.spec.ingress_class_name if ing.spec else None
                        ),
                        "paths": paths,
                        "tls_enabled": bool(tls_hosts),
                        "tls_hosts": tls_hosts,
                        "backend_services": backend_services,
                    },
                    region="N/A",
                    tags=ing.metadata.labels or {},
                )
                resources.append(resource)
        except Exception:
            pass

        return resources

    def discover_pvcs(self) -> List[Dict[str, Any]]:
        """Discover Kubernetes PersistentVolumeClaims."""
        resources = []

        try:
            pvcs = self.core_v1.list_persistent_volume_claim_for_all_namespaces()
            for pvc in pvcs.items:
                resource = self.format_resource(
                    resource_id=pvc.metadata.uid,
                    resource_type="k8s_pvc",
                    name=pvc.metadata.name,
                    metadata={
                        "namespace": pvc.metadata.namespace,
                        "volume_name": (
                            pvc.spec.volume_name if pvc.spec else None
                        ),
                        "storage_class": (
                            pvc.spec.storage_class_name if pvc.spec else None
                        ),
                        "access_modes": (
                            pvc.spec.access_modes if pvc.spec else []
                        ),
                        "capacity": (
                            pvc.status.capacity.get("storage")
                            if pvc.status and pvc.status.capacity
                            else None
                        ),
                        "phase": (
                            pvc.status.phase if pvc.status else None
                        ),
                    },
                    region="N/A",
                    tags=pvc.metadata.labels or {},
                )
                resources.append(resource)
        except Exception:
            pass

        return resources

    def discover_secrets(self) -> List[Dict[str, Any]]:
        """Discover Kubernetes Secrets (metadata only, NEVER values)."""
        resources = []

        try:
            secrets = self.core_v1.list_secret_for_all_namespaces()
            for secret in secrets.items:
                # Only store key names, NEVER actual values
                key_names = list(secret.data.keys()) if secret.data else []

                resource = self.format_resource(
                    resource_id=secret.metadata.uid,
                    resource_type="k8s_secret",
                    name=secret.metadata.name,
                    metadata={
                        "namespace": secret.metadata.namespace,
                        "type": secret.type,
                        "keys": key_names,
                        "annotations": (
                            dict(secret.metadata.annotations)
                            if secret.metadata.annotations
                            else {}
                        ),
                    },
                    region="N/A",
                    tags=secret.metadata.labels or {},
                )
                resources.append(resource)
        except Exception:
            pass

        return resources

    def discover_cert_manager_certs(self) -> List[Dict[str, Any]]:
        """Discover cert-manager Certificate resources."""
        resources = []

        try:
            certs = self.custom_objects.list_cluster_custom_object(
                "cert-manager.io", "v1", "certificates"
            )
            for cert in certs.get("items", []):
                metadata = cert.get("metadata", {})
                spec = cert.get("spec", {})
                status = cert.get("status", {})

                conditions = []
                for cond in status.get("conditions", []):
                    conditions.append({
                        "type": cond.get("type"),
                        "status": cond.get("status"),
                        "reason": cond.get("reason"),
                    })

                resource = self.format_resource(
                    resource_id=metadata.get("uid", metadata.get("name")),
                    resource_type="cert_manager_certificate",
                    name=metadata.get("name", ""),
                    metadata={
                        "namespace": metadata.get("namespace"),
                        "common_name": spec.get("commonName"),
                        "dns_names": spec.get("dnsNames", []),
                        "issuer_ref": spec.get("issuerRef", {}),
                        "secret_name": spec.get("secretName"),
                        "not_after": status.get("notAfter"),
                        "conditions": conditions,
                    },
                    region="N/A",
                    tags=metadata.get("labels", {}),
                )
                resources.append(resource)
        except Exception:
            pass

        return resources

    def discover_cni(self) -> List[Dict[str, Any]]:
        """Detect the CNI plugin running in the cluster."""
        resources = []

        try:
            # Check kube-system configmaps and pods to detect CNI
            cni_type = None
            cni_version = None

            # Check kube-system pods for CNI indicators
            pods = self.core_v1.list_namespaced_pod("kube-system")
            for pod in pods.items:
                pod_name = pod.metadata.name.lower()
                if "calico" in pod_name:
                    cni_type = "calico"
                elif "flannel" in pod_name:
                    cni_type = "flannel"
                elif "cilium" in pod_name:
                    cni_type = "cilium"
                elif "weave" in pod_name:
                    cni_type = "weave"
                elif "canal" in pod_name:
                    cni_type = "canal"

                if cni_type:
                    # Try to extract version from image tag
                    if pod.spec and pod.spec.containers:
                        for c in pod.spec.containers:
                            if c.image and ":" in c.image:
                                cni_version = c.image.split(":")[-1]
                    break

            if cni_type:
                resource = self.format_resource(
                    resource_id=f"cni-{cni_type}",
                    resource_type="k8s_cni",
                    name=f"{cni_type} CNI",
                    metadata={
                        "cni_type": cni_type,
                        "version": cni_version,
                    },
                    region="N/A",
                    tags={"cni": cni_type},
                )
                resources.append(resource)
        except Exception:
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
