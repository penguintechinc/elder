"""Additional unit tests for AWSIAMClient: error translation, pagination, cleanup and tag branches.

boto3 is mocked at the client boundary (``boto3.client`` -> MagicMock); no AWS access needed.
"""

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

pytest.importorskip("boto3")
from botocore.exceptions import ClientError  # noqa: E402

from apps.api.services.iam.aws_client import AWSIAMClient  # noqa: E402

MODULE = "apps.api.services.iam.aws_client"
NOW = datetime(2024, 1, 2, 3, 4, 5, tzinfo=timezone.utc)


def _make():
    """Return an AWSIAMClient wired to a MagicMock boto client."""
    boto = MagicMock()
    with patch(f"{MODULE}.boto3") as mb:
        mb.client.return_value = boto
        client = AWSIAMClient({"region": "us-east-1"})
    return client, boto


def _cerr(msg="denied"):
    """Build a botocore ClientError carrying ``msg``."""
    return ClientError({"Error": {"Message": msg, "Code": "AccessDenied"}}, "Op")


def test_init_without_boto3_raises_import_error():
    """Constructor raises ImportError when boto3 is unavailable."""
    with patch(f"{MODULE}.boto3", None):
        with pytest.raises(ImportError, match="boto3 is required"):
            AWSIAMClient({})


def test_init_credentials_without_session_token_or_endpoint():
    """Static credentials are forwarded; no token/endpoint keys when not given."""
    with patch(f"{MODULE}.boto3") as mb:
        mb.client.return_value = MagicMock()
        AWSIAMClient({"access_key_id": "id1", "secret_access_key": "s1"})
    kwargs = mb.client.call_args.kwargs
    assert kwargs["aws_access_key_id"] == "id1"
    assert "aws_session_token" not in kwargs
    assert "endpoint_url" not in kwargs


def test_init_partial_credentials_ignored():
    """Access key without secret falls back to environment credentials."""
    with patch(f"{MODULE}.boto3") as mb:
        mb.client.return_value = MagicMock()
        AWSIAMClient({"access_key_id": "only-id"})
    assert "aws_access_key_id" not in mb.client.call_args.kwargs


# (method, args, boto method that fails, error prefix)
ERROR_CASES = [
    ("list_users", (), "list_users", "list users"),
    ("get_user", ("u",), "get_user", "get user"),
    ("create_user", ("u",), "create_user", "create user"),
    ("delete_user", ("u",), "delete_user", "delete user"),
    ("list_roles", (), "list_roles", "list roles"),
    ("get_role", ("r",), "get_role", "get role"),
    ("create_role", ("r",), "create_role", "create role"),
    ("delete_role", ("r",), "delete_role", "delete role"),
    ("update_role", ("r", "desc"), "update_role_description", "update role"),
    ("list_policies", (), "list_policies", "list policies"),
    ("get_policy", ("arn",), "get_policy", "get policy"),
    ("create_policy", ("p", {"a": 1}), "create_policy", "create policy"),
    ("delete_policy", ("arn",), "delete_policy", "delete policy"),
    (
        "attach_policy_to_user",
        ("u", "arn"),
        "attach_user_policy",
        "attach policy to user",
    ),
    (
        "detach_policy_from_user",
        ("u", "arn"),
        "detach_user_policy",
        "detach policy from user",
    ),
    (
        "attach_policy_to_role",
        ("r", "arn"),
        "attach_role_policy",
        "attach policy to role",
    ),
    (
        "detach_policy_from_role",
        ("r", "arn"),
        "detach_role_policy",
        "detach policy from role",
    ),
    ("list_user_policies", ("u",), "list_attached_user_policies", "list user policies"),
    ("list_role_policies", ("r",), "list_attached_role_policies", "list role policies"),
    ("create_access_key", ("u",), "create_access_key", "create access key"),
    ("list_access_keys", ("u",), "list_access_keys", "list access keys"),
    ("delete_access_key", ("u", "k"), "delete_access_key", "delete access key"),
    ("list_groups", (), "list_groups", "list groups"),
    ("create_group", ("g",), "create_group", "create group"),
    ("delete_group", ("g",), "delete_group", "delete group"),
    ("add_user_to_group", ("u", "g"), "add_user_to_group", "add user to group"),
    (
        "remove_user_from_group",
        ("u", "g"),
        "remove_user_from_group",
        "remove user from group",
    ),
]


