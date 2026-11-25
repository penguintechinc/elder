"""Gap-filling coverage tests for the AWS discovery client.

boto3 is replaced at the module boundary with fake sessions whose clients
return canned paginator pages / API payloads. Complements test_aws_discovery.
"""

import tests.unit.conftest_worker_stubs  # noqa: F401  isort: skip

from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import pytest
from botocore.exceptions import BotoCoreError, ClientError

from apps.worker.discovery import aws_discovery as aws
from apps.worker.discovery.aws_discovery import AWSDiscoveryClient

NOW = datetime(2024, 1, 2, tzinfo=UTC)


def cerr(op="Op"):
    """Build a botocore ClientError for the given operation."""
    return ClientError({"Error": {"Code": "Denied", "Message": "nope"}}, op)


def fake(pages=None, **attrs):
    """Build a fake boto client; `pages` maps paginator op name to page list."""
    pages = pages or {}
    c = MagicMock()

    def get_paginator(op):
        """Return a paginator replaying the pages registered for op."""
        p = MagicMock()
        val = pages.get(op, [])
        if isinstance(val, Exception):
            p.paginate.side_effect = val
        else:
            p.paginate.return_value = iter(val)
        return p

    c.get_paginator.side_effect = get_paginator
    for k, v in attrs.items():
        setattr(c, k, v)
    return c


def mk(clients=None, **cfg):
    """Build an AWSDiscoveryClient whose session serves the given fake clients."""
    config = {"provider_type": "aws", "region": "us-east-2"}
    config.update(cfg)
    with patch.object(aws, "boto3") as b3:
        b3.Session.return_value = MagicMock()
        c = AWSDiscoveryClient(config)
    reg = clients or {}
    c.session = MagicMock()
    c.session.client.side_effect = lambda name: reg.get(name, MagicMock())
    return c


def raising(op_error=None):
    """Return a fake client whose every paginator raises ClientError."""
    return fake(
        {
            k: (op_error or cerr())
            for k in (
                "describe_instances",
                "describe_volumes",
                "describe_vpcs",
                "describe_subnets",
                "describe_security_groups",
                "describe_load_balancers",
                "describe_db_instances",
                "list_functions",
                "list_tables",
                "list_queues",
                "list_topics",
                "describe_repositories",
                "describe_log_groups",
                "list_state_machines",
                "list_rules",
                "get_rest_apis",
                "describe_file_systems",
                "describe_cache_clusters",
                "list_distributions",
                "list_hosted_zones",
                "list_users",
                "list_roles",
            )
        }
    )


