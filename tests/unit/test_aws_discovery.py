"""Unit tests for the AWS discovery client.

boto3 is stubbed by conftest_worker_stubs, so these tests drive the client with
MagicMock clients rather than touching AWS. They cover the behaviours that
previously broke silently: terminated-instance filtering, pagination, the
service filter, and error handling that used to swallow failures.
"""

import logging
from unittest.mock import MagicMock, patch

import pytest
from botocore.exceptions import ClientError

from apps.worker.discovery.aws_discovery import AWSDiscoveryClient

BASE_CONFIG = {
    "provider_type": "aws",
    "region": "us-east-2",
    "access_key_id": "AKIAEXAMPLE",
    "secret_access_key": "secret",  # nosec B105 - dummy value for tests
}


def make_client(**overrides):
    """Build an AWSDiscoveryClient with a mocked boto3 session."""
    config = {**BASE_CONFIG, **overrides}
    with patch("apps.worker.discovery.aws_discovery.boto3") as mock_boto3:
        mock_boto3.Session.return_value = MagicMock()
        client = AWSDiscoveryClient(config)
    return client


def paginated(client_mock, pages):
    """Wire a boto3 client mock so any get_paginator(...) yields `pages`."""
    paginator = MagicMock()
    paginator.paginate.return_value = iter(pages)
    client_mock.get_paginator.return_value = paginator
    return paginator


class TestAuthResolution:
    def test_static_credentials_take_priority(self):
        client = make_client()
        assert client.auth_method == AWSDiscoveryClient.AUTH_STATIC_CREDENTIALS

    def test_falls_back_to_environment_without_static_keys(self):
        client = make_client(access_key_id="", secret_access_key="")
        assert client.auth_method == AWSDiscoveryClient.AUTH_ENVIRONMENT

    def test_region_defaults_are_respected(self):
        assert make_client(region="eu-west-1").region == "eu-west-1"


class TestTerminatedInstances:
    """Terminated instances linger in describe_instances and are not assets."""

    def test_paginate_filters_to_live_states(self):
        client = make_client()
        ec2 = MagicMock()
        paginator = paginated(ec2, [{"Reservations": []}])
        client.session.client.return_value = ec2

        client.discover_compute()

        kwargs = paginator.paginate.call_args.kwargs
        filters = kwargs["Filters"]
        states = filters[0]["Values"]
        assert filters[0]["Name"] == "instance-state-name"
        assert "terminated" not in states
        assert "running" in states

    def test_running_instance_is_returned(self):
        client = make_client()
        ec2 = MagicMock()
        paginated(
            ec2,
            [
                {
                    "Reservations": [
                        {
                            "Instances": [
                                {
                                    "InstanceId": "i-abc",
                                    "InstanceType": "t3.micro",
                                    "State": {"Name": "running"},
                                    "Tags": [{"Key": "Name", "Value": "web"}],
                                }
                            ]
                        }
                    ]
                }
            ],
        )
        client.session.client.return_value = ec2

        resources = client.discover_compute()

        assert len(resources) == 1
        assert resources[0]["resource_id"] == "i-abc"
        assert resources[0]["name"] == "web"
        assert resources[0]["resource_type"] == "ec2_instance"


class TestPagination:
    """Results used to truncate at the first page for several services."""

    def test_instances_across_pages_are_all_returned(self):
        client = make_client()
        ec2 = MagicMock()

        def instance(i):
            return {
                "InstanceId": f"i-{i}",
                "State": {"Name": "running"},
                "Tags": [],
            }

        paginated(
            ec2,
            [
                {"Reservations": [{"Instances": [instance(1)]}]},
                {"Reservations": [{"Instances": [instance(2)]}]},
                {"Reservations": [{"Instances": [instance(3)]}]},
            ],
        )
        client.session.client.return_value = ec2

        resources = client.discover_compute()

        assert [r["resource_id"] for r in resources] == ["i-1", "i-2", "i-3"]

    def test_untagged_instances_get_distinct_names(self):
        client = make_client()
        ec2 = MagicMock()
        paginated(
            ec2,
            [
                {
                    "Reservations": [
                        {
                            "Instances": [
                                {"InstanceId": "i-1", "State": {"Name": "running"}},
                                {"InstanceId": "i-2", "State": {"Name": "running"}},
                            ]
                        }
                    ]
                }
            ],
        )
        client.session.client.return_value = ec2

        names = {r["name"] for r in client.discover_compute()}

        # regression: both would have been "Unnamed" and collapsed downstream
        assert names == {"i-1", "i-2"}


