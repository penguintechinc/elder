"""Unit tests for the GCP Cloud KMS key provider client.

The ``kms`` SDK module handle is replaced by a MagicMock at the module
boundary; real ``google.api_core`` exception classes are used so the
NotFound / GoogleAPIError / generic translation branches are exercised
faithfully. No network or credentials are involved.
"""

import base64
import json
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

pytest.importorskip("google.api_core")
from google.api_core import exceptions as google_exceptions  # noqa: E402

from apps.api.services.keys.gcp_client import GCPKMSClient  # noqa: E402

MODULE = "apps.api.services.keys.gcp_client"
NOW = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)
KEY_PATH = "projects/p/locations/l/keyRings/r/cryptoKeys/mykey"


@pytest.fixture
def env():
    """Patch the kms module handle and credential resolver; yield (kms, creds)."""
    with (
        patch(f"{MODULE}.kms") as kms,
        patch(f"{MODULE}.google_exceptions", google_exceptions),
        patch(f"{MODULE}.resolve_gcp_credentials", return_value=None) as creds,
    ):
        kms.KeyManagementServiceClient.return_value = MagicMock(name="kms_client")
        yield kms, creds


@pytest.fixture
def client(env):
    """Return a GCPKMSClient built on the mocked SDK."""
    c = GCPKMSClient({"project_id": "p", "location_id": "l", "keyring_id": "r"})
    c.client.key_ring_path.return_value = "projects/p/locations/l/keyRings/r"
    c.client.crypto_key_path.return_value = KEY_PATH
    return c


def _crypto_key(kms, state=None):
    """Build a fake CryptoKey resource."""
    return SimpleNamespace(
        name=KEY_PATH,
        primary=SimpleNamespace(
            state=state or kms.CryptoKeyVersion.CryptoKeyVersionState.ENABLED
        ),
        create_time=NOW,
        purpose=SimpleNamespace(name="ENCRYPT_DECRYPT"),
        version_template=SimpleNamespace(algorithm=SimpleNamespace(name="ALG")),
        labels={"a": "b"},
        rotation_period=SimpleNamespace(seconds=60),
        next_rotation_time=NOW,
    )


class TestInit:
    """Constructor branches and keyring bootstrap."""

    def test_missing_sdk_raises_import_error(self):
        """Without google-cloud-kms the constructor raises ImportError."""
        with patch(f"{MODULE}.kms", None), pytest.raises(ImportError, match="kms"):
            GCPKMSClient({"project_id": "p"})

    def test_project_id_required(self, env):
        """A missing project_id is rejected."""
        with pytest.raises(ValueError, match="project_id"):
            GCPKMSClient({})

    def test_defaults_and_default_credentials(self, env):
        """Defaults for location/keyring apply and no credentials kwarg is used."""
        kms, _ = env
        c = GCPKMSClient({"project_id": "p"})
        assert (c.location_id, c.keyring_id) == ("us-east1", "elder-keyring")
        kms.KeyManagementServiceClient.assert_called_once_with()

    def test_explicit_credentials_passed(self, env):
        """Resolved credentials are handed to the KMS client."""
        kms, creds = env
        sentinel = object()
        creds.return_value = sentinel
        GCPKMSClient({"project_id": "p"})
        kms.KeyManagementServiceClient.assert_called_once_with(credentials=sentinel)

    def test_keyring_created_when_missing(self, env):
        """A NotFound on get_key_ring triggers create_key_ring."""
        kms, _ = env
        inst = kms.KeyManagementServiceClient.return_value
        inst.location_path.return_value = "loc"
        inst.get_key_ring.side_effect = google_exceptions.NotFound("nf")
        GCPKMSClient({"project_id": "p", "keyring_id": "kr"})
        inst.create_key_ring.assert_called_once_with(
            request={"parent": "loc", "key_ring_id": "kr"}
        )

    def test_keyring_errors_are_ignored(self, env):
        """Any failure while ensuring the keyring does not break construction."""
        kms, _ = env
        kms.KeyManagementServiceClient.return_value.location_path.side_effect = (
            RuntimeError("no perms")
        )
        assert GCPKMSClient({"project_id": "p"}).project_id == "p"


