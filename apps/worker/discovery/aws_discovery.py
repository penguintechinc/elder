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
from datetime import datetime
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
        ]

    def discover_all(self) -> Dict[str, Any]:
        """Discover all AWS resources."""
        start_time = datetime.utcnow()

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

        if not self.services or any(s in self.services for s in ["s3", "ebs"]):
            results["storage"].extend(self.discover_storage())

        if not self.services or any(s in self.services for s in ["vpc", "elb"]):
            results["network"].extend(self.discover_network())

        if not self.services or "rds" in self.services:
            results["database"].extend(self.discover_databases())

        if not self.services or "lambda" in self.services:
            results["serverless"].extend(self.discover_serverless())

        # Discover IAM users and roles
        if not self.services or "iam" in self.services:
            results["iam"] = self.discover_iam()

        # Calculate totals
        resources_count = sum(len(resources) for resources in results.values())

        return {
            **results,
            "resources_count": resources_count,
            "discovery_time": datetime.utcnow(),
            "duration_seconds": (datetime.utcnow() - start_time).total_seconds(),
        }

    def discover_compute(self) -> List[Dict[str, Any]]:
        """Discover EC2 instances."""
        resources = []

        try:
            ec2 = self.session.client("ec2")
            response = ec2.describe_instances()

            for reservation in response.get("Reservations", []):
                for instance in reservation.get("Instances", []):
                    resource = self.format_resource(
                        resource_id=instance["InstanceId"],
                        resource_type="ec2_instance",
                        name=self._get_name_from_tags(instance.get("Tags", [])),
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
                    )
                    resources.append(resource)

        except (ClientError, BotoCoreError) as e:
            # Log error but continue discovery
            pass

        return resources

    def discover_storage(self) -> List[Dict[str, Any]]:
        """Discover S3 buckets and EBS volumes."""
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

            except (ClientError, BotoCoreError):
                pass

        # EBS Volumes
        if not self.services or "ebs" in self.services:
            try:
                ec2 = self.session.client("ec2")
                response = ec2.describe_volumes()

                for volume in response.get("Volumes", []):
                    resource = self.format_resource(
                        resource_id=volume["VolumeId"],
                        resource_type="ebs_volume",
                        name=self._get_name_from_tags(volume.get("Tags", [])),
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
                    )
                    resources.append(resource)

            except (ClientError, BotoCoreError):
                pass

        return resources

    def discover_network(self) -> List[Dict[str, Any]]:
        """Discover VPCs, subnets, and load balancers."""
        resources = []

        # VPCs
        if not self.services or "vpc" in self.services:
            try:
                ec2 = self.session.client("ec2")
                response = ec2.describe_vpcs()

                for vpc in response.get("Vpcs", []):
                    resource = self.format_resource(
                        resource_id=vpc["VpcId"],
                        resource_type="vpc",
                        name=self._get_name_from_tags(vpc.get("Tags", [])),
                        metadata={
                            "cidr_block": vpc.get("CidrBlock"),
                            "state": vpc.get("State"),
                            "is_default": vpc.get("IsDefault"),
                        },
                        region=self.region,
                        tags=self._normalize_tags(vpc.get("Tags", [])),
                    )
                    resources.append(resource)

                # Subnets
                subnets_response = ec2.describe_subnets()
                for subnet in subnets_response.get("Subnets", []):
                    resource = self.format_resource(
                        resource_id=subnet["SubnetId"],
                        resource_type="subnet",
                        name=self._get_name_from_tags(subnet.get("Tags", [])),
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
                    )
                    resources.append(resource)

            except (ClientError, BotoCoreError):
                pass

        # Load Balancers (ELBv2)
        if not self.services or "elb" in self.services:
            try:
                elbv2 = self.session.client("elbv2")
                response = elbv2.describe_load_balancers()

                for lb in response.get("LoadBalancers", []):
                    # Get tags for load balancer
                    try:
                        tags_response = elbv2.describe_tags(
                            ResourceArns=[lb["LoadBalancerArn"]]
                        )
                        tags_list = tags_response.get("TagDescriptions", [{}])[0].get(
                            "Tags", []
                        )
                        tags = self._normalize_tags(tags_list)
                    except:
                        tags = {}

                    resource = self.format_resource(
                        resource_id=lb["LoadBalancerArn"],
                        resource_type="load_balancer",
                        name=lb.get("LoadBalancerName"),
                        metadata={
                            "type": lb.get("Type"),
                            "scheme": lb.get("Scheme"),
                            "vpc_id": lb.get("VpcId"),
                            "state": lb.get("State", {}).get("Code"),
                            "dns_name": lb.get("DNSName"),
                        },
                        region=self.region,
                        tags=tags,
                    )
                    resources.append(resource)

            except (ClientError, BotoCoreError):
                pass

        return resources

    def discover_databases(self) -> List[Dict[str, Any]]:
        """Discover RDS databases."""
        resources = []

        try:
            rds = self.session.client("rds")
            response = rds.describe_db_instances()

            for db_instance in response.get("DBInstances", []):
                # Get tags for DB instance
                try:
                    tags_response = rds.list_tags_for_resource(
                        ResourceName=db_instance["DBInstanceArn"]
                    )
                    tags = self._normalize_tags(tags_response.get("TagList", []))
                except:
                    tags = {}

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
                )
                resources.append(resource)

        except (ClientError, BotoCoreError):
            pass

        return resources

    def discover_serverless(self) -> List[Dict[str, Any]]:
        """Discover Lambda functions."""
        resources = []

        try:
            lambda_client = self.session.client("lambda")
            paginator = lambda_client.get_paginator("list_functions")

            for page in paginator.paginate():
                for function in page.get("Functions", []):
                    function_arn = function["FunctionArn"]

                    # Get tags
                    try:
                        tags_response = lambda_client.list_tags(Resource=function_arn)
                        tags = tags_response.get("Tags", {})
                    except:
                        tags = {}

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
                            "vpc_id": function.get("VpcConfig", {}).get("VpcId"),
                        },
                        region=self.region,
                        tags=tags,
                    )
                    resources.append(resource)

        except (ClientError, BotoCoreError):
            pass

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

    def _get_name_from_tags(self, tags: List[Dict[str, str]]) -> str:
        """
        Extract Name tag from AWS tags list.

        Args:
            tags: List of tag dicts with Key/Value

        Returns:
            Name tag value or "Unnamed"
        """
        if not tags:
            return "Unnamed"

        for tag in tags:
            if tag.get("Key") == "Name":
                return tag.get("Value", "Unnamed")

        return "Unnamed"
