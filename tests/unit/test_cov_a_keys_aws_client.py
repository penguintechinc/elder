"""Unit tests for the AWS KMS key provider client and the key base class.

The boto3 KMS client is replaced by a MagicMock at the client boundary
(``create_aws_session_and_client``); no AWS credentials or network are used.
Covers every operation's happy path, optional-argument branches, and the
ClientError / generic-exception translation branches.
"""

import base64
from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import pytest

pytest.importorskip("botocore")
from botocore.exceptions import ClientError  # noqa: E402

from apps.api.services.keys.aws_client import AWSKMSClient  # noqa: E402
from apps.api.services.keys.base import BaseKeyProvider  # noqa: E402

MODULE = "apps.api.services.keys.aws_client"
NOW = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)


def _client_error(code="AccessDenied", message="denied"):
    """Build a botocore ClientError with the given code and message."""
    return ClientError({"Error": {"Code": code, "Message": message}}, "Op")


def _make():
    """Build an AWSKMSClient whose boto3 client is a MagicMock."""
    fake = MagicMock()
    with patch(f"{MODULE}.create_aws_session_and_client", return_value=fake) as p:
        client = AWSKMSClient({"region": "us-east-1", "provider_type": "aws_kms"})
    return client, fake, p


def _meta(**over):
    """Return a KeyMetadata dict as boto3 would."""
    meta = {
        "KeyId": "kid-1",
        "Arn": "arn:aws:kms:us-east-1:1:key/kid-1",
        "KeyState": "Enabled",
        "Enabled": True,
        "CreationDate": NOW,
        "Description": "d",
        "KeyUsage": "ENCRYPT_DECRYPT",
        "KeySpec": "SYMMETRIC_DEFAULT",
    }
    meta.update(over)
    return meta


def test_init_builds_kms_client_and_records_provider_type():
    """Constructor requests a 'kms' boto client and stores provider_type."""
    client, fake, p = _make()
    assert client.client is fake
    assert client.provider_type == "aws_kms"
    assert p.call_args.args[1] == "kms"


class TestCreateKey:
    """create_key mapping, tags, alias handling and errors."""

    @pytest.mark.parametrize(
        ("key_type", "spec", "usage"),
        [
            ("symmetric", "SYMMETRIC_DEFAULT", "ENCRYPT_DECRYPT"),
            ("asymmetric", "RSA_2048", "ENCRYPT_DECRYPT"),
            ("hmac", "HMAC_256", "GENERATE_VERIFY_MAC"),
            ("weird", "SYMMETRIC_DEFAULT", "ENCRYPT_DECRYPT"),
        ],
    )
    def test_default_spec_mapping(self, key_type, spec, usage):
        """Unspecified key_spec is derived from key_type, usage set accordingly."""
        client, fake, _ = _make()
        fake.create_key.return_value = {"KeyMetadata": _meta()}
        out = client.create_key("k1", key_type=key_type)
        kwargs = fake.create_key.call_args.kwargs
        assert kwargs["KeySpec"] == spec
        assert kwargs["KeyUsage"] == usage
        assert kwargs["Description"] == "Elder managed key: k1"
        assert "Tags" not in kwargs
        assert out["key_spec"] == spec
        assert out["alias"] == "alias/k1"
        fake.create_alias.assert_called_once_with(
            AliasName="alias/k1", TargetKeyId="kid-1"
        )

    def test_explicit_spec_tags_description_and_alias_prefix(self):
        """Explicit spec, tags and an existing alias/ prefix are passed through."""
        client, fake, _ = _make()
        fake.create_key.return_value = {"KeyMetadata": _meta()}
        out = client.create_key(
            "alias/mine", key_spec="RSA_4096", description="desc", tags={"a": "b"}
        )
        kwargs = fake.create_key.call_args.kwargs
        assert kwargs["KeySpec"] == "RSA_4096"
        assert kwargs["Description"] == "desc"
        assert kwargs["Tags"] == [{"TagKey": "a", "TagValue": "b"}]
        assert out["alias"] == "alias/mine"
        assert out["created_at"] == NOW.isoformat()
        assert out["description"] == "desc"

    def test_alias_already_exists_is_tolerated(self):
        """AlreadyExistsException on alias creation does not fail the call."""
        client, fake, _ = _make()
        fake.create_key.return_value = {"KeyMetadata": _meta()}
        fake.create_alias.side_effect = _client_error("AlreadyExistsException", "x")
        assert client.create_key("k")["key_id"] == "kid-1"

    def test_alias_other_error_propagates(self):
        """Any other alias ClientError is re-raised as a create-key error."""
        client, fake, _ = _make()
        fake.create_key.return_value = {"KeyMetadata": _meta()}
        fake.create_alias.side_effect = _client_error("AccessDenied", "nope")
        with pytest.raises(Exception, match="create key error: nope"):
            client.create_key("k")

    def test_client_error_translated(self):
        """ClientError from create_key surfaces its Message."""
        client, fake, _ = _make()
        fake.create_key.side_effect = _client_error(message="quota")
        with pytest.raises(Exception, match="create key error: quota"):
            client.create_key("k")

    def test_generic_error_translated(self):
        """Non-ClientError exceptions are wrapped too."""
        client, fake, _ = _make()
        fake.create_key.side_effect = RuntimeError("boom")
        with pytest.raises(Exception, match="create key error: boom"):
            client.create_key("k")