class TestServiceFilter:
    def test_empty_filter_means_discover_everything(self):
        client = make_client()
        assert client.services == []

    def test_explicit_filter_is_preserved(self):
        client = make_client(services=["ec2", "s3"])
        assert client.services == ["ec2", "s3"]

    @pytest.mark.parametrize(
        "service",
        [
            "dynamodb",
            "sqs",
            "sns",
            "ecr",
            "logs",
            "stepfunctions",
            "events",
            "apigateway",
            "efs",
            "elasticache",
            "cloudfront",
            "route53",
        ],
    )
    def test_free_tier_services_are_supported(self, service):
        assert service in make_client().get_supported_services()


class TestEC2Relationships:
    """EC2 instances emit relationships to VPCs, subnets, and security groups."""

    def test_ec2_emits_vpc_relationship(self):
        client = make_client()
        ec2 = MagicMock()
        paginated(
            ec2,
            [
                {
                    "Reservations": [
                        {
                            "Instances": [
                                {
                                    "InstanceId": "i-123",
                                    "State": {"Name": "running"},
                                    "VpcId": "vpc-abc",
                                    "Tags": [],
                                }
                            ]
                        }
                    ]
                }
            ],
        )
        client.session.client.return_value = ec2

        resources = client.discover_compute()

        assert len(resources) == 1
        assert resources[0]["external_id"] == "i-123"
        assert any(
            r["edge_type"] == "in_network" for r in resources[0]["relationships"]
        )
        vpc_rel = [
            r for r in resources[0]["relationships"] if r["edge_type"] == "in_network"
        ][0]
        assert vpc_rel["target_external_id"] == "vpc-abc"
        assert vpc_rel["target_kind"] == "networking_resource"

    def test_ec2_emits_subnet_relationship(self):
        client = make_client()
        ec2 = MagicMock()
        paginated(
            ec2,
            [
                {
                    "Reservations": [
                        {
                            "Instances": [
                                {
                                    "InstanceId": "i-123",
                                    "State": {"Name": "running"},
                                    "SubnetId": "subnet-def",
                                    "Tags": [],
                                }
                            ]
                        }
                    ]
                }
            ],
        )
        client.session.client.return_value = ec2

        resources = client.discover_compute()

        assert len(resources) == 1
        subnet_rels = [
            r for r in resources[0]["relationships"] if r["edge_type"] == "in_subnet"
        ]
        assert len(subnet_rels) == 1
        assert subnet_rels[0]["target_external_id"] == "subnet-def"

    def test_ec2_emits_security_group_relationships(self):
        client = make_client()
        ec2 = MagicMock()
        paginated(
            ec2,
            [
                {
                    "Reservations": [
                        {
                            "Instances": [
                                {
                                    "InstanceId": "i-123",
                                    "State": {"Name": "running"},
                                    "SecurityGroups": [
                                        {"GroupId": "sg-1"},
                                        {"GroupId": "sg-2"},
                                    ],
                                    "Tags": [],
                                }
                            ]
                        }
                    ]
                }
            ],
        )
        client.session.client.return_value = ec2

        resources = client.discover_compute()

        sg_rels = [
            r
            for r in resources[0]["relationships"]
            if r["edge_type"] == "uses_security_group"
        ]
        assert len(sg_rels) == 2
        assert set(r["target_external_id"] for r in sg_rels) == {"sg-1", "sg-2"}


class TestEBSRelationships:
    """EBS volumes emit relationships to attached EC2 instances."""

    def test_ebs_emits_attachment_relationship(self):
        client = make_client()
        ec2 = MagicMock()
        paginated(
            ec2,
            [
                {
                    "Volumes": [
                        {
                            "VolumeId": "vol-123",
                            "State": "in-use",
                            "Size": 100,
                            "Attachments": [
                                {"InstanceId": "i-abc", "Device": "/dev/sda1"}
                            ],
                            "Tags": [],
                        }
                    ]
                }
            ],
        )
        client.session.client.return_value = ec2

        resources = client.discover_storage()

        vol = [r for r in resources if r["resource_type"] == "ebs_volume"][0]
        assert vol["external_id"] == "vol-123"
        assert len(vol["relationships"]) == 1
        assert vol["relationships"][0]["edge_type"] == "attached_to"
        assert vol["relationships"][0]["target_external_id"] == "i-abc"
        assert vol["relationships"][0]["target_kind"] == "entity"