class TestCreateKey:
    """create_key purposes, algorithms, labels and errors."""

    @pytest.mark.parametrize(
        ("key_type", "algo_attr"),
        [
            ("symmetric", "GOOGLE_SYMMETRIC_ENCRYPTION"),
            ("asymmetric", "RSA_SIGN_PSS_2048_SHA256"),
            ("hmac", "HMAC_SHA256"),
            ("other", None),
        ],
    )
    def test_algorithm_selection(self, client, env, key_type, algo_attr):
        """Algorithm in the version template follows key_type; unknown has none."""
        kms, _ = env
        client.client.create_crypto_key.return_value = _crypto_key(kms)
        out = client.create_key("mykey", key_type=key_type, description="d")
        req = client.client.create_crypto_key.call_args.kwargs["request"]
        template = req["crypto_key"]["version_template"]
        if algo_attr:
            expected = getattr(
                kms.CryptoKeyVersion.CryptoKeyVersionAlgorithm, algo_attr
            )
            assert template["algorithm"] is expected
        else:
            assert template == {}
        assert req["crypto_key_id"] == "mykey"
        assert "labels" not in req["crypto_key"]
        assert out["state"] == "Enabled"
        assert out["labels"] == {}
        assert out["created_at"] == NOW.isoformat()

    def test_labels_forwarded(self, client, env):
        """Tags become labels on the crypto key and in the result."""
        kms, _ = env
        client.client.create_crypto_key.return_value = _crypto_key(kms)
        out = client.create_key("mykey", tags={"t": "v"})
        req = client.client.create_crypto_key.call_args.kwargs["request"]
        assert req["crypto_key"]["labels"] == {"t": "v"}
        assert out["labels"] == {"t": "v"}

    @pytest.mark.parametrize(
        "exc",
        [google_exceptions.PermissionDenied("denied"), RuntimeError("boom")],
    )
    def test_errors(self, client, exc):
        """Provider and generic errors are wrapped in a create-key error."""
        client.client.create_crypto_key.side_effect = exc
        with pytest.raises(Exception, match="create key error"):
            client.create_key("mykey")


class TestGetKey:
    """get_key name resolution, rotation and error translation."""

    def test_short_id_resolved_and_metadata_built(self, client, env):
        """A short id is expanded to a resource path; metadata is mapped."""
        kms, _ = env
        client.client.get_crypto_key.return_value = _crypto_key(kms)
        out = client.get_key("mykey")
        client.client.crypto_key_path.assert_called_once_with("p", "l", "r", "mykey")
        assert out["key_id"] == "mykey"
        assert out["enabled"] is True
        assert out["labels"] == {"a": "b"}
        assert out["rotation_period"] == 60
        assert out["algorithm"] == "ALG"

    def test_full_name_not_expanded_and_no_rotation(self, client, env):
        """A projects/... id is used verbatim; absent rotation yields None."""
        kms, _ = env
        key = _crypto_key(
            kms, state=kms.CryptoKeyVersion.CryptoKeyVersionState.DISABLED
        )
        key.rotation_period = None
        client.client.get_crypto_key.return_value = key
        out = client.get_key(KEY_PATH)
        client.client.crypto_key_path.assert_not_called()
        client.client.get_crypto_key.assert_called_once_with(name=KEY_PATH)
        assert out["rotation_period"] is None
        assert out["state"] == "Disabled"
        assert out["enabled"] is False

    def test_not_found(self, client):
        """NotFound is translated to a 'key not found' error."""
        client.client.get_crypto_key.side_effect = google_exceptions.NotFound("x")
        with pytest.raises(Exception, match="key not found: ghost"):
            client.get_key("ghost")

    @pytest.mark.parametrize(
        "exc", [google_exceptions.PermissionDenied("d"), RuntimeError("zz")]
    )
    def test_other_errors(self, client, exc):
        """API errors and generic errors become 'get key error'."""
        client.client.get_crypto_key.side_effect = exc
        with pytest.raises(Exception, match="get key error"):
            client.get_key("k")


class TestListKeys:
    """list_keys parameters, token handling and errors."""

    def test_params_and_next_token(self, client, env):
        """limit and next_token map to page_size/page_token; token is returned."""
        kms, _ = env
        response = MagicMock()
        response.__iter__.return_value = iter([_crypto_key(kms)])
        response.next_page_token = "tok2"
        client.client.list_crypto_keys.return_value = response
        out = client.list_keys(limit=3, next_token="tok1")
        req = client.client.list_crypto_keys.call_args.kwargs["request"]
        assert req["page_size"] == 3
        assert req["page_token"] == "tok1"
        assert out["next_token"] == "tok2"
        assert out["keys"][0]["key_id"] == "mykey"

    def test_empty_no_params(self, client):
        """No params and an empty token give next_token None."""
        response = MagicMock()
        response.__iter__.return_value = iter([])
        response.next_page_token = ""
        client.client.list_crypto_keys.return_value = response
        out = client.list_keys()
        req = client.client.list_crypto_keys.call_args.kwargs["request"]
        assert set(req) == {"parent"}
        assert out == {"keys": [], "next_token": None}

    @pytest.mark.parametrize(
        "exc", [google_exceptions.PermissionDenied("d"), RuntimeError("zz")]
    )
    def test_errors(self, client, exc):
        """Errors become 'list keys error'."""
        client.client.list_crypto_keys.side_effect = exc
        with pytest.raises(Exception, match="list keys error"):
            client.list_keys()


