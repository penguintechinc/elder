"""Behavioural tests for the AWS connector's remaining paths (auth, errors, global services)."""

from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from botocore.exceptions import ClientError, NoCredentialsError

import tests.unit.conftest_worker_stubs  # noqa: F401
from apps.worker.config.settings import settings
from apps.worker.connectors import aws_connector as mod
from apps.worker.connectors.aws_connector import AWSConnector
from tests.unit._cov_f_helpers import (
    assert_not_leaked,
    by_name,
    created_entities,
    fake_secret,
    logged_text,
    make_elder,
    spy_logger,
    updated_entities,
)

REGION = "us-east-1"
ORG_ID = 21
ACCOUNT = "123456789012"


def _client_error(code: str, operation: str = "Describe") -> ClientError:
    """Build a botocore ClientError with the given AWS error code."""
    return ClientError({"Error": {"Code": code, "Message": code}}, operation)


def _paginator(pages: list[dict[str, Any]] | Exception) -> MagicMock:
    """Return a boto3 paginator double yielding the given pages (or raising)."""
    paginator = MagicMock()
    if isinstance(pages, Exception):
        paginator.paginate.side_effect = pages
    else:
        paginator.paginate.return_value = iter(pages)
    return paginator


@pytest.fixture(autouse=True)
def _frozen_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    """Freeze the connector's clock and pin the default region."""
    monkeypatch.setattr(mod.time, "time", lambda: 1_700_000_000.2)
    monkeypatch.setattr(settings, "aws_default_region", REGION)
    monkeypatch.setattr(settings, "create_missing_organizations", True)


@pytest.fixture
def connector() -> AWSConnector:
    """Return a connector with an Elder double that knows no entities yet."""
    c = AWSConnector()
    c.elder_client = make_elder()
    c.elder_client.get_entity_by_external_id.return_value = None
    ids = iter(range(1000, 1100))
    c.elder_client.create_entity.side_effect = lambda _e: {"id": next(ids)}
    spy_logger(c)
    return c


def _service(connector: AWSConnector, service: str, region: str = REGION) -> MagicMock:
    """Install and return a boto3 client double for one service/region."""
    client = MagicMock(name=f"boto3.{service}")
    connector.aws_clients[f"{service}:{region}"] = client
    return client