class TestInitAndAuth:
    """Constructor, session creation and identity helpers."""

    def test_boto3_missing(self):
        """Missing boto3 raises ImportError."""
        with patch.object(aws, "boto3", None):
            with pytest.raises(ImportError):
                AWSDiscoveryClient({"provider_type": "aws"})

    def test_region_env_default(self, monkeypatch):
        """Region falls back to AWS_DEFAULT_REGION, then us-east-1."""
        monkeypatch.setenv("AWS_DEFAULT_REGION", "eu-west-2")
        assert mk(region="").region == "eu-west-2"
        monkeypatch.delenv("AWS_DEFAULT_REGION")
        assert mk(region="").region == "us-east-1"

    def test_session_token_forwarded(self):
        """Static credentials include the optional session token."""
        sec = "-".join(("cove", "sec"))
        with patch.object(aws, "boto3") as b3:
            AWSDiscoveryClient(
                {
                    "access_key_id": "id1",
                    "secret_access_key": sec,
                    "session_token": "tok",
                    "region": "r",
                }
            )
        kwargs = b3.Session.call_args.kwargs
        assert kwargs["aws_session_token"] == "tok"

    def test_web_identity_from_env(self, monkeypatch, tmp_path):
        """AWS_ROLE_ARN + token file env vars select web identity auth."""
        tf = tmp_path / "token"
        tf.write_text("jwt-value\n")
        monkeypatch.setenv("AWS_ROLE_ARN", "arn:aws:iam::1:role/r")
        monkeypatch.setenv("AWS_WEB_IDENTITY_TOKEN_FILE", str(tf))
        with patch.object(aws, "boto3") as b3:
            sts = b3.Session.return_value.client.return_value
            sts.assume_role_with_web_identity.return_value = {
                "Credentials": {
                    "AccessKeyId": "a",
                    "SecretAccessKey": "b",
                    "SessionToken": "c",
                }
            }
            c = AWSDiscoveryClient({"region": "r"})
        assert c.get_auth_method() == AWSDiscoveryClient.AUTH_WEB_IDENTITY
        call = sts.assume_role_with_web_identity.call_args.kwargs
        assert call["WebIdentityToken"] == "jwt-value"
        assert call["RoleSessionName"] == "elder-discovery"

    def test_web_identity_errors(self, tmp_path):
        """Missing, unreadable token files and STS errors become ValueError."""
        c = mk()
        with pytest.raises(ValueError, match="not found"):
            c._create_web_identity_session("arn", str(tmp_path / "missing"))
        with pytest.raises(ValueError, match="Failed to read"):
            c._create_web_identity_session("arn", str(tmp_path))  # a directory
        tf = tmp_path / "t"
        tf.write_text("x")
        with patch.object(aws, "boto3") as b3:
            sts = b3.Session.return_value.client.return_value
            sts.assume_role_with_web_identity.side_effect = cerr()
            with pytest.raises(ValueError, match="Denied - nope"):
                c._create_web_identity_session("arn", str(tf), "sess")

    def test_get_auth_method_unknown(self):
        """An unset auth method reports 'unknown'."""
        c = mk()
        c.auth_method = None
        assert c.get_auth_method() == "unknown"

    def test_caller_identity(self):
        """Identity success and failure paths."""
        c = mk()
        sts = MagicMock()
        sts.get_caller_identity.return_value = {
            "Account": "1",
            "Arn": "a",
            "UserId": "u",
        }
        c.session.client.side_effect = lambda n: sts
        assert c.get_caller_identity() == {"account": "1", "arn": "a", "user_id": "u"}
        sts.get_caller_identity.side_effect = cerr()
        assert c.get_caller_identity() is None
        assert c.test_connection() is False
        sts.get_caller_identity.side_effect = BotoCoreError()
        assert c.test_connection() is False
        sts.get_caller_identity.side_effect = None
        assert c.test_connection() is True


class TestDiscoverAllDispatch:
    """discover_all honours the service filter for each category."""

    CATS = {
        "compute": ["ec2"],
        "storage": ["s3", "ebs", "ecr", "logs", "efs", "elasticache", "dynamodb"],
        "network": ["vpc", "elb", "cloudfront", "route53", "apigateway"],
        "databases": ["rds", "dynamodb"],
        "serverless": ["lambda", "sqs", "sns", "stepfunctions", "events"],
        "iam": ["iam"],
    }
    METHODS = {
        "compute": "discover_compute",
        "storage": "discover_storage",
        "network": "discover_network",
        "databases": "discover_databases",
        "serverless": "discover_serverless",
        "iam": "discover_iam",
    }

    def _patched(self, c):
        """Patch every category method on c to return one item; return mocks."""
        mocks = {}
        for cat, meth in self.METHODS.items():
            m = MagicMock(return_value=[cat])
            setattr(c, meth, m)
            mocks[cat] = m
        return mocks

    def test_no_filter_runs_everything(self):
        """An empty filter calls every category."""
        c = mk()
        mocks = self._patched(c)
        out = c.discover_all()
        assert all(m.called for m in mocks.values())
        assert out["resources_count"] == 6 and out["iam"] == ["iam"]

    @pytest.mark.parametrize("cat", list(CATS))
    def test_single_service_filter(self, cat):
        """Each filter service selects only its categories."""
        for svc in self.CATS[cat]:
            c = mk(services=[svc])
            mocks = self._patched(c)
            c.discover_all()
            for other, m in mocks.items():
                expected = svc in self.CATS[other]
                assert m.called is expected, (svc, other)

    def test_unrelated_filter_runs_nothing(self):
        """A filter naming only unrelated services runs no category."""
        c = mk(services=["nothing"])
        mocks = self._patched(c)
        assert c.discover_all()["resources_count"] == 0
        assert not any(m.called for m in mocks.values())


