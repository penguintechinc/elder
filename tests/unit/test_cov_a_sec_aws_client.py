"""Unit tests for AWSSecretsManagerClient with the boto3 client mocked at the boundary."""

# flake8: noqa: E501

import json
from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import pytest
from botocore.exceptions import ClientError

from apps.api.services.secrets.aws_client import AWSSecretsManagerClient
from apps.api.services.secrets.base import (
    InvalidSecretConfigException,
    SecretAccessDeniedException,
    SecretAlreadyExistsException,
    SecretNotFoundException,
    SecretProviderException,
)

FACTORY = "apps.api.services.secrets.aws_client.create_aws_session_and_client"


def _err(code, msg="boom", op="Op"):
    """Build a botocore ClientError with the given code and message."""
    return ClientError({"Error": {"Code": code, "Message": msg}}, op)


@pytest.fixture
def boto():
    """Mocked boto3 secretsmanager client."""
    return MagicMock()


@pytest.fixture
def client(boto):
    """AWSSecretsManagerClient wired to the mocked boto3 client."""
    with patch(FACTORY, return_value=boto):
        return AWSSecretsManagerClient({"region": "us-east-1"})


class TestInit:
    """Construction and config validation."""

    def test_missing_region_rejected(self):
        """A config without region raises InvalidSecretConfigException."""
        with patch(FACTORY) as factory:
            with pytest.raises(InvalidSecretConfigException, match="region"):
                AWSSecretsManagerClient({})
        factory.assert_not_called()

    def test_factory_receives_config_and_service(self, boto):
        """The session factory is called with the config and 'secretsmanager'."""
        cfg = {"region": "eu-west-1"}
        with patch(FACTORY, return_value=boto) as factory:
            c = AWSSecretsManagerClient(cfg)
        factory.assert_called_once_with(cfg, "secretsmanager")
        assert c.client is boto

    def test_factory_failure_wrapped(self):
        """A failing session factory is wrapped in SecretProviderException."""
        with patch(FACTORY, side_effect=RuntimeError("no creds")):
            with pytest.raises(SecretProviderException, match="Failed to initialize"):
                AWSSecretsManagerClient({"region": "us-east-1"})


class TestConnection:
    """test_connection result mapping."""

    def test_success(self, client, boto):
        """A successful probe returns True using MaxResults=1."""
        assert client.test_connection() is True
        boto.list_secrets.assert_called_once_with(MaxResults=1)

    @pytest.mark.parametrize("exc", [_err("AccessDenied", "nope"), RuntimeError("x")])
    def test_failures_return_false(self, client, boto, exc):
        """Client and generic errors both yield False."""
        boto.list_secrets.side_effect = exc
        assert client.test_connection() is False


class TestGetSecret:
    """get_secret parsing and error mapping."""

    def _resp(self, **over):
        """Minimal get_secret_value response."""
        base = {
            "Name": "db/pw",
            "ARN": "arn:aws:secretsmanager:us-east-1:000000000000:secret:db/pw",
            "SecretString": "plain-value",
            "VersionId": "v1",
            "CreatedDate": datetime(2026, 1, 1, tzinfo=UTC),
            "VersionStages": ["AWSCURRENT"],
        }
        base.update(over)
        return base

    def test_plain_string(self, client, boto):
        """A non-JSON string is returned as the value with metadata."""
        boto.get_secret_value.return_value = self._resp()
        sv = client.get_secret("db/pw")
        assert sv.value == "plain-value" and sv.is_kv is False and sv.version == "v1"
        assert sv.metadata["version_stages"] == ["AWSCURRENT"]
        boto.get_secret_value.assert_called_once_with(SecretId="db/pw")

    def test_json_object_is_kv_and_hides_raw(self, client, boto):
        """A JSON object becomes kv_pairs and the raw JSON is not exposed."""
        boto.get_secret_value.return_value = self._resp(
            SecretString=json.dumps({"u": "a", "p": "b"})
        )
        sv = client.get_secret("db/pw")
        assert sv.is_kv is True and sv.kv_pairs == {"u": "a", "p": "b"}
        assert sv.value is None

    def test_json_non_object_stays_plain(self, client, boto):
        """A JSON list/number is kept as a plain string value."""
        boto.get_secret_value.return_value = self._resp(SecretString="[1, 2]")
        sv = client.get_secret("db/pw")
        assert sv.is_kv is False and sv.value == "[1, 2]"

    def test_version_passed_and_binary_secret(self, client, boto):
        """A version is forwarded as VersionId; secrets without SecretString give no value."""
        resp = self._resp()
        del resp["SecretString"]
        boto.get_secret_value.return_value = resp
        sv = client.get_secret("db/pw", version="abc")
        boto.get_secret_value.assert_called_once_with(SecretId="db/pw", VersionId="abc")
        assert sv.value is None and sv.is_kv is False

    @pytest.mark.parametrize(
        "code,exc",
        [
            ("ResourceNotFoundException", SecretNotFoundException),
            ("AccessDeniedException", SecretAccessDeniedException),
            ("ThrottlingException", SecretProviderException),
        ],
    )
    def test_client_error_mapping(self, client, boto, code, exc):
        """AWS error codes map to the matching provider exception type."""
        boto.get_secret_value.side_effect = _err(code, "msg")
        with pytest.raises(exc) as ei:
            client.get_secret("p")
        if exc is SecretProviderException:
            assert type(ei.value) is SecretProviderException

    def test_unexpected_error_wrapped(self, client, boto):
        """Non-AWS errors are wrapped as SecretProviderException."""
        boto.get_secret_value.side_effect = RuntimeError("net")
        with pytest.raises(SecretProviderException, match="Unexpected"):
            client.get_secret("p")


