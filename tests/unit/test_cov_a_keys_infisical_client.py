"""Unit tests for the Infisical key provider client.

HTTP is mocked at the ``requests`` boundary (module-level ``requests.get/post/
patch/delete``); the cryptography path (AES-GCM) runs for real so encrypt
output is verified by decrypting it independently.
"""

import base64
import json
import os
from unittest.mock import MagicMock, patch

import pytest
import requests
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from apps.api.services.keys.infisical_client import InfisicalClient

MODULE = "apps.api.services.keys.infisical_client"
WS = "ws-1"


def _resp(status=200, body=None, text="err"):
    """Build a fake requests.Response."""
    r = MagicMock()
    r.status_code = status
    r.json.return_value = body if body is not None else {}
    r.text = text
    return r


def _secret(**over):
    """Return a stored key payload dict."""
    data = {
        "type": "symmetric",
        "algorithm": "AES-256-GCM",
        "material": base64.b64encode(os.urandom(32)).decode(),
        "enabled": True,
        "created_at": "2026-01-01T00:00:00+00:00",
        "description": "d",
    }
    data.update(over)
    return data


def _wrap(data):
    """Wrap a key payload as the Infisical single-secret response."""
    return {"secret": {"secretValue": json.dumps(data)}}


@pytest.fixture
def client():
    """Return an InfisicalClient with a stubbed session factory."""
    session = MagicMock()
    session.headers = {"Authorization": "Bearer " + "-".join(["fake", "tok"])}
    with patch(
        f"{MODULE}.create_infisical_session", return_value=(session, "http://inf/api")
    ):
        yield InfisicalClient({"workspace_id": WS})


@pytest.fixture
def http():
    """Patch the four requests verbs used by the client."""
    with (
        patch(f"{MODULE}.requests.get") as get,
        patch(f"{MODULE}.requests.post") as post,
        patch(f"{MODULE}.requests.patch") as patch_,
        patch(f"{MODULE}.requests.delete") as delete,
    ):
        yield SimpleHttp(get, post, patch_, delete)


class SimpleHttp:
    """Bundle of patched requests verbs."""

    def __init__(self, get, post, patch_, delete):
        """Store the patched callables."""
        self.get, self.post, self.patch, self.delete = get, post, patch_, delete


def test_init_requires_workspace():
    """A missing workspace_id is rejected before any session is built."""
    with pytest.raises(ValueError, match="workspace_id"):
        InfisicalClient({})


def test_init_sets_session_state(client):
    """Constructor stores base url, headers copy and workspace id."""
    assert client.base_url == "http://inf/api"
    assert client.workspace_id == WS
    assert client.headers["Authorization"].startswith("Bearer ")


class TestCreateKey:
    """create_key material generation and error handling."""

    @pytest.mark.parametrize(
        ("key_type", "algo", "material_len"),
        [
            ("symmetric", "AES-256-GCM", 32),
            ("hmac", "HMAC-SHA256", 32),
            ("asymmetric", "RSA-2048", None),
        ],
    )
    def test_stores_expected_secret(self, client, http, key_type, algo, material_len):
        """The stored secret payload has the right type, algorithm and material."""
        http.post.return_value = _resp(201, {})
        out = client.create_key("k1", key_type=key_type, tags={"t": "v"})
        stored = json.loads(http.post.call_args.kwargs["json"]["secretValue"])
        assert stored["type"] == key_type
        assert stored["algorithm"] == algo
        assert stored["tags"] == {"t": "v"}
        assert stored["enabled"] is True
        if material_len:
            assert len(base64.b64decode(stored["material"])) == material_len
        assert http.post.call_args.kwargs["json"]["workspaceId"] == WS
        assert http.post.call_args.kwargs["timeout"] == 15
        assert out["key_arn"] == f"infisical://{WS}/k1"
        assert out["state"] == "Enabled"

    def test_custom_spec_and_description(self, client, http):
        """key_spec and description override the defaults."""
        http.post.return_value = _resp(200)
        client.create_key("k", key_spec="CUSTOM", description="mine")
        payload = http.post.call_args.kwargs["json"]
        assert json.loads(payload["secretValue"])["algorithm"] == "CUSTOM"
        assert payload["secretComment"] == "mine"

    def test_default_description(self, client, http):
        """A missing description yields the Elder default in the payload."""
        http.post.return_value = _resp(200)
        client.create_key("k")
        stored = json.loads(http.post.call_args.kwargs["json"]["secretValue"])
        assert stored["description"] == "Elder managed key: k"

    def test_unsupported_type(self, client, http):
        """An unknown key_type is rejected without any HTTP call."""
        with pytest.raises(Exception, match="Unsupported key type: rsa"):
            client.create_key("k", key_type="rsa")
        http.post.assert_not_called()

    def test_api_error(self, client, http):
        """A non-2xx response surfaces the body text."""
        http.post.return_value = _resp(500, text="kaboom")
        with pytest.raises(
            Exception, match="create key error: Infisical API error: kaboom"
        ):
            client.create_key("k")

    def test_request_exception(self, client, http):
        """Network failures (timeouts) are wrapped."""
        http.post.side_effect = requests.Timeout("slow")
        with pytest.raises(Exception, match="create key error: slow"):
            client.create_key("k")


