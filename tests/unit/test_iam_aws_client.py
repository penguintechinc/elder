"""Unit tests for the AWS IAM provider client.

boto3 is mocked (``boto3.client`` returns a MagicMock), so no AWS credentials
or network are required. Covers user/role list/get/create/delete, the
normalize mapping, tag handling, connectivity test, the aggregate sync, and the
ClientError → Exception translation.
"""

import json
from unittest.mock import MagicMock, patch

import pytest

pytest.importorskip("boto3")
from botocore.exceptions import ClientError  # noqa: E402

from apps.api.services.iam.aws_client import AWSIAMClient  # noqa: E402

MODULE = "apps.api.services.iam.aws_client"


def _make_client(config=None):
    cfg = {"region": "us-east-1"}
    if config:
        cfg.update(config)
    fake_boto_client = MagicMock()
    with patch(f"{MODULE}.boto3") as mock_boto3:
        mock_boto3.client.return_value = fake_boto_client
        client = AWSIAMClient(cfg)
    return client, fake_boto_client


def _client_error(message="denied", code="AccessDenied", op="Op"):
    return ClientError({"Error": {"Message": message, "Code": code}}, op)


class TestInit:
    def test_passes_credentials_to_boto(self):
        cfg = {
            "region": "eu-west-1",
            "access_key_id": "AKIA",
            "secret_access_key": "secret",
            "session_token": "tok",
            "endpoint_url": "http://localstack:4566",
        }
        with patch(f"{MODULE}.boto3") as mock_boto3:
            mock_boto3.client.return_value = MagicMock()
            AWSIAMClient(cfg)
        _, kwargs = mock_boto3.client.call_args
        assert kwargs["region_name"] == "eu-west-1"
        assert kwargs["aws_access_key_id"] == "AKIA"
        assert kwargs["aws_session_token"] == "tok"
        assert kwargs["endpoint_url"] == "http://localstack:4566"


class TestUsers:
    def test_list_users_normalizes(self):
        client, boto = _make_client()
        boto.list_users.return_value = {
            "Users": [
                {"UserName": "alice", "UserId": "AID1", "Arn": "arn:alice"},
                {"UserName": "bob", "UserId": "AID2", "Arn": "arn:bob"},
            ],
            "IsTruncated": True,
            "Marker": "next",
        }
        result = client.list_users(limit=10)
        assert result["truncated"] is True
        assert result["next_token"] == "next"
        assert result["users"][0]["username"] == "alice"
        assert result["users"][0]["id"] == "AID1"
        # MaxItems capped and passed.
        assert boto.list_users.call_args.kwargs["MaxItems"] == 10

    def test_get_user_with_tags(self):
        client, boto = _make_client()
        boto.get_user.return_value = {
            "User": {"UserName": "alice", "UserId": "AID1", "Arn": "arn:alice"}
        }
        boto.list_user_tags.return_value = {"Tags": [{"Key": "team", "Value": "sec"}]}
        user = client.get_user("alice")
        assert user["username"] == "alice"
        assert user["tags"] == {"team": "sec"}

    def test_get_user_tag_failure_defaults_empty(self):
        client, boto = _make_client()
        boto.get_user.return_value = {"User": {"UserName": "a", "UserId": "1"}}
        boto.list_user_tags.side_effect = RuntimeError("no perms")
        user = client.get_user("a")
        assert user["tags"] == {}

    def test_create_user_with_tags(self):
        client, boto = _make_client()
        boto.create_user.return_value = {
            "User": {"UserName": "carol", "UserId": "AID9", "Arn": "arn:carol"}
        }
        user = client.create_user("carol", tags={"env": "prod"}, path="/eng/")
        assert user["username"] == "carol"
        assert user["tags"] == {"env": "prod"}
        boto.tag_user.assert_called_once()
        assert boto.create_user.call_args.kwargs["Path"] == "/eng/"

    def test_delete_user_cleans_up_dependencies(self):
        client, boto = _make_client()
        boto.list_access_keys.return_value = {
            "AccessKeyMetadata": [{"AccessKeyId": "AKIA1"}]
        }
        boto.list_attached_user_policies.return_value = {
            "AttachedPolicies": [{"PolicyArn": "arn:pol"}]
        }
        boto.list_user_policies.return_value = {"PolicyNames": ["inline1"]}
        boto.list_groups_for_user.return_value = {"Groups": [{"GroupName": "g1"}]}
        result = client.delete_user("alice")
        assert "deleted successfully" in result["message"]
        boto.delete_access_key.assert_called_once()
        boto.detach_user_policy.assert_called_once()
        boto.delete_user_policy.assert_called_once()
        boto.remove_user_from_group.assert_called_once()
        boto.delete_user.assert_called_once()

    def test_list_users_client_error_wrapped(self):
        client, boto = _make_client()
        boto.list_users.side_effect = _client_error("list boom", op="ListUsers")
        with pytest.raises(Exception, match="list boom"):
            client.list_users()