class TestListSecrets:
    """list_secrets pagination, filters and errors."""

    def _page(self, *secrets):
        """Wrap secrets in a paginator page."""
        return {"SecretList": list(secrets)}

    def test_lists_across_pages_with_prefix_filter(self, client, boto):
        """All pages are consumed and the prefix becomes a name filter."""
        paginator = MagicMock()
        paginator.paginate.return_value = [
            self._page(
                {"Name": "a", "ARN": "arn:a", "SecretString": '{"k": "v"}', "Tags": []},
                {"Name": "b", "ARN": "arn:b", "SecretString": "not json"},
            ),
            self._page({"Name": "c", "ARN": "arn:c", "Description": "d"}),
        ]
        boto.get_paginator.return_value = paginator
        res = client.list_secrets(prefix="pre")
        paginator.paginate.assert_called_once_with(
            Filters=[{"Key": "name", "Values": ["pre"]}]
        )
        assert [s.path for s in res] == ["a", "b", "c"]
        assert [s.is_kv for s in res] == [True, False, False]
        assert res[2].metadata["description"] == "d"

    def test_no_prefix_no_filter(self, client, boto):
        """Without a prefix no Filters argument is sent."""
        paginator = MagicMock()
        paginator.paginate.return_value = [self._page()]
        boto.get_paginator.return_value = paginator
        assert client.list_secrets() == []
        paginator.paginate.assert_called_once_with()

    def test_client_error_wrapped(self, client, boto):
        """AWS errors while listing become SecretProviderException."""
        boto.get_paginator.return_value.paginate.side_effect = _err("X", "denied")
        with pytest.raises(SecretProviderException, match="AWS error listing"):
            client.list_secrets()

    def test_unexpected_error_wrapped(self, client, boto):
        """Unexpected errors while listing are wrapped."""
        boto.get_paginator.side_effect = RuntimeError("x")
        with pytest.raises(SecretProviderException, match="Unexpected error listing"):
            client.list_secrets()


class TestCreateUpdate:
    """create_secret and update_secret."""

    def test_create_with_full_metadata(self, client, boto):
        """Description, tags and KMS key are forwarded; JSON values flag is_kv."""
        boto.create_secret.return_value = {"VersionId": "v1", "ARN": "arn:x"}
        res = client.create_secret(
            "p",
            '{"a": 1}',
            {"description": "d", "tags": {"env": "prod"}, "kms_key_id": "key-1"},
        )
        boto.create_secret.assert_called_once_with(
            Name="p",
            SecretString='{"a": 1}',
            Description="d",
            Tags=[{"Key": "env", "Value": "prod"}],
            KmsKeyId="key-1",
        )
        assert res.is_kv is True and res.version == "v1"
        assert res.metadata == {"arn": "arn:x"}

    def test_create_plain_without_metadata(self, client, boto):
        """With no metadata only Name and SecretString are sent."""
        boto.create_secret.return_value = {"VersionId": "v1", "ARN": "arn:x"}
        res = client.create_secret("p", "plain")
        boto.create_secret.assert_called_once_with(Name="p", SecretString="plain")
        assert res.is_kv is False

    @pytest.mark.parametrize(
        "metadata,expected_extra",
        [
            ({"description": "d"}, {"Description": "d"}),
            ({"tags": {"a": "b"}}, {"Tags": [{"Key": "a", "Value": "b"}]}),
            ({"kms_key_id": "k"}, {"KmsKeyId": "k"}),
            ({"unrelated": 1}, {}),
        ],
    )
    def test_create_partial_metadata(self, client, boto, metadata, expected_extra):
        """Each metadata key independently maps to its AWS parameter."""
        boto.create_secret.return_value = {"VersionId": "v1", "ARN": "arn:x"}
        client.create_secret("p", "v", metadata)
        boto.create_secret.assert_called_once_with(
            Name="p", SecretString="v", **expected_extra
        )

    @pytest.mark.parametrize(
        "code,exc",
        [
            ("ResourceExistsException", SecretAlreadyExistsException),
            ("AccessDeniedException", SecretAccessDeniedException),
            ("InternalServiceError", SecretProviderException),
        ],
    )
    def test_create_error_mapping(self, client, boto, code, exc):
        """Create maps AWS error codes to specific exceptions."""
        boto.create_secret.side_effect = _err(code)
        with pytest.raises(exc):
            client.create_secret("p", "v")

    def test_create_unexpected_wrapped(self, client, boto):
        """Unexpected errors on create are wrapped."""
        boto.create_secret.side_effect = RuntimeError("x")
        with pytest.raises(SecretProviderException, match="Unexpected error creating"):
            client.create_secret("p", "v")

    def test_update_success(self, client, boto):
        """update_secret puts a new value and returns the new version."""
        boto.put_secret_value.return_value = {
            "VersionId": "v2",
            "ARN": "arn:x",
            "VersionStages": ["AWSCURRENT"],
        }
        res = client.update_secret("p", "new")
        boto.put_secret_value.assert_called_once_with(SecretId="p", SecretString="new")
        assert res.version == "v2" and res.metadata["version_stages"] == ["AWSCURRENT"]

    @pytest.mark.parametrize(
        "code,exc",
        [
            ("ResourceNotFoundException", SecretNotFoundException),
            ("AccessDeniedException", SecretAccessDeniedException),
            ("Other", SecretProviderException),
        ],
    )
    def test_update_error_mapping(self, client, boto, code, exc):
        """Update maps AWS error codes to specific exceptions."""
        boto.put_secret_value.side_effect = _err(code)
        with pytest.raises(exc):
            client.update_secret("p", "v")

    def test_update_unexpected_wrapped(self, client, boto):
        """Unexpected errors on update are wrapped."""
        boto.put_secret_value.side_effect = RuntimeError("x")
        with pytest.raises(SecretProviderException, match="Unexpected error updating"):
            client.update_secret("p", "v")