class TestComputeDetails:
    """EC2 instance profile resolution and optional fields."""

    def _run(self, instance, iam=None):
        """Run discover_compute for one instance with an optional IAM fake."""
        ec2 = fake(
            {"describe_instances": [{"Reservations": [{"Instances": [instance]}]}]}
        )
        return mk({"ec2": ec2, "iam": iam or MagicMock()}).discover_compute()[0]

    def test_profile_resolved(self):
        """Instance profile resolves to a role arn relationship."""
        iam = MagicMock()
        iam.get_instance_profile.return_value = {
            "InstanceProfile": {"Roles": [{"Arn": "arn:role"}]}
        }
        r = self._run(
            {
                "InstanceId": "i-1",
                "IamInstanceProfile": {"Arn": "arn:aws:iam::1:instance-profile/p"},
                "LaunchTime": NOW,
            },
            iam,
        )
        assert r["relationships"][-1]["edge_type"] == "assumes_role"
        assert r["metadata"]["launch_time"] == NOW.isoformat()
        assert iam.get_instance_profile.call_args.kwargs == {"InstanceProfileName": "p"}

    @pytest.mark.parametrize(
        "profile_info",
        [
            {"InstanceProfile": {"Roles": []}},
            {"InstanceProfile": {"Roles": [{}]}},
        ],
    )
    def test_profile_without_role(self, profile_info):
        """Profiles with no usable role add no relationship."""
        iam = MagicMock()
        iam.get_instance_profile.return_value = profile_info
        r = self._run({"InstanceId": "i-1", "IamInstanceProfile": {"Arn": "x/p"}}, iam)
        assert r["relationships"] == []

    def test_profile_error_and_no_arn(self):
        """IAM errors are tolerated; a profile without ARN is skipped."""
        iam = MagicMock()
        iam.get_instance_profile.side_effect = RuntimeError("x")
        r = self._run({"InstanceId": "i", "IamInstanceProfile": {"Arn": "x/p"}}, iam)
        assert r["relationships"] == []
        r = self._run({"InstanceId": "i", "IamInstanceProfile": {}})
        assert r["relationships"] == [] and r["metadata"]["launch_time"] is None

    def test_error_returns_empty(self):
        """AWS errors yield an empty list."""
        assert mk({"ec2": raising()}).discover_compute() == []