class TestGetKey:
    """get_key parsing, 404 and error handling."""

    def test_parses_stored_payload(self, client, http):
        """Stored JSON is mapped into the normalised metadata."""
        http.get.return_value = _resp(200, _wrap(_secret(enabled=False)))
        out = client.get_key("k")
        assert out["state"] == "Disabled"
        assert out["enabled"] is False
        assert out["type"] == "symmetric"
        assert out["algorithm"] == "AES-256-GCM"
        assert http.get.call_args.kwargs["params"]["workspaceId"] == WS

    def test_dict_secret_value_and_defaults(self, client, http):
        """A secretValue that is already a dict is accepted; missing fields default."""
        http.get.return_value = _resp(200, {"secret": {"secretValue": {}}})
        out = client.get_key("k")
        assert out["state"] == "Enabled"
        assert out["created_at"] == ""
        assert out["type"] == "symmetric"

    def test_missing_secret_defaults_to_empty(self, client, http):
        """A response with no secret key behaves as an empty key payload."""
        http.get.return_value = _resp(200, {})
        assert client.get_key("k")["enabled"] is True

    def test_not_found(self, client, http):
        """404 becomes a 'Key not found' error."""
        http.get.return_value = _resp(404)
        with pytest.raises(Exception, match="Key not found: nope"):
            client.get_key("nope")

    def test_api_error(self, client, http):
        """Other non-200 statuses surface the body."""
        http.get.return_value = _resp(503, text="unavail")
        with pytest.raises(
            Exception, match="get key error: Infisical API error: unavail"
        ):
            client.get_key("k")

    def test_request_exception(self, client, http):
        """Connection errors are wrapped."""
        http.get.side_effect = requests.ConnectionError("refused")
        with pytest.raises(Exception, match="get key error: refused"):
            client.get_key("k")

    def test_malformed_json_wrapped(self, client, http):
        """An unparsable secretValue is wrapped, not leaked as JSONDecodeError."""
        http.get.return_value = _resp(200, {"secret": {"secretValue": "{not json"}})
        with pytest.raises(Exception, match="get key error"):
            client.get_key("k")


class TestListKeys:
    """list_keys filtering, limit and errors."""

    def test_filters_non_keys_and_malformed(self, client, http):
        """Only key-typed secrets are listed; malformed values are skipped."""
        http.get.return_value = _resp(
            200,
            {
                "secrets": [
                    {"secretKey": "a", "secretValue": json.dumps(_secret())},
                    {"secretKey": "b", "secretValue": json.dumps({"type": "other"})},
                    {"secretKey": "c", "secretValue": "{broken"},
                    {
                        "secretKey": "d",
                        "secretValue": _secret(enabled=False, type="hmac"),
                    },
                    {"secretKey": "e", "secretValue": "[1, 2]"},
                ]
            },
        )
        out = client.list_keys()
        assert [k["key_id"] for k in out["keys"]] == ["a", "d"]
        assert out["keys"][1]["state"] == "Disabled"
        assert out["keys"][0]["key_arn"] == f"infisical://{WS}/a"
        assert out["next_token"] is None

    def test_limit_applied(self, client, http):
        """limit truncates the result."""
        http.get.return_value = _resp(
            200,
            {
                "secrets": [
                    {"secretKey": f"k{i}", "secretValue": json.dumps(_secret())}
                    for i in range(3)
                ]
            },
        )
        assert [k["key_id"] for k in client.list_keys(limit=2)["keys"]] == ["k0", "k1"]

    def test_no_secrets(self, client, http):
        """An empty/missing secrets list yields no keys."""
        http.get.return_value = _resp(200, {})
        assert client.list_keys()["keys"] == []

    def test_api_error(self, client, http):
        """A non-200 response is an error."""
        http.get.return_value = _resp(401, text="unauth")
        with pytest.raises(
            Exception, match="list keys error: Infisical API error: unauth"
        ):
            client.list_keys()

    def test_request_exception(self, client, http):
        """Timeouts are wrapped."""
        http.get.side_effect = requests.Timeout("slow")
        with pytest.raises(Exception, match="list keys error: slow"):
            client.list_keys()


