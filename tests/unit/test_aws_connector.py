"""
Unit tests for AWS connector bugs.

regression: gh-109 — dedup used attributes key (renamed to metadata in v3.2.0),
            causing every entity to be re-created on each sync.
regression: gh-110 — no dependencies were ever written for AWS resources.
regression: aws-identities — IAM users/roles were never synced as Elder identities.
"""

import pytest
import tests.unit.conftest_worker_stubs  # noqa: F401 — stubs heavy optional deps before any connector import

from unittest.mock import AsyncMock, MagicMock, patch

from apps.worker.connectors.aws_connector import AWSConnector
from apps.worker.utils.elder_client import Entity


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_vpc(vpc_id: str = "vpc-abc123") -> dict:
    return {"VpcId": vpc_id, "CidrBlock": "10.0.0.0/16", "State": "available", "Tags": [], "IsDefault": False}


def _make_instance(instance_id: str = "i-abc123", vpc_id: str = "vpc-abc123", subnet_id: str = "subnet-abc") -> dict:
    return {
        "InstanceId": instance_id,
        "InstanceType": "t3.micro",
        "State": {"Name": "running"},
        "PrivateIpAddress": "10.0.0.1",
        "VpcId": vpc_id,
        "SubnetId": subnet_id,
        "SecurityGroups": [{"GroupId": "sg-abc", "GroupName": "default"}],
        "Placement": {"AvailabilityZone": "us-east-1a"},
        "Tags": [],
        "LaunchTime": None,
    }


# ---------------------------------------------------------------------------
# regression: gh-109 — dedup reads metadata, not attributes
# ---------------------------------------------------------------------------


class TestDedupUsesMetadata:
    """
    Regression: gh-109
    The old connector read item.get("attributes", {}) for dedup.
    The API has returned `metadata` since v3.2.0; attributes is always {}.
    As a result every sync re-created all entities.
    """

    @pytest.fixture
    def connector(self):
        c = AWSConnector()
        c.elder_client = AsyncMock()
        c.organization_cache = {"root:AWS": 1, "1:AWS us-east-1": 2}
        return c

    @pytest.mark.asyncio
    async def test_existing_entity_returned_via_external_id(self, connector):
        """get_entity_by_external_id returning a result means update, not create."""
        existing = {"id": 42, "name": "VPC: vpc-abc123", "metadata": {"vpc_id": "vpc-abc123"}}
        connector.elder_client.get_entity_by_external_id = AsyncMock(return_value=existing)
        connector.elder_client.update_entity = AsyncMock(return_value=existing)
        connector.elder_client.create_entity = AsyncMock()

        entity_id = await connector._upsert_entity(Entity(
            name="VPC: vpc-abc123",
            entity_type="vpc",
            organization_id=2,
            external_id="vpc-abc123",
            attributes={"vpc_id": "vpc-abc123"},
        ))

        connector.elder_client.update_entity.assert_awaited_once_with(42, connector.elder_client.update_entity.call_args[0][1])
        connector.elder_client.create_entity.assert_not_awaited()
        assert entity_id == 42

    @pytest.mark.asyncio
    async def test_missing_entity_triggers_create(self, connector):
        """When get_entity_by_external_id returns None, create_entity is called."""
        connector.elder_client.get_entity_by_external_id = AsyncMock(return_value=None)
        connector.elder_client.create_entity = AsyncMock(return_value={"id": 99})
        connector.elder_client.update_entity = AsyncMock()

        entity_id = await connector._upsert_entity(Entity(
            name="VPC: vpc-new",
            entity_type="vpc",
            organization_id=2,
            external_id="vpc-new",
            attributes={"vpc_id": "vpc-new"},
        ))

        connector.elder_client.create_entity.assert_awaited_once()
        connector.elder_client.update_entity.assert_not_awaited()
        assert entity_id == 99

    @pytest.mark.asyncio
    async def test_second_sync_updates_not_creates(self, connector):
        """
        Simulates two sync passes on the same VPC.
        After the first pass the entity exists; the second pass must update it,
        not create a duplicate. This is the core gh-109 regression scenario.
        """
        existing = {"id": 77, "name": "VPC: vpc-abc123", "metadata": {"vpc_id": "vpc-abc123"}}

        # Pass 1: entity does not exist yet
        connector.elder_client.get_entity_by_external_id = AsyncMock(return_value=None)
        connector.elder_client.create_entity = AsyncMock(return_value={"id": 77})
        connector.elder_client.update_entity = AsyncMock(return_value=existing)

        await connector._upsert_entity(Entity(
            name="VPC: vpc-abc123", entity_type="vpc", organization_id=2,
            external_id="vpc-abc123", attributes={"vpc_id": "vpc-abc123"},
        ))
        assert connector.elder_client.create_entity.await_count == 1
        assert connector.elder_client.update_entity.await_count == 0

        # Pass 2: entity now exists (simulate next sync cycle)
        connector.elder_client.get_entity_by_external_id = AsyncMock(return_value=existing)
        connector.elder_client.create_entity.reset_mock()
        connector.elder_client.update_entity.reset_mock()

        await connector._upsert_entity(Entity(
            name="VPC: vpc-abc123", entity_type="vpc", organization_id=2,
            external_id="vpc-abc123", attributes={"vpc_id": "vpc-abc123"},
        ))
        assert connector.elder_client.create_entity.await_count == 0
        assert connector.elder_client.update_entity.await_count == 1