class TestNetworkingRelationships:
    """VPCs, subnets, and security groups emit in_network relationships."""

    def test_vpc_has_external_id(self):
        client = make_client()
        ec2 = MagicMock()
        vpc_paginator = MagicMock()
        vpc_paginator.paginate.return_value = iter(
            [
                {
                    "Vpcs": [
                        {
                            "VpcId": "vpc-123",
                            "CidrBlock": "10.0.0.0/16",
                            "State": "available",
                            "Tags": [],
                        }
                    ]
                }
            ]
        )
        ec2.get_paginator.side_effect = lambda op: (
            vpc_paginator if op == "describe_vpcs" else MagicMock()
        )
        client.session.client.return_value = ec2

        resources = client.discover_network()

        vpcs = [r for r in resources if r["resource_type"] == "vpc"]
        assert len(vpcs) == 1
        assert vpcs[0]["external_id"] == "vpc-123"

    def test_subnet_emits_vpc_relationship(self):
        client = make_client()
        ec2 = MagicMock()

        def subnet_paginator():
            p = MagicMock()
            p.paginate.return_value = iter(
                [
                    {
                        "Subnets": [
                            {
                                "SubnetId": "subnet-123",
                                "VpcId": "vpc-abc",
                                "CidrBlock": "10.0.1.0/24",
                                "Tags": [],
                            }
                        ]
                    }
                ]
            )
            return p

        def sg_paginator():
            p = MagicMock()
            p.paginate.return_value = iter([{"SecurityGroups": []}])
            return p

        def get_paginator(op):
            if op == "describe_subnets":
                return subnet_paginator()
            elif op == "describe_security_groups":
                return sg_paginator()
            else:
                p = MagicMock()
                p.paginate.return_value = iter([{"Vpcs": []}, {"LoadBalancers": []}])
                return p

        ec2.get_paginator.side_effect = get_paginator
        client.session.client.return_value = ec2

        resources = client.discover_network()

        subnets = [r for r in resources if r["resource_type"] == "subnet"]
        assert len(subnets) == 1
        assert subnets[0]["external_id"] == "subnet-123"
        assert len(subnets[0]["relationships"]) == 1
        assert subnets[0]["relationships"][0]["edge_type"] == "in_network"
        assert subnets[0]["relationships"][0]["target_external_id"] == "vpc-abc"


class TestSecurityGroupDiscovery:
    """Security groups are discovered and linked to VPCs."""

    def test_discover_security_groups_returns_sgs(self):
        client = make_client()
        ec2 = MagicMock()
        paginated(
            ec2,
            [
                {
                    "SecurityGroups": [
                        {
                            "GroupId": "sg-123",
                            "GroupName": "web-sg",
                            "VpcId": "vpc-abc",
                            "Description": "Web traffic",
                            "IpPermissions": [],
                            "IpPermissionsEgress": [],
                            "Tags": [],
                        }
                    ]
                }
            ],
        )
        client.session.client.return_value = ec2

        resources = client.discover_security_groups()

        assert len(resources) == 1
        assert resources[0]["resource_type"] == "security_group"
        assert resources[0]["external_id"] == "sg-123"
        assert resources[0]["name"] == "web-sg"

    def test_security_group_emits_vpc_relationship(self):
        client = make_client()
        ec2 = MagicMock()
        paginated(
            ec2,
            [
                {
                    "SecurityGroups": [
                        {
                            "GroupId": "sg-123",
                            "GroupName": "web-sg",
                            "VpcId": "vpc-abc",
                            "Description": "Web traffic",
                            "IpPermissions": [],
                            "IpPermissionsEgress": [],
                            "Tags": [],
                        }
                    ]
                }
            ],
        )
        client.session.client.return_value = ec2

        resources = client.discover_security_groups()

        assert len(resources[0]["relationships"]) == 1
        assert resources[0]["relationships"][0]["edge_type"] == "in_network"
        assert resources[0]["relationships"][0]["target_external_id"] == "vpc-abc"
        assert resources[0]["relationships"][0]["target_kind"] == "networking_resource"