class TestStorage:
    """S3, EBS and storage service dispatch."""

    def test_s3_buckets(self):
        """Bucket location/tag lookups degrade gracefully per bucket."""
        s3 = MagicMock()
        s3.list_buckets.return_value = {
            "Buckets": [
                {"Name": "a", "CreationDate": NOW},
                {"Name": "b"},
                {"Name": "c"},
            ]
        }
        loc = {
            "a": {"LocationConstraint": "eu-west-1"},
            "b": {"LocationConstraint": None},
        }

        def get_loc(Bucket):
            """Return a location or raise for bucket c."""
            if Bucket not in loc:
                raise cerr()
            return loc[Bucket]

        def get_tags(Bucket):
            """Return tags for a only; raise otherwise."""
            if Bucket == "a":
                return {"TagSet": [{"Key": "k", "Value": "v"}]}
            raise cerr()

        s3.get_bucket_location.side_effect = get_loc
        s3.get_bucket_tagging.side_effect = get_tags
        c = mk({"s3": s3}, services=["s3"])
        out = c.discover_storage()
        assert [r["region"] for r in out] == ["eu-west-1", "us-east-1", "unknown"]
        assert out[0]["tags"] == {"k": "v"} and out[1]["tags"] == {}
        assert out[0]["metadata"]["creation_date"] == NOW.isoformat()
        assert out[1]["metadata"]["creation_date"] is None

    def test_s3_error(self):
        """list_buckets errors yield an empty list."""
        s3 = MagicMock()
        s3.list_buckets.side_effect = cerr()
        assert mk({"s3": s3}, services=["s3"]).discover_storage() == []

    def test_ebs(self):
        """Volumes emit attached_to edges only for attachments with instances."""
        ec2 = fake(
            {
                "describe_volumes": [
                    {
                        "Volumes": [
                            {
                                "VolumeId": "v1",
                                "Attachments": [{"InstanceId": "i-1"}, {}],
                            }
                        ]
                    }
                ]
            }
        )
        out = mk({"ec2": ec2}, services=["ebs"]).discover_storage()
        assert len(out[0]["relationships"]) == 1
        assert mk({"ec2": raising()}, services=["ebs"]).discover_storage() == []

    @pytest.mark.parametrize(
        "svc,meth",
        [
            ("dynamodb", "discover_dynamodb"),
            ("ecr", "discover_ecr"),
            ("logs", "discover_logs"),
            ("efs", "discover_efs"),
            ("elasticache", "discover_elasticache"),
        ],
    )
    def test_dispatch(self, svc, meth):
        """Each storage service calls only its sub-discoverer when filtered."""
        c = mk(services=[svc])
        names = [
            "discover_dynamodb",
            "discover_ecr",
            "discover_logs",
            "discover_efs",
            "discover_elasticache",
        ]
        mocks = {}
        for n in names:
            mocks[n] = MagicMock(return_value=[n])
            setattr(c, n, mocks[n])
        assert c.discover_storage() == [meth]

    def test_dispatch_all(self):
        """No filter calls every storage sub-discoverer."""
        c = mk({"s3": MagicMock(), "ec2": raising()})
        c.session.client.side_effect = lambda n: (
            raising()
            if n == "ec2"
            else MagicMock(list_buckets=MagicMock(return_value={}))
        )
        for n in (
            "discover_dynamodb",
            "discover_ecr",
            "discover_logs",
            "discover_efs",
            "discover_elasticache",
        ):
            setattr(c, n, MagicMock(return_value=[n]))
        assert len(c.discover_storage()) == 5


