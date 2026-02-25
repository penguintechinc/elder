"""AWS connector for syncing AWS resources to Elder."""

# flake8: noqa: E501


import time
from typing import Any, Dict, Optional

import boto3
from botocore.exceptions import ClientError, NoCredentialsError

from apps.worker.config.settings import settings
from apps.worker.connectors.base import BaseConnector, SyncResult
from apps.worker.utils.elder_client import ElderAPIClient, Entity, Organization


class AWSConnector(BaseConnector):
    """Connector for AWS resources."""

    def __init__(self):
        """Initialize AWS connector."""
        super().__init__("aws")
        self.elder_client: Optional[ElderAPIClient] = None
        self.aws_clients: Dict[str, Any] = {}
        self.organization_cache: Dict[str, int] = (
            {}
        )  # Map AWS account/region to Elder org ID

    async def connect(self) -> None:
        """Establish connection to AWS and Elder API."""
        self.logger.info("Connecting to AWS services")

        # Verify AWS credentials
        try:
            sts = boto3.client(
                "sts",
                aws_access_key_id=settings.aws_access_key_id,
                aws_secret_access_key=settings.aws_secret_access_key,
                region_name=settings.aws_default_region,
            )
            identity = sts.get_caller_identity()
            self.logger.info(
                "AWS credentials verified",
                account_id=identity["Account"],
                arn=identity["Arn"],
            )
        except NoCredentialsError:
            self.logger.error("AWS credentials not found")
            raise
        except ClientError as e:
            self.logger.error("Failed to verify AWS credentials", error=str(e))
            raise

        # Initialize Elder API client
        self.elder_client = ElderAPIClient()
        await self.elder_client.connect()

        self.logger.info("AWS connector connected successfully")

    async def disconnect(self) -> None:
        """Disconnect from AWS and Elder API."""
        if self.elder_client:
            await self.elder_client.close()
        self.aws_clients.clear()
        self.organization_cache.clear()
        self.logger.info("AWS connector disconnected")

    def _get_aws_client(self, service: str, region: str):
        """
        Get or create AWS client for a specific service and region.

        Args:
            service: AWS service name (ec2, s3, iam, etc.)
            region: AWS region

        Returns:
            boto3 client instance
        """
        key = f"{service}:{region}"
        if key not in self.aws_clients:
            self.aws_clients[key] = boto3.client(
                service,
                aws_access_key_id=settings.aws_access_key_id,
                aws_secret_access_key=settings.aws_secret_access_key,
                region_name=region,
            )
        return self.aws_clients[key]

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

    async def _sync_vpcs(self, region: str, region_org_id: int) -> tuple[int, int]:
        """
        Sync VPCs from AWS region.

        Args:
            region: AWS region
            region_org_id: Elder organization ID for the region

        Returns:
            Tuple of (created_count, updated_count)
        """
        ec2 = self._get_aws_client("ec2", region)
        created = 0
        updated = 0

        try:
            vpcs = ec2.describe_vpcs()
            for vpc in vpcs.get("Vpcs", []):
                vpc_id = vpc["VpcId"]
                vpc_name = next(
                    (
                        tag["Value"]
                        for tag in vpc.get("Tags", [])
                        if tag["Key"] == "Name"
                    ),
                    vpc_id,
                )

                # Get current status with timestamp (v1.2.1)
                state = vpc.get("State", "unknown")
                status_metadata = {
                    "status": state.capitalize(),
                    "timestamp": int(time.time()),
                }

                entity = Entity(
                    name=f"VPC: {vpc_name}",
                    entity_type="vpc",
                    organization_id=region_org_id,
                    description=f"AWS VPC in {region}",
                    attributes={
                        "vpc_id": vpc_id,
                        "cidr_block": vpc.get("CidrBlock"),
                        "state": state,
                        "is_default": vpc.get("IsDefault", False),
                        "region": region,
                        "provider": "aws",
                    },
                    status_metadata=status_metadata,
                    tags=["aws", "vpc", region],
                )

                # Check if entity already exists by searching for vpc_id in attributes
                existing = await self.elder_client.list_entities(
                    organization_id=region_org_id,
                    entity_type="vpc",
                )

                found = None
                for item in existing.get("items", []):
                    if item.get("attributes", {}).get("vpc_id") == vpc_id:
                        found = item
                        break

                if found:
                    await self.elder_client.update_entity(found["id"], entity)
                    updated += 1
                else:
                    await self.elder_client.create_entity(entity)
                    created += 1

        except ClientError as e:
            self.logger.error("Failed to sync VPCs", region=region, error=str(e))

        return created, updated

    async def _sync_ec2_instances(
        self, region: str, region_org_id: int
    ) -> tuple[int, int]:
        """
        Sync EC2 instances from AWS region.

        Args:
            region: AWS region
            region_org_id: Elder organization ID for the region

        Returns:
            Tuple of (created_count, updated_count)
        """
        ec2 = self._get_aws_client("ec2", region)
        created = 0
        updated = 0

        try:
            reservations = ec2.describe_instances()
            for reservation in reservations.get("Reservations", []):
                for instance in reservation.get("Instances", []):
                    instance_id = instance["InstanceId"]
                    instance_name = next(
                        (
                            tag["Value"]
                            for tag in instance.get("Tags", [])
                            if tag["Key"] == "Name"
                        ),
                        instance_id,
                    )

                    # Get current status with timestamp (v1.2.1)
                    state = instance.get("State", {}).get("Name", "unknown")
                    status_metadata = {
                        "status": state.capitalize(),
                        "timestamp": int(time.time()),
                    }

                    entity = Entity(
                        name=f"EC2: {instance_name}",
                        entity_type="compute",
                        organization_id=region_org_id,
                        description=f"AWS EC2 instance in {region}",
                        attributes={
                            "instance_id": instance_id,
                            "instance_type": instance.get("InstanceType"),
                            "state": state,
                            "private_ip": instance.get("PrivateIpAddress"),
                            "public_ip": instance.get("PublicIpAddress"),
                            "vpc_id": instance.get("VpcId"),
                            "subnet_id": instance.get("SubnetId"),
                            "availability_zone": instance.get("Placement", {}).get(
                                "AvailabilityZone"
                            ),
                            "region": region,
                            "provider": "aws",
                            "launch_time": (
                                instance.get("LaunchTime").isoformat()
                                if instance.get("LaunchTime")
                                else None
                            ),
                        },
                        status_metadata=status_metadata,
                        tags=["aws", "ec2", "compute", region],
                        is_active=state == "running",
                    )

                    # Check if entity already exists
                    existing = await self.elder_client.list_entities(
                        organization_id=region_org_id,
                        entity_type="compute",
                    )

                    found = None
                    for item in existing.get("items", []):
                        if item.get("attributes", {}).get("instance_id") == instance_id:
                            found = item
                            break

                    if found:
                        await self.elder_client.update_entity(found["id"], entity)
                        updated += 1
                    else:
                        await self.elder_client.create_entity(entity)
                        created += 1

        except ClientError as e:
            self.logger.error(
                "Failed to sync EC2 instances", region=region, error=str(e)
            )

        return created, updated

    async def _sync_rds_instances(
        self, region: str, region_org_id: int
    ) -> tuple[int, int]:
        """
        Sync RDS instances (including Aurora) from AWS region.

        Args:
            region: AWS region
            region_org_id: Elder organization ID for the region

        Returns:
            Tuple of (created_count, updated_count)
        """
        rds = self._get_aws_client("rds", region)
        created = 0
        updated = 0

        try:
            # Sync RDS instances
            response = rds.describe_db_instances()
            for db_instance in response.get("DBInstances", []):
                instance_id = db_instance["DBInstanceIdentifier"]
                engine = db_instance.get("Engine", "unknown")
                is_aurora = engine.startswith("aurora")

                # Get current status with timestamp
                status = db_instance.get("DBInstanceStatus", "unknown")
                status_metadata = {
                    "status": status.capitalize(),  # available -> Available
                    "timestamp": int(time.time()),
                }

                entity = Entity(
                    name=f"RDS: {instance_id}" + (" (Aurora)" if is_aurora else ""),
                    entity_type="storage",
                    sub_type="database",
                    organization_id=region_org_id,
                    description=f"AWS RDS {engine} database in {region}",
                    attributes={
                        "instance_id": instance_id,
                        "engine": engine,
                        "engine_version": db_instance.get("EngineVersion"),
                        "instance_class": db_instance.get("DBInstanceClass"),
                        "allocated_storage_gb": db_instance.get("AllocatedStorage"),
                        "status": status,
                        "endpoint": (
                            db_instance.get("Endpoint", {}).get("Address")
                            if db_instance.get("Endpoint")
                            else None
                        ),
                        "port": (
                            db_instance.get("Endpoint", {}).get("Port")
                            if db_instance.get("Endpoint")
                            else None
                        ),
                        "availability_zone": db_instance.get("AvailabilityZone"),
                        "multi_az": db_instance.get("MultiAZ", False),
                        "storage_encrypted": db_instance.get("StorageEncrypted", False),
                        "vpc_id": (
                            db_instance.get("DBSubnetGroup", {}).get("VpcId")
                            if db_instance.get("DBSubnetGroup")
                            else None
                        ),
                        "is_aurora": is_aurora,
                        "region": region,
                        "provider": "aws",
                        "service": "rds",
                        "created_time": (
                            db_instance.get("InstanceCreateTime").isoformat()
                            if db_instance.get("InstanceCreateTime")
                            else None
                        ),
                    },
                    status_metadata=status_metadata,
                    tags=["aws", "rds", "database", engine, region]
                    + (["aurora"] if is_aurora else []),
                    is_active=status.lower() == "available",
                )

                # Check if entity already exists
                existing = await self.elder_client.list_entities(
                    organization_id=region_org_id,
                    entity_type="storage",
                )

                found = None
                for item in existing.get("items", []):
                    if item.get("attributes", {}).get("instance_id") == instance_id:
                        found = item
                        break

                if found:
                    await self.elder_client.update_entity(found["id"], entity)
                    updated += 1
                else:
                    await self.elder_client.create_entity(entity)
                    created += 1

        except ClientError as e:
            self.logger.error(
                "Failed to sync RDS instances", region=region, error=str(e)
            )

        return created, updated

    async def _sync_elasticache_clusters(
        self, region: str, region_org_id: int
    ) -> tuple[int, int]:
        """
        Sync Elasticache clusters from AWS region.

        Args:
            region: AWS region
            region_org_id: Elder organization ID for the region

        Returns:
            Tuple of (created_count, updated_count)
        """
        elasticache = self._get_aws_client("elasticache", region)
        created = 0
        updated = 0

        try:
            # Sync Elasticache clusters
            response = elasticache.describe_cache_clusters()
            for cluster in response.get("CacheClusters", []):
                cluster_id = cluster["CacheClusterId"]
                engine = cluster.get("Engine", "unknown")

                # Get current status with timestamp
                status = cluster.get("CacheClusterStatus", "unknown")
                status_metadata = {
                    "status": status.capitalize(),
                    "timestamp": int(time.time()),
                }

                entity = Entity(
                    name=f"ElastiCache: {cluster_id}",
                    entity_type="storage",
                    sub_type="caching",
                    organization_id=region_org_id,
                    description=f"AWS ElastiCache {engine} cluster in {region}",
                    attributes={
                        "cluster_id": cluster_id,
                        "engine": engine,
                        "engine_version": cluster.get("EngineVersion"),
                        "node_type": cluster.get("CacheNodeType"),
                        "num_cache_nodes": cluster.get("NumCacheNodes", 0),
                        "status": status,
                        "endpoint": (
                            cluster.get("CacheNodes", [{}])[0]
                            .get("Endpoint", {})
                            .get("Address")
                            if cluster.get("CacheNodes")
                            else None
                        ),
                        "port": (
                            cluster.get("CacheNodes", [{}])[0]
                            .get("Endpoint", {})
                            .get("Port")
                            if cluster.get("CacheNodes")
                            else None
                        ),
                        "availability_zone": cluster.get("PreferredAvailabilityZone"),
                        "vpc_id": cluster.get("CacheSubnetGroupName"),
                        "region": region,
                        "provider": "aws",
                        "service": "elasticache",
                        "created_time": (
                            cluster.get("CacheClusterCreateTime").isoformat()
                            if cluster.get("CacheClusterCreateTime")
                            else None
                        ),
                    },
                    status_metadata=status_metadata,
                    tags=["aws", "elasticache", "cache", engine, region],
                    is_active=status.lower() == "available",
                )

                # Check if entity already exists
                existing = await self.elder_client.list_entities(
                    organization_id=region_org_id,
                    entity_type="storage",
                )

                found = None
                for item in existing.get("items", []):
                    if item.get("attributes", {}).get("cluster_id") == cluster_id:
                        found = item
                        break

                if found:
                    await self.elder_client.update_entity(found["id"], entity)
                    updated += 1
                else:
                    await self.elder_client.create_entity(entity)
                    created += 1

        except ClientError as e:
            self.logger.error(
                "Failed to sync ElastiCache clusters", region=region, error=str(e)
            )

        return created, updated

    async def _sync_sqs_queues(
        self, region: str, region_org_id: int
    ) -> tuple[int, int]:
        """
        Sync SQS queues from AWS region.

        Args:
            region: AWS region
            region_org_id: Elder organization ID for the region

        Returns:
            Tuple of (created_count, updated_count)
        """
        sqs = self._get_aws_client("sqs", region)
        created = 0
        updated = 0

        try:
            # List all queues
            response = sqs.list_queues()
            queue_urls = response.get("QueueUrls", [])

            for queue_url in queue_urls:
                queue_name = queue_url.split("/")[-1]

                # Get queue attributes
                try:
                    attrs = sqs.get_queue_attributes(
                        QueueUrl=queue_url, AttributeNames=["All"]
                    )
                    attributes = attrs.get("Attributes", {})

                    # SQS queues don't have a traditional status, mark as Available
                    status_metadata = {
                        "status": "Available",
                        "timestamp": int(time.time()),
                    }

                    entity = Entity(
                        name=f"SQS: {queue_name}",
                        entity_type="storage",
                        sub_type="queue_system",
                        organization_id=region_org_id,
                        description=f"AWS SQS queue in {region}",
                        attributes={
                            "queue_url": queue_url,
                            "queue_name": queue_name,
                            "queue_arn": attributes.get("QueueArn"),
                            "approximate_messages": int(
                                attributes.get("ApproximateNumberOfMessages", 0)
                            ),
                            "message_retention_seconds": int(
                                attributes.get("MessageRetentionPeriod", 0)
                            ),
                            "visibility_timeout": int(
                                attributes.get("VisibilityTimeout", 0)
                            ),
                            "delay_seconds": int(attributes.get("DelaySeconds", 0)),
                            "receive_wait_time": int(
                                attributes.get("ReceiveMessageWaitTimeSeconds", 0)
                            ),
                            "is_fifo": queue_name.endswith(".fifo"),
                            "region": region,
                            "provider": "aws",
                            "service": "sqs",
                            "created_timestamp": int(
                                attributes.get("CreatedTimestamp", 0)
                            ),
                        },
                        status_metadata=status_metadata,
                        tags=["aws", "sqs", "queue", region],
                        is_active=True,
                    )

                    # Check if entity already exists
                    existing = await self.elder_client.list_entities(
                        organization_id=region_org_id,
                        entity_type="storage",
                    )

                    found = None
                    for item in existing.get("items", []):
                        if item.get("attributes", {}).get("queue_url") == queue_url:
                            found = item
                            break

                    if found:
                        await self.elder_client.update_entity(found["id"], entity)
                        updated += 1
                    else:
                        await self.elder_client.create_entity(entity)
                        created += 1

                except ClientError as queue_error:
                    self.logger.warning(
                        "Failed to get queue attributes",
                        queue=queue_name,
                        error=str(queue_error),
                    )

        except ClientError as e:
            self.logger.error("Failed to sync SQS queues", region=region, error=str(e))

        return created, updated

    async def _sync_s3_buckets(self) -> tuple[int, int]:
        """
        Sync S3 buckets (S3 is global).

        Returns:
            Tuple of (created_count, updated_count)
        """
        s3 = self._get_aws_client("s3", settings.aws_default_region)
        created = 0
        updated = 0

        # Get or create AWS root organization
        aws_org_id = await self._get_or_create_organization(
            "AWS",
            "Amazon Web Services",
        )

        try:
            buckets = s3.list_buckets()
            for bucket in buckets.get("Buckets", []):
                bucket_name = bucket["Name"]

                # Get bucket location
                try:
                    location = s3.get_bucket_location(Bucket=bucket_name)
                    region = location.get("LocationConstraint") or "us-east-1"
                except Exception as e:
                    self.logger.warning(
                        "Failed to get bucket location",
                        bucket=bucket_name,
                        error=str(e),
                    )
                    region = "unknown"

                entity = Entity(
                    name=f"S3: {bucket_name}",
                    entity_type="network",  # S3 is networked storage
                    organization_id=aws_org_id,
                    description="AWS S3 bucket",
                    attributes={
                        "bucket_name": bucket_name,
                        "region": region,
                        "provider": "aws",
                        "service": "s3",
                        "creation_date": (
                            bucket.get("CreationDate").isoformat()
                            if bucket.get("CreationDate")
                            else None
                        ),
                    },
                    tags=["aws", "s3", "storage", region],
                )

                # Check if entity already exists
                existing = await self.elder_client.list_entities(
                    organization_id=aws_org_id,
                    entity_type="network",
                )

                found = None
                for item in existing.get("items", []):
                    if item.get("attributes", {}).get("bucket_name") == bucket_name:
                        found = item
                        break

                if found:
                    await self.elder_client.update_entity(found["id"], entity)
                    updated += 1
                else:
                    await self.elder_client.create_entity(entity)
                    created += 1

        except ClientError as e:
            self.logger.error("Failed to sync S3 buckets", error=str(e))

        return created, updated

    async def _sync_lambda_functions(
        self, region: str, region_org_id: int
    ) -> tuple[int, int]:
        """
        Sync AWS Lambda functions for a region.

        Args:
            region: AWS region
            region_org_id: Elder organization ID for this region

        Returns:
            Tuple of (created_count, updated_count)
        """
        lambda_client = self._get_aws_client("lambda", region)
        created = 0
        updated = 0

        try:
            # Fetch all existing compute entities once and build an index by function_arn
            existing_response = await self.elder_client.list_entities(
                organization_id=region_org_id,
                entity_type="compute",
            )
            existing_entities_by_arn = {
                item.get("attributes", {}).get("function_arn"): item
                for item in existing_response.get("items", [])
                if item.get("attributes", {}).get("function_arn")
            }

            paginator = lambda_client.get_paginator("list_functions")

            for page in paginator.paginate():
                for func in page.get("Functions", []):
                    function_arn = func.get("FunctionArn")
                    function_name = func.get("FunctionName")
                    state = func.get("State", "Active")

                    # Build attributes with Lambda metadata
                    attributes = {
                        "function_arn": function_arn,
                        "function_name": function_name,
                        "runtime": func.get("Runtime"),
                        "handler": func.get("Handler"),
                        "code_size_bytes": func.get("CodeSize"),
                        "memory_mb": func.get("MemorySize"),
                        "timeout_seconds": func.get("Timeout"),
                        "last_modified": func.get("LastModified"),
                        "role_arn": func.get("Role"),
                        "architectures": func.get("Architectures", ["x86_64"]),
                        "package_type": func.get("PackageType", "Zip"),
                        "region": region,
                        "provider": "aws",
                        "service": "lambda",
                    }

                    # Add VPC config if present
                    vpc_config = func.get("VpcConfig", {})
                    if vpc_config.get("VpcId"):
                        attributes["vpc_config"] = {
                            "vpc_id": vpc_config.get("VpcId"),
                            "subnet_ids": vpc_config.get("SubnetIds", []),
                            "security_group_ids": vpc_config.get(
                                "SecurityGroupIds", []
                            ),
                        }

                    # Store environment variable keys only (not values for security)
                    env_vars = func.get("Environment", {}).get("Variables", {})
                    if env_vars:
                        attributes["environment_variable_keys"] = list(env_vars.keys())

                    # Add layer ARNs
                    layers = func.get("Layers", [])
                    if layers:
                        attributes["layers"] = [layer.get("Arn") for layer in layers]

                    # Add ephemeral storage size
                    ephemeral = func.get("EphemeralStorage", {})
                    if ephemeral.get("Size"):
                        attributes["ephemeral_storage_mb"] = ephemeral.get("Size")

                    status_metadata = {
                        "status": state.capitalize(),
                        "timestamp": int(time.time()),
                    }

                    entity = Entity(
                        name=f"Lambda: {function_name}",
                        entity_type="compute",
                        sub_type="serverless",
                        organization_id=region_org_id,
                        description=(
                            func.get("Description")
                            or f"AWS Lambda function in {region}"
                        ),
                        attributes=attributes,
                        status_metadata=status_metadata,
                        tags=["aws", "lambda", "serverless", region],
                    )

                    # Check if entity already exists using the indexed lookup
                    found = existing_entities_by_arn.get(function_arn)

                    if found:
                        await self.elder_client.update_entity(found["id"], entity)
                        updated += 1
                    else:
                        await self.elder_client.create_entity(entity)
                        created += 1

        except ClientError as e:
            self.logger.error(
                "Failed to sync Lambda functions", region=region, error=str(e)
            )

        return created, updated

    async def sync(self) -> SyncResult:
        """
        Synchronize AWS resources to Elder.

        Returns:
            SyncResult with statistics
        """
        result = SyncResult(connector_name=self.name)
        self.logger.info("Starting AWS sync")

        try:
            # Create AWS root organization
            aws_org_id = await self._get_or_create_organization(
                "AWS",
                "Amazon Web Services",
            )
            result.organizations_created += 1

            # Sync S3 buckets (global)
            s3_created, s3_updated = await self._sync_s3_buckets()
            result.entities_created += s3_created
            result.entities_updated += s3_updated

            # Sync per-region resources
            for region in settings.aws_regions_list:
                self.logger.info("Syncing AWS region", region=region)

                # Create region organization
                region_org_id = await self._get_or_create_organization(
                    f"AWS {region}",
                    f"AWS region {region}",
                    parent_id=aws_org_id,
                )
                result.organizations_created += 1

                # Sync VPCs
                vpc_created, vpc_updated = await self._sync_vpcs(region, region_org_id)
                result.entities_created += vpc_created
                result.entities_updated += vpc_updated

                # Sync EC2 instances
                ec2_created, ec2_updated = await self._sync_ec2_instances(
                    region, region_org_id
                )
                result.entities_created += ec2_created
                result.entities_updated += ec2_updated

                # Sync RDS instances (v1.2.1)
                rds_created, rds_updated = await self._sync_rds_instances(
                    region, region_org_id
                )
                result.entities_created += rds_created
                result.entities_updated += rds_updated

                # Sync ElastiCache clusters (v1.2.1)
                cache_created, cache_updated = await self._sync_elasticache_clusters(
                    region, region_org_id
                )
                result.entities_created += cache_created
                result.entities_updated += cache_updated

                # Sync SQS queues (v1.2.1)
                sqs_created, sqs_updated = await self._sync_sqs_queues(
                    region, region_org_id
                )
                result.entities_created += sqs_created
                result.entities_updated += sqs_updated

                # Sync Lambda functions (v3.0.2)
                lambda_created, lambda_updated = await self._sync_lambda_functions(
                    region, region_org_id
                )
                result.entities_created += lambda_created
                result.entities_updated += lambda_updated

            self.logger.info(
                "AWS sync completed",
                total_ops=result.total_operations,
                orgs_created=result.organizations_created,
                entities_created=result.entities_created,
                entities_updated=result.entities_updated,
            )

        except Exception as e:
            error_msg = f"AWS sync failed: {str(e)}"
            self.logger.error(error_msg, exc_info=True)
            result.errors.append(error_msg)

        return result

    async def health_check(self) -> bool:
        """Check AWS connectivity and credentials."""
        try:
            sts = boto3.client(
                "sts",
                aws_access_key_id=settings.aws_access_key_id,
                aws_secret_access_key=settings.aws_secret_access_key,
                region_name=settings.aws_default_region,
            )
            sts.get_caller_identity()
            return True
        except Exception as e:
            self.logger.warning("AWS health check failed", error=str(e))
            return False