class TestErrorHandling:
    def test_aws_error_degrades_to_empty_list(self):
        """A denied permission must not abort the whole scan."""
        client = make_client()
        client.session.client.side_effect = ClientError(
            {"Error": {"Code": "AccessDenied", "Message": "not authorized"}},
            "DescribeInstances",
        )

        assert client.discover_compute() == []

    def test_aws_error_is_logged_not_swallowed(self, caplog):
        """Regression: failures used to `pass` silently, so a permissions
        problem looked identical to an account with no resources."""
        client = make_client()
        client.session.client.side_effect = ClientError(
            {"Error": {"Code": "AccessDenied", "Message": "not authorized"}},
            "DescribeInstances",
        )

        with caplog.at_level(logging.WARNING):
            client.discover_compute()

        assert any("EC2" in r.message or "ec2" in r.message for r in caplog.records)

    def test_unexpected_error_is_not_masked(self):
        """Only AWS errors are handled; a genuine bug must still surface."""
        client = make_client()
        client.session.client.side_effect = TypeError("programming error")

        with pytest.raises(TypeError):
            client.discover_compute()


class TestPhaseB2RDS:
    """Phase B2: RDS relationship capture (in_network, uses_security_group)."""

    def test_rds_instance_has_external_id_set(self):
        """RDS external_id must be the DBInstanceArn for edge resolution."""
        client = make_client()
        rds = MagicMock()
        paginated(
            rds,
            [
                {
                    "DBInstances": [
                        {
                            "DBInstanceIdentifier": "prod-db",
                            "DBInstanceArn": "arn:aws:rds:us-east-2:123456789012:db:prod-db",
                            "Engine": "postgres",
                            "EngineVersion": "14.7",
                            "DBInstanceClass": "db.t3.micro",
                            "StorageType": "gp2",
                            "AllocatedStorage": 20,
                            "DBInstanceStatus": "available",
                            "DBSubnetGroup": {"VpcId": "vpc-subnet-group"},
                            "VpcSecurityGroups": [],
                            "Tags": [],
                        }
                    ]
                }
            ],
        )
        client.session.client.return_value = rds

        resources = client.discover_databases()

        assert len(resources) == 1
        assert (
            resources[0]["external_id"]
            == "arn:aws:rds:us-east-2:123456789012:db:prod-db"
        )

    def test_rds_to_vpc_relationship(self):
        """RDS emits in_network relationship to its VPC."""
        client = make_client()
        rds = MagicMock()
        paginated(
            rds,
            [
                {
                    "DBInstances": [
                        {
                            "DBInstanceIdentifier": "prod-db",
                            "DBInstanceArn": "arn:aws:rds:us-east-2:123456789012:db:prod-db",
                            "Engine": "postgres",
                            "DBSubnetGroup": {"VpcId": "vpc-abc"},
                            "VpcSecurityGroups": [],
                            "Tags": [],
                        }
                    ]
                }
            ],
        )
        client.session.client.return_value = rds

        resources = client.discover_databases()

        assert len(resources[0]["relationships"]) >= 1
        vpc_rel = [
            r for r in resources[0]["relationships"] if r["edge_type"] == "in_network"
        ]
        assert len(vpc_rel) == 1
        assert vpc_rel[0]["target_external_id"] == "vpc-abc"
        assert vpc_rel[0]["target_kind"] == "networking_resource"

    def test_rds_to_security_groups_relationship(self):
        """RDS emits uses_security_group relationship for each assigned SG."""
        client = make_client()
        rds = MagicMock()
        paginated(
            rds,
            [
                {
                    "DBInstances": [
                        {
                            "DBInstanceIdentifier": "prod-db",
                            "DBInstanceArn": "arn:aws:rds:us-east-2:123456789012:db:prod-db",
                            "Engine": "postgres",
                            "DBSubnetGroup": {"VpcId": "vpc-abc"},
                            "VpcSecurityGroups": [
                                {"VpcSecurityGroupId": "sg-1"},
                                {"VpcSecurityGroupId": "sg-2"},
                            ],
                            "Tags": [],
                        }
                    ]
                }
            ],
        )
        client.session.client.return_value = rds

        resources = client.discover_databases()

        sg_rels = [
            r
            for r in resources[0]["relationships"]
            if r["edge_type"] == "uses_security_group"
        ]
        assert len(sg_rels) == 2
        assert set(r["target_external_id"] for r in sg_rels) == {"sg-1", "sg-2"}