class TestUnsupportedOperations:
    """Operations GCP KMS cannot perform raise NotImplementedError."""

    @pytest.mark.parametrize(
        ("op", "args"),
        [
            ("enable_key", ("k",)),
            ("cancel_key_deletion", ("k",)),
            ("verify", ("k", "m", "s", "A")),
        ],
    )
    def test_not_implemented(self, client, op, args):
        """Each unsupported operation fails loudly."""
        with pytest.raises(NotImplementedError):
            getattr(client, op)(*args)


class TestDisableAndDelete:
    """disable_key / schedule_key_deletion version handling and errors."""

    def test_disable(self, client, env):
        """Primary version is set DISABLED and refreshed metadata returned."""
        kms, _ = env
        with patch.object(client, "get_key", return_value={"key_arn": KEY_PATH}) as gk:
            out = client.disable_key("mykey")
        req = client.client.update_crypto_key_version.call_args.kwargs["request"]
        assert req["crypto_key_version"]["name"] == f"{KEY_PATH}/cryptoKeyVersions/1"
        assert (
            req["crypto_key_version"]["state"]
            is kms.CryptoKeyVersion.CryptoKeyVersionState.DISABLED
        )
        assert gk.call_count == 2
        assert out == {"key_arn": KEY_PATH}

    def test_schedule_deletion(self, client):
        """Destroy is called on version 1 and a scheduled state returned."""
        with patch.object(client, "get_key", return_value={"key_arn": KEY_PATH}):
            out = client.schedule_key_deletion("mykey", 10)
        client.client.destroy_crypto_key_version.assert_called_once_with(
            name=f"{KEY_PATH}/cryptoKeyVersions/1"
        )
        assert out["state"] == "DESTROY_SCHEDULED"

    @pytest.mark.parametrize("op", ["disable_key", "schedule_key_deletion"])
    @pytest.mark.parametrize(
        "exc", [google_exceptions.PermissionDenied("d"), RuntimeError("zz")]
    )
    def test_errors(self, client, op, exc):
        """Both API-error and generic branches are translated."""
        with (
            patch.object(client, "get_key", side_effect=exc),
            pytest.raises(Exception, match="error"),
        ):
            getattr(client, op)("mykey")

    def test_api_error_branch_in_disable(self, client):
        """A GoogleAPIError raised by the SDK call hits the API-error branch."""
        client.client.update_crypto_key_version.side_effect = (
            google_exceptions.PermissionDenied("d")
        )
        with (
            patch.object(client, "get_key", return_value={"key_arn": KEY_PATH}),
            pytest.raises(Exception, match="disable key error"),
        ):
            client.disable_key("mykey")

    def test_api_error_branch_in_delete(self, client):
        """A GoogleAPIError raised by destroy hits the API-error branch."""
        client.client.destroy_crypto_key_version.side_effect = (
            google_exceptions.PermissionDenied("d")
        )
        with (
            patch.object(client, "get_key", return_value={"key_arn": KEY_PATH}),
            pytest.raises(Exception, match="schedule deletion error"),
        ):
            client.schedule_key_deletion("mykey")