class TestGetKey:
    """get_key metadata, deletion date and errors."""

    def test_returns_metadata_with_aliases(self):
        """Metadata and aliases are merged; no DeletionDate gives None."""
        client, fake, _ = _make()
        fake.describe_key.return_value = {"KeyMetadata": _meta()}
        fake.list_aliases.return_value = {"Aliases": [{"AliasName": "alias/x"}]}
        out = client.get_key("kid-1")
        assert out["aliases"] == ["alias/x"]
        assert out["deletion_date"] is None
        assert out["enabled"] is True
        assert out["key_usage"] == "ENCRYPT_DECRYPT"

    def test_deletion_date_is_iso(self):
        """A DeletionDate is rendered as ISO-8601."""
        client, fake, _ = _make()
        fake.describe_key.return_value = {"KeyMetadata": _meta(DeletionDate=NOW)}
        fake.list_aliases.return_value = {}
        out = client.get_key("kid-1")
        assert out["deletion_date"] == NOW.isoformat()
        assert out["aliases"] == []

    def test_client_error(self):
        """ClientError on describe is translated."""
        client, fake, _ = _make()
        fake.describe_key.side_effect = _client_error(message="NotFound")
        with pytest.raises(Exception, match="get key error: NotFound"):
            client.get_key("x")

    def test_generic_error(self):
        """Unexpected errors are wrapped."""
        client, fake, _ = _make()
        fake.describe_key.side_effect = RuntimeError("bad")
        with pytest.raises(Exception, match="get key error: bad"):
            client.get_key("x")


class TestListKeys:
    """list_keys pagination, limit clamp, skipped keys and errors."""

    def test_params_and_skip_inaccessible(self):
        """Limit is clamped to 1000, marker forwarded, failing keys skipped."""
        client, fake, _ = _make()
        fake.list_keys.return_value = {
            "Keys": [{"KeyId": "a"}, {"KeyId": "b"}],
            "NextMarker": "m2",
            "Truncated": True,
        }
        with patch.object(
            client, "get_key", side_effect=[{"key_id": "a"}, Exception("denied")]
        ):
            out = client.list_keys(limit=5000, next_token="m1")
        fake.list_keys.assert_called_once_with(Limit=1000, Marker="m1")
        assert out == {"keys": [{"key_id": "a"}], "next_token": "m2", "truncated": True}

    def test_no_params(self):
        """No limit/token sends an empty parameter set and defaults flags."""
        client, fake, _ = _make()
        fake.list_keys.return_value = {}
        out = client.list_keys()
        fake.list_keys.assert_called_once_with()
        assert out == {"keys": [], "next_token": None, "truncated": False}

    @pytest.mark.parametrize(
        ("exc", "match"),
        [
            (_client_error(message="slow"), "list keys error: slow"),
            (RuntimeError("zz"), "list keys error: zz"),
        ],
    )
    def test_errors(self, exc, match):
        """ClientError and generic errors are translated."""
        client, fake, _ = _make()
        fake.list_keys.side_effect = exc
        with pytest.raises(Exception, match=match):
            client.list_keys()


class TestEnableDisable:
    """enable_key / disable_key call KMS then return refreshed metadata."""

    @pytest.mark.parametrize("op", ["enable_key", "disable_key"])
    def test_success(self, op):
        """The KMS operation runs and get_key's result is returned."""
        client, fake, _ = _make()
        with patch.object(client, "get_key", return_value={"state": "x"}) as gk:
            assert getattr(client, op)("kid") == {"state": "x"}
        getattr(fake, op).assert_called_once_with(KeyId="kid")
        gk.assert_called_once_with("kid")

    @pytest.mark.parametrize("op", ["enable_key", "disable_key"])
    @pytest.mark.parametrize(
        ("exc", "tail"),
        [(_client_error(message="msg"), "msg"), (RuntimeError("oops"), "oops")],
    )
    def test_errors(self, op, exc, tail):
        """Both error branches are translated for each operation."""
        client, fake, _ = _make()
        getattr(fake, op).side_effect = exc
        with pytest.raises(Exception, match=f"{op.split('_')[0]} key error: {tail}"):
            getattr(client, op)("kid")