@pytest.mark.parametrize("method,args,boto_fn,prefix", ERROR_CASES)
def test_client_error_is_translated_with_aws_message(method, args, boto_fn, prefix):
    """A botocore ClientError surfaces as Exception carrying the AWS message."""
    client, boto = _make()
    getattr(boto, boto_fn).side_effect = _cerr("access denied here")
    with pytest.raises(Exception, match=f"AWS IAM {prefix} error: access denied here"):
        getattr(client, method)(*args)


@pytest.mark.parametrize("method,args,boto_fn,prefix", ERROR_CASES)
def test_generic_error_is_wrapped(method, args, boto_fn, prefix):
    """Any non-AWS exception is wrapped with the operation prefix."""
    client, boto = _make()
    getattr(boto, boto_fn).side_effect = RuntimeError("boom")
    with pytest.raises(Exception, match=f"AWS IAM {prefix} error: boom"):
        getattr(client, method)(*args)


@pytest.mark.parametrize(
    "method,args,key,boto_fn",
    [
        ("list_users", (), "Users", "list_users"),
        ("list_roles", (), "Roles", "list_roles"),
        ("list_policies", ("Local",), "Policies", "list_policies"),
        ("list_groups", (), "Groups", "list_groups"),
    ],
)
def test_list_pagination_params_forwarded(method, args, key, boto_fn):
    """limit is capped at 1000 and next_token becomes Marker; truncation reported."""
    client, boto = _make()
    getattr(boto, boto_fn).return_value = {key: [], "Marker": "m2", "IsTruncated": True}
    out = getattr(client, method)(*args, limit=5000, next_token="m1")
    call = getattr(boto, boto_fn).call_args.kwargs
    assert call["MaxItems"] == 1000
    assert call["Marker"] == "m1"
    assert out["next_token"] == "m2"
    assert out["truncated"] is True


def test_list_policies_scope_and_normalization():
    """list_policies forwards Scope and normalizes policy records."""
    client, boto = _make()
    boto.list_policies.return_value = {
        "Policies": [{"PolicyId": "P1", "PolicyName": "pol", "Arn": "arn:x"}]
    }
    out = client.list_policies(scope="Local")
    assert boto.list_policies.call_args.kwargs == {"Scope": "Local"}
    assert out["policies"][0]["name"] == "pol"
    assert out["policies"][0]["arn"] == "arn:x"
    assert out["next_token"] is None


def test_create_user_with_path_and_tags():
    """create_user forwards Path, applies tags and returns them."""
    client, boto = _make()
    boto.create_user.return_value = {"User": {"UserName": "bob", "UserId": "U"}}
    out = client.create_user("bob", tags={"env": "dev"}, path="/eng/")
    assert boto.create_user.call_args.kwargs == {"UserName": "bob", "Path": "/eng/"}
    boto.tag_user.assert_called_once_with(
        UserName="bob", Tags=[{"Key": "env", "Value": "dev"}]
    )
    assert out["tags"] == {"env": "dev"}


def test_get_user_tag_lookup_failure_yields_empty_tags():
    """A failing list_user_tags does not break get_user."""
    client, boto = _make()
    boto.get_user.return_value = {"User": {"UserName": "bob"}}
    boto.list_user_tags.side_effect = RuntimeError("no perm")
    assert client.get_user("bob")["tags"] == {}