class TestCrypto:
    """encrypt / decrypt / generate_data_key / sign."""

    def test_encrypt_with_context_short_id(self, client):
        """Context is JSON-encoded as AAD; a short id is expanded."""
        client.client.encrypt.return_value = SimpleNamespace(ciphertext=b"ct")
        out = client.encrypt("mykey", "hi", {"c": "1"})
        req = client.client.encrypt.call_args.kwargs["request"]
        assert req["name"] == KEY_PATH
        assert req["plaintext"] == b"hi"
        assert json.loads(req["additional_authenticated_data"]) == {"c": "1"}
        assert out == {
            "ciphertext": base64.b64encode(b"ct").decode(),
            "key_id": "mykey",
        }

    def test_encrypt_full_name_no_context(self, client):
        """A full resource name is used verbatim; no AAD without context."""
        client.client.encrypt.return_value = SimpleNamespace(ciphertext=b"ct")
        client.encrypt(KEY_PATH, "hi")
        req = client.client.encrypt.call_args.kwargs["request"]
        assert req["name"] == KEY_PATH
        assert "additional_authenticated_data" not in req
        client.client.crypto_key_path.assert_not_called()

    def test_decrypt(self, client):
        """Decrypt targets the keyring, decodes input and forwards AAD."""
        client.client.decrypt.return_value = SimpleNamespace(plaintext=b"clear")
        out = client.decrypt(base64.b64encode(b"ct").decode(), {"c": "1"})
        req = client.client.decrypt.call_args.kwargs["request"]
        assert req["ciphertext"] == b"ct"
        assert json.loads(req["additional_authenticated_data"]) == {"c": "1"}
        assert out == {"plaintext": "clear", "key_id": "embedded"}

    def test_decrypt_no_context(self, client):
        """Without context no AAD is sent."""
        client.client.decrypt.return_value = SimpleNamespace(plaintext=b"x")
        client.decrypt(base64.b64encode(b"ct").decode())
        assert (
            "additional_authenticated_data"
            not in (client.client.decrypt.call_args.kwargs["request"])
        )

    @pytest.mark.parametrize(("spec", "length"), [("AES_256", 32), ("AES_128", 16)])
    def test_generate_data_key(self, client, spec, length):
        """A random key of the right size is generated and wrapped via encrypt."""
        with patch.object(
            client, "encrypt", return_value={"ciphertext": "wrapped"}
        ) as enc:
            out = client.generate_data_key("mykey", spec, {"c": "1"})
        assert len(base64.b64decode(out["plaintext_key"])) == length
        assert out["ciphertext_key"] == "wrapped"
        assert enc.call_args.args[0] == "mykey"
        assert enc.call_args.args[1] == out["plaintext_key"]

    def test_generate_data_key_error(self, client):
        """Encrypt failure surfaces as a generate-data-key error."""
        with (
            patch.object(client, "encrypt", side_effect=Exception("enc")),
            pytest.raises(Exception, match="generate data key error: enc"),
        ):
            client.generate_data_key("mykey")

    @pytest.mark.parametrize("key_id", ["mykey", KEY_PATH])
    def test_sign(self, client, key_id):
        """Message is SHA-256 digested and signed against version 1."""
        client.client.asymmetric_sign.return_value = SimpleNamespace(signature=b"sg")
        out = client.sign(key_id, "msg", "ALGO")
        req = client.client.asymmetric_sign.call_args.kwargs["request"]
        assert req["name"] == f"{KEY_PATH}/cryptoKeyVersions/1"
        assert len(req["digest"]["sha256"]) == 32
        assert out["algorithm"] == "ALGO"
        assert out["signature"] == base64.b64encode(b"sg").decode()

    @pytest.mark.parametrize(
        ("op", "args", "label"),
        [
            ("encrypt", ("mykey", "p"), "encrypt"),
            ("decrypt", (base64.b64encode(b"c").decode(),), "decrypt"),
            ("sign", ("mykey", "m"), "sign"),
        ],
    )
    @pytest.mark.parametrize(
        "exc", [google_exceptions.PermissionDenied("d"), RuntimeError("zz")]
    )
    def test_errors(self, client, op, args, label, exc):
        """API and generic errors are translated per operation."""
        getattr(
            client.client, op if op != "sign" else "asymmetric_sign"
        ).side_effect = exc
        with pytest.raises(Exception, match=f"{label} error"):
            getattr(client, op)(*args)


class TestRotateAndConnection:
    """rotate_key, test_connection, state translation."""

    @pytest.mark.parametrize("key_id", ["mykey", KEY_PATH])
    def test_rotate(self, client, key_id):
        """A 90-day rotation period is configured and next rotation reported."""
        client.client.update_crypto_key.return_value = SimpleNamespace(
            next_rotation_time=NOW
        )
        out = client.rotate_key(key_id)
        req = client.client.update_crypto_key.call_args.kwargs["request"]
        assert req["crypto_key"]["name"] == KEY_PATH
        assert req["crypto_key"]["rotation_period"].seconds == 90 * 24 * 3600
        assert out["next_rotation"] == NOW.isoformat()
        assert out["rotation_period_days"] == 90

    @pytest.mark.parametrize(
        "exc", [google_exceptions.PermissionDenied("d"), RuntimeError("zz")]
    )
    def test_rotate_errors(self, client, exc):
        """Errors become 'rotate key error'."""
        client.client.update_crypto_key.side_effect = exc
        with pytest.raises(Exception, match="rotate key error"):
            client.rotate_key("mykey")

    def test_connection(self, client):
        """test_connection True when listing works, False on failure."""
        client.client.list_key_rings.return_value = iter([])
        assert client.test_connection() is True
        client.client.list_key_rings.side_effect = RuntimeError("down")
        assert client.test_connection() is False

    def test_translate_state(self, client, env):
        """Known states map to names; unknown states map to 'Unknown'."""
        kms, _ = env
        st = kms.CryptoKeyVersion.CryptoKeyVersionState
        assert client._translate_state(st.DESTROYED) == "Destroyed"
        assert client._translate_state(st.DESTROY_SCHEDULED) == "PendingDeletion"
        assert client._translate_state("???") == "Unknown"