class TestDeletion:
    """schedule_key_deletion / cancel_key_deletion."""

    @pytest.mark.parametrize(("given", "sent"), [(1, 7), (15, 15), (99, 30)])
    def test_schedule_clamps_window(self, given, sent):
        """The pending window is clamped to AWS's 7-30 day range."""
        client, fake, _ = _make()
        fake.schedule_key_deletion.return_value = {
            "KeyId": "kid",
            "DeletionDate": NOW,
        }
        out = client.schedule_key_deletion("kid", given)
        assert (
            fake.schedule_key_deletion.call_args.kwargs["PendingWindowInDays"] == sent
        )
        assert out == {
            "key_id": "kid",
            "deletion_date": NOW.isoformat(),
            "state": "PendingDeletion",
        }

    def test_cancel(self):
        """Cancel returns the disabled state."""
        client, fake, _ = _make()
        fake.cancel_key_deletion.return_value = {"KeyId": "kid"}
        assert client.cancel_key_deletion("kid") == {
            "key_id": "kid",
            "state": "Disabled",
        }

    @pytest.mark.parametrize("op", ["schedule_key_deletion", "cancel_key_deletion"])
    @pytest.mark.parametrize(
        ("exc", "tail"),
        [(_client_error(message="msg"), "msg"), (RuntimeError("oops"), "oops")],
    )
    def test_errors(self, op, exc, tail):
        """Both error branches are translated for each operation."""
        client, fake, _ = _make()
        getattr(fake, op).side_effect = exc
        with pytest.raises(Exception, match=f"deletion error: {tail}"):
            getattr(client, op)("kid")


class TestCrypto:
    """encrypt / decrypt / generate_data_key / sign / verify."""

    def test_encrypt_with_context(self):
        """Encrypt base64-encodes the blob and forwards the context."""
        client, fake, _ = _make()
        fake.encrypt.return_value = {"CiphertextBlob": b"\x01\x02", "KeyId": "kid"}
        out = client.encrypt("kid", "hello", {"c": "1"})
        kwargs = fake.encrypt.call_args.kwargs
        assert kwargs["Plaintext"] == b"hello"
        assert kwargs["EncryptionContext"] == {"c": "1"}
        assert out == {
            "ciphertext": base64.b64encode(b"\x01\x02").decode(),
            "key_id": "kid",
        }

    def test_encrypt_without_context(self):
        """No context means no EncryptionContext parameter."""
        client, fake, _ = _make()
        fake.encrypt.return_value = {"CiphertextBlob": b"x", "KeyId": "kid"}
        client.encrypt("kid", "p")
        assert "EncryptionContext" not in fake.encrypt.call_args.kwargs

    def test_decrypt_roundtrip_args(self):
        """Decrypt base64-decodes input, forwards context, decodes plaintext."""
        client, fake, _ = _make()
        fake.decrypt.return_value = {"Plaintext": b"secret", "KeyId": "kid"}
        ct = base64.b64encode(b"blob").decode()
        out = client.decrypt(ct, {"c": "1"})
        kwargs = fake.decrypt.call_args.kwargs
        assert kwargs["CiphertextBlob"] == b"blob"
        assert kwargs["EncryptionContext"] == {"c": "1"}
        assert out == {"plaintext": "secret", "key_id": "kid"}

    def test_decrypt_without_context(self):
        """No context means no EncryptionContext parameter."""
        client, fake, _ = _make()
        fake.decrypt.return_value = {"Plaintext": b"s", "KeyId": "kid"}
        client.decrypt(base64.b64encode(b"b").decode())
        assert "EncryptionContext" not in fake.decrypt.call_args.kwargs

    def test_generate_data_key(self):
        """Both key halves are base64-encoded; context is forwarded."""
        client, fake, _ = _make()
        fake.generate_data_key.return_value = {
            "Plaintext": b"P",
            "CiphertextBlob": b"C",
            "KeyId": "kid",
        }
        out = client.generate_data_key("kid", "AES_128", {"c": "1"})
        kwargs = fake.generate_data_key.call_args.kwargs
        assert kwargs["KeySpec"] == "AES_128"
        assert kwargs["EncryptionContext"] == {"c": "1"}
        assert out["plaintext_key"] == base64.b64encode(b"P").decode()
        assert out["ciphertext_key"] == base64.b64encode(b"C").decode()

    def test_generate_data_key_no_context(self):
        """Without context the parameter is omitted."""
        client, fake, _ = _make()
        fake.generate_data_key.return_value = {
            "Plaintext": b"P",
            "CiphertextBlob": b"C",
            "KeyId": "kid",
        }
        client.generate_data_key("kid")
        assert "EncryptionContext" not in fake.generate_data_key.call_args.kwargs

    def test_sign(self):
        """Sign returns base64 signature and the algorithm used."""
        client, fake, _ = _make()
        fake.sign.return_value = {
            "Signature": b"sig",
            "KeyId": "kid",
            "SigningAlgorithm": "ALG",
        }
        out = client.sign("kid", "msg", "ALG")
        assert fake.sign.call_args.kwargs["Message"] == b"msg"
        assert out["signature"] == base64.b64encode(b"sig").decode()
        assert out["algorithm"] == "ALG"

    @pytest.mark.parametrize("valid", [True, False])
    def test_verify(self, valid):
        """Verify forwards decoded signature bytes and reports validity."""
        client, fake, _ = _make()
        fake.verify.return_value = {"SignatureValid": valid, "KeyId": "kid"}
        sig = base64.b64encode(b"sig").decode()
        out = client.verify("kid", "msg", sig, "ALG")
        assert fake.verify.call_args.kwargs["Signature"] == b"sig"
        assert out == {"valid": valid, "key_id": "kid"}

    @pytest.mark.parametrize(
        ("op", "args", "label"),
        [
            ("encrypt", ("kid", "p"), "encrypt"),
            ("decrypt", (base64.b64encode(b"b").decode(),), "decrypt"),
            ("generate_data_key", ("kid",), "generate data key"),
            ("sign", ("kid", "m"), "sign"),
            ("verify", ("kid", "m", base64.b64encode(b"s").decode(), "A"), "verify"),
        ],
    )
    @pytest.mark.parametrize(
        ("exc", "tail"),
        [(_client_error(message="msg"), "msg"), (RuntimeError("oops"), "oops")],
    )
    def test_errors(self, op, args, label, exc, tail):
        """Both ClientError and generic failures are translated per operation."""
        client, fake, _ = _make()
        getattr(fake, op).side_effect = exc
        with pytest.raises(Exception, match=f"{label} error: {tail}"):
            getattr(client, op)(*args)