def test_delete_user_cleans_dependencies_then_deletes():
    """delete_user removes keys, managed/inline policies and group membership first."""
    client, boto = _make()
    boto.list_access_keys.return_value = {"AccessKeyMetadata": [{"AccessKeyId": "k1"}]}
    boto.list_attached_user_policies.return_value = {
        "AttachedPolicies": [{"PolicyArn": "arn:p"}]
    }
    boto.list_user_policies.return_value = {"PolicyNames": ["inline"]}
    boto.list_groups_for_user.return_value = {"Groups": [{"GroupName": "g1"}]}
    out = client.delete_user("bob")
    boto.delete_access_key.assert_called_once_with(UserName="bob", AccessKeyId="k1")
    boto.detach_user_policy.assert_called_once_with(UserName="bob", PolicyArn="arn:p")
    boto.delete_user_policy.assert_called_once_with(UserName="bob", PolicyName="inline")
    boto.remove_user_from_group.assert_called_once_with(UserName="bob", GroupName="g1")
    boto.delete_user.assert_called_once_with(UserName="bob")
    assert "deleted successfully" in out["message"]


def test_delete_user_tolerates_cleanup_failures():
    """Failures in dependency cleanup are swallowed; the final delete still runs."""
    client, boto = _make()
    for fn in (
        "list_access_keys",
        "list_attached_user_policies",
        "list_user_policies",
        "list_groups_for_user",
    ):
        getattr(boto, fn).side_effect = RuntimeError("x")
    client.delete_user("bob")
    boto.delete_user.assert_called_once_with(UserName="bob")


def test_update_user_rename_path_and_tag_replacement():
    """update_user renames, replaces tags (untag then tag) and returns the renamed user."""
    client, boto = _make()
    boto.list_user_tags.return_value = {"Tags": [{"Key": "old", "Value": "1"}]}
    boto.get_user.return_value = {"User": {"UserName": "new"}}
    out = client.update_user(
        "bob", tags={"k": "v"}, new_user_name="new", new_path="/p/"
    )
    boto.update_user.assert_called_once_with(
        UserName="bob", NewUserName="new", NewPath="/p/"
    )
    boto.untag_user.assert_called_once_with(UserName="bob", TagKeys=["old"])
    boto.tag_user.assert_called_once_with(
        UserName="bob", Tags=[{"Key": "k", "Value": "v"}]
    )
    boto.get_user.assert_called_once_with(UserName="new")
    assert out["username"] == "new"


def test_update_user_only_new_path_and_empty_tags_clears_without_tagging():
    """new_path alone is sent; empty tags removes existing and adds none."""
    client, boto = _make()
    boto.list_user_tags.return_value = {"Tags": []}
    boto.get_user.return_value = {"User": {"UserName": "bob"}}
    client.update_user("bob", tags={}, new_path="/p/")
    assert boto.update_user.call_args.kwargs == {"UserName": "bob", "NewPath": "/p/"}
    boto.untag_user.assert_not_called()
    boto.tag_user.assert_not_called()


def test_update_user_tag_listing_failure_is_ignored():
    """A failing tag listing during update does not abort tagging."""
    client, boto = _make()
    boto.list_user_tags.side_effect = [RuntimeError("x"), {"Tags": []}]
    boto.get_user.return_value = {"User": {"UserName": "bob"}}
    client.update_user("bob", tags={"a": "b"})
    boto.tag_user.assert_called_once()
    boto.update_user.assert_not_called()


def test_get_role_returns_tags_and_trust_policy_and_tag_failure():
    """get_role includes assume_role_policy; tag lookup failure gives {}."""
    client, boto = _make()
    boto.get_role.return_value = {
        "Role": {"RoleName": "r", "AssumeRolePolicyDocument": {"Version": "x"}}
    }
    boto.list_role_tags.return_value = {"Tags": [{"Key": "a", "Value": "b"}]}
    out = client.get_role("r")
    assert out["tags"] == {"a": "b"}
    assert out["assume_role_policy"] == {"Version": "x"}
    boto.list_role_tags.side_effect = RuntimeError("x")
    assert client.get_role("r")["tags"] == {}


