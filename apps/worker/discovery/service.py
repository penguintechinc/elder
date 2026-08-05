"""Discovery service - business logic layer for cloud resource discovery."""

# flake8: noqa: E501

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# Canonical edge types allowed in dependencies table.
CANONICAL_EDGE_TYPES: frozenset[str] = frozenset(
    {
        "in_network",
        "in_subnet",
        "uses_security_group",
        "attached_to",
        "routes_to",
        "assumes_role",
        "in_resource_group",
        "bound_to",
        "runs_on",
        "runs_in",
        "manages",
        "discovered_from",
    }
)

from apps.worker.discovery.aws_discovery import AWSDiscoveryClient
from apps.worker.discovery.azure_discovery import AzureDiscoveryClient
from apps.worker.discovery.base import BaseDiscoveryProvider
from apps.worker.discovery.gcp_discovery import GCPDiscoveryClient
from apps.worker.discovery.k8s_discovery import KubernetesDiscoveryClient
from apps.worker.discovery.vultr_discovery import VultrDiscoveryClient
from shared.certificates import calculate_certificate_status


class DiscoveryService:
    """Service layer for cloud discovery operations."""

    def __init__(self, db):
        """
        Initialize DiscoveryService.

        Args:
            db: PyDAL database instance
        """
        self.db = db

    def _get_discovery_client(self, job_id: int) -> BaseDiscoveryProvider:
        """
        Get configured discovery client for a job.

        Args:
            job_id: Discovery job ID

        Returns:
            Configured discovery client instance

        Raises:
            Exception: If job not found or invalid provider type
        """
        job = self.db.discovery_jobs[job_id]

        if not job:
            raise Exception(f"Discovery job not found: {job_id}")

        config = job.as_dict()
        config["provider_type"] = job.provider

        # Add config_json fields to config
        if job.config_json:
            config.update(job.config_json)

        # Create appropriate client based on provider
        provider_type = job.provider.lower()

        if provider_type == "aws":
            return AWSDiscoveryClient(config)
        elif provider_type == "gcp":
            return GCPDiscoveryClient(config)
        elif provider_type == "azure":
            return AzureDiscoveryClient(config)
        elif provider_type == "kubernetes":
            return KubernetesDiscoveryClient(config)
        elif provider_type == "vultr":
            return VultrDiscoveryClient(config)
        else:
            raise Exception(f"Unsupported provider type: {provider_type}")

    # Discovery Job Management

    def list_jobs(
        self,
        provider: Optional[str] = None,
        enabled: Optional[bool] = None,
        organization_id: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """List all discovery jobs with optional filters."""
        query = self.db.discovery_jobs.id > 0

        if provider:
            query &= self.db.discovery_jobs.provider == provider

        if enabled is not None:
            query &= self.db.discovery_jobs.enabled == enabled

        if organization_id:
            query &= self.db.discovery_jobs.organization_id == organization_id

        jobs = self.db(query).select(orderby=self.db.discovery_jobs.name)

        return [self._sanitize_job(j.as_dict()) for j in jobs]

    def get_job(self, job_id: int) -> Dict[str, Any]:
        """Get discovery job details."""
        job = self.db.discovery_jobs[job_id]

        if not job:
            raise Exception(f"Discovery job not found: {job_id}")

        return self._sanitize_job(job.as_dict())

    def create_job(
        self,
        name: str,
        provider: str,
        config: Dict[str, Any],
        organization_id: int = None,  # Optional - stored in config for now
        schedule_interval: Optional[int] = None,
        description: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Create a new discovery job."""
        # Validate provider type
        valid_providers = [
            "aws",
            "gcp",
            "azure",
            "kubernetes",
            "network",
            "http_screenshot",
            "banner",
        ]
        if provider.lower() not in valid_providers:
            raise Exception(
                f"Invalid provider: {provider}. Must be one of {valid_providers}"
            )

        # Store organization_id and description in config for now
        # (until schema migration adds these columns)
        job_config = dict(config)
        if organization_id:
            job_config["_organization_id"] = organization_id
        if description:
            job_config["_description"] = description

        # Create job
        now = datetime.now(timezone.utc)
        job_id = self.db.discovery_jobs.insert(
            name=name,
            provider=provider.lower(),
            enabled=True,
            config_json=job_config,
            schedule_interval=schedule_interval or 3600,
            created_at=now,
            updated_at=now,
        )

        self.db.commit()

        return self.get_job(job_id)

    def update_job(
        self,
        job_id: int,
        name: Optional[str] = None,
        config: Optional[Dict[str, Any]] = None,
        schedule_interval: Optional[int] = None,
        description: Optional[str] = None,
        enabled: Optional[bool] = None,
    ) -> Dict[str, Any]:
        """Update discovery job configuration."""
        job = self.db.discovery_jobs[job_id]

        if not job:
            raise Exception(f"Discovery job not found: {job_id}")

        update_data = {}
        if name is not None:
            update_data["name"] = name
        if config is not None:
            update_data["config_json"] = config
        if schedule_interval is not None:
            update_data["schedule_interval"] = schedule_interval
        if description is not None:
            update_data["description"] = description
        if enabled is not None:
            update_data["enabled"] = enabled

        if update_data:
            self.db(self.db.discovery_jobs.id == job_id).update(**update_data)
            self.db.commit()

        return self.get_job(job_id)

    def delete_job(self, job_id: int) -> Dict[str, Any]:
        """Delete a discovery job."""
        job = self.db.discovery_jobs[job_id]

        if not job:
            raise Exception(f"Discovery job not found: {job_id}")

        # Delete associated history records
        self.db(self.db.discovery_history.job_id == job_id).delete()

        # Delete job
        self.db(self.db.discovery_jobs.id == job_id).delete()
        self.db.commit()

        return {"message": "Discovery job deleted successfully"}

    def test_job(self, job_id: int) -> Dict[str, Any]:
        """Test discovery job connectivity."""
        try:
            client = self._get_discovery_client(job_id)
            success = client.test_connection()

            result = {
                "job_id": job_id,
                "success": success,
                "tested_at": datetime.now(timezone.utc).isoformat(),
            }

            # Add auth method if available (AWS client)
            if hasattr(client, "get_auth_method"):
                result["auth_method"] = client.get_auth_method()

            # Add identity info if available (AWS client)
            if hasattr(client, "get_caller_identity") and success:
                identity = client.get_caller_identity()
                if identity:
                    result["identity"] = identity

            return result

        except Exception as e:
            return {
                "job_id": job_id,
                "success": False,
                "error": str(e),
                "tested_at": datetime.now(timezone.utc).isoformat(),
            }

    # Discovery Execution

    def run_discovery(self, job_id: int) -> Dict[str, Any]:
        """Execute discovery for a job."""
        job = self.db.discovery_jobs[job_id]

        if not job:
            raise Exception(f"Discovery job not found: {job_id}")

        try:
            client = self._get_discovery_client(job_id)
            results = client.discover_all()

            # Convert datetime to string for JSON storage
            results_for_storage = dict(results)
            if "discovery_time" in results_for_storage:
                results_for_storage["discovery_time"] = results_for_storage[
                    "discovery_time"
                ].isoformat()

            # Get organization_id from config if available
            organization_id = None
            if job.config_json:
                organization_id = job.config_json.get("_organization_id")

            # Store discovered resources as entities (if organization_id
            # available) and capture edge-linkage counts so they're visible
            # in both the persisted history row and the job result below.
            edge_counts = {"edges_created": 0, "unresolved_edges": 0}
            if organization_id:
                edge_counts = self._store_discovered_resources(organization_id, results)
            results_for_storage.update(edge_counts)

            # Record discovery history
            now = datetime.now(timezone.utc)
            history_id = self.db.discovery_history.insert(
                job_id=job_id,
                started_at=now,
                completed_at=now,
                entities_discovered=results["resources_count"],
                entities_created=0,
                entities_updated=0,
                status="completed",
                results_json=results_for_storage,
            )

            # Update job's last_run timestamp
            self.db(self.db.discovery_jobs.id == job_id).update(
                last_run_at=datetime.now(timezone.utc)
            )

            self.db.commit()

            return {
                "job_id": job_id,
                "history_id": history_id,
                "resources_discovered": results["resources_count"],
                "success": True,
                "discovery_time": results["discovery_time"].isoformat(),
                **edge_counts,
            }

        except Exception as e:
            # Record failed discovery
            now = datetime.now(timezone.utc)
            self.db.discovery_history.insert(
                job_id=job_id,
                started_at=now,
                completed_at=now,
                entities_discovered=0,
                entities_created=0,
                entities_updated=0,
                status="failed",
                error_message=str(e),
            )
            self.db.commit()

            return {
                "job_id": job_id,
                "resources_discovered": 0,
                "success": False,
                "error": str(e),
            }

    def queue_job_for_worker(self, job_id: int) -> Dict[str, Any]:
        """Queue a discovery job for the worker service by setting next_run_at = now.

        The worker polls discovery_jobs and will pick this up on its next cycle.

        Args:
            job_id: Discovery job ID

        Returns:
            Status dict with job_id and queued message
        """
        job = self.db.discovery_jobs[job_id]
        if not job:
            raise Exception(f"Discovery job not found: {job_id}")

        self.db(self.db.discovery_jobs.id == job_id).update(
            next_run_at=datetime.now(timezone.utc),
        )
        self.db.commit()

        logger.info(f"Discovery job {job_id} queued for worker execution")
        return {
            "job_id": job_id,
            "success": True,
            "message": "Job queued for worker execution",
            "queued_at": datetime.now(timezone.utc).isoformat(),
        }

    def get_discovery_history(
        self, job_id: Optional[int] = None, limit: int = 50
    ) -> List[Dict[str, Any]]:
        """Get discovery execution history."""
        query = self.db.discovery_history.id > 0

        if job_id:
            query &= self.db.discovery_history.job_id == job_id

        history = self.db(query).select(
            orderby=~self.db.discovery_history.started_at, limitby=(0, limit)
        )

        return [h.as_dict() for h in history]

    # Helper Methods

    # Provider detection

    def _detect_provider_type(self, discovery_results: Dict[str, Any]) -> str:
        """Detect cloud provider from discovery results."""
        for category in ["compute", "storage", "network", "iam"]:
            resources = discovery_results.get(category, [])
            if not resources:
                continue
            rt = resources[0].get("resource_type", "")
            if rt.startswith("k8s_"):
                return "kubernetes"
            if rt.startswith("gce_") or rt.startswith("gcs_"):
                return "gcp"
            prov = resources[0].get("provider", "")
            if prov == "aws":
                return "aws"
            if prov == "gcp":
                return "gcp"
        return "unknown"

    # Provider root entity

    def _ensure_provider_root_entity(
        self, organization_id: int, provider: str, config: Dict[str, Any]
    ) -> Optional[int]:
        """Create or update a provider root entity (cluster/account/project)."""
        sub_type_map = {
            "kubernetes": "kubernetes_cluster",
            "aws": "aws_account",
            "gcp": "gcp_project",
        }
        sub_type = sub_type_map.get(provider)
        if not sub_type:
            return None

        name = config.get(
            "name",
            config.get(
                "cluster_name",
                config.get("project_id", config.get("account_id", f"{provider} root")),
            ),
        )

        existing = (
            self.db(
                (self.db.entities.organization_id == organization_id)
                & (self.db.entities.sub_type == sub_type)
                & (self.db.entities.name == name)
            )
            .select()
            .first()
        )

        if existing:
            self.db(self.db.entities.id == existing.id).update(
                updated_at=datetime.now(timezone.utc),
            )
            return existing.id

        now = datetime.now(timezone.utc)
        entity_id = self.db.entities.insert(
            name=name,
            type="compute",
            sub_type=sub_type,
            organization_id=organization_id,
            metadata={
                "provider": provider,
                "discovered_at": now.isoformat(),
            },
            created_at=now,
            updated_at=now,
        )
        return entity_id

    # Intermediate networking

    def _ensure_intermediate_networking(
        self,
        organization_id: int,
        provider: str,
        root_entity_id: Optional[int],
        discovery_results: Dict[str, Any],
    ) -> Dict[str, int]:
        """Create intermediate networking resources (VPCs, Namespaces) and return lookup maps."""
        networking_lookup = {}

        if provider == "kubernetes":
            # Extract unique namespaces from pods and services
            namespaces = set()
            for category in ["compute", "network", "iam"]:
                for resource in discovery_results.get(category, []):
                    ns = resource.get("metadata", {}).get("namespace")
                    if ns:
                        namespaces.add(ns)

            for ns_name in namespaces:
                net_id = self._upsert_networking_resource(
                    organization_id=organization_id,
                    name=ns_name,
                    network_type="namespace",
                    region=None,
                    attributes={"provider": "kubernetes"},
                    tags=["kubernetes", "namespace", "discovered"],
                )
                if net_id and root_entity_id:
                    self._upsert_network_entity_mapping(
                        net_id, root_entity_id, "attached"
                    )
                networking_lookup[f"namespace:{ns_name}"] = net_id

        elif provider == "aws":
            # Extract VPCs and subnets from network category
            vpc_lookup = {}
            for resource in discovery_results.get("network", []):
                rt = resource.get("resource_type", "")
                if rt == "vpc":
                    meta = resource.get("metadata", {})
                    vpc_id_str = meta.get("vpc_id", resource.get("resource_id", ""))
                    net_id = self._upsert_networking_resource(
                        organization_id=organization_id,
                        name=resource.get("name", vpc_id_str),
                        network_type="other",
                        region=resource.get("region"),
                        attributes={
                            "cidr_block": meta.get("cidr_block"),
                            "vpc_id": vpc_id_str,
                            "is_default": meta.get("is_default"),
                        },
                        tags=["aws", "vpc", "discovered"],
                        external_id=vpc_id_str,
                    )
                    if net_id and root_entity_id:
                        self._upsert_network_entity_mapping(
                            net_id, root_entity_id, "attached"
                        )
                    vpc_lookup[vpc_id_str] = net_id
                    networking_lookup[f"vpc:{vpc_id_str}"] = net_id

            for resource in discovery_results.get("network", []):
                rt = resource.get("resource_type", "")
                if rt == "subnet":
                    meta = resource.get("metadata", {})
                    parent_vpc = meta.get("vpc_id")
                    parent_id = vpc_lookup.get(parent_vpc) if parent_vpc else None
                    subnet_id_str = meta.get(
                        "subnet_id", resource.get("resource_id", "")
                    )
                    net_id = self._upsert_networking_resource(
                        organization_id=organization_id,
                        name=resource.get("name", ""),
                        network_type="subnet",
                        region=resource.get("region"),
                        parent_id=parent_id,
                        attributes={
                            "cidr_block": meta.get("cidr_block"),
                            "availability_zone": meta.get("availability_zone"),
                            "available_ips": meta.get("available_ips"),
                        },
                        tags=["aws", "subnet", "discovered"],
                        external_id=subnet_id_str,
                    )
                    networking_lookup[f"subnet:{subnet_id_str}"] = net_id

        elif provider == "gcp":
            for resource in discovery_results.get("network", []):
                rt = resource.get("resource_type", "")
                if rt == "vpc":
                    net_id = self._upsert_networking_resource(
                        organization_id=organization_id,
                        name=resource.get("name", ""),
                        network_type="other",
                        region="global",
                        attributes={
                            "auto_create_subnets": resource.get("metadata", {}).get(
                                "auto_create_subnets"
                            )
                        },
                        tags=["gcp", "vpc", "discovered"],
                        external_id=resource.get("resource_id"),
                    )
                    if net_id and root_entity_id:
                        self._upsert_network_entity_mapping(
                            net_id, root_entity_id, "attached"
                        )
                    networking_lookup[f"vpc:{resource.get('resource_id', '')}"] = net_id

        return networking_lookup

    def _upsert_networking_resource(
        self,
        organization_id: int,
        name: str,
        network_type: str,
        region: Optional[str] = None,
        parent_id: Optional[int] = None,
        attributes: Optional[Dict] = None,
        tags: Optional[List[str]] = None,
        external_id: Optional[str] = None,
    ) -> Optional[int]:
        """Create or update a networking_resources record."""
        try:
            existing = (
                self.db(
                    (self.db.networking_resources.organization_id == organization_id)
                    & (self.db.networking_resources.name == name)
                    & (self.db.networking_resources.network_type == network_type)
                )
                .select()
                .first()
            )

            if existing:
                update_dict = {
                    "attributes": attributes or {},
                    "updated_at": datetime.now(timezone.utc),
                }
                if external_id is not None:
                    update_dict["external_id"] = external_id
                self.db(self.db.networking_resources.id == existing.id).update(
                    **update_dict
                )
                return existing.id

            now = datetime.now(timezone.utc)
            net_id = self.db.networking_resources.insert(
                name=name,
                network_type=network_type,
                organization_id=organization_id,
                region=region,
                parent_id=parent_id,
                attributes=attributes or {},
                tags=tags or [],
                external_id=external_id,
                created_at=now,
                updated_at=now,
            )
            return net_id
        except Exception as e:
            logger.warning("Failed to upsert networking_resource %s: %s", name, e)
            return None

    def _upsert_network_entity_mapping(
        self, network_id: int, entity_id: int, relationship_type: str = "attached"
    ) -> None:
        """Link a networking_resource to an entity via network_entity_mappings."""
        try:
            existing = (
                self.db(
                    (self.db.network_entity_mappings.network_id == network_id)
                    & (self.db.network_entity_mappings.entity_id == entity_id)
                )
                .select()
                .first()
            )
            if not existing:
                now = datetime.now(timezone.utc)
                self.db.network_entity_mappings.insert(
                    network_id=network_id,
                    entity_id=entity_id,
                    relationship_type=relationship_type,
                    created_at=now,
                    updated_at=now,
                )
        except Exception as e:
            logger.warning("Failed to upsert network_entity_mapping: %s", e)

    # Domain table helpers

    def _store_as_service(
        self, organization_id: int, resource: Dict[str, Any], provider: str
    ) -> Optional[int]:
        """Store a K8s Service, Lambda function, or messaging service in the services table."""
        name = resource.get("name", "Unnamed")
        metadata = resource.get("metadata", {})
        resource_type = resource.get("resource_type", "")

        if resource_type == "k8s_service":
            deployment_method = "kubernetes"
            ports = metadata.get("ports", [])
            port = ports[0]["port"] if ports else None
            status = "active"
        elif resource_type == "lambda_function":
            deployment_method = "serverless"
            port = None
            status = "active" if metadata.get("state") == "Active" else "active"
        elif resource_type == "sqs_queue":
            deployment_method = "aws_sqs"
            port = None
            status = "active"
        elif resource_type == "sns_topic":
            deployment_method = "aws_sns"
            port = None
            status = "active"
        elif resource_type == "step_function":
            deployment_method = "aws_stepfunctions"
            port = None
            status = "active"
        elif resource_type == "eventbridge_rule":
            deployment_method = "aws_eventbridge"
            port = None
            status = "active"
        elif resource_type == "api_gateway_rest_api":
            deployment_method = "api_gateway"
            port = None
            status = "active"
        else:
            return None

        try:
            existing = (
                self.db(
                    (self.db.services.organization_id == organization_id)
                    & (self.db.services.name == name)
                    & (self.db.services.deployment_method == deployment_method)
                )
                .select()
                .first()
            )

            native_id = resource.get("external_id") or resource.get("resource_id")

            if existing:
                self.db(self.db.services.id == existing.id).update(
                    external_id=native_id,
                    updated_at=datetime.now(timezone.utc),
                )
                return existing.id

            now = datetime.now(timezone.utc)
            svc_id = self.db.services.insert(
                name=name,
                tenant_id=self._tenant_for_org(organization_id),
                organization_id=organization_id,
                deployment_method=deployment_method,
                port=port,
                status=status,
                is_public=False,
                tags=[provider, "discovered"],
                notes=f"Discovered from {provider} discovery",
                external_id=native_id,
                created_at=now,
                updated_at=now,
            )
            return svc_id
        except Exception as e:
            logger.warning("Failed to store service %s: %s", name, e)
            return None

    def _store_as_data_store(
        self, organization_id: int, resource: Dict[str, Any], provider: str
    ) -> Optional[int]:
        """Store S3/EBS/GCS/RDS/DynamoDB/ECR/Logs/EFS/ElastiCache/PV in the data_stores table."""
        name = resource.get("name", "Unnamed")
        metadata = resource.get("metadata", {})
        resource_type = resource.get("resource_type", "")

        type_map = {
            "s3_bucket": ("s3", "AWS"),
            "ebs_volume": ("disk", "AWS"),
            "rds_instance": ("database", "AWS"),
            "gcs_bucket": ("gcs", "GCP"),
            "k8s_persistent_volume": ("disk", "Kubernetes"),
            "k8s_pvc": ("disk", "Kubernetes"),
            "dynamodb_table": ("database", "AWS"),
            "ecr_repository": ("container_registry", "AWS"),
            "cloudwatch_log_group": ("logs", "AWS"),
            "efs_filesystem": ("filesystem", "AWS"),
            "elasticache_cluster": ("cache", "AWS"),
        }

        if resource_type not in type_map:
            return None

        storage_type, storage_provider = type_map[resource_type]

        try:
            existing = (
                self.db(
                    (self.db.data_stores.organization_id == organization_id)
                    & (self.db.data_stores.name == name)
                    & (self.db.data_stores.storage_type == storage_type)
                )
                .select()
                .first()
            )

            native_id = resource.get("external_id") or resource.get("resource_id")

            if existing:
                self.db(self.db.data_stores.id == existing.id).update(
                    external_id=native_id,
                    updated_at=datetime.now(timezone.utc),
                )
                return existing.id

            now = datetime.now(timezone.utc)
            ds_id = self.db.data_stores.insert(
                name=name,
                tenant_id=self._tenant_for_org(organization_id),
                organization_id=organization_id,
                storage_type=storage_type,
                storage_provider=storage_provider,
                location_region=resource.get("region"),
                metadata=metadata,
                external_id=native_id,
                created_at=now,
                updated_at=now,
            )
            return ds_id
        except Exception as e:
            logger.warning("Failed to store data_store %s: %s", name, e)
            return None

    def _store_as_networking_resource(
        self,
        organization_id: int,
        resource: Dict[str, Any],
        networking_lookup: Dict[str, int],
    ) -> Optional[int]:
        """Store load balancers in the networking_resources table."""
        resource_type = resource.get("resource_type", "")
        if resource_type != "load_balancer":
            return None

        metadata = resource.get("metadata", {})
        vpc_id = metadata.get("vpc_id")
        parent_id = networking_lookup.get(f"vpc:{vpc_id}") if vpc_id else None

        return self._upsert_networking_resource(
            organization_id=organization_id,
            name=resource.get("name", ""),
            network_type="proxy",
            region=resource.get("region"),
            parent_id=parent_id,
            attributes={
                "dns_name": metadata.get("dns_name"),
                "scheme": metadata.get("scheme"),
                "lb_type": metadata.get("type"),
                "state": metadata.get("state"),
            },
            tags=["aws", "load-balancer", "discovered"],
            external_id=resource.get("external_id") or resource.get("resource_id"),
        )

    def _store_k8s_service_account_as_identity(
        self, organization_id: int, resource: Dict[str, Any], cluster_name: str
    ) -> Optional[int]:
        """Store a K8s ServiceAccount in the identities table."""
        name = resource.get("name", "Unnamed")
        metadata = resource.get("metadata", {})
        namespace = metadata.get("namespace", "default")

        username = f"k8s:{cluster_name}:{namespace}:{name}"

        try:
            existing = (
                self.db(
                    (self.db.identities.username == username)
                    & (self.db.identities.auth_provider == "kubernetes")
                )
                .select()
                .first()
            )

            if existing:
                self.db(self.db.identities.id == existing.id).update(
                    updated_at=datetime.now(timezone.utc),
                )
                return existing.id

            now = datetime.now(timezone.utc)
            identity_id = self.db.identities.insert(
                tenant_id=self._tenant_for_org(organization_id),
                identity_type="serviceAccount",
                username=username,
                full_name=f"{namespace}/{name}",
                organization_id=organization_id,
                auth_provider="kubernetes",
                portal_role="observer",
                is_active=True,
                is_superuser=False,
                mfa_enabled=False,
                must_change_password=False,
                created_at=now,
                updated_at=now,
            )
            return identity_id
        except Exception as e:
            logger.warning("Failed to store K8s SA %s: %s", name, e)
            return None

    def _store_container_image_as_software(
        self, organization_id: int, image: str
    ) -> Optional[int]:
        """Store a container image in the software table."""
        # Parse image:tag
        if ":" in image and not image.startswith("sha256:"):
            parts = image.rsplit(":", 1)
            image_name = parts[0]
            version = parts[1]
        else:
            image_name = image
            version = "latest"

        # Extract vendor from registry
        vendor = image_name.split("/")[0] if "/" in image_name else "docker.io"

        try:
            existing = (
                self.db(
                    (self.db.software.organization_id == organization_id)
                    & (self.db.software.name == image_name)
                    & (self.db.software.version == version)
                )
                .select()
                .first()
            )

            if existing:
                return existing.id

            now = datetime.now(timezone.utc)
            sw_id = self.db.software.insert(
                name=image_name,
                version=version,
                tenant_id=self._tenant_for_org(organization_id),
                organization_id=organization_id,
                software_type="container",
                is_active=True,
                vendor=vendor,
                tags=["kubernetes", "container-image", "discovered"],
                external_id=image,
                created_at=now,
                updated_at=now,
            )
            return sw_id
        except Exception as e:
            logger.warning("Failed to store software %s: %s", image_name, e)
            return None

    def _create_dependency_link(
        self,
        source_type: str,
        source_id: int,
        target_type: str,
        target_id: int,
        tenant_id: int,
        dep_type: str = "discovered_from",
        meta: Optional[Dict] = None,
    ) -> bool:
        """Create a dependencies record linking domain table entries.

        Args:
            source_type: Type of source resource
            source_id: ID of source resource
            target_type: Type of target resource
            target_id: ID of target resource
            tenant_id: Tenant ID (scopes the dependency)
            dep_type: Dependency type (default: discovered_from)
            meta: Optional metadata dictionary

        Returns:
            True if the row now exists (inserted or already present), False
            if the write failed. Callers that need to count actual edges
            (e.g. the pass-2 linker) rely on this to avoid counting a
            swallowed write exception as a success.
        """
        # Warn on non-canonical dependency types but still persist them
        if dep_type not in CANONICAL_EDGE_TYPES:
            logger.warning(
                "Non-canonical dependency_type %r for edge %s(%s)->"
                "%s(%s); persisting anyway",
                dep_type,
                source_type,
                source_id,
                target_type,
                target_id,
            )

        try:
            existing = (
                self.db(
                    (self.db.dependencies.source_type == source_type)
                    & (self.db.dependencies.source_id == source_id)
                    & (self.db.dependencies.target_type == target_type)
                    & (self.db.dependencies.target_id == target_id)
                    & (self.db.dependencies.dependency_type == dep_type)
                )
                .select()
                .first()
            )
            if not existing:
                now = datetime.now(timezone.utc)
                self.db.dependencies.insert(
                    source_type=source_type,
                    source_id=source_id,
                    target_type=target_type,
                    target_id=target_id,
                    tenant_id=tenant_id,
                    dependency_type=dep_type,
                    metadata=meta or {},
                    created_at=now,
                    updated_at=now,
                )
            return True
        except Exception as e:
            logger.warning("Failed to create dependency link: %s", e)
            return False

    # Native-id -> domain table for DB-fallback resolution.
    _RESOLVE_TABLES = {
        "entity": "entities",
        "networking_resource": "networking_resources",
        "data_store": "data_stores",
        "service": "services",
        "software": "software",
        "identity": "identities",
    }

    def _resolve_target(
        self,
        scan_index: Dict[Any, Any],
        provider: str,
        target_external_id: Optional[str],
        target_kind: Optional[str],
        organization_id: int,
    ) -> Optional[Tuple[str, int]]:
        """Resolve a native cloud id to (type_string, row_id).

        Scan-local index first (same job), then a DB lookup on external_id so
        targets discovered by an earlier scan still resolve.
        """
        hit = scan_index.get((provider, target_external_id))
        if hit:
            return hit
        return self._resolve_target_in_db(
            target_external_id, target_kind, organization_id
        )

    def _resolve_target_in_db(
        self,
        external_id: Optional[str],
        target_kind: Optional[str],
        organization_id: int,
    ) -> Optional[Tuple[str, int]]:
        """Look up a persisted row by external_id, hinted table first."""
        order = []
        if target_kind in self._RESOLVE_TABLES:
            order.append(target_kind)
        order += [k for k in self._RESOLVE_TABLES if k not in order]
        for type_string in order:
            table = getattr(self.db, self._RESOLVE_TABLES[type_string])
            row = (
                self.db(
                    (table.external_id == external_id)
                    & (table.organization_id == organization_id)
                )
                .select()
                .first()
            )
            if row:
                return (type_string, row.id)
        return None

    def _link_resources(
        self,
        source: Tuple[str, int],
        target: Tuple[str, int],
        edge_type: Optional[str],
        tenant_id: int,
    ) -> bool:
        """Write a dependencies edge; dual-write network membership.

        Args:
            source: Tuple of (source_type, source_id)
            target: Tuple of (target_type, target_id)
            edge_type: Type of dependency edge
            tenant_id: Tenant ID (scopes the dependency)

        Returns:
            True/False from `_create_dependency_link`. The network_entity_mappings
            dual-write is a projection of that edge, so its own success/failure
            doesn't change the return value here.
        """
        src_type, src_id = source
        tgt_type, tgt_id = target
        linked = self._create_dependency_link(
            src_type,
            src_id,
            tgt_type,
            tgt_id,
            tenant_id,
            edge_type,
            {"linked_by": "discovery"},
        )
        if (
            edge_type in ("in_network", "in_subnet")
            and src_type == "entity"
            and tgt_type == "networking_resource"
        ):
            self._upsert_network_entity_mapping(tgt_id, src_id, edge_type)
        return linked

    def _register(
        self,
        scan_index: Dict[Any, Any],
        provider: str,
        resource: Dict[str, Any],
        type_string: str,
        row_id: Optional[int],
    ) -> None:
        """Index a just-persisted resource by its native id for edge resolution."""
        if not row_id:
            return
        ext = resource.get("external_id") or resource.get("resource_id")
        if ext:
            scan_index[(provider, ext)] = (type_string, row_id)

    def _store_k8s_ingress(
        self,
        organization_id: int,
        resource: Dict[str, Any],
        networking_lookup: Dict[str, int],
        tenant_id: int,
    ) -> Optional[int]:
        """Store a K8s Ingress in the networking_resources table.

        Args:
            organization_id: Organization ID
            resource: K8s Ingress resource data
            networking_lookup: Mapping of network names to IDs
            tenant_id: Tenant ID (scopes dependencies)
        """
        metadata = resource.get("metadata", {})
        namespace = metadata.get("namespace", "default")
        parent_id = networking_lookup.get(f"namespace:{namespace}")

        ingress_id = self._upsert_networking_resource(
            organization_id=organization_id,
            name=resource.get("name", ""),
            network_type="ingress",
            region=resource.get("region"),
            parent_id=parent_id,
            attributes={
                "ingress_class": metadata.get("ingress_class"),
                "paths": metadata.get("paths", []),
                "tls_enabled": metadata.get("tls_enabled", False),
                "tls_hosts": metadata.get("tls_hosts", []),
                "backend_services": metadata.get("backend_services", []),
            },
            tags=["kubernetes", "ingress", "discovered"],
        )

        # Link ingress to backend services via dependencies
        if ingress_id:
            for svc_name in metadata.get("backend_services", []):
                try:
                    svc = (
                        self.db(
                            (self.db.services.organization_id == organization_id)
                            & (self.db.services.name == svc_name)
                        )
                        .select()
                        .first()
                    )
                    if svc:
                        self._create_dependency_link(
                            "networking_resource",
                            ingress_id,
                            "service",
                            svc.id,
                            tenant_id,
                            "routes_to",
                            {"ingress": resource.get("name", "")},
                        )
                        # Append ingress paths to service.paths for ServiceEndpoints page
                        for path_entry in metadata.get("paths", []):
                            if path_entry.get("service") == svc_name:
                                endpoint = f"GET {path_entry.get('host', '*')}{path_entry.get('path', '/')}"
                                existing_paths = svc.paths or []
                                if endpoint not in existing_paths:
                                    existing_paths.append(endpoint)
                                    self.db(self.db.services.id == svc.id).update(
                                        paths=existing_paths,
                                        updated_at=datetime.now(timezone.utc),
                                    )
                except Exception as e:
                    logger.warning(
                        "Failed to link ingress to service %s: %s", svc_name, e
                    )

        return ingress_id

    def _store_k8s_pvc_as_data_store(
        self,
        organization_id: int,
        resource: Dict[str, Any],
        provider: str,
        tenant_id: int,
    ) -> Optional[int]:
        """Store a K8s PVC in data_stores and link to PV via dependencies.

        Args:
            organization_id: Organization ID
            resource: K8s PVC resource data
            provider: Provider type (e.g., 'kubernetes')
            tenant_id: Tenant ID (scopes dependencies)
        """
        pvc_id = self._store_as_data_store(organization_id, resource, provider)

        if pvc_id:
            metadata = resource.get("metadata", {})
            volume_name = metadata.get("volume_name")
            if volume_name:
                # Find the PV and create dependency link
                try:
                    pv = (
                        self.db(
                            (self.db.data_stores.organization_id == organization_id)
                            & (self.db.data_stores.name == volume_name)
                            & (self.db.data_stores.storage_type == "disk")
                            & (self.db.data_stores.storage_provider == "Kubernetes")
                        )
                        .select()
                        .first()
                    )
                    if pv:
                        self._create_dependency_link(
                            "data_store",
                            pvc_id,
                            "data_store",
                            pv.id,
                            tenant_id,
                            "bound_to",
                            {"pvc": resource.get("name", ""), "pv": volume_name},
                        )
                except Exception as e:
                    logger.warning("Failed to link PVC to PV %s: %s", volume_name, e)

        return pvc_id

    def _store_k8s_secret(
        self, organization_id: int, resource: Dict[str, Any]
    ) -> Optional[int]:
        """Store a K8s Secret in builtin_secrets (metadata only, NEVER values)."""
        name = resource.get("name", "Unnamed")
        metadata = resource.get("metadata", {})
        namespace = metadata.get("namespace", "default")

        full_name = f"k8s:{namespace}:{name}"

        try:
            existing = (
                self.db(
                    (self.db.builtin_secrets.organization_id == organization_id)
                    & (self.db.builtin_secrets.name == full_name)
                )
                .select()
                .first()
            )

            if existing:
                self.db(self.db.builtin_secrets.id == existing.id).update(
                    secret_json={
                        "namespace": namespace,
                        "k8s_type": metadata.get("type", "Opaque"),
                        "keys": metadata.get("keys", []),
                        "cluster": "kubernetes",
                        "annotations": metadata.get("annotations", {}),
                    },
                    updated_at=datetime.now(timezone.utc),
                )
                return existing.id

            now = datetime.now(timezone.utc)
            secret_id = self.db.builtin_secrets.insert(
                name=full_name,
                organization_id=organization_id,
                secret_type="other",
                is_active=True,
                secret_value=None,
                secret_json={
                    "namespace": namespace,
                    "k8s_type": metadata.get("type", "Opaque"),
                    "keys": metadata.get("keys", []),
                    "cluster": "kubernetes",
                    "annotations": metadata.get("annotations", {}),
                },
                tags=["kubernetes", "k8s-secret", "discovered"],
                created_at=now,
                updated_at=now,
            )
            return secret_id
        except Exception as e:
            logger.warning("Failed to store K8s secret %s: %s", full_name, e)
            return None

    def _store_cert_manager_certificate(
        self, organization_id: int, resource: Dict[str, Any]
    ) -> Optional[int]:
        """Store a cert-manager Certificate in the certificates table."""
        name = resource.get("name", "Unnamed")
        metadata = resource.get("metadata", {})

        common_name = metadata.get("common_name", name)
        dns_names = metadata.get("dns_names", [])
        issuer_ref = metadata.get("issuer_ref", {})
        not_after = metadata.get("not_after")
        not_before = metadata.get("not_before")

        # Parse expiration date
        expiration = None
        if not_after:
            try:
                expiration = datetime.fromisoformat(not_after.replace("Z", "+00:00"))
            except (ValueError, AttributeError):
                pass

        # issue_date is NOT NULL. Prefer the certificate's own notBefore; fall
        # back to the discovery date, which is the earliest point we can attest
        # the certificate existed.
        issue_date = datetime.now(timezone.utc).date()
        if not_before:
            try:
                issue_date = datetime.fromisoformat(
                    not_before.replace("Z", "+00:00")
                ).date()
            except (ValueError, AttributeError):
                pass

        try:
            existing = (
                self.db(
                    (self.db.certificates.organization_id == organization_id)
                    & (self.db.certificates.name == name)
                    & (self.db.certificates.creator == "cert_manager")
                )
                .select()
                .first()
            )

            if existing:
                self.db(self.db.certificates.id == existing.id).update(
                    common_name=common_name,
                    subject_alternative_names=dns_names,
                    expiration_date=expiration,
                    updated_at=datetime.now(timezone.utc),
                )
                return existing.id

            now = datetime.now(timezone.utc)
            cert_id = self.db.certificates.insert(
                tenant_id=self._tenant_for_org(organization_id),
                name=name,
                issue_date=issue_date,
                status=calculate_certificate_status(
                    expiration.date() if expiration else None, 30, False
                ),
                is_active=True,
                organization_id=organization_id,
                creator="cert_manager",
                cert_type="server_cert",
                common_name=common_name,
                subject_alternative_names=dns_names,
                issuer_common_name=issuer_ref.get("name"),
                issuer_organization=issuer_ref.get("kind", "ClusterIssuer"),
                expiration_date=expiration,
                is_revoked=False,
                auto_renew=True,
                tags=["kubernetes", "cert-manager", "discovered"],
                created_at=now,
                updated_at=now,
            )
            return cert_id
        except Exception as e:
            logger.warning("Failed to store cert-manager cert %s: %s", name, e)
            return None

    def _store_cni_as_networking(
        self,
        organization_id: int,
        resource: Dict[str, Any],
        networking_lookup: Dict[str, int],
    ) -> Optional[int]:
        """Store CNI plugin info in networking_resources."""
        metadata = resource.get("metadata", {})

        return self._upsert_networking_resource(
            organization_id=organization_id,
            name=resource.get("name", "Unknown CNI"),
            network_type="cni",
            region=resource.get("region"),
            parent_id=None,
            attributes={
                "cni_type": metadata.get("cni_type"),
                "version": metadata.get("version"),
            },
            tags=["kubernetes", "cni", "discovered"],
        )

    # Resource type routing

    def _resource_type_to_domain(self, resource_type: str) -> str:
        """Map a resource_type to its domain table."""
        domain_map = {
            "k8s_service": "service",
            "k8s_service_account": "identity",
            "k8s_persistent_volume": "data_store",
            "k8s_pvc": "data_store",
            "k8s_node": "entity",
            "k8s_pod": "entity",
            "k8s_ingress": "networking",
            "k8s_cni": "networking",
            "k8s_secret": "builtin_secret",
            "cert_manager_certificate": "certificate",
            "vpc": "networking",
            "subnet": "networking",
            "security_group": "networking",
            "load_balancer": "networking",
            "ec2_instance": "entity",
            "s3_bucket": "data_store",
            "ebs_volume": "data_store",
            "rds_instance": "data_store",
            "lambda_function": "service",
            "iam_user": "identity",
            "iam_role": "identity",
            "gce_instance": "entity",
            "gcs_bucket": "data_store",
            "dynamodb_table": "data_store",
            "sqs_queue": "service",
            "sns_topic": "service",
            "ecr_repository": "data_store",
            "cloudwatch_log_group": "data_store",
            "step_function": "service",
            "eventbridge_rule": "service",
            "api_gateway_rest_api": "service",
            "efs_filesystem": "data_store",
            "elasticache_cluster": "data_store",
            "cloudfront_distribution": "networking",
            "route53_hosted_zone": "networking",
        }
        return domain_map.get(resource_type, "entity")

    # Main storage orchestrator

    def _tenant_for_org(self, organization_id: int) -> int:
        """Resolve the tenant owning an organization.

        Domain tables carry a NOT NULL tenant_id, and discovery runs without a
        request token, so the tenant is derived from the organization rather
        than assumed to be the default one.
        """
        org = self.db.organizations[organization_id]
        if org is None or getattr(org, "tenant_id", None) is None:
            raise ValueError(
                f"Cannot resolve tenant for organization {organization_id}"
            )
        return org.tenant_id

    def _store_discovered_resources(
        self, organization_id: int, discovery_results: Dict[str, Any]
    ) -> Dict[str, int]:
        """
        Store discovered resources with hierarchy and domain table mapping.

        Creates provider root entities, intermediate networking, and routes
        resources to their proper domain tables (services, data_stores,
        networking_resources, identities, software, entities). A second pass
        then resolves each resource's declared `relationships` into
        dependencies edges (native-id resolution, scan-local then DB).

        Returns:
            Dict with `edges_created` and `unresolved_edges` counts.
        """
        provider = self._detect_provider_type(discovery_results)

        # Resolve tenant_id from organization
        tenant_id = self._tenant_for_org(organization_id)

        # Get config for root entity naming
        root_config = {"name": f"{provider} discovery"}
        root_entity_id = self._ensure_provider_root_entity(
            organization_id, provider, root_config
        )

        # Create intermediate networking (VPCs, Namespaces, Subnets)
        networking_lookup = self._ensure_intermediate_networking(
            organization_id, provider, root_entity_id, discovery_results
        )

        # (provider, external_id) -> (type_string, row_id); seeded from the
        # networking resources so pass 2 can resolve edges into VPCs/subnets/
        # namespaces without a DB round-trip.
        scan_index: Dict[Any, Any] = {}
        for key, net_id in networking_lookup.items():
            _, _, ext = key.partition(":")
            if ext and net_id:
                scan_index[(provider, ext)] = ("networking_resource", net_id)
        edges_created = 0
        unresolved_edges = 0

        # Track container images for software extraction
        seen_images = set()

        # Map discovery categories to entity types (fallback)
        category_to_entity_type = {
            "compute": "compute",
            "storage": "storage",
            "network": "network",
            "database": "storage",
            "serverless": "compute",
        }

        for category, resources in discovery_results.items():
            if category in ["resources_count", "discovery_time", "duration_seconds"]:
                continue
            if not resources:
                continue

            for resource in resources:
                resource_type = resource.get("resource_type", "")
                domain = self._resource_type_to_domain(resource_type)

                if domain == "identity":
                    if resource_type in ("iam_user", "iam_role"):
                        identity_id = self._store_iam_as_identity(
                            organization_id, resource
                        )
                        self._register(
                            scan_index, provider, resource, "identity", identity_id
                        )
                        if identity_id and root_entity_id:
                            self._create_dependency_link(
                                "identity",
                                identity_id,
                                "entity",
                                root_entity_id,
                                tenant_id,
                                "discovered_from",
                                {"provider": provider},
                            )
                    elif resource_type == "k8s_service_account":
                        cluster_name = root_config.get("name", "unknown")
                        sa_id = self._store_k8s_service_account_as_identity(
                            organization_id, resource, cluster_name
                        )
                        self._register(
                            scan_index, provider, resource, "identity", sa_id
                        )
                        if sa_id and root_entity_id:
                            self._create_dependency_link(
                                "identity",
                                sa_id,
                                "entity",
                                root_entity_id,
                                tenant_id,
                                "discovered_from",
                                {"provider": provider},
                            )

                elif domain == "service":
                    svc_id = self._store_as_service(organization_id, resource, provider)
                    self._register(scan_index, provider, resource, "service", svc_id)
                    if svc_id and root_entity_id:
                        self._create_dependency_link(
                            "service",
                            svc_id,
                            "entity",
                            root_entity_id,
                            tenant_id,
                            "discovered_from",
                            {"provider": provider},
                        )

                elif domain == "data_store":
                    if resource_type == "k8s_pvc":
                        ds_id = self._store_k8s_pvc_as_data_store(
                            organization_id, resource, provider, tenant_id
                        )
                    else:
                        ds_id = self._store_as_data_store(
                            organization_id, resource, provider
                        )
                    self._register(scan_index, provider, resource, "data_store", ds_id)
                    if ds_id and root_entity_id:
                        self._create_dependency_link(
                            "data_store",
                            ds_id,
                            "entity",
                            root_entity_id,
                            tenant_id,
                            "discovered_from",
                            {"provider": provider},
                        )

                elif domain == "builtin_secret":
                    secret_id = self._store_k8s_secret(organization_id, resource)
                    if secret_id and root_entity_id:
                        self._create_dependency_link(
                            "builtin_secret",
                            secret_id,
                            "entity",
                            root_entity_id,
                            tenant_id,
                            "discovered_from",
                            {"provider": provider},
                        )

                elif domain == "certificate":
                    cert_id = self._store_cert_manager_certificate(
                        organization_id, resource
                    )
                    if cert_id and root_entity_id:
                        self._create_dependency_link(
                            "certificate",
                            cert_id,
                            "entity",
                            root_entity_id,
                            tenant_id,
                            "discovered_from",
                            {"provider": provider},
                        )

                elif domain == "networking":
                    if resource_type == "load_balancer":
                        lb_id = self._store_as_networking_resource(
                            organization_id, resource, networking_lookup
                        )
                        self._register(
                            scan_index, provider, resource, "networking_resource", lb_id
                        )
                    elif resource_type == "k8s_ingress":
                        self._store_k8s_ingress(
                            organization_id, resource, networking_lookup, tenant_id
                        )
                    elif resource_type == "k8s_cni":
                        self._store_cni_as_networking(
                            organization_id, resource, networking_lookup
                        )
                    elif resource_type == "security_group":
                        # Store security group as networking resource
                        net_id = self._upsert_networking_resource(
                            organization_id=organization_id,
                            name=resource.get("name", ""),
                            network_type="security_group",
                            region=resource.get("region"),
                            attributes=resource.get("metadata", {}),
                            tags=["aws", "security_group", "discovered"],
                            external_id=resource.get("external_id")
                            or resource.get("resource_id"),
                        )
                        self._register(
                            scan_index,
                            provider,
                            resource,
                            "networking_resource",
                            net_id,
                        )
                    # VPCs and subnets already handled in _ensure_intermediate_networking

                else:
                    # Default: store as entity with parent_id
                    entity_type = category_to_entity_type.get(category, "compute")
                    entity_id = self._store_as_entity(
                        organization_id,
                        resource,
                        entity_type,
                        parent_id=root_entity_id,
                        networking_lookup=networking_lookup,
                    )
                    self._register(scan_index, provider, resource, "entity", entity_id)

                    # Link entity to networking resource if applicable
                    metadata = resource.get("metadata", {})
                    if entity_id:
                        vpc_id = metadata.get("vpc_id")
                        # AWS now emits explicit in_network relationships in pass 2,
                        # so skip the legacy connected_to mapping for it (it would
                        # otherwise duplicate the edge on the Topology tab). Providers
                        # not yet migrated (gcp/azure) keep the legacy fallback.
                        if (
                            vpc_id
                            and provider != "aws"
                            and f"vpc:{vpc_id}" in networking_lookup
                        ):
                            self._upsert_network_entity_mapping(
                                networking_lookup[f"vpc:{vpc_id}"],
                                entity_id,
                                "connected_to",
                            )
                        ns = metadata.get("namespace")
                        if ns and f"namespace:{ns}" in networking_lookup:
                            self._upsert_network_entity_mapping(
                                networking_lookup[f"namespace:{ns}"],
                                entity_id,
                                "connected_to",
                            )

                # Extract container images from K8s pods
                if resource_type == "k8s_pod":
                    containers = resource.get("metadata", {}).get("containers", [])
                    for container in containers:
                        image = container.get("image", "")
                        if image and image not in seen_images:
                            seen_images.add(image)
                            sw_id = self._store_container_image_as_software(
                                organization_id, image
                            )
                            # Register the image under its OWN native id (the
                            # image string, which is also its external_id),
                            # never via _register(resource=<pod>, ...) — the
                            # pod dict's resource_id/external_id belongs to
                            # the pod's own ("entity", entity_id) scan_index
                            # entry (set above), and keying software off the
                            # pod's id would clobber it, silently corrupting
                            # resolution for any relationship naming the pod.
                            if sw_id:
                                scan_index[(provider, image)] = ("software", sw_id)
                            if sw_id and root_entity_id:
                                self._create_dependency_link(
                                    "software",
                                    sw_id,
                                    "entity",
                                    root_entity_id,
                                    tenant_id,
                                    "discovered_from",
                                    {"provider": provider},
                                )

        # Pass 2: resolve declared relationships into edges.
        for category, resources in discovery_results.items():
            if category in ["resources_count", "discovery_time", "duration_seconds"]:
                continue
            for resource in resources or []:
                rels = resource.get("relationships") or []
                if not rels:
                    continue
                ext = resource.get("external_id") or resource.get("resource_id")
                source = scan_index.get((provider, ext))
                if not source:
                    # The resource's own id was never registered (its branch
                    # returned no row id, or the row id came back None) — its
                    # declared relationships have nowhere to originate from.
                    # Count and log each one rather than dropping silently.
                    unresolved_edges += len(rels)
                    logger.warning(
                        "Unresolved discovery edge source: %s has no registered "
                        "scan_index entry (provider=%s); dropping %d relationship(s)",
                        ext,
                        provider,
                        len(rels),
                    )
                    continue
                for rel in rels:
                    target = self._resolve_target(
                        scan_index,
                        provider,
                        rel.get("target_external_id"),
                        rel.get("target_kind"),
                        organization_id,
                    )
                    if not target:
                        unresolved_edges += 1
                        logger.warning(
                            "Unresolved discovery edge: %s -[%s]-> %s (provider=%s)",
                            ext,
                            rel.get("edge_type"),
                            rel.get("target_external_id"),
                            provider,
                        )
                        continue
                    if self._link_resources(
                        source, target, rel.get("edge_type"), tenant_id
                    ):
                        edges_created += 1

        self.db.commit()
        return {"edges_created": edges_created, "unresolved_edges": unresolved_edges}

    def _store_iam_as_identity(
        self, organization_id: int, resource: Dict[str, Any]
    ) -> Optional[int]:
        """
        Store IAM user or role as an Identity resource.

        Args:
            organization_id: Organization ID
            resource: Discovered IAM resource data

        Returns:
            Identity row ID or None
        """
        resource_type = resource.get("resource_type", "")
        name = resource.get("name", "Unnamed")
        metadata = resource.get("metadata", {})
        arn = metadata.get("arn", resource.get("resource_id", ""))

        # Determine identity type based on IAM resource type
        if resource_type == "iam_user":
            identity_type = "integration"  # AWS IAM users are integrations
        elif resource_type == "iam_role":
            identity_type = "serviceAccount"  # IAM roles are service accounts
        else:
            identity_type = "other"

        # Generate a unique username for AWS resources
        # Format: aws:<account_id>:<user_or_role_name>
        # Extract account ID from ARN: arn:aws:iam::123456789012:user/username
        account_id = ""
        if arn and "::" in arn:
            parts = arn.split(":")
            if len(parts) >= 5:
                account_id = parts[4]

        aws_username = f"aws:{account_id}:{name}" if account_id else f"aws:{name}"

        # Check if identity already exists
        existing = (
            self.db(
                (self.db.identities.username == aws_username)
                | (
                    (self.db.identities.auth_provider == "aws")
                    & (self.db.identities.auth_provider_id == arn)
                )
            )
            .select()
            .first()
        )

        if existing:
            # Update existing identity
            self.db(self.db.identities.id == existing.id).update(
                full_name=name,
                external_id=arn,
                updated_at=datetime.now(timezone.utc),
            )
            return existing.id
        else:
            # Create new identity
            now = datetime.now(timezone.utc)
            identity_id = self.db.identities.insert(
                tenant_id=self._tenant_for_org(organization_id),
                identity_type=identity_type,
                username=aws_username,
                full_name=name,
                organization_id=organization_id,
                auth_provider="aws",
                auth_provider_id=arn,
                external_id=arn,
                portal_role="observer",  # AWS identities get observer role by default
                is_active=True,
                is_superuser=False,
                mfa_enabled=False,
                must_change_password=False,
                created_at=now,
                updated_at=now,
            )
            return identity_id

    def _store_as_entity(
        self,
        organization_id: int,
        resource: Dict[str, Any],
        entity_type: str,
        parent_id: Optional[int] = None,
        networking_lookup: Optional[Dict[str, int]] = None,
    ) -> Optional[int]:
        """
        Store a discovered resource in the generic entities table.

        Args:
            organization_id: Organization ID
            resource: Discovered resource data
            entity_type: Entity type (compute, storage, network, etc.)
            parent_id: Parent entity ID (provider root)
            networking_lookup: Map of networking resource keys to IDs

        Returns:
            Entity ID or None
        """
        name = resource.get("name", "Unnamed")
        resource_type = resource.get("resource_type", "")
        native_id = resource.get("external_id") or resource.get("resource_id")

        # Check if entity already exists
        existing = (
            self.db(
                (self.db.entities.organization_id == organization_id)
                & (self.db.entities.sub_type == resource_type)
                & (self.db.entities.name == name)
            )
            .select()
            .first()
        )

        # Prepare attributes JSON
        resource_attrs = {
            "resource_id": resource.get("resource_id"),
            "resource_type": resource_type,
            "region": resource.get("region"),
            "tags": resource.get("tags", {}),
            "metadata": resource.get("metadata", {}),
            "discovered_at": datetime.now(timezone.utc).isoformat(),
        }

        if existing:
            # Update existing entity
            update_data = {
                "name": name,
                "metadata": resource_attrs,
                "external_id": native_id,
                "updated_at": datetime.now(timezone.utc),
            }
            if parent_id is not None:
                update_data["parent_id"] = parent_id
            self.db(self.db.entities.id == existing.id).update(**update_data)
            return existing.id
        else:
            # Create new entity
            now = datetime.now(timezone.utc)
            insert_data = {
                "name": name,
                "type": entity_type,
                "sub_type": resource_type,
                "organization_id": organization_id,
                "metadata": resource_attrs,
                "external_id": native_id,
                "created_at": now,
                "updated_at": now,
            }
            if parent_id is not None:
                insert_data["parent_id"] = parent_id
            return self.db.entities.insert(**insert_data)

    def _sanitize_job(self, job: Dict[str, Any]) -> Dict[str, Any]:
        """Remove sensitive fields from job data."""
        sanitized = dict(job)

        # Mask sensitive config fields
        if "config_json" in sanitized and sanitized["config_json"]:
            config = dict(sanitized["config_json"])

            sensitive_fields = [
                "access_key_id",
                "secret_access_key",
                "credentials_json",
                "token",
                "client_secret",
                "kubeconfig",
            ]

            for field in sensitive_fields:
                if field in config:
                    config[field] = "***REDACTED***"

            sanitized["config_json"] = config

        return sanitized

    # Scanner service methods

    def get_pending_jobs(self) -> List[Dict[str, Any]]:
        """
        Get jobs that are ready to be picked up by the scanner.

        A job is pending if:
        - It's enabled
        - It's a local scan type (network, http_screenshot, banner)
        - Either: schedule_interval=0 (one-time) and never run, or scheduled and due

        Returns:
            List of pending job dictionaries
        """
        # Get local scan providers
        local_providers = ["network", "http_screenshot", "banner"]

        # Find pending one-time jobs (never run)
        pending_jobs = self.db(
            (self.db.discovery_jobs.enabled == True)  # noqa: E712
            & (self.db.discovery_jobs.provider.belongs(local_providers))
            & (self.db.discovery_jobs.schedule_interval == 0)
            & (self.db.discovery_jobs.last_run_at == None)  # noqa: E711
        ).select()

        # Also get scheduled jobs that are due
        now = datetime.now(timezone.utc)
        scheduled_jobs = self.db(
            (self.db.discovery_jobs.enabled == True)  # noqa: E712
            & (self.db.discovery_jobs.provider.belongs(local_providers))
            & (self.db.discovery_jobs.schedule_interval > 0)
            & (
                (self.db.discovery_jobs.next_run_at == None)  # noqa: E711
                | (self.db.discovery_jobs.next_run_at <= now)
            )
        ).select()

        all_jobs = list(pending_jobs) + list(scheduled_jobs)

        return [self._sanitize_job(job.as_dict()) for job in all_jobs]

    def mark_job_running(self, job_id: int) -> Dict[str, Any]:
        """
        Mark a job as currently running.

        Args:
            job_id: Job ID

        Returns:
            Updated job info

        Raises:
            Exception: If job not found
        """
        job = self.db.discovery_jobs[job_id]
        if not job:
            raise Exception(f"Job not found: {job_id}")

        # Update job status
        self.db(self.db.discovery_jobs.id == job_id).update(
            last_run_at=datetime.now(timezone.utc),
        )

        # Create history entry
        now = datetime.now(timezone.utc)
        self.db.discovery_history.insert(
            job_id=job_id,
            started_at=now,
            status="running",
            entities_discovered=0,
            entities_updated=0,
            entities_created=0,
        )

        self.db.commit()

        return {"success": True, "message": "Job marked as running", "job_id": job_id}

    def complete_job(
        self,
        job_id: int,
        success: bool,
        results: Dict[str, Any],
        error_message: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Complete a job and record results.

        Args:
            job_id: Job ID
            success: Whether the scan succeeded
            results: Scan results
            error_message: Error message if failed

        Returns:
            Completion status

        Raises:
            Exception: If job not found
        """
        job = self.db.discovery_jobs[job_id]
        if not job:
            raise Exception(f"Job not found: {job_id}")

        # Find the running history entry
        history_entry = (
            self.db(
                (self.db.discovery_history.job_id == job_id)
                & (self.db.discovery_history.status == "running")
            )
            .select(orderby=~self.db.discovery_history.started_at)
            .first()
        )

        if history_entry:
            # Update history entry
            status = "completed" if success else "failed"
            self.db(self.db.discovery_history.id == history_entry.id).update(
                completed_at=datetime.now(timezone.utc),
                status=status,
                error_message=error_message,
                results_json=results,
            )

        # Update next_run_at for scheduled jobs
        if job.schedule_interval and job.schedule_interval > 0:
            from datetime import timedelta

            next_run = datetime.now(timezone.utc) + timedelta(
                seconds=job.schedule_interval
            )
            self.db(self.db.discovery_jobs.id == job_id).update(next_run_at=next_run)

        self.db.commit()

        return {
            "success": True,
            "message": "Job completed",
            "job_id": job_id,
            "status": "completed" if success else "failed",
        }