# ---------------------------------------------------------------------------
# regression: gh-110 — EC2/Lambda/RDS must produce dependency rows
# ---------------------------------------------------------------------------


class TestDependenciesCreated:
    """
    Regression: gh-110
    AWS connector never called create_dependency / get_or_create_dependency.
    After this fix, EC2, Lambda, and RDS create the correct relationships.
    """

    @pytest.fixture
    def connector(self):
        c = AWSConnector()
        c.elder_client = AsyncMock()
        c.organization_cache = {"root:AWS": 1, "1:AWS us-east-1": 2}
        c._entity_id_cache = {"vpc-abc123": 10, "subnet-abc": 11, "sg-abc": 12}
        return c

    @pytest.mark.asyncio
    async def test_ec2_links_vpc_subnet_sg(self, connector):
        """EC2 instance must produce in_vpc, in_subnet, and uses_sg dependencies."""
        instance = _make_instance()
        connector.elder_client.get_entity_by_external_id = AsyncMock(return_value=None)
        connector.elder_client.create_entity = AsyncMock(return_value={"id": 20})
        connector.elder_client.get_or_create_dependency = AsyncMock(return_value={})

        with patch("apps.worker.connectors.aws_connector.boto3") as mock_boto3:
            mock_ec2 = MagicMock()
            mock_ec2.describe_instances.return_value = {
                "Reservations": [{"Instances": [instance]}]
            }
            mock_boto3.client.return_value = mock_ec2
            connector.aws_clients["ec2:us-east-1"] = mock_ec2

            await connector._sync_ec2_instances("us-east-1", 2)

        dep_calls = [
            call.args[1:]  # skip source_entity_id, extract (target_id, dep_type)
            for call in connector.elder_client.get_or_create_dependency.call_args_list
        ]
        dep_types = {kwargs["dependency_type"] for call in connector.elder_client.get_or_create_dependency.call_args_list for kwargs in [call.kwargs]}
        assert "in_vpc" in dep_types, "EC2 must create in_vpc dependency"
        assert "in_subnet" in dep_types, "EC2 must create in_subnet dependency"
        assert "uses_sg" in dep_types, "EC2 must create uses_sg dependency"

    @pytest.mark.asyncio
    async def test_rds_links_vpc(self, connector):
        """RDS instance must produce an in_vpc dependency."""
        db_instance = {
            "DBInstanceIdentifier": "mydb",
            "Engine": "mysql",
            "DBInstanceStatus": "available",
            "DBSubnetGroup": {"VpcId": "vpc-abc123"},
        }
        connector.elder_client.get_entity_by_external_id = AsyncMock(return_value=None)
        connector.elder_client.create_entity = AsyncMock(return_value={"id": 30})
        connector.elder_client.get_or_create_dependency = AsyncMock(return_value={})

        with patch("apps.worker.connectors.aws_connector.boto3") as mock_boto3:
            mock_rds = MagicMock()
            mock_rds.describe_db_instances.return_value = {"DBInstances": [db_instance]}
            connector.aws_clients["rds:us-east-1"] = mock_rds

            await connector._sync_rds_instances("us-east-1", 2)

        dep_types = {
            call.kwargs.get("dependency_type") or call.args[2]
            for call in connector.elder_client.get_or_create_dependency.call_args_list
        }
        assert "in_vpc" in dep_types, "RDS must create in_vpc dependency"

    @pytest.mark.asyncio
    async def test_lambda_links_vpc_and_role(self, connector):
        """Lambda function must produce in_vpc and assumes_role dependencies."""
        func = {
            "FunctionArn": "arn:aws:lambda:us-east-1:123456789012:function:my-fn",
            "FunctionName": "my-fn",
            "State": "Active",
            "VpcConfig": {
                "VpcId": "vpc-abc123",
                "SubnetIds": [],
                "SecurityGroupIds": [],
            },
            "Role": "arn:aws:iam::123456789012:role/my-role",
        }
        connector.elder_client.get_entity_by_external_id = AsyncMock(return_value=None)
        connector.elder_client.create_entity = AsyncMock(return_value={"id": 40})
        connector.elder_client.get_or_create_dependency = AsyncMock(return_value={})
        # Pre-seed role ARN → identity id (normally from IAM sync)
        connector._entity_id_cache["arn:aws:iam::123456789012:role/my-role"] = 99

        with patch("apps.worker.connectors.aws_connector.boto3") as mock_boto3:
            mock_lambda = MagicMock()
            pages = [{"Functions": [func]}]
            mock_paginator = MagicMock()
            mock_paginator.paginate.return_value = iter(pages)
            mock_lambda.get_paginator.return_value = mock_paginator
            connector.aws_clients["lambda:us-east-1"] = mock_lambda

            await connector._sync_lambda_functions("us-east-1", 2)

        dep_types = {
            call.kwargs.get("dependency_type") or call.args[2]
            for call in connector.elder_client.get_or_create_dependency.call_args_list
        }
        assert "in_vpc" in dep_types, "Lambda must create in_vpc dependency"
        assert "assumes_role" in dep_types, "Lambda must create assumes_role dependency"


