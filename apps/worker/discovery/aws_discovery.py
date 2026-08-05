"""AWS cloud discovery client for Elder.

Supports multiple authentication methods:
1. Static credentials (access_key_id + secret_access_key)
2. IAM roles (instance profiles, ECS task roles)
3. Web Identity / OIDC (role_arn + web_identity_token_file)
4. AWS SSO / IAM Identity Center
"""

# flake8: noqa: E501


import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

try:
    import boto3
    from botocore.exceptions import BotoCoreError, ClientError
except ImportError:
    boto3 = None

from apps.worker.discovery.base import BaseDiscoveryProvider

logger = logging.getLogger(__name__)


class AWSDiscoveryClient(BaseDiscoveryProvider):
    """AWS cloud resource discovery implementation.

    Supports multiple authentication methods:
    - Static credentials: Traditional access key + secret key
    - IAM roles: Instance profiles, ECS task roles, Lambda execution roles
    - Web Identity/OIDC: GitHub Actions, Kubernetes IRSA, GitLab CI
    - Environment: Uses AWS_* environment variables automatically
    """

    # Authentication method constants
    AUTH_STATIC_CREDENTIALS = "static_credentials"
    AUTH_IAM_ROLE = "iam_role"
    AUTH_WEB_IDENTITY = "web_identity"
    AUTH_ENVIRONMENT = "environment"

    def __init__(self, config: Dict[str, Any]):
        """
        Initialize AWS discovery client.

        Args:
            config: Configuration with AWS credentials and region
                {
                    "provider_type": "aws",
                    "region": "us-east-1",

                    # Option 1: Static credentials
                    "access_key_id": "AKIA...",
                    "secret_access_key": "...",

                    # Option 2: Web Identity / OIDC
                    "role_arn": "arn:aws:iam::123456789012:role/ElderDiscoveryRole",
                    "web_identity_token_file": "/var/run/secrets/.../token",
                    "role_session_name": "elder-discovery",  # optional

                    # Option 3: Assume Role (cross-account)
                    "assume_role_arn": "arn:aws:iam::TARGET:role/ElderDiscoveryRole",
                    "external_id": "...",  # optional

                    # Common options
                    "services": ["ec2", "rds", "s3", "lambda"],  # optional filter
                }
        """
        super().__init__(config)

        if boto3 is None:
            raise ImportError(
                "boto3 is required for AWS discovery. Install with: pip install boto3"
            )

        self.region = config.get("region") or os.environ.get(
            "AWS_DEFAULT_REGION", "us-east-1"
        )
        self.services = config.get("services", [])  # Empty = discover all
        self.auth_method = None

        # Initialize boto3 session based on authentication method
        self.session = self._create_session(config)

    def _create_session(self, config: Dict[str, Any]) -> "boto3.Session":
        """
        Create boto3 session using the appropriate authentication method.

        Authentication priority:
        1. Explicit static credentials in config
        2. Web Identity / OIDC if role_arn and token file provided
        3. Environment variables (AWS_ACCESS_KEY_ID, AWS_ROLE_ARN, etc.)
        4. IAM role (instance profile, ECS task role, etc.)
        """
        session_config = {"region_name": self.region}

        # Option 1: Static credentials provided in config
        if config.get("access_key_id") and config.get("secret_access_key"):
            logger.info("Using static credentials for AWS authentication")
            self.auth_method = self.AUTH_STATIC_CREDENTIALS
            session_config["aws_access_key_id"] = config["access_key_id"]
            session_config["aws_secret_access_key"] = config["secret_access_key"]
            if config.get("session_token"):
                session_config["aws_session_token"] = config["session_token"]
            return boto3.Session(**session_config)

        # Option 2: Web Identity / OIDC
        role_arn = config.get("role_arn") or os.environ.get("AWS_ROLE_ARN")
        token_file = config.get("web_identity_token_file") or os.environ.get(
            "AWS_WEB_IDENTITY_TOKEN_FILE"
        )

        if role_arn and token_file:
            logger.info(f"Using Web Identity/OIDC authentication with role: {role_arn}")
            self.auth_method = self.AUTH_WEB_IDENTITY
            return self._create_web_identity_session(
                role_arn=role_arn,
                token_file=token_file,
                session_name=config.get("role_session_name", "elder-discovery"),
            )

        # Option 3: Environment variables or IAM role
        # boto3 automatically uses:
        # - AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY
        # - AWS_PROFILE
        # - Instance metadata (EC2, ECS, Lambda)
        logger.info("Using environment/IAM role for AWS authentication")
        self.auth_method = self.AUTH_ENVIRONMENT
        return boto3.Session(**session_config)

    def _create_web_identity_session(
        self,
        role_arn: str,
        token_file: str,
        session_name: str = "elder-discovery",
    ) -> "boto3.Session":
        """
        Create a boto3 session using Web Identity (OIDC) federation.

        This method reads the OIDC token from the specified file and uses
        STS AssumeRoleWithWebIdentity to obtain temporary credentials.

        Args:
            role_arn: The ARN of the IAM role to assume
            token_file: Path to the file containing the OIDC token
            session_name: Name for the role session (for CloudTrail)

        Returns:
            boto3.Session configured with temporary credentials
        """
        # Read the web identity token
        try:
            with open(token_file, "r") as f:
                web_identity_token = f.read().strip()
        except FileNotFoundError:
            raise ValueError(f"Web identity token file not found: {token_file}")
        except IOError as e:
            raise ValueError(f"Failed to read web identity token: {e}")

        # Create a basic session to call STS
        base_session = boto3.Session(region_name=self.region)
        sts_client = base_session.client("sts")

        # Assume role with web identity
        try:
            response = sts_client.assume_role_with_web_identity(
                RoleArn=role_arn,
                RoleSessionName=session_name,
                WebIdentityToken=web_identity_token,
            )
        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "Unknown")
            error_msg = e.response.get("Error", {}).get("Message", str(e))
            raise ValueError(
                f"Failed to assume role with web identity: {error_code} - {error_msg}"
            )

        # Extract credentials from response
        credentials = response["Credentials"]

        # Create new session with temporary credentials
        return boto3.Session(
            aws_access_key_id=credentials["AccessKeyId"],
            aws_secret_access_key=credentials["SecretAccessKey"],
            aws_session_token=credentials["SessionToken"],
            region_name=self.region,
        )

    def get_auth_method(self) -> str:
        """Return the authentication method being used."""
        return self.auth_method or "unknown"

    def get_caller_identity(self) -> Optional[Dict[str, str]]:
        """
        Get the AWS identity of the current session.

        Returns:
            Dict with Account, Arn, UserId or None if failed
        """
        try:
            sts = self.session.client("sts")
            identity = sts.get_caller_identity()
            return {
                "account": identity.get("Account"),
                "arn": identity.get("Arn"),
                "user_id": identity.get("UserId"),
            }
        except (ClientError, BotoCoreError) as e:
            logger.error(f"Failed to get caller identity: {e}")
            return None

    def test_connection(self) -> bool:
        """Test AWS connectivity using STS get_caller_identity."""
        try:
            sts = self.session.client("sts")
            sts.get_caller_identity()
            return True
        except (ClientError, BotoCoreError):
            return False

    def get_supported_services(self) -> List[str]:
        """Get list of AWS services supported for discovery."""
        return [
            "ec2",  # EC2 instances
            "rds",  # RDS databases
            "s3",  # S3 buckets
            "lambda",  # Lambda functions
            "vpc",  # VPCs and subnets
            "elb",  # Load balancers
            "ebs",  # EBS volumes
            "iam",  # IAM users and roles
            "dynamodb",  # DynamoDB tables
            "sqs",  # SQS queues
            "sns",  # SNS topics
            "ecr",  # ECR repositories
            "logs",  # CloudWatch log groups
            "stepfunctions",  # Step Functions state machines
            "events",  # EventBridge rules
            "apigateway",  # API Gateway REST APIs
            "efs",  # EFS file systems
            "elasticache",  # ElastiCache clusters
            "cloudfront",  # CloudFront distributions
            "route53",  # Route 53 hosted zones
        ]

    def discover_all(self) -> Dict[str, Any]:
        """Discover all AWS resources."""
        start_time = datetime.now(timezone.utc)

        results = {
            "compute": [],
            "storage": [],
            "network": [],
            "database": [],
            "serverless": [],
        }

        # Discover each category
        if not self.services or "ec2" in self.services:
            results["compute"].extend(self.discover_compute())

        if not self.services or any(
            s in self.services
            for s in [
                "s3",
                "ebs",
                "ecr",
                "logs",
                "efs",
                "elasticache",
                "dynamodb",
            ]
        ):
            results["storage"].extend(self.discover_storage())

        if not self.services or any(
            s in self.services
            for s in ["vpc", "elb", "cloudfront", "route53", "apigateway"]
        ):
            results["network"].extend(self.discover_network())

        if not self.services or any(s in self.services for s in ["rds", "dynamodb"]):
            results["database"].extend(self.discover_databases())

        if not self.services or any(
            s in self.services
            for s in ["lambda", "sqs", "sns", "stepfunctions", "events"]
        ):
            results["serverless"].extend(self.discover_serverless())

        # Discover IAM users and roles
        if not self.services or "iam" in self.services:
            results["iam"] = self.discover_iam()

        # Calculate totals
        resources_count = sum(len(resources) for resources in results.values())

        return {
            **results,
            "resources_count": resources_count,
            "discovery_time": datetime.now(timezone.utc),
            "duration_seconds": (
                datetime.now(timezone.utc) - start_time
            ).total_seconds(),
        }

    # EC2 keeps terminated instances visible to describe_instances for roughly
    # an hour. They are not assets, so they are excluded server-side rather than
    # inventoried and then aged out.
    _LIVE_INSTANCE_STATES = [
        "pending",
        "running",
        "shutting-down",
        "stopping",
        "stopped",
    ]

    def discover_compute(self) -> List[Dict[str, Any]]:
        """Discover EC2 instances, excluding terminated ones."""
        resources = []

        try:
            ec2 = self.session.client("ec2")
            paginator = ec2.get_paginator("describe_instances")

            for page in paginator.paginate(
                Filters=[
                    {
                        "Name": "instance-state-name",
                        "Values": self._LIVE_INSTANCE_STATES,
                    }
                ]
            ):
                for reservation in page.get("Reservations", []):
                    for instance in reservation.get("Instances", []):
                        instance_id = instance["InstanceId"]
                        relationships = []

                        # EC2 -> VPC (in_network)
                        if instance.get("VpcId"):
                            relationships.append({
                                "target_external_id": instance["VpcId"],
                                "target_kind": "networking_resource",
                                "edge_type": "in_network",
                            })

                        # EC2 -> Subnet (in_subnet)
                        if instance.get("SubnetId"):
                            relationships.append({
                                "target_external_id": instance["SubnetId"],
                                "target_kind": "networking_resource",
                                "edge_type": "in_subnet",
                            })

                        # EC2 -> Security Groups (uses_security_group)
                        for sg in instance.get("SecurityGroups", []):
                            relationships.append({
                                "target_external_id": sg["GroupId"],
                                "target_kind": "networking_resource",
                                "edge_type": "uses_security_group",
                            })

                        # EC2 -> IAM Role (assumes_role)
                        if instance.get("IamInstanceProfile"):
                            profile_arn = instance["IamInstanceProfile"].get("Arn")
                            if profile_arn:
                                try:
                                    # Resolve instance profile ARN to role ARN
                                    iam = self.session.client("iam")
                                    profile_name = profile_arn.split("/")[-1]
                                    profile_info = iam.get_instance_profile(
                                        InstanceProfileName=profile_name
                                    )
                                    roles = profile_info.get("InstanceProfile", {}).get(
                                        "Roles", []
                                    )
                                    if roles:
                                        role_arn = roles[0].get("Arn")
                                        if role_arn:
                                            relationships.append({
                                                "target_external_id": role_arn,
                                                "target_kind": "identity",
                                                "edge_type": "assumes_role",
                                            })
                                except Exception as e:
                                    logger.warning(
                                        f"Failed to resolve instance profile {profile_arn}: {e}"
                                    )

                        resource = self.format_resource(
                            resource_id=instance_id,
                            resource_type="ec2_instance",
                            name=self._get_name_from_tags(
                                instance.get("Tags", []), instance_id
                            ),
                            metadata={
                                "instance_type": instance.get("InstanceType"),
                                "state": instance.get("State", {}).get("Name"),
                                "private_ip": instance.get("PrivateIpAddress"),
                                "public_ip": instance.get("PublicIpAddress"),
                                "vpc_id": instance.get("VpcId"),
                                "subnet_id": instance.get("SubnetId"),
                                "launch_time": (
                                    instance.get("LaunchTime").isoformat()
                                    if instance.get("LaunchTime")
                                    else None
                                ),
                            },
                            region=self.region,
                            tags=self._normalize_tags(instance.get("Tags", [])),
                            external_id=instance_id,
                            relationships=relationships,
                        )
                        resources.append(resource)

        except (ClientError, BotoCoreError) as e:
            logger.warning(f"Failed to discover EC2 instances: {e}")

        return resources

    def discover_storage(self) -> List[Dict[str, Any]]:
        """Discover S3 buckets, EBS volumes, and Free Tier storage services."""
        resources = []

        # S3 Buckets
        if not self.services or "s3" in self.services:
            try:
                s3 = self.session.client("s3")
                response = s3.list_buckets()

                for bucket in response.get("Buckets", []):
                    bucket_name = bucket["Name"]

                    # Get bucket location
                    try:
                        location = s3.get_bucket_location(Bucket=bucket_name)
                        bucket_region = (
                            location.get("LocationConstraint") or "us-east-1"
                        )
                    except:
                        bucket_region = "unknown"

                    # Get bucket tags
                    try:
                        tags_response = s3.get_bucket_tagging(Bucket=bucket_name)
                        tags = self._normalize_tags(tags_response.get("TagSet", []))
                    except:
                        tags = {}

                    resource = self.format_resource(
                        resource_id=bucket_name,
                        resource_type="s3_bucket",
                        name=bucket_name,
                        metadata={
                            "creation_date": (
                                bucket.get("CreationDate").isoformat()
                                if bucket.get("CreationDate")
                                else None
                            ),
                        },
                        region=bucket_region,
                        tags=tags,
                    )
                    resources.append(resource)

            except (ClientError, BotoCoreError) as e:
                logger.warning(f"Failed to discover S3 buckets: {e}")

        # EBS Volumes
        if not self.services or "ebs" in self.services:
            try:
                ec2 = self.session.client("ec2")
                paginator = ec2.get_paginator("describe_volumes")

                for page in paginator.paginate():
                    for volume in page.get("Volumes", []):
                        volume_id = volume["VolumeId"]
                        relationships = []

                        # EBS Volume -> EC2 instance (attached_to)
                        for attachment in volume.get("Attachments", []):
                            if attachment.get("InstanceId"):
                                relationships.append({
                                    "target_external_id": attachment["InstanceId"],
                                    "target_kind": "entity",
                                    "edge_type": "attached_to",
                                })

                        resource = self.format_resource(
                            resource_id=volume_id,
                            resource_type="ebs_volume",
                            name=self._get_name_from_tags(
                                volume.get("Tags", []), volume_id
                            ),
                            metadata={
                                "size_gb": volume.get("Size"),
                                "volume_type": volume.get("VolumeType"),
                                "state": volume.get("State"),
                                "iops": volume.get("Iops"),
                                "encrypted": volume.get("Encrypted"),
                                "availability_zone": volume.get("AvailabilityZone"),
                            },
                            region=self.region,
                            tags=self._normalize_tags(volume.get("Tags", [])),
                            external_id=volume_id,
                            relationships=relationships,
                        )
                        resources.append(resource)

            except (ClientError, BotoCoreError) as e:
                logger.warning(f"Failed to discover EBS volumes: {e}")

        # DynamoDB Tables
        if not self.services or "dynamodb" in self.services:
            resources.extend(self.discover_dynamodb())

        # ECR Repositories
        if not self.services or "ecr" in self.services:
            resources.extend(self.discover_ecr())

        # CloudWatch Log Groups
        if not self.services or "logs" in self.services:
            resources.extend(self.discover_logs())

        # EFS File Systems
        if not self.services or "efs" in self.services:
            resources.extend(self.discover_efs())

        # ElastiCache Clusters
        if not self.services or "elasticache" in self.services:
            resources.extend(self.discover_elasticache())

        return resources

    def discover_network(self) -> List[Dict[str, Any]]:
        """Discover VPCs, subnets, load balancers, and Free Tier networking services."""
        resources = []

        # VPCs
        if not self.services or "vpc" in self.services:
            try:
                ec2 = self.session.client("ec2")
                paginator = ec2.get_paginator("describe_vpcs")

                for page in paginator.paginate():
                    for vpc in page.get("Vpcs", []):
                        vpc_id = vpc["VpcId"]
                        resource = self.format_resource(
                            resource_id=vpc_id,
                            resource_type="vpc",
                            name=self._get_name_from_tags(
                                vpc.get("Tags", []), vpc_id
                            ),
                            metadata={
                                "cidr_block": vpc.get("CidrBlock"),
                                "state": vpc.get("State"),
                                "is_default": vpc.get("IsDefault"),
                            },
                            region=self.region,
                            tags=self._normalize_tags(vpc.get("Tags", [])),
                            external_id=vpc_id,
                        )
                        resources.append(resource)

            except (ClientError, BotoCoreError) as e:
                logger.warning(f"Failed to discover VPCs: {e}")

            # Subnets
            try:
                ec2 = self.session.client("ec2")
                paginator = ec2.get_paginator("describe_subnets")

                for page in paginator.paginate():
                    for subnet in page.get("Subnets", []):
                        subnet_id = subnet["SubnetId"]
                        relationships = []

                        # Subnet -> VPC (in_network)
                        if subnet.get("VpcId"):
                            relationships.append({
                                "target_external_id": subnet["VpcId"],
                                "target_kind": "networking_resource",
                                "edge_type": "in_network",
                            })

                        resource = self.format_resource(
                            resource_id=subnet_id,
                            resource_type="subnet",
                            name=self._get_name_from_tags(
                                subnet.get("Tags", []), subnet_id
                            ),
                            metadata={
                                "vpc_id": subnet.get("VpcId"),
                                "cidr_block": subnet.get("CidrBlock"),
                                "availability_zone": subnet.get("AvailabilityZone"),
                                "available_ip_addresses": subnet.get(
                                    "AvailableIpAddressCount"
                                ),
                            },
                            region=self.region,
                            tags=self._normalize_tags(subnet.get("Tags", [])),
                            external_id=subnet_id,
                            relationships=relationships,
                        )
                        resources.append(resource)

            except (ClientError, BotoCoreError) as e:
                logger.warning(f"Failed to discover subnets: {e}")

            # Security Groups
            resources.extend(self.discover_security_groups())

        # Load Balancers (ELBv2)
        if not self.services or "elb" in self.services:
            try:
                elbv2 = self.session.client("elbv2")
                paginator = elbv2.get_paginator("describe_load_balancers")

                for page in paginator.paginate():
                    for lb in page.get("LoadBalancers", []):
                        lb_arn = lb["LoadBalancerArn"]
                        relationships = []

                        # Get tags for load balancer
                        try:
                            tags_response = elbv2.describe_tags(
                                ResourceArns=[lb_arn]
                            )
                            tags_list = tags_response.get("TagDescriptions", [{}])[
                                0
                            ].get("Tags", [])
                            tags = self._normalize_tags(tags_list)
                        except:
                            tags = {}

                        # ELB -> VPC (in_network)
                        vpc_id = lb.get("VpcId")
                        if vpc_id:
                            relationships.append({
                                "target_external_id": vpc_id,
                                "target_kind": "networking_resource",
                                "edge_type": "in_network",
                            })

                        # ELB -> Target Instances (routes_to)
                        try:
                            # Get target groups for this LB
                            tg_response = elbv2.describe_target_groups(
                                LoadBalancerArn=lb_arn
                            )
                            for tg in tg_response.get("TargetGroups", []):
                                tg_arn = tg.get("TargetGroupArn")
                                if tg_arn:
                                    # Get target health for this TG
                                    health_response = (
                                        elbv2.describe_target_health(
                                            TargetGroupArn=tg_arn
                                        )
                                    )
                                    for target in health_response.get(
                                        "TargetHealthDescriptions", []
                                    ):
                                        target_id = target.get("Target", {}).get(
                                            "Id"
                                        )
                                        if target_id:
                                            relationships.append({
                                                "target_external_id": target_id,
                                                "target_kind": "entity",
                                                "edge_type": "routes_to",
                                            })
                        except Exception as e:
                            logger.warning(
                                f"Failed to enumerate target groups for LB {lb_arn}: {e}"
                            )

                        resource = self.format_resource(
                            resource_id=lb_arn,
                            resource_type="load_balancer",
                            name=lb.get("LoadBalancerName"),
                            metadata={
                                "type": lb.get("Type"),
                                "scheme": lb.get("Scheme"),
                                "vpc_id": vpc_id,
                                "state": lb.get("State", {}).get("Code"),
                                "dns_name": lb.get("DNSName"),
                            },
                            region=self.region,
                            tags=tags,
                            external_id=lb_arn,
                            relationships=relationships,
                        )
                        resources.append(resource)

            except (ClientError, BotoCoreError) as e:
                logger.warning(f"Failed to discover load balancers: {e}")

        # API Gateway REST APIs
        if not self.services or "apigateway" in self.services:
            resources.extend(self.discover_apigateway())

        # CloudFront Distributions
        if not self.services or "cloudfront" in self.services:
            resources.extend(self.discover_cloudfront())

        # Route 53 Hosted Zones
        if not self.services or "route53" in self.services:
            resources.extend(self.discover_route53())

        return resources

    def discover_security_groups(self) -> List[Dict[str, Any]]:
        """Discover security groups and their VPC associations."""
        resources = []

        try:
            ec2 = self.session.client("ec2")
            paginator = ec2.get_paginator("describe_security_groups")

            for page in paginator.paginate():
                for sg in page.get("SecurityGroups", []):
                    sg_id = sg["GroupId"]
                    relationships = []

                    # Security Group -> VPC (in_network)
                    if sg.get("VpcId"):
                        relationships.append({
                            "target_external_id": sg["VpcId"],
                            "target_kind": "networking_resource",
                            "edge_type": "in_network",
                        })

                    resource = self.format_resource(
                        resource_id=sg_id,
                        resource_type="security_group",
                        name=sg.get("GroupName", sg_id),
                        metadata={
                            "group_name": sg.get("GroupName"),
                            "vpc_id": sg.get("VpcId"),
                            "description": sg.get("GroupDescription"),
                            "ingress_rules": len(sg.get("IpPermissions", [])),
                            "egress_rules": len(sg.get("IpPermissionsEgress", [])),
                        },
                        region=self.region,
                        tags=self._normalize_tags(sg.get("Tags", [])),
                        external_id=sg_id,
                        relationships=relationships,
                    )
                    resources.append(resource)

        except (ClientError, BotoCoreError) as e:
            logger.warning(f"Failed to discover security groups: {e}")

        return resources

    def discover_databases(self) -> List[Dict[str, Any]]:
        """Discover RDS databases and DynamoDB tables."""
        resources = []

        # RDS Instances
        try:
            rds = self.session.client("rds")
            paginator = rds.get_paginator("describe_db_instances")

            for page in paginator.paginate():
                for db_instance in page.get("DBInstances", []):
                    db_instance_arn = db_instance["DBInstanceArn"]
                    relationships = []

                    # Get tags for DB instance
                    try:
                        tags_response = rds.list_tags_for_resource(
                            ResourceName=db_instance_arn
                        )
                        tags = self._normalize_tags(tags_response.get("TagList", []))
                    except:
                        tags = {}

                    # RDS -> VPC (in_network)
                    db_subnet_group = db_instance.get("DBSubnetGroup", {})
                    vpc_id = db_subnet_group.get("VpcId")
                    if vpc_id:
                        relationships.append({
                            "target_external_id": vpc_id,
                            "target_kind": "networking_resource",
                            "edge_type": "in_network",
                        })

                    # RDS -> Security Groups (uses_security_group)
                    for vpc_sg in db_instance.get("VpcSecurityGroups", []):
                        sg_id = vpc_sg.get("VpcSecurityGroupId")
                        if sg_id:
                            relationships.append({
                                "target_external_id": sg_id,
                                "target_kind": "networking_resource",
                                "edge_type": "uses_security_group",
                            })

                    resource = self.format_resource(
                        resource_id=db_instance["DBInstanceIdentifier"],
                        resource_type="rds_instance",
                        name=db_instance.get("DBInstanceIdentifier"),
                        metadata={
                            "engine": db_instance.get("Engine"),
                            "engine_version": db_instance.get("EngineVersion"),
                            "instance_class": db_instance.get("DBInstanceClass"),
                            "storage_type": db_instance.get("StorageType"),
                            "allocated_storage": db_instance.get("AllocatedStorage"),
                            "status": db_instance.get("DBInstanceStatus"),
                            "endpoint": db_instance.get("Endpoint", {}).get("Address"),
                            "port": db_instance.get("Endpoint", {}).get("Port"),
                            "multi_az": db_instance.get("MultiAZ"),
                            "availability_zone": db_instance.get("AvailabilityZone"),
                        },
                        region=self.region,
                        tags=tags,
                        external_id=db_instance_arn,
                        relationships=relationships,
                    )
                    resources.append(resource)

        except (ClientError, BotoCoreError) as e:
            logger.warning(f"Failed to discover RDS instances: {e}")

        return resources

    def discover_serverless(self) -> List[Dict[str, Any]]:
        """Discover Lambda functions and Free Tier messaging services."""
        resources = []

        # Lambda Functions
        try:
            lambda_client = self.session.client("lambda")
            paginator = lambda_client.get_paginator("list_functions")

            for page in paginator.paginate():
                for function in page.get("Functions", []):
                    function_arn = function["FunctionArn"]
                    relationships = []

                    # Get tags
                    try:
                        tags_response = lambda_client.list_tags(Resource=function_arn)
                        tags = tags_response.get("Tags", {})
                    except:
                        tags = {}

                    # Lambda -> VPC (in_network)
                    vpc_config = function.get("VpcConfig", {})
                    vpc_id = vpc_config.get("VpcId")
                    if vpc_id:
                        relationships.append({
                            "target_external_id": vpc_id,
                            "target_kind": "networking_resource",
                            "edge_type": "in_network",
                        })

                    # Lambda -> Security Groups (uses_security_group)
                    for sg_id in vpc_config.get("SecurityGroupIds", []):
                        relationships.append({
                            "target_external_id": sg_id,
                            "target_kind": "networking_resource",
                            "edge_type": "uses_security_group",
                        })

                    # Lambda -> IAM Role (assumes_role)
                    role_arn = function.get("Role")
                    if role_arn:
                        relationships.append({
                            "target_external_id": role_arn,
                            "target_kind": "identity",
                            "edge_type": "assumes_role",
                        })

                    resource = self.format_resource(
                        resource_id=function_arn,
                        resource_type="lambda_function",
                        name=function.get("FunctionName"),
                        metadata={
                            "runtime": function.get("Runtime"),
                            "handler": function.get("Handler"),
                            "memory_size_mb": function.get("MemorySize"),
                            "timeout_seconds": function.get("Timeout"),
                            "last_modified": function.get("LastModified"),
                            "code_size_bytes": function.get("CodeSize"),
                            "vpc_id": vpc_id,
                        },
                        region=self.region,
                        tags=tags,
                        external_id=function_arn,
                        relationships=relationships,
                    )
                    resources.append(resource)

        except (ClientError, BotoCoreError) as e:
            logger.warning(f"Failed to discover Lambda functions: {e}")

        # SQS Queues
        if not self.services or "sqs" in self.services:
            resources.extend(self.discover_sqs())

        # SNS Topics
        if not self.services or "sns" in self.services:
            resources.extend(self.discover_sns())

        # Step Functions State Machines
        if not self.services or "stepfunctions" in self.services:
            resources.extend(self.discover_stepfunctions())

        # EventBridge Rules
        if not self.services or "events" in self.services:
            resources.extend(self.discover_events())

        return resources

    def discover_dynamodb(self) -> List[Dict[str, Any]]:
        """Discover DynamoDB tables."""
        resources = []

        try:
            dynamodb = self.session.client("dynamodb")
            paginator = dynamodb.get_paginator("list_tables")

            for page in paginator.paginate():
                for table_name in page.get("TableNames", []):
                    try:
                        table_info = dynamodb.describe_table(TableName=table_name)
                        table = table_info["Table"]

                        # Get tags for table
                        try:
                            tags_response = dynamodb.list_tags_of_resource(
                                ResourceArn=table["TableArn"]
                            )
                            tags = self._normalize_tags(tags_response.get("Tags", []))
                        except:
                            tags = {}

                        resource = self.format_resource(
                            resource_id=table["TableArn"],
                            resource_type="dynamodb_table",
                            name=table_name,
                            metadata={
                                "status": table.get("TableStatus"),
                                "item_count": table.get("ItemCount"),
                                "size_bytes": table.get("TableSizeBytes"),
                                "billing_mode": table.get("BillingModeSummary", {}).get(
                                    "BillingMode"
                                ),
                                "provisioned_throughput": {
                                    "read_capacity_units": table.get(
                                        "ProvisionedThroughput", {}
                                    ).get("ReadCapacityUnits"),
                                    "write_capacity_units": table.get(
                                        "ProvisionedThroughput", {}
                                    ).get("WriteCapacityUnits"),
                                },
                                "sse_description": table.get("SSEDescription", {}).get(
                                    "Status"
                                ),
                                "creation_date": (
                                    table.get("CreationDateTime").isoformat()
                                    if table.get("CreationDateTime")
                                    else None
                                ),
                            },
                            region=self.region,
                            tags=tags,
                        )
                        resources.append(resource)
                    except (ClientError, BotoCoreError) as e:
                        logger.warning(
                            f"Failed to describe DynamoDB table {table_name}: {e}"
                        )

        except (ClientError, BotoCoreError) as e:
            logger.warning(f"Failed to discover DynamoDB tables: {e}")

        return resources

    def discover_sqs(self) -> List[Dict[str, Any]]:
        """Discover SQS queues."""
        resources = []

        try:
            sqs = self.session.client("sqs")
            paginator = sqs.get_paginator("list_queues")

            for page in paginator.paginate():
                for queue_url in page.get("QueueUrls", []):
                    try:
                        # Get queue attributes
                        attrs_response = sqs.get_queue_attributes(
                            QueueUrl=queue_url, AttributeNames=["All"]
                        )
                        attributes = attrs_response.get("Attributes", {})

                        # Extract queue name from URL
                        queue_name = queue_url.split("/")[-1]

                        resource = self.format_resource(
                            resource_id=queue_url,
                            resource_type="sqs_queue",
                            name=queue_name,
                            metadata={
                                "arn": attributes.get("QueueArn"),
                                "approximate_message_count": int(
                                    attributes.get("ApproximateNumberOfMessages", 0)
                                ),
                                "message_retention_period": int(
                                    attributes.get("MessageRetentionPeriod", 0)
                                ),
                                "visibility_timeout": int(
                                    attributes.get("VisibilityTimeout", 30)
                                ),
                                "receive_message_wait_time": int(
                                    attributes.get("ReceiveMessageWaitTimeSeconds", 0)
                                ),
                                "created_timestamp": attributes.get("CreatedTimestamp"),
                                "last_modified_timestamp": attributes.get(
                                    "LastModifiedTimestamp"
                                ),
                            },
                            region=self.region,
                            tags={},
                        )
                        resources.append(resource)
                    except (ClientError, BotoCoreError) as e:
                        logger.warning(
                            f"Failed to get SQS queue attributes {queue_url}: {e}"
                        )

        except (ClientError, BotoCoreError) as e:
            logger.warning(f"Failed to discover SQS queues: {e}")

        return resources

    def discover_sns(self) -> List[Dict[str, Any]]:
        """Discover SNS topics."""
        resources = []

        try:
            sns = self.session.client("sns")
            paginator = sns.get_paginator("list_topics")

            for page in paginator.paginate():
                for topic in page.get("Topics", []):
                    topic_arn = topic["TopicArn"]
                    try:
                        # Get topic attributes
                        attrs_response = sns.get_topic_attributes(TopicArn=topic_arn)
                        attributes = attrs_response.get("Attributes", {})

                        # Extract topic name from ARN
                        topic_name = topic_arn.split(":")[-1]

                        resource = self.format_resource(
                            resource_id=topic_arn,
                            resource_type="sns_topic",
                            name=topic_name,
                            metadata={
                                "subscriptions_confirmed": int(
                                    attributes.get("SubscriptionsConfirmed", 0)
                                ),
                                "subscriptions_pending": int(
                                    attributes.get("SubscriptionsPending", 0)
                                ),
                                "subscriptions_deleted": int(
                                    attributes.get("SubscriptionsDeleted", 0)
                                ),
                                "display_name": attributes.get("DisplayName"),
                                "creation_timestamp": attributes.get(
                                    "CreatedTimestamp"
                                ),
                            },
                            region=self.region,
                            tags={},
                        )
                        resources.append(resource)
                    except (ClientError, BotoCoreError) as e:
                        logger.warning(
                            f"Failed to get SNS topic attributes {topic_arn}: {e}"
                        )

        except (ClientError, BotoCoreError) as e:
            logger.warning(f"Failed to discover SNS topics: {e}")

        return resources

    def discover_ecr(self) -> List[Dict[str, Any]]:
        """Discover ECR repositories."""
        resources = []

        try:
            ecr = self.session.client("ecr")
            paginator = ecr.get_paginator("describe_repositories")

            for page in paginator.paginate():
                for repo in page.get("repositories", []):
                    resource = self.format_resource(
                        resource_id=repo["repositoryArn"],
                        resource_type="ecr_repository",
                        name=repo["repositoryName"],
                        metadata={
                            "repository_uri": repo.get("repositoryUri"),
                            "image_tag_mutability": repo.get("imageTagMutability"),
                            "image_scan_on_push": repo.get(
                                "imageScanningConfiguration", {}
                            ).get("scanOnPush"),
                            "encryption_type": repo.get(
                                "encryptionConfiguration", {}
                            ).get("encryptionType"),
                            "created_date": (
                                repo.get("createdAt").isoformat()
                                if repo.get("createdAt")
                                else None
                            ),
                        },
                        region=self.region,
                        tags={},
                    )
                    resources.append(resource)

        except (ClientError, BotoCoreError) as e:
            logger.warning(f"Failed to discover ECR repositories: {e}")

        return resources

    def discover_logs(self) -> List[Dict[str, Any]]:
        """Discover CloudWatch log groups."""
        resources = []

        try:
            logs = self.session.client("logs")
            paginator = logs.get_paginator("describe_log_groups")

            for page in paginator.paginate():
                for log_group in page.get("logGroups", []):
                    resource = self.format_resource(
                        resource_id=log_group["arn"],
                        resource_type="cloudwatch_log_group",
                        name=log_group["logGroupName"],
                        metadata={
                            "creation_time": log_group.get("creationTime"),
                            "retention_in_days": log_group.get("retentionInDays"),
                            "stored_bytes": log_group.get("storedBytes"),
                            "metric_filter_count": log_group.get("metricFilterCount"),
                        },
                        region=self.region,
                        tags=log_group.get("tags", {}),
                    )
                    resources.append(resource)

        except (ClientError, BotoCoreError) as e:
            logger.warning(f"Failed to discover CloudWatch log groups: {e}")

        return resources

    def discover_stepfunctions(self) -> List[Dict[str, Any]]:
        """Discover Step Functions state machines."""
        resources = []

        try:
            sfn = self.session.client("stepfunctions")
            paginator = sfn.get_paginator("list_state_machines")

            for page in paginator.paginate():
                for state_machine in page.get("stateMachines", []):
                    resource = self.format_resource(
                        resource_id=state_machine["stateMachineArn"],
                        resource_type="step_function",
                        name=state_machine["name"],
                        metadata={
                            "type": state_machine.get("type"),
                            "creation_date": (
                                state_machine.get("creationDate").isoformat()
                                if state_machine.get("creationDate")
                                else None
                            ),
                        },
                        region=self.region,
                        tags={},
                    )
                    resources.append(resource)

        except (ClientError, BotoCoreError) as e:
            logger.warning(f"Failed to discover Step Functions state machines: {e}")

        return resources

    def discover_events(self) -> List[Dict[str, Any]]:
        """Discover EventBridge rules."""
        resources = []

        try:
            events = self.session.client("events")
            paginator = events.get_paginator("list_rules")

            for page in paginator.paginate():
                for rule in page.get("Rules", []):
                    resource = self.format_resource(
                        resource_id=rule.get("Arn"),
                        resource_type="eventbridge_rule",
                        name=rule.get("Name"),
                        metadata={
                            "state": rule.get("State"),
                            "description": rule.get("Description"),
                            "event_pattern": rule.get("EventPattern"),
                            "schedule_expression": rule.get("ScheduleExpression"),
                            "creation_time": (
                                rule.get("CreationTime").isoformat()
                                if rule.get("CreationTime")
                                else None
                            ),
                        },
                        region=self.region,
                        tags=rule.get("Tags", {}),
                    )
                    resources.append(resource)

        except (ClientError, BotoCoreError) as e:
            logger.warning(f"Failed to discover EventBridge rules: {e}")

        return resources

    def discover_apigateway(self) -> List[Dict[str, Any]]:
        """Discover API Gateway REST APIs."""
        resources = []

        try:
            apigw = self.session.client("apigateway")
            paginator = apigw.get_paginator("get_rest_apis")

            for page in paginator.paginate():
                for api in page.get("items", []):
                    resource = self.format_resource(
                        resource_id=api["id"],
                        resource_type="api_gateway_rest_api",
                        name=api.get("name"),
                        metadata={
                            "description": api.get("description"),
                            "endpoint_configuration": api.get(
                                "endpointConfiguration", {}
                            ).get("types"),
                            "created_date": (
                                api.get("createdDate").isoformat()
                                if api.get("createdDate")
                                else None
                            ),
                        },
                        region=self.region,
                        tags=api.get("tags", {}),
                    )
                    resources.append(resource)

        except (ClientError, BotoCoreError) as e:
            logger.warning(f"Failed to discover API Gateway REST APIs: {e}")

        return resources

    def discover_efs(self) -> List[Dict[str, Any]]:
        """Discover EFS file systems."""
        resources = []

        try:
            efs = self.session.client("efs")
            paginator = efs.get_paginator("describe_file_systems")

            for page in paginator.paginate():
                for file_system in page.get("FileSystems", []):
                    resource = self.format_resource(
                        resource_id=file_system["FileSystemId"],
                        resource_type="efs_filesystem",
                        name=file_system.get("Name", file_system["FileSystemId"]),
                        metadata={
                            "performance_mode": file_system.get("PerformanceMode"),
                            "throughput_mode": file_system.get("ThroughputMode"),
                            "size_in_bytes": file_system.get("SizeInBytes", {}).get(
                                "Value"
                            ),
                            "encrypted": file_system.get("Encrypted"),
                            "kms_key_id": file_system.get("KmsKeyId"),
                            "creation_time": (
                                file_system.get("CreationTime").isoformat()
                                if file_system.get("CreationTime")
                                else None
                            ),
                            "lifecycle_state": file_system.get("LifeCycleState"),
                        },
                        region=self.region,
                        tags=file_system.get("Tags", {}),
                    )
                    resources.append(resource)

        except (ClientError, BotoCoreError) as e:
            logger.warning(f"Failed to discover EFS file systems: {e}")

        return resources

    def discover_elasticache(self) -> List[Dict[str, Any]]:
        """Discover ElastiCache clusters."""
        resources = []

        try:
            elasticache = self.session.client("elasticache")
            paginator = elasticache.get_paginator("describe_cache_clusters")

            for page in paginator.paginate():
                for cluster in page.get("CacheClusters", []):
                    resource = self.format_resource(
                        resource_id=cluster["ARN"],
                        resource_type="elasticache_cluster",
                        name=cluster["CacheClusterId"],
                        metadata={
                            "engine": cluster.get("Engine"),
                            "engine_version": cluster.get("EngineVersion"),
                            "cache_node_type": cluster.get("CacheNodeType"),
                            "num_cache_nodes": cluster.get("NumCacheNodes"),
                            "cache_cluster_status": cluster.get("CacheClusterStatus"),
                            "preferred_availability_zone": cluster.get(
                                "PreferredAvailabilityZone"
                            ),
                            "cache_security_groups": [
                                g.get("CacheSecurityGroupName")
                                for g in cluster.get("CacheSecurityGroups", [])
                            ],
                            "vpc_security_group_ids": [
                                g.get("VpcSecurityGroupId")
                                for g in cluster.get("SecurityGroups", [])
                            ],
                        },
                        region=self.region,
                        tags={},
                    )
                    resources.append(resource)

        except (ClientError, BotoCoreError) as e:
            logger.warning(f"Failed to discover ElastiCache clusters: {e}")

        return resources

    def discover_cloudfront(self) -> List[Dict[str, Any]]:
        """Discover CloudFront distributions (global service)."""
        resources = []

        try:
            cloudfront = self.session.client("cloudfront")
            paginator = cloudfront.get_paginator("list_distributions")

            for page in paginator.paginate():
                for distribution in page.get("DistributionList", {}).get("Items", []):
                    resource = self.format_resource(
                        resource_id=distribution["Id"],
                        resource_type="cloudfront_distribution",
                        name=distribution.get("DomainName"),
                        metadata={
                            "status": distribution.get("Status"),
                            "enabled": distribution.get("Enabled"),
                            "comment": distribution.get("Comment"),
                            "default_root_object": distribution.get(
                                "DefaultRootObject"
                            ),
                            "origins": [
                                o.get("DomainName")
                                for o in distribution.get("Origins", {}).get(
                                    "Items", []
                                )
                            ],
                            "last_modified_time": (
                                distribution.get("LastModifiedTime").isoformat()
                                if distribution.get("LastModifiedTime")
                                else None
                            ),
                        },
                        region="global",
                        tags={},
                    )
                    resources.append(resource)

        except (ClientError, BotoCoreError) as e:
            logger.warning(f"Failed to discover CloudFront distributions: {e}")

        return resources

    def discover_route53(self) -> List[Dict[str, Any]]:
        """Discover Route 53 hosted zones (global service)."""
        resources = []

        try:
            route53 = self.session.client("route53")
            paginator = route53.get_paginator("list_hosted_zones")

            for page in paginator.paginate():
                for hosted_zone in page.get("HostedZones", []):
                    resource = self.format_resource(
                        resource_id=hosted_zone["Id"],
                        resource_type="route53_hosted_zone",
                        name=hosted_zone.get("Name"),
                        metadata={
                            "private_zone": hosted_zone.get("Config", {}).get(
                                "PrivateZone"
                            ),
                            "resource_record_set_count": hosted_zone.get(
                                "ResourceRecordSetCount"
                            ),
                            "caller_reference": hosted_zone.get("CallerReference"),
                        },
                        region="global",
                        tags={},
                    )
                    resources.append(resource)

        except (ClientError, BotoCoreError) as e:
            logger.warning(f"Failed to discover Route 53 hosted zones: {e}")

        return resources

    def discover_iam(self) -> List[Dict[str, Any]]:
        """
        Discover IAM users and roles.

        Returns:
            List of IAM users and roles with metadata.
        """
        resources = []

        try:
            iam = self.session.client("iam")

            # Discover IAM Users
            paginator = iam.get_paginator("list_users")
            for page in paginator.paginate():
                for user in page.get("Users", []):
                    user_name = user["UserName"]

                    # Get user tags
                    try:
                        tags_response = iam.list_user_tags(UserName=user_name)
                        tags = self._normalize_tags(
                            [
                                {"Key": t["Key"], "Value": t["Value"]}
                                for t in tags_response.get("Tags", [])
                            ]
                        )
                    except (ClientError, BotoCoreError):
                        tags = {}

                    # Get user access keys info (for metadata)
                    access_keys_count = 0
                    try:
                        keys_response = iam.list_access_keys(UserName=user_name)
                        access_keys_count = len(
                            keys_response.get("AccessKeyMetadata", [])
                        )
                    except (ClientError, BotoCoreError):
                        pass

                    # Get user groups
                    groups = []
                    try:
                        groups_response = iam.list_groups_for_user(UserName=user_name)
                        groups = [
                            g["GroupName"] for g in groups_response.get("Groups", [])
                        ]
                    except (ClientError, BotoCoreError):
                        pass

                    resource = self.format_resource(
                        resource_id=user["Arn"],
                        resource_type="iam_user",
                        name=user_name,
                        metadata={
                            "user_id": user.get("UserId"),
                            "arn": user.get("Arn"),
                            "path": user.get("Path"),
                            "create_date": (
                                user.get("CreateDate").isoformat()
                                if user.get("CreateDate")
                                else None
                            ),
                            "password_last_used": (
                                user.get("PasswordLastUsed").isoformat()
                                if user.get("PasswordLastUsed")
                                else None
                            ),
                            "access_keys_count": access_keys_count,
                            "groups": groups,
                        },
                        region="global",  # IAM is global
                        tags=tags,
                    )
                    resources.append(resource)

            # Discover IAM Roles
            paginator = iam.get_paginator("list_roles")
            for page in paginator.paginate():
                for role in page.get("Roles", []):
                    role_name = role["RoleName"]

                    # Skip AWS service-linked roles (they clutter the list)
                    if role.get("Path", "").startswith("/aws-service-role/"):
                        continue

                    # Get role tags
                    try:
                        tags_response = iam.list_role_tags(RoleName=role_name)
                        tags = self._normalize_tags(
                            [
                                {"Key": t["Key"], "Value": t["Value"]}
                                for t in tags_response.get("Tags", [])
                            ]
                        )
                    except (ClientError, BotoCoreError):
                        tags = {}

                    resource = self.format_resource(
                        resource_id=role["Arn"],
                        resource_type="iam_role",
                        name=role_name,
                        metadata={
                            "role_id": role.get("RoleId"),
                            "arn": role.get("Arn"),
                            "path": role.get("Path"),
                            "description": role.get("Description"),
                            "create_date": (
                                role.get("CreateDate").isoformat()
                                if role.get("CreateDate")
                                else None
                            ),
                            "max_session_duration": role.get("MaxSessionDuration"),
                        },
                        region="global",  # IAM is global
                        tags=tags,
                    )
                    resources.append(resource)

        except (ClientError, BotoCoreError) as e:
            logger.warning(f"Failed to discover IAM resources: {e}")

        return resources

    def _get_name_from_tags(
        self, tags: List[Dict[str, str]], fallback: str = "Unnamed"
    ) -> str:
        """
        Extract the Name tag from an AWS tags list.

        Untagged resources fall back to their AWS resource id rather than a
        shared literal: downstream upserts dedupe on name, so a constant
        placeholder silently collapses distinct resources into one row.

        Args:
            tags: List of tag dicts with Key/Value
            fallback: Value to use when no Name tag is present

        Returns:
            Name tag value, or the fallback
        """
        if tags:
            for tag in tags:
                if tag.get("Key") == "Name":
                    value = tag.get("Value")
                    if value:
                        return value

        return fallback