class TestEnableDisable:
    """enable_key / disable_key read-modify-write behaviour."""

    def test_enable_writes_enabled_true(self, client, http):
        """enable_key PATCHes enabled=True and returns refreshed metadata."""
        http.get.return_value = _resp(200, _wrap(_secret(enabled=False)))
        http.patch.return_value = _resp(200)
        client.enable_key("k")
        sent = json.loads(http.patch.call_args.kwargs["json"]["secretValue"])
        assert sent["enabled"] is True

    def test_disable_writes_enabled_false(self, client, http):
        """disable_key PATCHes enabled=False."""
        http.get.return_value = _resp(200, _wrap(_secret()))
        http.patch.return_value = _resp(200)
        client.disable_key("k")
        sent = json.loads(http.patch.call_args.kwargs["json"]["secretValue"])
        assert sent["enabled"] is False

    @pytest.mark.parametrize("op", ["enable_key", "disable_key"])
    def test_patch_failure(self, client, http, op):
        """A failed PATCH surfaces the API error."""
        http.get.return_value = _resp(200, _wrap(_secret()))
        http.patch.return_value = _resp(400, text="bad patch")
        with pytest.raises(Exception, match="bad patch"):
            getattr(client, op)("k")

    @pytest.mark.parametrize("op", ["enable_key", "disable_key"])
    def test_fetch_failure(self, client, http, op):
        """A failed GET surfaces an error and never PATCHes."""
        http.get.return_value = _resp(500, text="down")
        with pytest.raises(Exception, match="down"):
            getattr(client, op)("k")
        http.patch.assert_not_called()

    def test_enable_second_fetch_failure(self, client, http):
        """enable_key's explicit refetch failing (after get_key succeeds) errors."""
        http.get.side_effect = [_resp(200, _wrap(_secret())), _resp(500, text="late")]
        with pytest.raises(
            Exception, match="enable key error: Infisical API error: late"
        ):
            client.enable_key("k")


class TestDelete:
    """schedule_key_deletion and cancel."""

    @pytest.mark.parametrize("status", [200, 204])
    def test_delete_success(self, client, http, status):
        """200 and 204 both count as deleted."""
        http.delete.return_value = _resp(status)
        out = client.schedule_key_deletion("k", 7)
        assert out["state"] == "Deleted"
        assert http.delete.call_args.kwargs["params"]["workspaceId"] == WS

    def test_delete_failure(self, client, http):
        """Other statuses surface the API error."""
        http.delete.return_value = _resp(403, text="forbidden")
        with pytest.raises(Exception, match="schedule deletion error: .*forbidden"):
            client.schedule_key_deletion("k")

    def test_cancel_not_supported(self, client):
        """cancel_key_deletion is unsupported."""
        with pytest.raises(NotImplementedError):
            client.cancel_key_deletion("k")