# ---------------------------------------------------------------------------
# regression: aws-identities — IAM users/roles must be synced
# ---------------------------------------------------------------------------


class TestIAMIdentitiesSync:
    """
    Regression: AWS identities were never pulled into Elder.
    _sync_iam_identities must iterate IAM users and roles and call
    get_or_create_identity for each.
    """

    @pytest.fixture
    def connector(self):
        c = AWSConnector()
        c.elder_client = AsyncMock()
        return c

    @pytest.mark.asyncio
    async def test_iam_users_synced_as_identities(self, connector):
        """IAM users must be synced as service_account identities with provider=aws."""
        users = [
            {"Arn": "arn:aws:iam::123:user/alice", "UserName": "alice"},
            {"Arn": "arn:aws:iam::123:user/bob", "UserName": "bob"},
        ]
        connector.elder_client.get_or_create_identity = AsyncMock(return_value={"id": 1})

        mock_iam = MagicMock()
        user_paginator = MagicMock()
        user_paginator.paginate.return_value = iter([{"Users": users}])
        role_paginator = MagicMock()
        role_paginator.paginate.return_value = iter([{"Roles": []}])
        mock_iam.get_paginator.side_effect = lambda name: (
            user_paginator if name == "list_users" else role_paginator
        )
        # Pre-seed IAM client so _get_aws_client cache-hits and skips boto3.client()
        connector.aws_clients["iam:us-east-1"] = mock_iam
        created, _ = await connector._sync_iam_identities()

        assert connector.elder_client.get_or_create_identity.await_count == 2
        call_args = connector.elder_client.get_or_create_identity.call_args_list
        providers = {c.args[0].auth_provider for c in call_args}
        assert providers == {"aws"}, "All IAM identities must have auth_provider='aws'"

    @pytest.mark.asyncio
    async def test_iam_roles_synced_and_cached(self, connector):
        """IAM roles must be synced and their ARNs stored in the entity_id_cache."""
        role_arn = "arn:aws:iam::123:role/my-role"
        roles = [{"Arn": role_arn, "RoleName": "my-role"}]
        connector.elder_client.get_or_create_identity = AsyncMock(return_value={"id": 55})

        mock_iam = MagicMock()
        user_paginator = MagicMock()
        user_paginator.paginate.return_value = iter([{"Users": []}])
        role_paginator = MagicMock()
        role_paginator.paginate.return_value = iter([{"Roles": roles}])
        mock_iam.get_paginator.side_effect = lambda name: (
            user_paginator if name == "list_users" else role_paginator
        )
        connector.aws_clients["iam:us-east-1"] = mock_iam
        await connector._sync_iam_identities()

        assert role_arn in connector._entity_id_cache, (
            "Role ARN must be in entity_id_cache so Lambda/EC2 can wire assumes_role deps"
        )
        assert connector._entity_id_cache[role_arn] == 55