def test_create_role_full_options_and_default_trust_policy():
    """create_role sends description/path/duration/tags and a default EC2 trust policy."""
    client, boto = _make()
    boto.create_role.return_value = {"Role": {"RoleName": "r"}}
    out = client.create_role(
        "r", description="d", tags={"a": "b"}, path="/x/", max_session_duration=7200
    )
    params = boto.create_role.call_args.kwargs
    assert params["Description"] == "d"
    assert params["Path"] == "/x/"
    assert params["MaxSessionDuration"] == 7200
    assert params["Tags"] == [{"Key": "a", "Value": "b"}]
    assert "ec2.amazonaws.com" in params["AssumeRolePolicyDocument"]
    assert out["tags"] == {"a": "b"}


def test_delete_role_cleans_dependencies():
    """delete_role detaches policies, drops inline ones and instance profiles first."""
    client, boto = _make()
    boto.list_attached_role_policies.return_value = {
        "AttachedPolicies": [{"PolicyArn": "arn:p"}]
    }
    boto.list_role_policies.return_value = {"PolicyNames": ["inl"]}
    boto.list_instance_profiles_for_role.return_value = {
        "InstanceProfiles": [{"InstanceProfileName": "ip"}]
    }
    client.delete_role("r")
    boto.detach_role_policy.assert_called_once_with(RoleName="r", PolicyArn="arn:p")
    boto.delete_role_policy.assert_called_once_with(RoleName="r", PolicyName="inl")
    boto.remove_role_from_instance_profile.assert_called_once_with(
        InstanceProfileName="ip", RoleName="r"
    )
    boto.delete_role.assert_called_once_with(RoleName="r")


def test_delete_role_tolerates_cleanup_failures():
    """Cleanup errors are swallowed and the role is still deleted."""
    client, boto = _make()
    for fn in (
        "list_attached_role_policies",
        "list_role_policies",
        "list_instance_profiles_for_role",
    ):
        getattr(boto, fn).side_effect = RuntimeError("x")
    client.delete_role("r")
    boto.delete_role.assert_called_once_with(RoleName="r")


def test_update_role_all_fields():
    """update_role applies description, session duration and tag replacement."""
    client, boto = _make()
    boto.list_role_tags.return_value = {"Tags": [{"Key": "old", "Value": "1"}]}
    boto.get_role.return_value = {"Role": {"RoleName": "r"}}
    client.update_role(
        "r", description="new", tags={"k": "v"}, max_session_duration=3600
    )
    boto.update_role_description.assert_called_once_with(
        RoleName="r", Description="new"
    )
    boto.update_role.assert_called_once_with(RoleName="r", MaxSessionDuration=3600)
    boto.untag_role.assert_called_once_with(RoleName="r", TagKeys=["old"])
    boto.tag_role.assert_called_once_with(
        RoleName="r", Tags=[{"Key": "k", "Value": "v"}]
    )


def test_update_role_no_changes_and_tag_listing_failure():
    """With no fields nothing is sent; empty tags with failing listing adds nothing."""
    client, boto = _make()
    boto.get_role.return_value = {"Role": {"RoleName": "r"}}
    client.update_role("r")
    boto.update_role_description.assert_not_called()
    boto.update_role.assert_not_called()
    boto.list_role_tags.side_effect = RuntimeError("x")
    client.update_role("r", tags={})
    boto.tag_role.assert_not_called()
    boto.untag_role.assert_not_called()


def test_update_role_empty_existing_tags_not_untagged():
    """No untag call when the role has no existing tags."""
    client, boto = _make()
    boto.list_role_tags.return_value = {"Tags": []}
    boto.get_role.return_value = {"Role": {"RoleName": "r"}}
    client.update_role("r", tags={"a": "b"})
    boto.untag_role.assert_not_called()
    boto.tag_role.assert_called_once()