class TestConnect:
    """Credential verification."""

    @pytest.mark.asyncio
    async def test_connect_verifies_identity_without_logging_secret(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """STS verifies the keys; the secret key never reaches the logs."""
        secret = fake_secret("aws", "secret", "key")
        monkeypatch.setattr(settings, "aws_access_key_id", fake_secret("akid"))
        monkeypatch.setattr(settings, "aws_secret_access_key", secret)
        c = AWSConnector()
        logger = spy_logger(c)
        elder = AsyncMock()
        with (
            patch.object(mod.boto3, "client") as boto_client,
            patch.object(mod, "ElderAPIClient", return_value=elder),
        ):
            boto_client.return_value.get_caller_identity.return_value = {
                "Account": ACCOUNT,
                "Arn": f"arn:aws:iam::{ACCOUNT}:user/elder",
            }
            await c.connect()

        assert boto_client.call_args.args == ("sts",)
        assert boto_client.call_args.kwargs["aws_secret_access_key"] == secret
        assert boto_client.call_args.kwargs["region_name"] == REGION
        elder.connect.assert_awaited_once()
        assert ACCOUNT in logged_text(logger)
        assert_not_leaked(secret, logged_text(logger))

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "error",
        [
            NoCredentialsError(),
            _client_error("InvalidClientTokenId", "GetCallerIdentity"),
        ],
        ids=["no-credentials", "invalid-token"],
    )
    async def test_connect_auth_failure_raises_before_elder(self, error: Any) -> None:
        """Missing or rejected credentials abort connect before Elder is contacted."""
        c = AWSConnector()
        logger = spy_logger(c)
        with (
            patch.object(mod.boto3, "client") as boto_client,
            patch.object(mod, "ElderAPIClient") as elder_cls,
        ):
            boto_client.return_value.get_caller_identity.side_effect = error
            with pytest.raises(type(error)):
                await c.connect()
        elder_cls.assert_not_called()
        logger.error.assert_called_once()

    @pytest.mark.asyncio
    async def test_disconnect_clears_all_state(self, connector: AWSConnector) -> None:
        """Disconnect closes Elder and drops clients plus both caches."""
        _service(connector, "ec2")
        connector.organization_cache["root:AWS"] = 1
        connector._entity_id_cache["vpc-1"] = 2
        elder = connector.elder_client
        await connector.disconnect()
        elder.close.assert_awaited_once()
        assert connector.aws_clients == {}
        assert connector.organization_cache == {}
        assert connector._entity_id_cache == {}

    def test_clients_are_cached_per_service_and_region(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """One boto3 client is built per service+region pair and then reused."""
        c = AWSConnector()
        with patch.object(
            mod.boto3, "client", side_effect=lambda *a, **k: object()
        ) as bc:
            first = c._get_aws_client("ec2", "us-east-1")
            assert c._get_aws_client("ec2", "us-east-1") is first
            assert c._get_aws_client("ec2", "eu-west-1") is not first
        assert bc.call_count == 2
        assert bc.call_args_list[1].kwargs["region_name"] == "eu-west-1"

    @pytest.mark.asyncio
    async def test_health_check_states(self) -> None:
        """Health mirrors whether STS accepts the credentials."""
        c = AWSConnector()
        spy_logger(c)
        with patch.object(mod.boto3, "client") as boto_client:
            assert await c.health_check() is True
            boto_client.return_value.get_caller_identity.side_effect = _client_error(
                "ExpiredToken"
            )
            assert await c.health_check() is False


class TestUpsertAndLink:
    """Entity upsert and dependency wiring."""

    @pytest.mark.asyncio
    async def test_link_uses_cache_then_elder_then_gives_up(
        self, connector: AWSConnector
    ) -> None:
        """Targets resolve from cache, then Elder; unknown targets create no edge."""
        connector._entity_id_cache["vpc-cached"] = 5
        await connector._link(1, "vpc-cached", "in_vpc")
        connector.elder_client.get_entity_by_external_id.assert_not_awaited()

        connector.elder_client.get_entity_by_external_id.return_value = {"id": 6}
        await connector._link(1, "vpc-remote", "in_vpc")
        assert connector._entity_id_cache["vpc-remote"] == 6

        connector.elder_client.get_entity_by_external_id.return_value = None
        await connector._link(1, "vpc-unknown", "in_vpc")

        edges = [
            c.kwargs
            for c in connector.elder_client.get_or_create_dependency.call_args_list
        ]
        assert edges == [
            {"source_entity_id": 1, "target_entity_id": 5, "dependency_type": "in_vpc"},
            {"source_entity_id": 1, "target_entity_id": 6, "dependency_type": "in_vpc"},
        ]


class TestRegionalServices:
    """VPC, EC2, RDS, ElastiCache, SQS and Lambda paths."""

    @pytest.mark.asyncio
    async def test_vpcs_named_from_tag_or_id(self, connector: AWSConnector) -> None:
        """A Name tag wins; otherwise the VPC id is the display name."""
        ec2 = _service(connector, "ec2")
        ec2.describe_vpcs.return_value = {
            "Vpcs": [
                {
                    "VpcId": "vpc-1",
                    "CidrBlock": "10.0.0.0/16",
                    "State": "available",
                    "IsDefault": True,
                    "Tags": [
                        {"Key": "env", "Value": "prod"},
                        {"Key": "Name", "Value": "core"},
                    ],
                },
                {"VpcId": "vpc-2"},
            ]
        }
        assert await connector._sync_vpcs(REGION, ORG_ID) == (2, 0)
        got = by_name(created_entities(connector.elder_client))
        core = got["VPC: core"]
        assert (core.entity_type, core.external_id, core.organization_id) == (
            "vpc",
            "vpc-1",
            ORG_ID,
        )
        assert core.attributes["is_default"] is True
        assert core.status_metadata == {
            "status": "Available",
            "timestamp": 1_700_000_000,
        }
        assert got["VPC: vpc-2"].attributes["state"] == "unknown"
        assert connector._entity_id_cache == {"vpc-1": 1000, "vpc-2": 1001}

    @pytest.mark.asyncio
    async def test_existing_vpc_counts_as_update(self, connector: AWSConnector) -> None:
        """A VPC already in Elder is updated and reported as an update."""
        connector.elder_client.get_entity_by_external_id.return_value = {"id": 77}
        _service(connector, "ec2").describe_vpcs.return_value = {
            "Vpcs": [{"VpcId": "vpc-1"}]
        }
        assert await connector._sync_vpcs(REGION, ORG_ID) == (0, 1)
        assert updated_entities(connector.elder_client)[0][0] == 77
        connector.elder_client.get_entity_by_external_id.assert_awaited_with(
            "vpc-1", organization_id=ORG_ID
        )

    @pytest.mark.asyncio
    async def test_ec2_links_instance_profile_and_counts_updates(
        self, connector: AWSConnector
    ) -> None:
        """An existing instance is updated and linked to its IAM instance profile."""
        profile_arn = f"arn:aws:iam::{ACCOUNT}:instance-profile/web"
        connector._entity_id_cache[profile_arn] = 400
        connector.elder_client.get_entity_by_external_id.return_value = {"id": 88}
        _service(connector, "ec2").describe_instances.return_value = {
            "Reservations": [
                {
                    "Instances": [
                        {
                            "InstanceId": "i-1",
                            "InstanceType": "t3.micro",
                            "State": {"Name": "running"},
                            "Placement": {"AvailabilityZone": "us-east-1a"},
                            "Tags": [{"Key": "Name", "Value": "web"}],
                            "SecurityGroups": [{"GroupName": "no-id"}],
                            "IamInstanceProfile": {"Arn": profile_arn},
                        }
                    ]
                }
            ]
        }
        assert await connector._sync_ec2_instances(REGION, ORG_ID) == (0, 1)
        connector.elder_client.get_or_create_dependency.assert_awaited_once_with(
            source_entity_id=88, target_entity_id=400, dependency_type="assumes_role"
        )

    @pytest.mark.asyncio
    async def test_rds_aurora_naming_and_update(self, connector: AWSConnector) -> None:
        """Aurora engines are flagged in the name; existing instances are updated."""
        connector.elder_client.get_entity_by_external_id.return_value = {"id": 90}
        _service(connector, "rds").describe_db_instances.return_value = {
            "DBInstances": [
                {
                    "DBInstanceIdentifier": "orders",
                    "Engine": "aurora-postgresql",
                    "DBInstanceStatus": "available",
                }
            ]
        }
        assert await connector._sync_rds_instances(REGION, ORG_ID) == (0, 1)
        ((entity_id, entity),) = updated_entities(connector.elder_client)
        assert (entity_id, entity.name) == (90, "RDS: orders (Aurora)")
        assert (entity.entity_type, entity.sub_type) == ("storage", "database")
        connector.elder_client.get_or_create_dependency.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_elasticache_clusters_mapped(self, connector: AWSConnector) -> None:
        """Cache clusters become storage entities; activity follows availability."""
        _service(connector, "elasticache").describe_cache_clusters.return_value = {
            "CacheClusters": [
                {
                    "CacheClusterId": "sessions",
                    "Engine": "redis",
                    "CacheClusterStatus": "available",
                },
                {"CacheClusterId": "warming", "CacheClusterStatus": "creating"},
            ]
        }
        connector.elder_client.get_entity_by_external_id.side_effect = [
            None,
            None,
            {"id": 5},
            {"id": 5},
        ]
        assert await connector._sync_elasticache_clusters(REGION, ORG_ID) == (1, 1)
        (sessions,) = created_entities(connector.elder_client)
        assert sessions.name == "ElastiCache: sessions"
        assert sessions.is_active is True
        assert sessions.tags == ["aws", "elasticache", "cache", "redis", REGION]
        ((_, warming),) = updated_entities(connector.elder_client)
        assert warming.is_active is False and "unknown" in warming.tags

    @pytest.mark.asyncio
    async def test_sqs_queue_attribute_failure_skips_only_that_queue(
        self, connector: AWSConnector
    ) -> None:
        """One queue's access-denied does not stop the other queues syncing."""
        sqs = _service(connector, "sqs")
        base = f"https://sqs.{REGION}.amazonaws.com/{ACCOUNT}"
        sqs.list_queues.return_value = {
            "QueueUrls": [f"{base}/denied", f"{base}/orders.fifo", f"{base}/plain"]
        }
        arn = f"arn:aws:sqs:{REGION}:{ACCOUNT}:orders.fifo"
        sqs.get_queue_attributes.side_effect = [
            _client_error("AccessDenied", "GetQueueAttributes"),
            {
                "Attributes": {
                    "QueueArn": arn,
                    "ReceiveMessageWaitTimeSeconds": "20",
                    "CreatedTimestamp": "1700000000",
                }
            },
            {},
        ]
        connector.elder_client.get_entity_by_external_id.side_effect = [
            None,
            None,
            {"id": 3},
            {"id": 3},
        ]
        assert await connector._sync_sqs_queues(REGION, ORG_ID) == (1, 1)
        (fifo,) = created_entities(connector.elder_client)
        assert (fifo.name, fifo.external_id) == ("SQS: orders.fifo", arn)
        assert fifo.attributes["is_fifo"] is True
        assert fifo.attributes["receive_wait_time"] == 20
        ((_, plain),) = updated_entities(connector.elder_client)
        assert plain.external_id == f"{base}/plain"
        assert plain.attributes["is_fifo"] is False
        connector.logger.warning.assert_called_once()

    @pytest.mark.asyncio
    async def test_lambda_exposes_env_keys_but_never_values(
        self, connector: AWSConnector
    ) -> None:
        """Environment variable names are synced; their values never leave AWS."""
        secret = fake_secret("lambda", "db", "password")
        role_arn = f"arn:aws:iam::{ACCOUNT}:role/fn"
        fn_arn = f"arn:aws:lambda:{REGION}:{ACCOUNT}:function:ingest"
        connector._entity_id_cache.update({"vpc-1": 1, "sg-1": 2, role_arn: 3})
        lam = _service(connector, "lambda")
        lam.get_paginator.return_value = _paginator(
            [
                {
                    "Functions": [
                        {
                            "FunctionArn": fn_arn,
                            "FunctionName": "ingest",
                            "Runtime": "python3.13",
                            "Role": role_arn,
                            "VpcConfig": {
                                "VpcId": "vpc-1",
                                "SubnetIds": ["subnet-1"],
                                "SecurityGroupIds": ["sg-1"],
                            },
                            "Environment": {"Variables": {"DB_PASSWORD": secret}},
                            "Layers": [{"Arn": "arn:aws:lambda:layer:otel:1"}],
                            "EphemeralStorage": {"Size": 1024},
                        }
                    ]
                },
                {"Functions": []},
            ]
        )
        assert await connector._sync_lambda_functions(REGION, ORG_ID) == (1, 0)
        lam.get_paginator.assert_called_once_with("list_functions")
        (fn,) = created_entities(connector.elder_client)
        assert (fn.name, fn.sub_type, fn.external_id) == (
            "Lambda: ingest",
            "serverless",
            fn_arn,
        )
        assert fn.attributes["environment_variable_keys"] == ["DB_PASSWORD"]
        assert fn.attributes["layers"] == ["arn:aws:lambda:layer:otel:1"]
        assert fn.attributes["ephemeral_storage_mb"] == 1024
        assert fn.attributes["vpc_config"]["subnet_ids"] == ["subnet-1"]
        assert_not_leaked(secret, fn, connector.logger.mock_calls)
        edge_types = [
            c.kwargs["dependency_type"]
            for c in connector.elder_client.get_or_create_dependency.call_args_list
        ]
        assert edge_types == ["in_vpc", "uses_sg", "assumes_role"]

    @pytest.mark.asyncio
    async def test_existing_lambda_counts_as_update(
        self, connector: AWSConnector
    ) -> None:
        """A function already in Elder is reported as updated with defaults applied."""
        connector.elder_client.get_entity_by_external_id.return_value = {"id": 9}
        _service(connector, "lambda").get_paginator.return_value = _paginator(
            [{"Functions": [{"FunctionArn": "arn:fn", "FunctionName": "bare"}]}]
        )
        assert await connector._sync_lambda_functions(REGION, ORG_ID) == (0, 1)
        ((_, fn),) = updated_entities(connector.elder_client)
        assert fn.description == f"AWS Lambda function in {REGION}"
        assert "environment_variable_keys" not in fn.attributes

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("method", "service", "call"),
        [
            ("_sync_vpcs", "ec2", "describe_vpcs"),
            ("_sync_ec2_instances", "ec2", "describe_instances"),
            ("_sync_rds_instances", "rds", "describe_db_instances"),
            ("_sync_elasticache_clusters", "elasticache", "describe_cache_clusters"),
            ("_sync_sqs_queues", "sqs", "list_queues"),
            ("_sync_lambda_functions", "lambda", "get_paginator"),
        ],
    )
    @pytest.mark.parametrize(
        "code", ["UnauthorizedOperation", "Throttling", "InternalFailure"]
    )
    async def test_client_errors_are_contained_per_service(
        self, connector: AWSConnector, method: str, service: str, call: str, code: str
    ) -> None:
        """Access-denied, throttling and 5xx errors log the region and write nothing."""
        getattr(_service(connector, service), call).side_effect = _client_error(code)
        assert await getattr(connector, method)(REGION, ORG_ID) == (0, 0)
        connector.logger.error.assert_called_once()
        assert connector.logger.error.call_args.kwargs["region"] == REGION
        connector.elder_client.create_entity.assert_not_awaited()

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("method", "service", "call", "payload"),
        [
            ("_sync_vpcs", "ec2", "describe_vpcs", {}),
            (
                "_sync_ec2_instances",
                "ec2",
                "describe_instances",
                {"Reservations": [{}]},
            ),
            ("_sync_rds_instances", "rds", "describe_db_instances", {}),
            (
                "_sync_elasticache_clusters",
                "elasticache",
                "describe_cache_clusters",
                {},
            ),
            ("_sync_sqs_queues", "sqs", "list_queues", {}),
        ],
    )
    async def test_empty_pages_write_nothing(
        self,
        connector: AWSConnector,
        method: str,
        service: str,
        call: str,
        payload: dict[str, Any],
    ) -> None:
        """Empty or partial provider pages yield no Elder writes."""
        getattr(_service(connector, service), call).return_value = payload
        assert await getattr(connector, method)(REGION, ORG_ID) == (0, 0)
        connector.elder_client.create_entity.assert_not_awaited()