class TestNetwork:
    """VPCs, subnets, load balancers and dispatch."""

    def test_vpc_subnet_sg(self):
        """VPCs, subnets (with and without VPC id) and SGs are produced."""
        ec2 = fake(
            {
                "describe_vpcs": [{"Vpcs": [{"VpcId": "vpc-1"}]}],
                "describe_subnets": [
                    {
                        "Subnets": [
                            {"SubnetId": "s1", "VpcId": "vpc-1"},
                            {"SubnetId": "s2"},
                        ]
                    }
                ],
                "describe_security_groups": [
                    {
                        "SecurityGroups": [
                            {"GroupId": "sg1", "VpcId": "vpc-1", "IpPermissions": [1]},
                            {"GroupId": "sg2"},
                        ]
                    }
                ],
            }
        )
        c = mk({"ec2": ec2}, services=["vpc"])
        out = c.discover_network()
        types = [r["resource_type"] for r in out]
        assert types == ["vpc", "subnet", "subnet", "security_group", "security_group"]
        assert out[1]["relationships"] and not out[2]["relationships"]
        assert out[3]["metadata"]["ingress_rules"] == 1
        assert out[4]["relationships"] == [] and out[4]["name"] == "sg2"

    def test_network_errors(self):
        """VPC/subnet/SG failures are tolerated."""
        c = mk({"ec2": raising()}, services=["vpc"])
        assert c.discover_network() == []
        assert c.discover_security_groups() == []

    def test_elb(self):
        """Load balancers resolve tags and targets, tolerating failures."""
        elb = MagicMock()
        elb.get_paginator.side_effect = lambda op: MagicMock(
            paginate=MagicMock(
                return_value=iter(
                    [
                        {
                            "LoadBalancers": [
                                {
                                    "LoadBalancerArn": "arn1",
                                    "LoadBalancerName": "lb1",
                                    "VpcId": "vpc-1",
                                    "State": {"Code": "active"},
                                },
                                {"LoadBalancerArn": "arn2", "LoadBalancerName": "lb2"},
                            ]
                        }
                    ]
                )
            )
        )

        def tags(ResourceArns):
            """Return tags for arn1, fail for arn2."""
            if ResourceArns == ["arn1"]:
                return {"TagDescriptions": [{"Tags": [{"Key": "a", "Value": "b"}]}]}
            raise cerr()

        def tgs(LoadBalancerArn):
            """Return target groups for arn1, fail for arn2."""
            if LoadBalancerArn == "arn1":
                return {"TargetGroups": [{"TargetGroupArn": "tg1"}, {}]}
            raise cerr()

        elb.describe_tags.side_effect = tags
        elb.describe_target_groups.side_effect = tgs
        elb.describe_target_health.return_value = {
            "TargetHealthDescriptions": [{"Target": {"Id": "i-1"}}, {"Target": {}}]
        }
        out = mk({"elbv2": elb}, services=["elb"]).discover_network()
        assert out[0]["tags"] == {"a": "b"}
        assert [r["edge_type"] for r in out[0]["relationships"]] == [
            "in_network",
            "routes_to",
        ]
        assert out[1]["tags"] == {} and out[1]["relationships"] == []

    def test_elb_error(self):
        """ELB listing errors yield an empty list."""
        assert mk({"elbv2": raising()}, services=["elb"]).discover_network() == []

    @pytest.mark.parametrize(
        "svc,meth",
        [
            ("apigateway", "discover_apigateway"),
            ("cloudfront", "discover_cloudfront"),
            ("route53", "discover_route53"),
        ],
    )
    def test_dispatch(self, svc, meth):
        """Each network service calls only its sub-discoverer."""
        c = mk(services=[svc])
        for n in ("discover_apigateway", "discover_cloudfront", "discover_route53"):
            setattr(c, n, MagicMock(return_value=[n]))
        assert c.discover_network() == [meth]

    def test_dispatch_all(self):
        """No filter calls every sub-discoverer."""
        c = mk({"ec2": raising(), "elbv2": raising()})
        for n in ("discover_apigateway", "discover_cloudfront", "discover_route53"):
            setattr(c, n, MagicMock(return_value=[n]))
        assert len(c.discover_network()) == 3