class TestRoles:
    def test_list_roles_normalizes(self):
        client, boto = _make_client()
        boto.list_roles.return_value = {
            "Roles": [{"RoleName": "r1", "RoleId": "RID1", "Arn": "arn:r1"}]
        }
        result = client.list_roles()
        assert result["roles"][0]["name"] == "r1"
        assert result["roles"][0]["id"] == "RID1"

    def test_get_role_includes_assume_policy(self):
        client, boto = _make_client()
        policy_doc = {"Version": "2012-10-17", "Statement": []}
        boto.get_role.return_value = {
            "Role": {
                "RoleName": "r1",
                "RoleId": "RID1",
                "AssumeRolePolicyDocument": policy_doc,
            }
        }
        boto.list_role_tags.return_value = {"Tags": []}
        role = client.get_role("r1")
        assert role["name"] == "r1"
        assert role["assume_role_policy"] == policy_doc

    def test_create_role_uses_default_trust_policy(self):
        client, boto = _make_client()
        boto.create_role.return_value = {"Role": {"RoleName": "r1", "RoleId": "RID1"}}
        client.create_role("r1")
        sent = boto.create_role.call_args.kwargs
        trust = json.loads(sent["AssumeRolePolicyDocument"])
        assert trust["Statement"][0]["Action"] == "sts:AssumeRole"

    def test_create_role_custom_trust_and_tags(self):
        client, boto = _make_client()
        boto.create_role.return_value = {"Role": {"RoleName": "r2", "RoleId": "RID2"}}
        custom = {"Version": "2012-10-17", "Statement": [{"Effect": "Allow"}]}
        role = client.create_role(
            "r2", description="d", trust_policy=custom, tags={"a": "b"}
        )
        assert role["tags"] == {"a": "b"}
        sent = boto.create_role.call_args.kwargs
        assert sent["Description"] == "d"
        assert sent["Tags"] == [{"Key": "a", "Value": "b"}]

    def test_delete_role_cleans_dependencies(self):
        client, boto = _make_client()
        boto.list_attached_role_policies.return_value = {
            "AttachedPolicies": [{"PolicyArn": "arn:pol"}]
        }
        boto.list_role_policies.return_value = {"PolicyNames": ["inline"]}
        boto.list_instance_profiles_for_role.return_value = {
            "InstanceProfiles": [{"InstanceProfileName": "ip1"}]
        }
        result = client.delete_role("r1")
        assert "deleted successfully" in result["message"]
        boto.detach_role_policy.assert_called_once()
        boto.delete_role_policy.assert_called_once()
        boto.remove_role_from_instance_profile.assert_called_once()
        boto.delete_role.assert_called_once()


class TestConnectionAndSync:
    def test_test_connection_success(self):
        client, boto = _make_client()
        boto.list_users.return_value = {"Users": []}
        assert client.test_connection() is True

    def test_test_connection_failure(self):
        client, boto = _make_client()
        boto.list_users.side_effect = RuntimeError("no creds")
        assert client.test_connection() is False

    def test_sync_from_provider_counts(self):
        client, boto = _make_client()
        boto.list_users.return_value = {"Users": [{"UserName": "a", "UserId": "1"}]}
        boto.list_roles.return_value = {"Roles": [{"RoleName": "r", "RoleId": "2"}]}
        boto.list_policies.return_value = {
            "Policies": [{"PolicyName": "p", "PolicyId": "3"}]
        }
        result = client.sync_from_provider()
        assert result["users_synced"] == 1
        assert result["roles_synced"] == 1
        assert result["errors"] == []

    def test_sync_collects_errors(self):
        client, boto = _make_client()
        boto.list_users.side_effect = RuntimeError("users down")
        boto.list_roles.return_value = {"Roles": []}
        boto.list_policies.return_value = {"Policies": []}
        result = client.sync_from_provider()
        assert any("Error syncing users" in e for e in result["errors"])