class TestPhaseB2Lambda:
    """Phase B2: Lambda relationship capture (in_network, uses_security_group, assumes_role)."""

    def test_lambda_function_has_external_id_set(self):
        """Lambda external_id must be the FunctionArn for edge resolution."""
        client = make_client()
        lambda_client = MagicMock()
        paginated(
            lambda_client,
            [
                {
                    "Functions": [
                        {
                            "FunctionName": "my-func",
                            "FunctionArn": "arn:aws:lambda:us-east-2:123456789012:function:my-func",
                            "Runtime": "python3.11",
                            "Handler": "index.handler",
                            "MemorySize": 128,
                            "Timeout": 30,
                            "VpcConfig": {},
                            "Role": "arn:aws:iam::123456789012:role/lambda-role",
                        }
                    ]
                }
            ],
        )
        client.session.client.return_value = lambda_client

        resources = client.discover_serverless()

        assert len(resources) == 1
        assert (
            resources[0]["external_id"]
            == "arn:aws:lambda:us-east-2:123456789012:function:my-func"
        )

    def test_lambda_with_vpc_config_emits_in_network(self):
        """Lambda in VPC emits in_network relationship to VPC."""
        client = make_client()
        lambda_client = MagicMock()
        paginated(
            lambda_client,
            [
                {
                    "Functions": [
                        {
                            "FunctionName": "my-func",
                            "FunctionArn": "arn:aws:lambda:us-east-2:123456789012:function:my-func",
                            "Runtime": "python3.11",
                            "Handler": "index.handler",
                            "MemorySize": 128,
                            "Timeout": 30,
                            "VpcConfig": {
                                "VpcId": "vpc-abc",
                                "SecurityGroupIds": ["sg-1"],
                            },
                            "Role": "arn:aws:iam::123456789012:role/lambda-role",
                        }
                    ]
                }
            ],
        )
        client.session.client.return_value = lambda_client

        resources = client.discover_serverless()

        vpc_rel = [
            r for r in resources[0]["relationships"] if r["edge_type"] == "in_network"
        ]
        assert len(vpc_rel) == 1
        assert vpc_rel[0]["target_external_id"] == "vpc-abc"

    def test_lambda_emits_uses_security_group(self):
        """Lambda in VPC emits uses_security_group for each SG."""
        client = make_client()
        lambda_client = MagicMock()
        paginated(
            lambda_client,
            [
                {
                    "Functions": [
                        {
                            "FunctionName": "my-func",
                            "FunctionArn": "arn:aws:lambda:us-east-2:123456789012:function:my-func",
                            "Runtime": "python3.11",
                            "Handler": "index.handler",
                            "MemorySize": 128,
                            "Timeout": 30,
                            "VpcConfig": {
                                "VpcId": "vpc-abc",
                                "SecurityGroupIds": ["sg-1", "sg-2"],
                            },
                            "Role": "arn:aws:iam::123456789012:role/lambda-role",
                        }
                    ]
                }
            ],
        )
        client.session.client.return_value = lambda_client

        resources = client.discover_serverless()

        sg_rels = [
            r
            for r in resources[0]["relationships"]
            if r["edge_type"] == "uses_security_group"
        ]
        assert len(sg_rels) == 2
        assert set(r["target_external_id"] for r in sg_rels) == {"sg-1", "sg-2"}

    def test_lambda_emits_assumes_role(self):
        """Lambda emits assumes_role relationship to its execution role."""
        client = make_client()
        lambda_client = MagicMock()
        paginated(
            lambda_client,
            [
                {
                    "Functions": [
                        {
                            "FunctionName": "my-func",
                            "FunctionArn": "arn:aws:lambda:us-east-2:123456789012:function:my-func",
                            "Runtime": "python3.11",
                            "Handler": "index.handler",
                            "MemorySize": 128,
                            "Timeout": 30,
                            "VpcConfig": {},
                            "Role": "arn:aws:iam::123456789012:role/lambda-role",
                        }
                    ]
                }
            ],
        )
        client.session.client.return_value = lambda_client

        resources = client.discover_serverless()

        role_rel = [
            r for r in resources[0]["relationships"] if r["edge_type"] == "assumes_role"
        ]
        assert len(role_rel) == 1
        assert (
            role_rel[0]["target_external_id"]
            == "arn:aws:iam::123456789012:role/lambda-role"
        )
        assert role_rel[0]["target_kind"] == "identity"


