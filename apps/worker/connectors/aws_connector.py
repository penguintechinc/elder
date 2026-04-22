"""AWS connector for syncing AWS resources to Elder."""

# flake8: noqa: E501


import time
from typing import Any, Dict, Optional

import boto3
from botocore.exceptions import ClientError, NoCredentialsError

from apps.worker.config.settings import settings
from apps.worker.connectors.base import BaseConnector, SyncResult
from apps.worker.utils.elder_client import (
    Dependency,
    ElderAPIClient,
    Entity,
    Identity,
    Organization,
)


class AWSConnector(BaseConnector):
    """Connector for AWS resources."""

    def __init__(self):
        """Initialize AWS connector."""
        super().__init__("aws")
        self.elder_client: Optional[ElderAPIClient] = None
        self.aws_clients: Dict[str, Any] = {}
        self.organization_cache: Dict[str, int] = {}
        # external_id -> entity_id cache to resolve dependency targets within a sync run
        self._entity_id_cache: Dict[str, int] = {}

    async def connect(self) -> None:
        """Establish connection to AWS and Elder API."""
        self.logger.info("Connecting to AWS services")

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

        self.elder_client = ElderAPIClient()
        await self.elder_client.connect()

        self.logger.info("AWS connector connected successfully")

    async def disconnect(self) -> None:
        """Disconnect from AWS and Elder API."""
        if self.elder_client:
            await self.elder_client.close()
        self.aws_clients.clear()
        self.organization_cache.clear()
        self._entity_id_cache.clear()
        self.logger.info("AWS connector disconnected")

    def _get_aws_client(self, service: str, region: str):
        """Get or create AWS client for a specific service and region."""
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
        """Get or create an organization in Elder."""
        cache_key = f"{parent_id or 'root'}:{name}"
        if cache_key in self.organization_cache:
            return self.organization_cache[cache_key]

        response = await self.elder_client.list_organizations(per_page=1000)
        for org in response.get("items", []):
            if org["name"] == name and org.get("parent_id") == parent_id:
                self.organization_cache[cache_key] = org["id"]
                return org["id"]

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

    async def _upsert_entity(self, entity: Entity) -> int:
        """
        Create or update entity using external_id for dedup.

        Returns the entity ID (for dependency wiring).
        """
        existing = await self.elder_client.get_entity_by_external_id(
            entity.external_id,
            organization_id=entity.organization_id,
        )

        if existing:
            await self.elder_client.update_entity(existing["id"], entity)
            entity_id = existing["id"]
        else:
            created = await self.elder_client.create_entity(entity)
            entity_id = created["id"]

        if entity.external_id:
            self._entity_id_cache[entity.external_id] = entity_id

        return entity_id

    async def _link(self, source_id: int, target_external_id: str, dep_type: str) -> None:
        """Create a dependency if the target entity is known in the cache."""
        target_id = self._entity_id_cache.get(target_external_id)
        if target_id is None:
            # Target not yet synced this run — resolve via API
            target = await self.elder_client.get_entity_by_external_id(target_external_id)
            if target is None:
                return
            target_id = target["id"]
            self._entity_id_cache[target_external_id] = target_id

        await self.elder_client.get_or_create_dependency(
            source_entity_id=source_id,
            target_entity_id=target_id,
            dependency_type=dep_type,
        )

    async def _sync_vpcs(self, region: str, region_org_id: int) -> tuple[int, int]:
        """Sync VPCs from AWS region."""
        ec2 = self._get_aws_client("ec2", region)
        created = 0
        updated = 0

        try:
            vpcs = ec2.describe_vpcs()
            for vpc in vpcs.get("Vpcs", []):
                vpc_id = vpc["VpcId"]
                vpc_name = next(
                    (tag["Value"] for tag in vpc.get("Tags", []) if tag["Key"] == "Name"),
                    vpc_id,
                )
                state = vpc.get("State", "unknown")
                was_new = await self.elder_client.get_entity_by_external_id(
                    vpc_id, organization_id=region_org_id
                ) is None

                await self._upsert_entity(Entity(
                    name=f"VPC: {vpc_name}",
                    entity_type="vpc",
                    organization_id=region_org_id,
                    description=f"AWS VPC in {region}",
                    external_id=vpc_id,
                    attributes={
                        "vpc_id": vpc_id,
                        "cidr_block": vpc.get("CidrBlock"),
                        "state": state,
                        "is_default": vpc.get("IsDefault", False),
                        "region": region,
                        "provider": "aws",
                    },
                    status_metadata={"status": state.capitalize(), "timestamp": int(time.time())},
                    tags=["aws", "vpc", region],
                ))

                if was_new:
                    created += 1
                else:
                    updated += 1

        except ClientError as e:
            self.logger.error("Failed to sync VPCs", region=region, error=str(e))

        return created, updated

    async def _sync_ec2_instances(self, region: str, region_org_id: int) -> tuple[int, int]:
        """Sync EC2 instances from AWS region."""
        ec2 = self._get_aws_client("ec2", region)
        created = 0
        updated = 0

        try:
            reservations = ec2.describe_instances()
            for reservation in reservations.get("Reservations", []):
                for instance in reservation.get("Instances", []):
                    instance_id = instance["InstanceId"]
                    instance_name = next(
                        (tag["Value"] for tag in instance.get("Tags", []) if tag["Key"] == "Name"),
                        instance_id,
                    )
                    state = instance.get("State", {}).get("Name", "unknown")
                    was_new = await self.elder_client.get_entity_by_external_id(
                        instance_id, organization_id=region_org_id
                    ) is None

                    entity_id = await self._upsert_entity(Entity(
                        name=f"EC2: {instance_name}",
                        entity_type="compute",
                        organization_id=region_org_id,
                        description=f"AWS EC2 instance in {region}",
                        external_id=instance_id,
                        attributes={
                            "instance_id": instance_id,
                            "instance_type": instance.get("InstanceType"),
                            "state": state,
                            "private_ip": instance.get("PrivateIpAddress"),
                            "public_ip": instance.get("PublicIpAddress"),
                            "vpc_id": instance.get("VpcId"),
                            "subnet_id": instance.get("SubnetId"),
                            "availability_zone": instance.get("Placement", {}).get("AvailabilityZone"),
                            "region": region,
                            "provider": "aws",
                            "launch_time": (
                                instance.get("LaunchTime").isoformat()
                                if instance.get("LaunchTime")
                                else None
                            ),
                        },
                        status_metadata={"status": state.capitalize(), "timestamp": int(time.time())},
                        tags=["aws", "ec2", "compute", region],
                        is_active=state == "running",
                    ))

                    # Wire dependencies: EC2 → VPC, subnet, security groups
                    if instance.get("VpcId"):
                        await self._link(entity_id, instance["VpcId"], "in_vpc")
                    if instance.get("SubnetId"):
                        await self._link(entity_id, instance["SubnetId"], "in_subnet")
                    for sg in instance.get("SecurityGroups", []):
                        if sg.get("GroupId"):
                            await self._link(entity_id, sg["GroupId"], "uses_sg")
                    if instance.get("IamInstanceProfile", {}).get("Arn"):
                        role_arn = instance["IamInstanceProfile"]["Arn"]
                        await self._link(entity_id, role_arn, "assumes_role")

                    if was_new:
                        created += 1
                    else:
                        updated += 1

        except ClientError as e:
            self.logger.error("Failed to sync EC2 instances", region=region, error=str(e))

        return created, updated

    async def _sync_rds_instances(self, region: str, region_org_id: int) -> tuple[int, int]:
        """Sync RDS instances (including Aurora) from AWS region."""
        rds = self._get_aws_client("rds", region)
        created = 0
        updated = 0

        try:
            response = rds.describe_db_instances()
            for db_instance in response.get("DBInstances", []):
                instance_id = db_instance["DBInstanceIdentifier"]
                engine = db_instance.get("Engine", "unknown")
                is_aurora = engine.startswith("aurora")
                status = db_instance.get("DBInstanceStatus", "unknown")
                was_new = await self.elder_client.get_entity_by_external_id(
                    instance_id, organization_id=region_org_id
                ) is None

                entity_id = await self._upsert_entity(Entity(
                    name=f"RDS: {instance_id}" + (" (Aurora)" if is_aurora else ""),
                    entity_type="storage",
                    sub_type="database",
                    organization_id=region_org_id,
                    description=f"AWS RDS {engine} database in {region}",
                    external_id=instance_id,
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
                    status_metadata={"status": status.capitalize(), "timestamp": int(time.time())},
                    tags=["aws", "rds", "database", engine, region] + (["aurora"] if is_aurora else []),
                    is_active=status.lower() == "available",
                ))

                vpc_id = (
                    db_instance.get("DBSubnetGroup", {}).get("VpcId")
                    if db_instance.get("DBSubnetGroup")
                    else None
                )
                if vpc_id:
                    await self._link(entity_id, vpc_id, "in_vpc")

                if was_new:
                    created += 1
                else:
                    updated += 1

        except ClientError as e:
            self.logger.error("Failed to sync RDS instances", region=region, error=str(e))

        return created, updated

    async def _sync_elasticache_clusters(self, region: str, region_org_id: int) -> tuple[int, int]:
        """Sync Elasticache clusters from AWS region."""
        elasticache = self._get_aws_client("elasticache", region)
        created = 0
        updated = 0

        try:
            response = elasticache.describe_cache_clusters()
            for cluster in response.get("CacheClusters", []):
                cluster_id = cluster["CacheClusterId"]
                engine = cluster.get("Engine", "unknown")
                status = cluster.get("CacheClusterStatus", "unknown")
                was_new = await self.elder_client.get_entity_by_external_id(
                    cluster_id, organization_id=region_org_id
                ) is None

                await self._upsert_entity(Entity(
                    name=f"ElastiCache: {cluster_id}",
                    entity_type="storage",
                    sub_type="caching",
                    organization_id=region_org_id,
                    description=f"AWS ElastiCache {engine} cluster in {region}",
                    external_id=cluster_id,
                    attributes={
                        "cluster_id": cluster_id,
                        "engine": engine,
                        "engine_version": cluster.get("EngineVersion"),
                        "node_type": cluster.get("CacheNodeType"),
                        "num_cache_nodes": cluster.get("NumCacheNodes", 0),
                        "status": status,
                        "endpoint": (
                            cluster.get("CacheNodes", [{}])[0].get("Endpoint", {}).get("Address")
                            if cluster.get("CacheNodes")
                            else None
                        ),
                        "port": (
                            cluster.get("CacheNodes", [{}])[0].get("Endpoint", {}).get("Port")
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
                    status_metadata={"status": status.capitalize(), "timestamp": int(time.time())},
                    tags=["aws", "elasticache", "cache", engine, region],
                    is_active=status.lower() == "available",
                ))

                if was_new:
                    created += 1
                else:
                    updated += 1

        except ClientError as e:
            self.logger.error("Failed to sync ElastiCache clusters", region=region, error=str(e))

        return created, updated

    async def _sync_sqs_queues(self, region: str, region_org_id: int) -> tuple[int, int]:
        """Sync SQS queues from AWS region."""
        sqs = self._get_aws_client("sqs", region)
        created = 0
        updated = 0

        try:
            response = sqs.list_queues()
            queue_urls = response.get("QueueUrls", [])

            for queue_url in queue_urls:
                queue_name = queue_url.split("/")[-1]

                try:
                    attrs = sqs.get_queue_attributes(QueueUrl=queue_url, AttributeNames=["All"])
                    attributes = attrs.get("Attributes", {})
                    queue_arn = attributes.get("QueueArn", queue_url)
                    was_new = await self.elder_client.get_entity_by_external_id(
                        queue_arn, organization_id=region_org_id
                    ) is None

                    await self._upsert_entity(Entity(
                        name=f"SQS: {queue_name}",
                        entity_type="storage",
                        sub_type="queue_system",
                        organization_id=region_org_id,
                        description=f"AWS SQS queue in {region}",
                        external_id=queue_arn,
                        attributes={
                            "queue_url": queue_url,
                            "queue_name": queue_name,
                            "queue_arn": queue_arn,
                            "approximate_messages": int(attributes.get("ApproximateNumberOfMessages", 0)),
                            "message_retention_seconds": int(attributes.get("MessageRetentionPeriod", 0)),
                            "visibility_timeout": int(attributes.get("VisibilityTimeout", 0)),
                            "delay_seconds": int(attributes.get("DelaySeconds", 0)),
                            "receive_wait_time": int(attributes.get("ReceiveMessageWaitTimeSeconds", 0)),
                            "is_fifo": queue_name.endswith(".fifo"),
                            "region": region,
                            "provider": "aws",
                            "service": "sqs",
                            "created_timestamp": int(attributes.get("CreatedTimestamp", 0)),
                        },
                        status_metadata={"status": "Available", "timestamp": int(time.time())},
                        tags=["aws", "sqs", "queue", region],
                        is_active=True,
                    ))

                    if was_new:
                        created += 1
                    else:
                        updated += 1

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
        """Sync S3 buckets (S3 is global)."""
        s3 = self._get_aws_client("s3", settings.aws_default_region)
        created = 0
        updated = 0

        aws_org_id = await self._get_or_create_organization("AWS", "Amazon Web Services")

        try:
            buckets = s3.list_buckets()
            for bucket in buckets.get("Buckets", []):
                bucket_name = bucket["Name"]
                bucket_arn = f"arn:aws:s3:::{bucket_name}"

                try:
                    location = s3.get_bucket_location(Bucket=bucket_name)
                    region = location.get("LocationConstraint") or "us-east-1"
                except Exception as e:
                    self.logger.warning("Failed to get bucket location", bucket=bucket_name, error=str(e))
                    region = "unknown"

                was_new = await self.elder_client.get_entity_by_external_id(
                    bucket_arn, organization_id=aws_org_id
                ) is None

                await self._upsert_entity(Entity(
                    name=f"S3: {bucket_name}",
                    entity_type="network",
                    organization_id=aws_org_id,
                    description="AWS S3 bucket",
                    external_id=bucket_arn,
                    attributes={
                        "bucket_name": bucket_name,
                        "bucket_arn": bucket_arn,
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
                ))

                if was_new:
                    created += 1
                else:
                    updated += 1

        except ClientError as e:
            self.logger.error("Failed to sync S3 buckets", error=str(e))

        return created, updated

    async def _sync_lambda_functions(self, region: str, region_org_id: int) -> tuple[int, int]:
        """Sync AWS Lambda functions for a region."""
        lambda_client = self._get_aws_client("lambda", region)
        created = 0
        updated = 0

        try:
            paginator = lambda_client.get_paginator("list_functions")

            for page in paginator.paginate():
                for func in page.get("Functions", []):
                    function_arn = func.get("FunctionArn")
                    function_name = func.get("FunctionName")
                    state = func.get("State", "Active")

                    attributes: Dict[str, Any] = {
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

                    vpc_config = func.get("VpcConfig", {})
                    if vpc_config.get("VpcId"):
                        attributes["vpc_config"] = {
                            "vpc_id": vpc_config.get("VpcId"),
                            "subnet_ids": vpc_config.get("SubnetIds", []),
                            "security_group_ids": vpc_config.get("SecurityGroupIds", []),
                        }

                    env_vars = func.get("Environment", {}).get("Variables", {})
                    if env_vars:
                        attributes["environment_variable_keys"] = list(env_vars.keys())

                    layers = func.get("Layers", [])
                    if layers:
                        attributes["layers"] = [layer.get("Arn") for layer in layers]

                    ephemeral = func.get("EphemeralStorage", {})
                    if ephemeral.get("Size"):
                        attributes["ephemeral_storage_mb"] = ephemeral.get("Size")

                    was_new = await self.elder_client.get_entity_by_external_id(
                        function_arn, organization_id=region_org_id
                    ) is None

                    entity_id = await self._upsert_entity(Entity(
                        name=f"Lambda: {function_name}",
                        entity_type="compute",
                        sub_type="serverless",
                        organization_id=region_org_id,
                        description=(func.get("Description") or f"AWS Lambda function in {region}"),
                        external_id=function_arn,
                        attributes=attributes,
                        status_metadata={"status": state.capitalize(), "timestamp": int(time.time())},
                        tags=["aws", "lambda", "serverless", region],
                    ))

                    # Wire dependencies: Lambda → VPC, security groups, IAM role
                    if vpc_config.get("VpcId"):
                        await self._link(entity_id, vpc_config["VpcId"], "in_vpc")
                    for sg_id in vpc_config.get("SecurityGroupIds", []):
                        await self._link(entity_id, sg_id, "uses_sg")
                    if func.get("Role"):
                        await self._link(entity_id, func["Role"], "assumes_role")

                    if was_new:
                        created += 1
                    else:
                        updated += 1

        except ClientError as e:
            self.logger.error("Failed to sync Lambda functions", region=region, error=str(e))

        return created, updated

    async def _sync_iam_identities(self) -> tuple[int, int]:
        """
        Sync IAM users and roles as Elder identities.

        IAM users → identity_type "human" (service users in practice)
        IAM roles → identity_type "service_account"
        """
        iam = self._get_aws_client("iam", settings.aws_default_region)
        created = 0
        updated = 0

        # Sync IAM users
        try:
            paginator = iam.get_paginator("list_users")
            for page in paginator.paginate():
                for user in page.get("Users", []):
                    arn = user["Arn"]
                    username = user["UserName"]
                    result = await self.elder_client.get_or_create_identity(
                        Identity(
                            username=f"aws:{username}",
                            identity_type="service_account",
                            auth_provider="aws",
                            auth_provider_id=arn,
                            full_name=username,
                            is_active=True,
                        )
                    )
                    # get_or_create returns existing or newly created; track via presence of created_at
                    if result.get("id"):
                        created += 1
        except ClientError as e:
            self.logger.error("Failed to sync IAM users", error=str(e))

        # Sync IAM roles
        try:
            paginator = iam.get_paginator("list_roles")
            for page in paginator.paginate():
                for role in page.get("Roles", []):
                    arn = role["Arn"]
                    role_name = role["RoleName"]
                    result = await self.elder_client.get_or_create_identity(
                        Identity(
                            username=f"aws-role:{role_name}",
                            identity_type="service_account",
                            auth_provider="aws",
                            auth_provider_id=arn,
                            full_name=f"IAM Role: {role_name}",
                            is_active=True,
                        )
                    )
                    if result.get("id"):
                        # Store in cache so Lambda/EC2 dependency links can use role ARN
                        self._entity_id_cache[arn] = result["id"]
                        created += 1
        except ClientError as e:
            self.logger.error("Failed to sync IAM roles", error=str(e))

        return created, updated

    async def sync(self) -> SyncResult:
        """Synchronize AWS resources to Elder."""
        result = SyncResult(connector_name=self.name)
        self.logger.info("Starting AWS sync")
        self._entity_id_cache.clear()

        try:
            aws_org_id = await self._get_or_create_organization("AWS", "Amazon Web Services")
            result.organizations_created += 1

            # IAM identities first so role ARNs are in cache for dependency wiring
            iam_created, _ = await self._sync_iam_identities()
            result.entities_created += iam_created

            # S3 (global)
            s3_created, s3_updated = await self._sync_s3_buckets()
            result.entities_created += s3_created
            result.entities_updated += s3_updated

            for region in settings.aws_regions_list:
                self.logger.info("Syncing AWS region", region=region)

                region_org_id = await self._get_or_create_organization(
                    f"AWS {region}",
                    f"AWS region {region}",
                    parent_id=aws_org_id,
                )
                result.organizations_created += 1

                vpc_created, vpc_updated = await self._sync_vpcs(region, region_org_id)
                result.entities_created += vpc_created
                result.entities_updated += vpc_updated

                ec2_created, ec2_updated = await self._sync_ec2_instances(region, region_org_id)
                result.entities_created += ec2_created
                result.entities_updated += ec2_updated

                rds_created, rds_updated = await self._sync_rds_instances(region, region_org_id)
                result.entities_created += rds_created
                result.entities_updated += rds_updated

                cache_created, cache_updated = await self._sync_elasticache_clusters(region, region_org_id)
                result.entities_created += cache_created
                result.entities_updated += cache_updated

                sqs_created, sqs_updated = await self._sync_sqs_queues(region, region_org_id)
                result.entities_created += sqs_created
                result.entities_updated += sqs_updated

                lambda_created, lambda_updated = await self._sync_lambda_functions(region, region_org_id)
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