class TestGlobalServices:
    """S3 and IAM."""

    @pytest.mark.asyncio
    async def test_s3_buckets_mapped_with_location_fallbacks(
        self, connector: AWSConnector
    ) -> None:
        """Bucket region comes from the location API, defaulting sensibly on gaps."""
        s3 = _service(connector, "s3")
        s3.list_buckets.return_value = {
            "Buckets": [
                {"Name": "eu-data", "CreationDate": datetime(2024, 1, 2, tzinfo=UTC)},
                {"Name": "classic"},
                {"Name": "locked"},
            ]
        }
        s3.get_bucket_location.side_effect = [
            {"LocationConstraint": "eu-west-1"},
            {"LocationConstraint": None},
            _client_error("AccessDenied", "GetBucketLocation"),
        ]
        connector.elder_client.get_entity_by_external_id.side_effect = [
            None,
            None,
            None,
            None,
            {"id": 4},
            {"id": 4},
        ]
        assert await connector._sync_s3_buckets() == (2, 1)
        got = by_name(created_entities(connector.elder_client))
        eu = got["S3: eu-data"]
        assert eu.external_id == "arn:aws:s3:::eu-data"
        assert eu.organization_id == 501
        assert eu.attributes["region"] == "eu-west-1"
        assert eu.attributes["creation_date"] == "2024-01-02T00:00:00+00:00"
        assert got["S3: classic"].attributes["region"] == "us-east-1"
        assert got["S3: classic"].attributes["creation_date"] is None
        ((_, locked),) = updated_entities(connector.elder_client)
        assert locked.attributes["region"] == "unknown"
        connector.logger.warning.assert_called_once()

    @pytest.mark.asyncio
    async def test_s3_listing_failure_is_contained(
        self, connector: AWSConnector
    ) -> None:
        """A failing bucket listing is logged and reports zero changes."""
        _service(connector, "s3").list_buckets.side_effect = _client_error(
            "AccessDenied"
        )
        assert await connector._sync_s3_buckets() == (0, 0)
        connector.logger.error.assert_called_once()

    @pytest.mark.asyncio
    async def test_iam_failures_are_isolated_between_users_and_roles(
        self, connector: AWSConnector
    ) -> None:
        """A denied user listing still lets roles sync, across multiple pages."""
        iam = _service(connector, "iam")
        role_pages = [
            {"Roles": [{"Arn": "arn:role/a", "RoleName": "a"}]},
            {"Roles": [{"Arn": "arn:role/b", "RoleName": "b"}]},
            {},
        ]
        iam.get_paginator.side_effect = lambda name: (
            _paginator(_client_error("AccessDenied", "ListUsers"))
            if name == "list_users"
            else _paginator(role_pages)
        )
        connector.elder_client.get_or_create_identity.side_effect = [
            {"id": 31},
            {},
        ]
        assert await connector._sync_iam_identities() == (1, 0)
        identities = [
            c.args[0]
            for c in connector.elder_client.get_or_create_identity.call_args_list
        ]
        assert [i.username for i in identities] == ["aws-role:a", "aws-role:b"]
        assert {i.identity_type for i in identities} == {"service_account"}
        assert connector._entity_id_cache == {"arn:role/a": 31}
        connector.logger.error.assert_called_once()

    @pytest.mark.asyncio
    async def test_iam_role_listing_failure_keeps_users(
        self, connector: AWSConnector
    ) -> None:
        """A denied role listing still reports the users that synced."""
        iam = _service(connector, "iam")
        iam.get_paginator.side_effect = lambda name: (
            _paginator([{"Users": [{"Arn": "arn:user/u", "UserName": "u"}]}])
            if name == "list_users"
            else _paginator(_client_error("Throttling", "ListRoles"))
        )
        connector.elder_client.get_or_create_identity.return_value = {"id": 8}
        assert await connector._sync_iam_identities() == (1, 0)
        connector.logger.error.assert_called_once()