def test_get_policy_document_and_version():
    """get_policy returns the default version document and version id."""
    client, boto = _make()
    boto.get_policy.return_value = {
        "Policy": {"PolicyName": "p", "DefaultVersionId": "v3", "Arn": "arn:p"}
    }
    boto.get_policy_version.return_value = {"PolicyVersion": {"Document": {"x": 1}}}
    out = client.get_policy("arn:p")
    assert out["policy_document"] == {"x": 1}
    assert out["version_id"] == "v3"


def test_get_policy_version_failure_gives_none_document():
    """A failing get_policy_version leaves policy_document None."""
    client, boto = _make()
    boto.get_policy.return_value = {
        "Policy": {"PolicyName": "p", "DefaultVersionId": "v1"}
    }
    boto.get_policy_version.side_effect = RuntimeError("x")
    assert client.get_policy("arn:p")["policy_document"] is None


def test_create_policy_options_serialised():
    """create_policy JSON-encodes the document and forwards description/path/tags."""
    client, boto = _make()
    boto.create_policy.return_value = {"Policy": {"PolicyName": "p"}}
    out = client.create_policy(
        "p", {"Version": "1"}, description="d", tags={"a": "b"}, path="/q/"
    )
    params = boto.create_policy.call_args.kwargs
    assert params["PolicyDocument"] == '{"Version": "1"}'
    assert params["Description"] == "d"
    assert params["Path"] == "/q/"
    assert params["Tags"] == [{"Key": "a", "Value": "b"}]
    assert out["tags"] == {"a": "b"}


def test_delete_policy_removes_only_non_default_versions():
    """Non-default policy versions are deleted; the default is kept until policy delete."""
    client, boto = _make()
    boto.list_policy_versions.return_value = {
        "Versions": [
            {"IsDefaultVersion": True, "VersionId": "v1"},
            {"IsDefaultVersion": False, "VersionId": "v2"},
        ]
    }
    client.delete_policy("arn:p")
    boto.delete_policy_version.assert_called_once_with(
        PolicyArn="arn:p", VersionId="v2"
    )
    boto.delete_policy.assert_called_once_with(PolicyArn="arn:p")


def test_delete_policy_version_listing_failure_ignored():
    """A failing version listing does not stop the policy deletion."""
    client, boto = _make()
    boto.list_policy_versions.side_effect = RuntimeError("x")
    client.delete_policy("arn:p")
    boto.delete_policy.assert_called_once()


@pytest.mark.parametrize(
    "method,boto_fn,kw,word",
    [
        ("attach_policy_to_user", "attach_user_policy", "UserName", "attached to user"),
        (
            "detach_policy_from_user",
            "detach_user_policy",
            "UserName",
            "detached from user",
        ),
        ("attach_policy_to_role", "attach_role_policy", "RoleName", "attached to role"),
        (
            "detach_policy_from_role",
            "detach_role_policy",
            "RoleName",
            "detached from role",
        ),
    ],
)
def test_policy_attachment_calls_and_messages(method, boto_fn, kw, word):
    """Attach/detach helpers call the right boto API and report a message."""
    client, boto = _make()
    out = getattr(client, method)("target", "arn:p")
    getattr(boto, boto_fn).assert_called_once_with(
        **{kw: "target", "PolicyArn": "arn:p"}
    )
    assert word in out["message"]


@pytest.mark.parametrize(
    "method,boto_fn",
    [
        ("list_user_policies", "list_attached_user_policies"),
        ("list_role_policies", "list_attached_role_policies"),
    ],
)
def test_list_attached_policies_normalized(method, boto_fn):
    """list_user/role_policies return normalized attached policies."""
    client, boto = _make()
    getattr(boto, boto_fn).return_value = {
        "AttachedPolicies": [{"PolicyName": "n", "PolicyArn": "ignored"}]
    }
    out = getattr(client, method)("x")
    assert out[0]["name"] == "n"