class TestPhaseB2ELB:
    """Phase B2: ELB/ALB relationship capture (in_network, routes_to)."""

    def test_load_balancer_has_external_id_set(self):
        """LB external_id must be the LoadBalancerArn for edge resolution."""
        client = make_client()
        ec2 = MagicMock()
        elbv2 = MagicMock()

        # Mock EC2 for VPCs (returns empty to simplify)
        ec2_paginator = MagicMock()
        ec2_paginator.paginate.return_value = iter(
            [{"Vpcs": [], "Subnets": [], "SecurityGroups": []}]
        )
        ec2.get_paginator.return_value = ec2_paginator

        # Mock ELBv2
        elbv2_paginator = MagicMock()
        elbv2_paginator.paginate.return_value = iter(
            [
                {
                    "LoadBalancers": [
                        {
                            "LoadBalancerName": "my-lb",
                            "LoadBalancerArn": "arn:aws:elasticloadbalancing:us-east-2:123456789012:loadbalancer/app/my-lb/50dc6c495c0c9188",
                            "Type": "application",
                            "Scheme": "internet-facing",
                            "VpcId": "vpc-abc",
                            "State": {"Code": "active"},
                            "DNSName": "my-lb-123.us-east-2.elb.amazonaws.com",
                        }
                    ]
                }
            ]
        )
        elbv2.get_paginator.return_value = elbv2_paginator
        elbv2.describe_tags.return_value = {"TagDescriptions": [{"Tags": []}]}
        elbv2.describe_target_groups.return_value = {"TargetGroups": []}

        def get_client_side_effect(service):
            if service == "elbv2":
                return elbv2
            return ec2

        client.session.client.side_effect = get_client_side_effect

        resources = client.discover_network()

        lb_resources = [r for r in resources if r["resource_type"] == "load_balancer"]
        assert (
            len(lb_resources) > 0
        ), f"No load balancers found. Found: {[r['resource_type'] for r in resources]}"
        lb_resource = lb_resources[0]
        assert (
            lb_resource["external_id"]
            == "arn:aws:elasticloadbalancing:us-east-2:123456789012:loadbalancer/app/my-lb/50dc6c495c0c9188"
        )

    def test_load_balancer_to_vpc_relationship(self):
        """LB emits in_network relationship to its VPC."""
        client = make_client()
        ec2 = MagicMock()
        elbv2 = MagicMock()

        # Mock EC2 for VPCs (returns empty to simplify)
        ec2_paginator = MagicMock()
        ec2_paginator.paginate.return_value = iter(
            [{"Vpcs": [], "Subnets": [], "SecurityGroups": []}]
        )
        ec2.get_paginator.return_value = ec2_paginator

        # Mock ELBv2
        elbv2_paginator = MagicMock()
        elbv2_paginator.paginate.return_value = iter(
            [
                {
                    "LoadBalancers": [
                        {
                            "LoadBalancerName": "my-lb",
                            "LoadBalancerArn": "arn:aws:elasticloadbalancing:us-east-2:123456789012:loadbalancer/app/my-lb/50dc6c495c0c9188",
                            "Type": "application",
                            "VpcId": "vpc-abc",
                            "State": {"Code": "active"},
                            "DNSName": "my-lb.us-east-2.elb.amazonaws.com",
                        }
                    ]
                }
            ]
        )
        elbv2.get_paginator.return_value = elbv2_paginator
        elbv2.describe_tags.return_value = {"TagDescriptions": [{"Tags": []}]}
        elbv2.describe_target_groups.return_value = {"TargetGroups": []}

        def get_client_side_effect(service):
            if service == "elbv2":
                return elbv2
            return ec2

        client.session.client.side_effect = get_client_side_effect

        resources = client.discover_network()

        lb_resources = [r for r in resources if r["resource_type"] == "load_balancer"]
        assert len(lb_resources) > 0
        lb_resource = lb_resources[0]
        vpc_rel = [
            r for r in lb_resource["relationships"] if r["edge_type"] == "in_network"
        ]
        assert len(vpc_rel) == 1
        assert vpc_rel[0]["target_external_id"] == "vpc-abc"

    def test_load_balancer_routes_to_targets(self):
        """LB emits routes_to relationship for each target instance."""
        client = make_client()
        ec2 = MagicMock()
        elbv2 = MagicMock()

        # Mock EC2 for VPCs (returns empty to simplify)
        ec2_paginator = MagicMock()
        ec2_paginator.paginate.return_value = iter(
            [{"Vpcs": [], "Subnets": [], "SecurityGroups": []}]
        )
        ec2.get_paginator.return_value = ec2_paginator

        # Mock ELBv2
        elbv2_paginator = MagicMock()
        elbv2_paginator.paginate.return_value = iter(
            [
                {
                    "LoadBalancers": [
                        {
                            "LoadBalancerName": "my-lb",
                            "LoadBalancerArn": "arn:aws:elasticloadbalancing:us-east-2:123456789012:loadbalancer/app/my-lb/50dc6c495c0c9188",
                            "Type": "application",
                            "VpcId": "vpc-abc",
                            "State": {"Code": "active"},
                            "DNSName": "my-lb.us-east-2.elb.amazonaws.com",
                        }
                    ]
                }
            ]
        )
        elbv2.get_paginator.return_value = elbv2_paginator
        elbv2.describe_tags.return_value = {"TagDescriptions": [{"Tags": []}]}
        elbv2.describe_target_groups.return_value = {
            "TargetGroups": [
                {
                    "TargetGroupArn": "arn:aws:elasticloadbalancing:us-east-2:123456789012:targetgroup/my-targets/73e2d6bc24d8a067"
                }
            ]
        }
        elbv2.describe_target_health.return_value = {
            "TargetHealthDescriptions": [
                {"Target": {"Id": "i-1"}},
                {"Target": {"Id": "i-2"}},
            ]
        }

        def get_client_side_effect(service):
            if service == "elbv2":
                return elbv2
            return ec2

        client.session.client.side_effect = get_client_side_effect

        resources = client.discover_network()

        lb_resources = [r for r in resources if r["resource_type"] == "load_balancer"]
        assert len(lb_resources) > 0
        lb_resource = lb_resources[0]
        routes_to_rels = [
            r for r in lb_resource["relationships"] if r["edge_type"] == "routes_to"
        ]
        assert len(routes_to_rels) == 2
        assert set(r["target_external_id"] for r in routes_to_rels) == {"i-1", "i-2"}