class TestDatabasesAndServerless:
    """RDS, Lambda and serverless dispatch."""

    def test_rds(self):
        """RDS instances with/without VPC, SGs and tag failures."""
        rds = fake(
            {
                "describe_db_instances": [
                    {
                        "DBInstances": [
                            {
                                "DBInstanceArn": "arn1",
                                "DBInstanceIdentifier": "d1",
                                "DBSubnetGroup": {"VpcId": "vpc-1"},
                                "VpcSecurityGroups": [
                                    {"VpcSecurityGroupId": "sg1"},
                                    {},
                                ],
                                "Endpoint": {"Address": "h", "Port": 5432},
                            },
                            {"DBInstanceArn": "arn2", "DBInstanceIdentifier": "d2"},
                        ]
                    }
                ]
            }
        )

        def tags(ResourceName):
            """Return tags for arn1; fail for others."""
            if ResourceName == "arn1":
                return {"TagList": [{"Key": "a", "Value": "b"}]}
            raise cerr()

        rds.list_tags_for_resource.side_effect = tags
        out = mk({"rds": rds}).discover_databases()
        assert len(out[0]["relationships"]) == 2 and out[0]["tags"] == {"a": "b"}
        assert out[1]["relationships"] == [] and out[1]["tags"] == {}

    def test_rds_error(self):
        """RDS failures yield an empty list."""
        assert mk({"rds": raising()}).discover_databases() == []

    def test_lambda(self):
        """Lambda functions emit VPC/SG/role edges and tolerate tag errors."""
        lam = fake(
            {
                "list_functions": [
                    {
                        "Functions": [
                            {
                                "FunctionArn": "arn1",
                                "FunctionName": "f1",
                                "VpcConfig": {
                                    "VpcId": "vpc-1",
                                    "SecurityGroupIds": ["sg1"],
                                },
                                "Role": "arn:role",
                            },
                            {"FunctionArn": "arn2", "FunctionName": "f2"},
                        ]
                    }
                ]
            }
        )

        def tags(Resource):
            """Return tags for arn1; fail otherwise."""
            if Resource == "arn1":
                return {"Tags": {"k": "v"}}
            raise cerr()

        lam.list_tags.side_effect = tags
        c = mk({"lambda": lam}, services=["lambda"])
        out = c.discover_serverless()
        assert len(out[0]["relationships"]) == 3 and out[0]["tags"] == {"k": "v"}
        assert out[1]["relationships"] == [] and out[1]["tags"] == {}

    def test_lambda_error(self):
        """Lambda failures yield an empty list."""
        assert (
            mk({"lambda": raising()}, services=["lambda"]).discover_serverless() == []
        )

    @pytest.mark.parametrize(
        "svc,meth",
        [
            ("sqs", "discover_sqs"),
            ("sns", "discover_sns"),
            ("stepfunctions", "discover_stepfunctions"),
            ("events", "discover_events"),
        ],
    )
    def test_dispatch(self, svc, meth):
        """Each serverless service calls only its sub-discoverer."""
        c = mk({"lambda": raising()}, services=[svc])
        for n in (
            "discover_sqs",
            "discover_sns",
            "discover_stepfunctions",
            "discover_events",
        ):
            setattr(c, n, MagicMock(return_value=[n]))
        assert c.discover_serverless() == [meth]

    def test_dispatch_all(self):
        """No filter calls every sub-discoverer."""
        c = mk({"lambda": raising()})
        for n in (
            "discover_sqs",
            "discover_sns",
            "discover_stepfunctions",
            "discover_events",
        ):
            setattr(c, n, MagicMock(return_value=[n]))
        assert len(c.discover_serverless()) == 4


class TestDynamoSqsSns:
    """Services with per-item API calls."""

    def test_dynamodb(self):
        """Tables with tags/dates, tag failures and describe failures."""
        ddb = fake({"list_tables": [{"TableNames": ["t1", "t2", "t3"]}]})

        def describe(TableName):
            """Describe t1/t2; fail t3."""
            if TableName == "t3":
                raise cerr()
            return {
                "Table": {
                    "TableArn": f"arn-{TableName}",
                    "CreationDateTime": NOW if TableName == "t1" else None,
                }
            }

        def tags(ResourceArn):
            """Tags for t1 only."""
            if ResourceArn == "arn-t1":
                return {"Tags": [{"Key": "a", "Value": "b"}]}
            raise cerr()

        ddb.describe_table.side_effect = describe
        ddb.list_tags_of_resource.side_effect = tags
        out = mk({"dynamodb": ddb}).discover_dynamodb()
        assert [r["name"] for r in out] == ["t1", "t2"]
        assert out[0]["tags"] == {"a": "b"} and out[1]["tags"] == {}
        assert out[0]["metadata"]["creation_date"] == NOW.isoformat()
        assert out[1]["metadata"]["creation_date"] is None
        assert mk({"dynamodb": raising()}).discover_dynamodb() == []

    def test_sqs(self):
        """Queues are described; per-queue failures are skipped."""
        sqs = fake(
            {"list_queues": [{"QueueUrls": ["https://q/1/qa", "https://q/1/qb"]}]}
        )

        def attrs(QueueUrl, AttributeNames):
            """Return attributes for qa; fail qb."""
            if QueueUrl.endswith("qb"):
                raise cerr()
            return {
                "Attributes": {"QueueArn": "arn", "ApproximateNumberOfMessages": "5"}
            }

        sqs.get_queue_attributes.side_effect = attrs
        out = mk({"sqs": sqs}).discover_sqs()
        assert len(out) == 1 and out[0]["name"] == "qa"
        assert out[0]["metadata"]["approximate_message_count"] == 5
        assert out[0]["metadata"]["visibility_timeout"] == 30
        assert mk({"sqs": raising()}).discover_sqs() == []

    def test_sns(self):
        """Topics are described; per-topic failures are skipped."""
        sns = fake(
            {
                "list_topics": [
                    {"Topics": [{"TopicArn": "a:b:ta"}, {"TopicArn": "a:b:tb"}]}
                ]
            }
        )

        def attrs(TopicArn):
            """Return attributes for ta; fail tb."""
            if TopicArn.endswith("tb"):
                raise cerr()
            return {"Attributes": {"SubscriptionsConfirmed": "2", "DisplayName": "d"}}

        sns.get_topic_attributes.side_effect = attrs
        out = mk({"sns": sns}).discover_sns()
        assert len(out) == 1 and out[0]["metadata"]["subscriptions_confirmed"] == 2
        assert mk({"sns": raising()}).discover_sns() == []