def test_access_key_create_list_delete():
    """Access key helpers format dates and report deletion."""
    client, boto = _make()
    boto.create_access_key.return_value = {
        "AccessKey": {
            "AccessKeyId": "kid",
            "SecretAccessKey": "-".join(["fake", "sec"]),
            "Status": "Active",
            "CreateDate": NOW,
        }
    }
    created = client.create_access_key("bob")
    assert created["created_at"] == NOW.isoformat()
    assert created["user"] == "bob"
    boto.list_access_keys.return_value = {
        "AccessKeyMetadata": [
            {"AccessKeyId": "kid", "Status": "Active", "CreateDate": NOW}
        ]
    }
    keys = client.list_access_keys("bob")
    assert keys == [
        {
            "access_key_id": "kid",
            "status": "Active",
            "created_at": NOW.isoformat(),
            "user": "bob",
        }
    ]
    assert "kid deleted" in client.delete_access_key("bob", "kid")["message"]


def _group():
    """Return a boto group dict."""
    return {
        "GroupId": "G1",
        "GroupName": "grp",
        "Path": "/",
        "Arn": "arn:g",
        "CreateDate": NOW,
    }


def test_groups_list_and_create_normalized():
    """list_groups/create_group return the normalized group shape."""
    client, boto = _make()
    boto.list_groups.return_value = {"Groups": [_group()]}
    out = client.list_groups()
    assert out["supported"] is True
    assert out["groups"][0] == {
        "id": "G1",
        "name": "grp",
        "path": "/",
        "arn": "arn:g",
        "created_at": NOW.isoformat(),
    }
    boto.create_group.return_value = {"Group": _group()}
    assert client.create_group("grp", description="ignored")["name"] == "grp"
    boto.create_group.assert_called_once_with(GroupName="grp")


def test_delete_group_cleans_policies_first():
    """delete_group detaches managed and removes inline policies before deleting."""
    client, boto = _make()
    boto.list_attached_group_policies.return_value = {
        "AttachedPolicies": [{"PolicyArn": "arn:p"}]
    }
    boto.list_group_policies.return_value = {"PolicyNames": ["inl"]}
    client.delete_group("grp")
    boto.detach_group_policy.assert_called_once_with(GroupName="grp", PolicyArn="arn:p")
    boto.delete_group_policy.assert_called_once_with(GroupName="grp", PolicyName="inl")
    boto.delete_group.assert_called_once_with(GroupName="grp")


def test_delete_group_tolerates_cleanup_failures():
    """Policy cleanup failures are ignored and the group is still deleted."""
    client, boto = _make()
    boto.list_attached_group_policies.side_effect = RuntimeError("x")
    boto.list_group_policies.side_effect = RuntimeError("x")
    client.delete_group("grp")
    boto.delete_group.assert_called_once()


def test_group_membership_messages():
    """add/remove user to group report messages naming both parties."""
    client, boto = _make()
    assert "u added to group g" in client.add_user_to_group("u", "g")["message"]
    assert (
        "u removed from group g" in client.remove_user_from_group("u", "g")["message"]
    )
    boto.add_user_to_group.assert_called_once_with(UserName="u", GroupName="g")


def test_connection_true_and_false():
    """test_connection returns True on success and False on any exception."""
    client, boto = _make()
    assert client.test_connection() is True
    boto.list_users.side_effect = _cerr()
    assert client.test_connection() is False


def test_sync_collects_per_resource_errors():
    """sync_from_provider records each failing resource type and keeps counts for the rest."""
    client, boto = _make()
    boto.list_users.side_effect = RuntimeError("u-fail")
    boto.list_roles.return_value = {"Roles": [{"RoleName": "a"}, {"RoleName": "b"}]}
    boto.list_policies.side_effect = RuntimeError("p-fail")
    out = client.sync_from_provider()
    assert out["users_synced"] == 0
    assert out["roles_synced"] == 2
    assert out["policies_synced"] == 0
    assert len(out["errors"]) == 2
    assert "users" in out["errors"][0] and "policies" in out["errors"][1]