class TestRotateAndConnection:
    """rotate_key and test_connection."""

    def test_rotate(self):
        """Rotation is enabled and status reported."""
        client, fake, _ = _make()
        fake.get_key_rotation_status.return_value = {"KeyRotationEnabled": True}
        out = client.rotate_key("kid")
        fake.enable_key_rotation.assert_called_once_with(KeyId="kid")
        assert out["rotation_enabled"] is True

    @pytest.mark.parametrize(
        ("exc", "tail"),
        [(_client_error(message="msg"), "msg"), (RuntimeError("oops"), "oops")],
    )
    def test_rotate_errors(self, exc, tail):
        """Both error branches are translated."""
        client, fake, _ = _make()
        fake.enable_key_rotation.side_effect = exc
        with pytest.raises(Exception, match=f"rotate key error: {tail}"):
            client.rotate_key("kid")

    def test_connection_ok_and_failure(self):
        """test_connection is True on success and False on any exception."""
        client, fake, _ = _make()
        assert client.test_connection() is True
        fake.list_keys.assert_called_once_with(Limit=1)
        fake.list_keys.side_effect = RuntimeError("down")
        assert client.test_connection() is False


class TestBaseNormalize:
    """BaseKeyProvider._normalize_key_metadata field aliasing."""

    def test_abstract_cannot_instantiate(self):
        """The abstract base cannot be instantiated directly."""
        with pytest.raises(TypeError):
            BaseKeyProvider({})

    def test_aws_style_keys(self):
        """AWS-style capitalised keys are normalised."""
        client, _, _ = _make()
        out = client._normalize_key_metadata(
            {
                "KeyId": "k",
                "KeyMetadata": {"Arn": "arn"},
                "KeyState": "Enabled",
                "CreationDate": "t",
                "Description": "d",
            }
        )
        assert out == {
            "key_id": "k",
            "key_arn": "arn",
            "state": "Enabled",
            "created_at": "t",
            "description": "d",
            "enabled": True,
        }

    def test_common_keys_win(self):
        """Already-normalised keys take precedence over provider keys."""
        client, _, _ = _make()
        out = client._normalize_key_metadata({"key_id": "a", "KeyId": "b"})
        assert out["key_id"] == "a"