class TestEncrypt:
    """encrypt runs real AES-GCM against mocked key material."""

    def _setup(self, http, data):
        """Make every GET return the same stored key."""
        http.get.return_value = _resp(200, _wrap(data))

    @pytest.mark.parametrize("context", [None, {"tenant": "t1"}])
    def test_ciphertext_decrypts_with_stored_material(self, client, http, context):
        """Output is nonce||ciphertext decryptable with the stored key and AAD."""
        data = _secret()
        self._setup(http, data)
        out = client.encrypt("k", "hello world", context)
        raw = base64.b64decode(out["ciphertext"])
        aad = json.dumps(context).encode() if context else None
        plain = AESGCM(base64.b64decode(data["material"])).decrypt(
            raw[:12], raw[12:], aad
        )
        assert plain == b"hello world"
        assert out["key_id"] == "k"

    def test_wrong_context_fails_to_decrypt(self, client, http):
        """AAD binds the ciphertext to its context."""
        data = _secret()
        self._setup(http, data)
        raw = base64.b64decode(client.encrypt("k", "x", {"a": "1"})["ciphertext"])
        with pytest.raises(Exception):
            AESGCM(base64.b64decode(data["material"])).decrypt(
                raw[:12], raw[12:], json.dumps({"a": "2"}).encode()
            )

    def test_disabled_key_refused(self, client, http):
        """A disabled key cannot encrypt."""
        self._setup(http, _secret(enabled=False))
        with pytest.raises(Exception, match="Key is disabled"):
            client.encrypt("k", "x")

    def test_fetch_failure(self, client, http):
        """A failing material fetch (after metadata fetch) is an error."""
        http.get.side_effect = [_resp(200, _wrap(_secret())), _resp(500, text="down")]
        with pytest.raises(Exception, match="encrypt error: Infisical API error: down"):
            client.encrypt("k", "x")

    def test_bad_material_length(self, client, http):
        """Invalid key material is rejected by AES-GCM and wrapped."""
        self._setup(http, _secret(material=base64.b64encode(b"short").decode()))
        with pytest.raises(Exception, match="encrypt error"):
            client.encrypt("k", "x")


class TestDataKeyAndUnsupported:
    """generate_data_key plus not-implemented operations."""

    @pytest.mark.parametrize(("spec", "length"), [("AES_256", 32), ("AES_128", 16)])
    def test_generate_data_key(self, client, spec, length):
        """Key length follows the spec and wrapping is delegated to encrypt."""
        with patch.object(
            client, "encrypt", return_value={"ciphertext": "wrapped"}
        ) as enc:
            out = client.generate_data_key("k", spec, {"c": "1"})
        assert len(base64.b64decode(out["plaintext_key"])) == length
        assert out["ciphertext_key"] == "wrapped"
        assert enc.call_args.args[2] == {"c": "1"}

    def test_generate_data_key_error(self, client):
        """An encrypt failure is wrapped."""
        with (
            patch.object(client, "encrypt", side_effect=Exception("enc")),
            pytest.raises(Exception, match="generate data key error: enc"),
        ):
            client.generate_data_key("k")

    @pytest.mark.parametrize(
        ("op", "args"),
        [
            ("decrypt", ("ct",)),
            ("sign", ("k", "m")),
            ("verify", ("k", "m", "s", "A")),
        ],
    )
    def test_not_implemented(self, client, op, args):
        """Unsupported operations fail loudly."""
        with pytest.raises(NotImplementedError):
            getattr(client, op)(*args)


class TestRotate:
    """rotate_key replaces key material."""

    def test_rotates_material(self, client, http):
        """New material differs from the old and rotated_at is recorded."""
        data = _secret()
        http.get.return_value = _resp(200, _wrap(data))
        http.patch.return_value = _resp(200)
        out = client.rotate_key("k")
        sent = json.loads(http.patch.call_args.kwargs["json"]["secretValue"])
        assert sent["material"] != data["material"]
        assert len(base64.b64decode(sent["material"])) == 32
        assert out["rotated_at"] == sent["rotated_at"]

    def test_fetch_failure(self, client, http):
        """A failed second GET aborts rotation before any write."""
        http.get.side_effect = [_resp(200, _wrap(_secret())), _resp(500, text="down")]
        with pytest.raises(
            Exception, match="rotate key error: Infisical API error: down"
        ):
            client.rotate_key("k")
        http.patch.assert_not_called()

    def test_patch_failure(self, client, http):
        """A failed PATCH is surfaced."""
        http.get.return_value = _resp(200, _wrap(_secret()))
        http.patch.return_value = _resp(500, text="nope")
        with pytest.raises(
            Exception, match="rotate key error: Infisical API error: nope"
        ):
            client.rotate_key("k")


class TestConnection:
    """test_connection result mapping."""

    @pytest.mark.parametrize(("status", "expected"), [(200, True), (401, False)])
    def test_status_mapping(self, client, http, status, expected):
        """Only HTTP 200 counts as connected; a short timeout is used."""
        http.get.return_value = _resp(status)
        assert client.test_connection() is expected
        assert http.get.call_args.kwargs["timeout"] == 5

    def test_exception_is_false(self, client, http):
        """Network failures yield False rather than raising."""
        http.get.side_effect = requests.Timeout("slow")
        assert client.test_connection() is False