class TestDeleteAndVersions:
    """delete_secret and get_secret_versions."""

    @pytest.mark.parametrize(
        "force,expected",
        [
            (True, {"SecretId": "p", "ForceDeleteWithoutRecovery": True}),
            (False, {"SecretId": "p", "RecoveryWindowInDays": 30}),
        ],
    )
    def test_delete_params(self, client, boto, force, expected):
        """Force deletes immediately; otherwise a 30-day recovery window is set."""
        assert client.delete_secret("p", force=force) is True
        boto.delete_secret.assert_called_once_with(**expected)

    @pytest.mark.parametrize(
        "code,exc",
        [
            ("ResourceNotFoundException", SecretNotFoundException),
            ("AccessDeniedException", SecretAccessDeniedException),
            ("Other", SecretProviderException),
        ],
    )
    def test_delete_error_mapping(self, client, boto, code, exc):
        """Delete maps AWS error codes to specific exceptions."""
        boto.delete_secret.side_effect = _err(code)
        with pytest.raises(exc):
            client.delete_secret("p")

    def test_delete_unexpected_wrapped(self, client, boto):
        """Unexpected errors on delete are wrapped."""
        boto.delete_secret.side_effect = RuntimeError("x")
        with pytest.raises(SecretProviderException, match="Unexpected error deleting"):
            client.delete_secret("p")

    def test_versions(self, client, boto):
        """Version ids are extracted in order; no Versions key yields []."""
        boto.list_secret_version_ids.return_value = {
            "Versions": [{"VersionId": "a"}, {"VersionId": "b"}]
        }
        assert client.get_secret_versions("p") == ["a", "b"]
        boto.list_secret_version_ids.assert_called_once_with(SecretId="p")
        boto.list_secret_version_ids.return_value = {}
        assert client.get_secret_versions("p") == []

    @pytest.mark.parametrize(
        "code,exc",
        [
            ("ResourceNotFoundException", SecretNotFoundException),
            ("AccessDeniedException", SecretAccessDeniedException),
            ("Other", SecretProviderException),
        ],
    )
    def test_versions_error_mapping(self, client, boto, code, exc):
        """Version listing maps AWS error codes to specific exceptions."""
        boto.list_secret_version_ids.side_effect = _err(code)
        with pytest.raises(exc):
            client.get_secret_versions("p")

    def test_versions_unexpected_wrapped(self, client, boto):
        """Unexpected errors on version listing are wrapped."""
        boto.list_secret_version_ids.side_effect = RuntimeError("x")
        with pytest.raises(SecretProviderException, match="Unexpected error listing"):
            client.get_secret_versions("p")


@pytest.mark.parametrize(
    "value,expected",
    [('{"a": 1}', True), ("[1]", False), ("plain", False), (None, False)],
)
def test_is_json_dict(value, expected):
    """_is_json_dict is True only for JSON objects and tolerates None/garbage."""
    assert AWSSecretsManagerClient._is_json_dict(value) is expected