class TestSimpleServices:
    """Services that are a single paginator loop."""

    CASES = [
        (
            "ecr",
            "discover_ecr",
            "describe_repositories",
            {
                "repositories": [
                    {"repositoryArn": "a", "repositoryName": "n", "createdAt": NOW},
                    {"repositoryArn": "b", "repositoryName": "m"},
                ]
            },
            "ecr_repository",
        ),
        (
            "logs",
            "discover_logs",
            "describe_log_groups",
            {"logGroups": [{"arn": "a", "logGroupName": "n"}]},
            "cloudwatch_log_group",
        ),
        (
            "stepfunctions",
            "discover_stepfunctions",
            "list_state_machines",
            {
                "stateMachines": [
                    {"stateMachineArn": "a", "name": "n", "creationDate": NOW},
                    {"stateMachineArn": "b", "name": "m"},
                ]
            },
            "step_function",
        ),
        (
            "events",
            "discover_events",
            "list_rules",
            {
                "Rules": [
                    {"Arn": "a", "Name": "n", "CreationTime": NOW},
                    {"Arn": "b", "Name": "m"},
                ]
            },
            "eventbridge_rule",
        ),
        (
            "apigateway",
            "discover_apigateway",
            "get_rest_apis",
            {
                "items": [
                    {"id": "a", "name": "n", "createdDate": NOW},
                    {"id": "b", "name": "m"},
                ]
            },
            "api_gateway_rest_api",
        ),
        (
            "efs",
            "discover_efs",
            "describe_file_systems",
            {
                "FileSystems": [
                    {"FileSystemId": "a", "Name": "n", "CreationTime": NOW},
                    {"FileSystemId": "b"},
                ]
            },
            "efs_filesystem",
        ),
        (
            "elasticache",
            "discover_elasticache",
            "describe_cache_clusters",
            {
                "CacheClusters": [
                    {
                        "ARN": "a",
                        "CacheClusterId": "n",
                        "CacheSecurityGroups": [{"CacheSecurityGroupName": "g"}],
                        "SecurityGroups": [{"VpcSecurityGroupId": "sg"}],
                    }
                ]
            },
            "elasticache_cluster",
        ),
        (
            "cloudfront",
            "discover_cloudfront",
            "list_distributions",
            {
                "DistributionList": {
                    "Items": [
                        {
                            "Id": "a",
                            "DomainName": "d.example.com",
                            "LastModifiedTime": NOW,
                            "Origins": {"Items": [{"DomainName": "o.example.com"}]},
                        },
                        {"Id": "b"},
                    ]
                }
            },
            "cloudfront_distribution",
        ),
        (
            "route53",
            "discover_route53",
            "list_hosted_zones",
            {
                "HostedZones": [
                    {"Id": "a", "Name": "n.", "Config": {"PrivateZone": True}}
                ]
            },
            "route53_hosted_zone",
        ),
    ]

    @pytest.mark.parametrize("svc,meth,op,page,rtype", CASES)
    def test_items_and_errors(self, svc, meth, op, page, rtype):
        """Each service maps its page to resources and tolerates errors."""
        out = getattr(mk({svc: fake({op: [page]})}), meth)()
        assert out and all(r["resource_type"] == rtype for r in out)
        assert getattr(mk({svc: fake({op: cerr()})}), meth)() == []
        assert getattr(mk({svc: fake({op: BotoCoreError()})}), meth)() == []


