"""Kubernetes connector for syncing K8S resources to Elder (v1.2.1)."""

# flake8: noqa: E501


import time
from typing import Dict, Optional

from kubernetes import client, config
from kubernetes.client.rest import ApiException

from apps.worker.config.settings import settings
from apps.worker.connectors.base import BaseConnector, SyncResult
from apps.worker.utils.elder_client import ElderAPIClient, Entity, Organization


class KubernetesConnector(BaseConnector):
    """Connector for Kubernetes resources."""

    def __init__(self):
        """Initialize Kubernetes connector."""
        super().__init__("kubernetes")
        self.elder_client: Optional[ElderAPIClient] = None
        self.k8s_core_v1: Optional[client.CoreV1Api] = None
        self.k8s_apps_v1: Optional[client.AppsV1Api] = None
        self.organization_cache: Dict[str, int] = (
            {}
        )  # Map cluster/namespace to Elder org ID
        self.cluster_name: Optional[str] = None

    async def connect(self) -> None:
        """Establish connection to Kubernetes and Elder API."""
        self.logger.info("Connecting to Kubernetes cluster")

        try:
            # Load kubeconfig (in-cluster or from file)
            try:
                config.load_incluster_config()
                self.cluster_name = "in-cluster"
                self.logger.info("Loaded in-cluster Kubernetes configuration")
            except config.ConfigException:
                config.load_kube_config()
                # Extract cluster name from kubeconfig
                contexts, active_context = config.list_kube_config_contexts()
                self.cluster_name = (
                    active_context["name"] if active_context else "default"
                )
                self.logger.info("Loaded kubeconfig", cluster=self.cluster_name)

            # Initialize K8S API clients
            self.k8s_core_v1 = client.CoreV1Api()
            self.k8s_apps_v1 = client.AppsV1Api()

            # Test connection
            version = self.k8s_core_v1.get_api_resources()
            self.logger.info("Kubernetes connection established")

        except Exception as e:
            self.logger.error("Failed to connect to Kubernetes", error=str(e))
            raise

        # Initialize Elder API client
        self.elder_client = ElderAPIClient()
        await self.elder_client.connect()

        self.logger.info("Kubernetes connector connected successfully")

    async def disconnect(self) -> None:
        """Disconnect from Kubernetes and Elder API."""
        if self.elder_client:
            await self.elder_client.close()
        self.organization_cache.clear()
        self.logger.info("Kubernetes connector disconnected")

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
        # Check cache
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

    async def _sync_namespaces(self, cluster_org_id: int) -> tuple[int, int]:
        """
        Sync Kubernetes namespaces.

        Args:
            cluster_org_id: Elder organization ID for the cluster

        Returns:
            Tuple of (created_count, updated_count)
        """
        created = 0
        updated = 0

        try:
            namespaces = self.k8s_core_v1.list_namespace()

            for ns in namespaces.items:
                ns_name = ns.metadata.name
                ns_status = ns.status.phase if ns.status else "Unknown"

                # Get status metadata
                status_metadata = {"status": ns_status, "timestamp": int(time.time())}

                # Get resource quotas if present
                resource_quota = {}
                try:
                    quotas = self.k8s_core_v1.list_namespaced_resource_quota(ns_name)
                    if quotas.items:
                        quota = quotas.items[0]
                        if quota.status and quota.status.hard:
                            resource_quota = {
                                k: str(v) for k, v in quota.status.hard.items()
                            }
                except ApiException:
                    pass

                entity = Entity(
                    name=f"Namespace: {ns_name}",
                    entity_type="network",
                    sub_type="namespace",
                    organization_id=cluster_org_id,
                    description=f"Kubernetes namespace in {self.cluster_name}",
                    attributes={
                        "namespace": ns_name,
                        "cluster": self.cluster_name,
                        "uid": ns.metadata.uid,
                        "creation_timestamp": (
                            ns.metadata.creation_timestamp.isoformat()
                            if ns.metadata.creation_timestamp
                            else None
                        ),
                        "labels": ns.metadata.labels or {},
                        "annotations": ns.metadata.annotations or {},
                        "resource_quota": resource_quota,
                        "provider": "kubernetes",
                    },
                    status_metadata=status_metadata,
                    tags=["kubernetes", "namespace", self.cluster_name],
                    is_active=ns_status == "Active",
                )

                # Check if entity already exists
                existing = await self.elder_client.list_entities(
                    organization_id=cluster_org_id,
                    entity_type="network",
                )

                found = None
                for item in existing.get("items", []):
                    attrs = item.get("attributes", {})
                    if (
                        attrs.get("namespace") == ns_name
                        and attrs.get("cluster") == self.cluster_name
                    ):
                        found = item
                        break

                if found:
                    await self.elder_client.update_entity(found["id"], entity)
                    updated += 1
                else:
                    await self.elder_client.create_entity(entity)
                    created += 1

        except ApiException as e:
            self.logger.error("Failed to sync namespaces", error=str(e))

        return created, updated

    async def _sync_secrets(self, cluster_org_id: int) -> tuple[int, int]:
        """
        Sync Kubernetes secrets (metadata only, not secret values).

        Args:
            cluster_org_id: Elder organization ID for the cluster

        Returns:
            Tuple of (created_count, updated_count)
        """
        created = 0
        updated = 0

        try:
            secrets = self.k8s_core_v1.list_secret_for_all_namespaces()

            for secret in secrets.items:
                secret_name = secret.metadata.name
                namespace = secret.metadata.namespace

                # Secrets are always "Available" (no status concept)
                status_metadata = {"status": "Available", "timestamp": int(time.time())}

                # Count secret keys without exposing values
                secret_keys = list(secret.data.keys()) if secret.data else []

                entity = Entity(
                    name=f"Secret: {namespace}/{secret_name}",
                    entity_type="security",
                    sub_type="config",  # Secrets are security configurations
                    organization_id=cluster_org_id,
                    description=f"Kubernetes secret in {self.cluster_name}/{namespace}",
                    attributes={
                        "secret_name": secret_name,
                        "namespace": namespace,
                        "cluster": self.cluster_name,
                        "uid": secret.metadata.uid,
                        "type": secret.type,
                        "secret_key_count": len(secret_keys),
                        "secret_keys": secret_keys,  # Key names only, no values
                        "creation_timestamp": (
                            secret.metadata.creation_timestamp.isoformat()
                            if secret.metadata.creation_timestamp
                            else None
                        ),
                        "labels": secret.metadata.labels or {},
                        "annotations": secret.metadata.annotations or {},
                        "provider": "kubernetes",
                    },
                    status_metadata=status_metadata,
                    tags=["kubernetes", "secret", namespace, self.cluster_name],
                    is_active=True,
                )

                # Check if entity already exists
                existing = await self.elder_client.list_entities(
                    organization_id=cluster_org_id,
                    entity_type="security",
                )

                found = None
                for item in existing.get("items", []):
                    attrs = item.get("attributes", {})
                    if (
                        attrs.get("secret_name") == secret_name
                        and attrs.get("namespace") == namespace
                        and attrs.get("cluster") == self.cluster_name
                    ):
                        found = item
                        break

                if found:
                    await self.elder_client.update_entity(found["id"], entity)
                    updated += 1
                else:
                    await self.elder_client.create_entity(entity)
                    created += 1

        except ApiException as e:
            self.logger.error("Failed to sync secrets", error=str(e))

        return created, updated

    async def _sync_pods(self, cluster_org_id: int) -> tuple[int, int]:
        """
        Sync Kubernetes pods (containers).

        Args:
            cluster_org_id: Elder organization ID for the cluster

        Returns:
            Tuple of (created_count, updated_count)
        """
        created = 0
        updated = 0

        try:
            pods = self.k8s_core_v1.list_pod_for_all_namespaces()

            for pod in pods.items:
                pod_name = pod.metadata.name
                namespace = pod.metadata.namespace
                pod_phase = pod.status.phase if pod.status else "Unknown"

                # Get status metadata
                status_metadata = {"status": pod_phase, "timestamp": int(time.time())}

                # Get container information
                containers = []
                for container in pod.spec.containers or []:
                    containers.append(
                        {
                            "name": container.name,
                            "image": container.image,
                            "ports": [
                                {
                                    "containerPort": p.container_port,
                                    "protocol": p.protocol,
                                }
                                for p in (container.ports or [])
                            ],
                        }
                    )

                entity = Entity(
                    name=f"Pod: {namespace}/{pod_name}",
                    entity_type="compute",
                    sub_type="kubernetes_node",  # Pods are compute nodes
                    organization_id=cluster_org_id,
                    description=f"Kubernetes pod in {self.cluster_name}/{namespace}",
                    attributes={
                        "pod_name": pod_name,
                        "namespace": namespace,
                        "cluster": self.cluster_name,
                        "uid": pod.metadata.uid,
                        "node_name": pod.spec.node_name if pod.spec else None,
                        "pod_ip": pod.status.pod_ip if pod.status else None,
                        "host_ip": pod.status.host_ip if pod.status else None,
                        "phase": pod_phase,
                        "containers": containers,
                        "restart_policy": pod.spec.restart_policy if pod.spec else None,
                        "service_account": (
                            pod.spec.service_account if pod.spec else None
                        ),
                        "creation_timestamp": (
                            pod.metadata.creation_timestamp.isoformat()
                            if pod.metadata.creation_timestamp
                            else None
                        ),
                        "labels": pod.metadata.labels or {},
                        "annotations": pod.metadata.annotations or {},
                        "provider": "kubernetes",
                    },
                    status_metadata=status_metadata,
                    tags=[
                        "kubernetes",
                        "pod",
                        "container",
                        namespace,
                        self.cluster_name,
                    ],
                    is_active=pod_phase == "Running",
                )

                # Check if entity already exists
                existing = await self.elder_client.list_entities(
                    organization_id=cluster_org_id,
                    entity_type="compute",
                )

                found = None
                for item in existing.get("items", []):
                    attrs = item.get("attributes", {})
                    if (
                        attrs.get("pod_name") == pod_name
                        and attrs.get("namespace") == namespace
                        and attrs.get("cluster") == self.cluster_name
                    ):
                        found = item
                        break

                if found:
                    await self.elder_client.update_entity(found["id"], entity)
                    updated += 1
                else:
                    await self.elder_client.create_entity(entity)
                    created += 1

        except ApiException as e:
            self.logger.error("Failed to sync pods", error=str(e))

        return created, updated

    async def _sync_pvcs(self, cluster_org_id: int) -> tuple[int, int]:
        """
        Sync Kubernetes Persistent Volume Claims.

        Args:
            cluster_org_id: Elder organization ID for the cluster

        Returns:
            Tuple of (created_count, updated_count)
        """
        created = 0
        updated = 0

        try:
            pvcs = self.k8s_core_v1.list_persistent_volume_claim_for_all_namespaces()

            for pvc in pvcs.items:
                pvc_name = pvc.metadata.name
                namespace = pvc.metadata.namespace
                pvc_phase = pvc.status.phase if pvc.status else "Unknown"

                # Get status metadata
                status_metadata = {"status": pvc_phase, "timestamp": int(time.time())}

                # Get capacity
                capacity = None
                if pvc.status and pvc.status.capacity:
                    capacity = pvc.status.capacity.get("storage")

                entity = Entity(
                    name=f"PVC: {namespace}/{pvc_name}",
                    entity_type="storage",
                    sub_type="virtual_disk",
                    organization_id=cluster_org_id,
                    description=f"Kubernetes PVC in {self.cluster_name}/{namespace}",
                    attributes={
                        "pvc_name": pvc_name,
                        "namespace": namespace,
                        "cluster": self.cluster_name,
                        "uid": pvc.metadata.uid,
                        "volume_name": pvc.spec.volume_name if pvc.spec else None,
                        "storage_class": (
                            pvc.spec.storage_class_name if pvc.spec else None
                        ),
                        "access_modes": pvc.spec.access_modes if pvc.spec else [],
                        "requested_storage": (
                            str(pvc.spec.resources.requests.get("storage"))
                            if pvc.spec
                            and pvc.spec.resources
                            and pvc.spec.resources.requests
                            else None
                        ),
                        "capacity": str(capacity) if capacity else None,
                        "phase": pvc_phase,
                        "creation_timestamp": (
                            pvc.metadata.creation_timestamp.isoformat()
                            if pvc.metadata.creation_timestamp
                            else None
                        ),
                        "labels": pvc.metadata.labels or {},
                        "annotations": pvc.metadata.annotations or {},
                        "provider": "kubernetes",
                    },
                    status_metadata=status_metadata,
                    tags=["kubernetes", "pvc", "storage", namespace, self.cluster_name],
                    is_active=pvc_phase == "Bound",
                )

                # Check if entity already exists
                existing = await self.elder_client.list_entities(
                    organization_id=cluster_org_id,
                    entity_type="storage",
                )

                found = None
                for item in existing.get("items", []):
                    attrs = item.get("attributes", {})
                    if (
                        attrs.get("pvc_name") == pvc_name
                        and attrs.get("namespace") == namespace
                        and attrs.get("cluster") == self.cluster_name
                    ):
                        found = item
                        break

                if found:
                    await self.elder_client.update_entity(found["id"], entity)
                    updated += 1
                else:
                    await self.elder_client.create_entity(entity)
                    created += 1

        except ApiException as e:
            self.logger.error("Failed to sync PVCs", error=str(e))

        return created, updated

    async def sync(self) -> SyncResult:
        """
        Synchronize Kubernetes resources to Elder.

        Returns:
            SyncResult with statistics
        """
        result = SyncResult(connector_name=self.name)
        self.logger.info("Starting Kubernetes sync", cluster=self.cluster_name)

        try:
            # Create Kubernetes root organization
            k8s_org_id = await self._get_or_create_organization(
                f"Kubernetes: {self.cluster_name}",
                f"Kubernetes cluster {self.cluster_name}",
            )
            result.organizations_created += 1

            # Sync namespaces
            ns_created, ns_updated = await self._sync_namespaces(k8s_org_id)
            result.entities_created += ns_created
            result.entities_updated += ns_updated

            # Sync secrets (metadata only)
            secret_created, secret_updated = await self._sync_secrets(k8s_org_id)
            result.entities_created += secret_created
            result.entities_updated += secret_updated

            # Sync pods (containers)
            pod_created, pod_updated = await self._sync_pods(k8s_org_id)
            result.entities_created += pod_created
            result.entities_updated += pod_updated

            # Sync PVCs
            pvc_created, pvc_updated = await self._sync_pvcs(k8s_org_id)
            result.entities_created += pvc_created
            result.entities_updated += pvc_updated

            self.logger.info(
                "Kubernetes sync completed",
                cluster=self.cluster_name,
                total_ops=result.total_operations,
                orgs_created=result.organizations_created,
                entities_created=result.entities_created,
                entities_updated=result.entities_updated,
            )

        except Exception as e:
            error_msg = f"Kubernetes sync failed: {str(e)}"
            self.logger.error(error_msg, exc_info=True)
            result.errors.append(error_msg)

        return result

    async def health_check(self) -> bool:
        """Check Kubernetes connectivity."""
        try:
            if self.k8s_core_v1:
                self.k8s_core_v1.get_api_resources()
                return True
            return False
        except Exception as e:
            self.logger.warning("Kubernetes health check failed", error=str(e))
            return False