class TestFullSync:
    """End-to-end orchestration."""

    @pytest.mark.asyncio
    async def test_sync_walks_every_region_under_the_aws_org(
        self, connector: AWSConnector, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Each configured region gets its own child org and its own VPC sync."""
        monkeypatch.setattr(settings, "aws_regions", "us-east-1, eu-west-1,")
        ids = iter([600, 601, 602])
        connector.elder_client.create_organization.side_effect = lambda _o: {
            "id": next(ids)
        }
        for service in ("iam", "s3"):
            client = _service(connector, service)
            client.get_paginator.return_value = _paginator([])
            client.list_buckets.return_value = {}
        for region, vpc in (("us-east-1", "vpc-use1"), ("eu-west-1", "vpc-euw1")):
            ec2 = _service(connector, "ec2", region)
            ec2.describe_vpcs.return_value = {"Vpcs": [{"VpcId": vpc}]}
            ec2.describe_instances.return_value = {}
            for service, call in (
                ("rds", "describe_db_instances"),
                ("elasticache", "describe_cache_clusters"),
                ("sqs", "list_queues"),
            ):
                getattr(_service(connector, service, region), call).return_value = {}
            _service(
                connector, "lambda", region
            ).get_paginator.return_value = _paginator([])

        result = await connector.sync()

        assert result.errors == []
        assert result.organizations_created == 3
        assert result.entities_created == 2
        orgs = [
            (c.args[0].name, c.args[0].parent_id)
            for c in connector.elder_client.create_organization.call_args_list
        ]
        assert orgs == [("AWS", None), ("AWS us-east-1", 600), ("AWS eu-west-1", 600)]
        placement = {
            e.external_id: e.organization_id
            for e in created_entities(connector.elder_client)
        }
        assert placement == {"vpc-use1": 601, "vpc-euw1": 602}

    @pytest.mark.asyncio
    async def test_sync_reports_failure_and_resets_cache(
        self, connector: AWSConnector
    ) -> None:
        """A failed sync is reported in errors and starts from a clean id cache."""
        connector._entity_id_cache["stale"] = 1
        connector.elder_client.list_organizations.side_effect = RuntimeError("down")
        result = await connector.sync()
        assert result.errors == ["AWS sync failed: down"]
        assert connector._entity_id_cache == {}