class TestIam:
    """IAM users and roles."""

    def test_users_and_roles(self):
        """Users/roles map metadata and tolerate sub-call failures."""
        iam = fake(
            {
                "list_users": [
                    {
                        "Users": [
                            {
                                "UserName": "u1",
                                "Arn": "arn:u1",
                                "CreateDate": NOW,
                                "PasswordLastUsed": NOW,
                            },
                            {"UserName": "u2", "Arn": "arn:u2"},
                        ]
                    }
                ],
                "list_roles": [
                    {
                        "Roles": [
                            {"RoleName": "r1", "Arn": "arn:r1", "CreateDate": NOW},
                            {"RoleName": "r2", "Arn": "arn:r2"},
                            {
                                "RoleName": "slr",
                                "Arn": "arn:slr",
                                "Path": "/aws-service-role/x/",
                            },
                        ]
                    }
                ],
            }
        )

        def user_tags(UserName):
            """Tags for u1; fail for u2."""
            if UserName == "u1":
                return {"Tags": [{"Key": "a", "Value": "b"}]}
            raise cerr()

        def keys(UserName):
            """Keys for u1; fail for u2."""
            if UserName == "u1":
                return {"AccessKeyMetadata": [{}, {}]}
            raise cerr()

        def groups(UserName):
            """Groups for u1; fail for u2."""
            if UserName == "u1":
                return {"Groups": [{"GroupName": "g"}]}
            raise cerr()

        def role_tags(RoleName):
            """Tags for r1; fail for r2."""
            if RoleName == "r1":
                return {"Tags": [{"Key": "x", "Value": "y"}]}
            raise cerr()

        iam.list_user_tags.side_effect = user_tags
        iam.list_access_keys.side_effect = keys
        iam.list_groups_for_user.side_effect = groups
        iam.list_role_tags.side_effect = role_tags
        out = mk({"iam": iam}).discover_iam()
        names = [r["name"] for r in out]
        assert names == ["u1", "u2", "r1", "r2"]
        u1, u2, r1, r2 = out
        assert u1["metadata"]["access_keys_count"] == 2 and u1["metadata"][
            "groups"
        ] == ["g"]
        assert (
            u1["tags"] == {"a": "b"}
            and u1["metadata"]["create_date"] == NOW.isoformat()
        )
        assert u2["metadata"]["access_keys_count"] == 0 and u2["tags"] == {}
        assert u2["metadata"]["password_last_used"] is None
        assert r1["tags"] == {"x": "y"} and r2["tags"] == {}
        assert r1["metadata"]["create_date"] == NOW.isoformat()
        assert r2["metadata"]["create_date"] is None

    def test_error(self):
        """Top-level IAM errors yield an empty list."""
        assert mk({"iam": raising()}).discover_iam() == []


class TestNameFromTags:
    """_get_name_from_tags fallbacks."""

    @pytest.mark.parametrize(
        "tags,expected",
        [
            ([{"Key": "Name", "Value": "web"}], "web"),
            ([{"Key": "Name", "Value": ""}], "fb"),
            ([{"Key": "Other", "Value": "x"}], "fb"),
            ([], "fb"),
            (None, "fb"),
        ],
    )
    def test_names(self, tags, expected):
        """Name tag wins; everything else falls back."""
        assert mk()._get_name_from_tags(tags, "fb") == expected