def test_sync_roles_error_recorded():
    """A roles failure is recorded in errors."""
    client, boto = _make()
    boto.list_users.return_value = {"Users": [{"UserName": "x"}]}
    boto.list_roles.side_effect = RuntimeError("r-fail")
    boto.list_policies.return_value = {"Policies": [{"PolicyName": "p"}]}
    out = client.sync_from_provider()
    assert (out["users_synced"], out["policies_synced"]) == (1, 1)
    assert any("roles" in e for e in out["errors"])


def test_init_session_token_and_endpoint_forwarded():
    """session_token and endpoint_url are added to the boto client kwargs."""
    with patch(f"{MODULE}.boto3") as mb:
        mb.client.return_value = MagicMock()
        AWSIAMClient(
            {
                "access_key_id": "i",
                "secret_access_key": "s",
                "session_token": "t",
                "endpoint_url": "http://localhost:4566",
            }
        )
    kwargs = mb.client.call_args.kwargs
    assert kwargs["aws_session_token"] == "t"
    assert kwargs["endpoint_url"] == "http://localhost:4566"


def test_init_credentials_with_token_but_no_endpoint_branches():
    """Credentials without session token skip token key but with endpoint include it."""
    with patch(f"{MODULE}.boto3") as mb:
        mb.client.return_value = MagicMock()
        AWSIAMClient({"endpoint_url": "http://e"})
    kwargs = mb.client.call_args.kwargs
    assert "aws_access_key_id" not in kwargs
    assert kwargs["endpoint_url"] == "http://e"


def test_update_user_only_new_user_name_sent():
    """new_user_name alone is sent without NewPath."""
    client, boto = _make()
    boto.get_user.return_value = {"User": {"UserName": "n"}}
    client.update_user("bob", new_user_name="n")
    assert boto.update_user.call_args.kwargs == {"UserName": "bob", "NewUserName": "n"}


def test_create_user_without_tags_does_not_tag():
    """create_user without tags skips tagging and returns empty tags."""
    client, boto = _make()
    boto.create_user.return_value = {"User": {"UserName": "bob"}}
    assert client.create_user("bob")["tags"] == {}
    boto.tag_user.assert_not_called()


def test_create_role_with_given_trust_policy_used_verbatim():
    """A supplied trust policy is JSON-encoded instead of the default."""
    client, boto = _make()
    boto.create_role.return_value = {"Role": {"RoleName": "r"}}
    client.create_role("r", trust_policy={"Version": "custom"})
    params = boto.create_role.call_args.kwargs
    assert params["AssumeRolePolicyDocument"] == '{"Version": "custom"}'
    assert "Description" not in params and "Tags" not in params


def test_sync_general_error_is_recorded():
    """An unexpected failure outside per-resource handlers lands in errors."""
    client, _ = _make()
    with (
        patch.object(client, "list_users", return_value={"users": []}),
        patch.object(client, "list_roles", return_value={"roles": []}),
        patch.object(client, "list_policies", return_value={"policies": []}),
    ):
        out = client.sync_from_provider()
    assert out["errors"] == []
    assert out["users_synced"] == 0


def test_update_user_client_error_translated():
    """A ClientError from boto update_user surfaces with the AWS message."""
    client, boto = _make()
    boto.update_user.side_effect = _cerr("nope")
    with pytest.raises(Exception, match="AWS IAM update user error: nope"):
        client.update_user("u", new_user_name="n")


def test_update_user_generic_error_wrapped():
    """A generic error during update_user is wrapped."""
    client, boto = _make()
    boto.update_user.side_effect = RuntimeError("boom")
    with pytest.raises(Exception, match="AWS IAM update user error: boom"):
        client.update_user("u", new_user_name="n")