class TestPhaseB2EC2Role:
    """Phase B2: EC2 assumes_role relationship capture."""

    def test_ec2_emits_assumes_role_with_resolved_role_arn(self):
        """EC2 resolves instance profile to role ARN and emits assumes_role."""
        client = make_client()
        ec2 = MagicMock()
        iam = MagicMock()
        paginated(
            ec2,
            [
                {
                    "Reservations": [
                        {
                            "Instances": [
                                {
                                    "InstanceId": "i-abc",
                                    "State": {"Name": "running"},
                                    "VpcId": "vpc-123",
                                    "SubnetId": "subnet-123",
                                    "SecurityGroups": [],
                                    "IamInstanceProfile": {
                                        "Arn": "arn:aws:iam::123456789012:instance-profile/ec2-role"
                                    },
                                    "Tags": [],
                                }
                            ]
                        }
                    ]
                }
            ],
        )
        iam.get_instance_profile.return_value = {
            "InstanceProfile": {
                "Roles": [{"Arn": "arn:aws:iam::123456789012:role/ec2-role"}]
            }
        }

        def get_client_side_effect(service):
            if service == "ec2":
                return ec2
            elif service == "iam":
                return iam
            return MagicMock()

        client.session.client.side_effect = get_client_side_effect

        resources = client.discover_compute()

        role_rels = [
            r for r in resources[0]["relationships"] if r["edge_type"] == "assumes_role"
        ]
        assert len(role_rels) == 1
        assert (
            role_rels[0]["target_external_id"]
            == "arn:aws:iam::123456789012:role/ec2-role"
        )
        assert role_rels[0]["target_kind"] == "identity"
