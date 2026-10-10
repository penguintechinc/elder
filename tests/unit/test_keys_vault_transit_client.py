"""Unit tests for the Vault Transit key-management client.

The Vault HTTP session is mocked, so no live Vault is required. Covers key
create/get/list, encrypt/decrypt (incl. the base64 round-trip and the Transit
decrypt limitation), data-key generation, rotation, and ``_make_request`` error
handling.
"""

import base64
from unittest.mock import MagicMock, patch

import pytest
import requests

from apps.api.services.keys.vault_client import VaultTransitClient


def _make_response(status_code=200, json_data=None, text="{}"):
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_data if json_data is not None else {}
    resp.text = text
    return resp


def _make_client():
    cfg = {"url": "https://vault.example.com:8200/", "token": "s.test"}
    fake_session = MagicMock()
    with patch(
        "apps.api.common.providers.vault.create_vault_session",
        return_value=fake_session,
    ):
        client = VaultTransitClient(cfg)
    return client, fake_session


class TestInitAndRequest:
    def test_init_defaults_mount_point(self):
        client, _ = _make_client()
        assert client.base_url == "https://vault.example.com:8200"
        assert client.mount_point == "transit"

    def test_make_request_404_raises(self):
        client, session = _make_client()
        session.request.return_value = _make_response(status_code=404)
        with pytest.raises(Exception, match="not found"):
            client._make_request("GET", "/v1/transit/keys/x")

    def test_make_request_error_extracts_message(self):
        client, session = _make_client()
        session.request.return_value = _make_response(
            status_code=400,
            json_data={"errors": ["bad key type"]},
            text='{"errors":["bad key type"]}',
        )
        with pytest.raises(Exception, match="bad key type"):
            client._make_request("POST", "/v1/transit/keys/x")

    def test_make_request_connection_error_wrapped(self):
        client, session = _make_client()
        session.request.side_effect = requests.exceptions.ConnectionError("down")
        with pytest.raises(Exception, match="request failed"):
            client._make_request("GET", "/v1/transit/keys/x")


class TestCreateGetList:
    def test_create_key_maps_type_and_fetches_info(self):
        client, session = _make_client()
        session.request.side_effect = [
            _make_response(json_data={}),  # POST create
            _make_response(  # GET key info
                json_data={"data": {"type": "aes256-gcm96", "latest_version": 1}}
            ),
        ]
        result = client.create_key("mykey", key_type="symmetric")
        assert result["key_id"] == "mykey"
        assert result["key_arn"] == "vault://transit/keys/mykey"
        assert result["key_type"] == "symmetric"
        # First POST payload used the mapped vault type.
        create_payload = session.request.call_args_list[0].kwargs["json"]
        assert create_payload["type"] == "aes256-gcm96"

    def test_create_asymmetric_default_spec(self):
        client, session = _make_client()
        session.request.side_effect = [
            _make_response(json_data={}),
            _make_response(json_data={"data": {"type": "rsa-2048"}}),
        ]
        client.create_key("k", key_type="asymmetric")
        assert session.request.call_args_list[0].kwargs["json"]["type"] == "rsa-2048"

    def test_get_key_maps_state(self):
        client, session = _make_client()
        session.request.return_value = _make_response(
            json_data={
                "data": {
                    "type": "aes256-gcm96",
                    "deletion_allowed": True,
                    "supports_encryption": True,
                }
            }
        )
        info = client.get_key("mykey")
        assert info["state"] == "pending_deletion"
        assert info["supports_encryption"] is True

    def test_list_keys_enriches_each(self):
        client, session = _make_client()
        session.request.side_effect = [
            _make_response(json_data={"data": {"keys": ["k1", "k2"]}}),  # LIST
            _make_response(json_data={"data": {"type": "aes256-gcm96"}}),  # get k1
            _make_response(json_data={"data": {"type": "rsa-2048"}}),  # get k2
        ]
        result = client.list_keys()
        assert len(result["keys"]) == 2
        assert result["next_token"] is None


class TestEncryptDecrypt:
    def test_encrypt_base64_encodes_plaintext(self):
        client, session = _make_client()
        session.request.return_value = _make_response(
            json_data={"data": {"ciphertext": "vault:v1:abc"}}
        )
        out = client.encrypt("mykey", "hello")
        assert out["ciphertext"] == "vault:v1:abc"
        sent = session.request.call_args.kwargs["json"]["plaintext"]
        assert base64.b64decode(sent).decode() == "hello"

    def test_encrypt_includes_context(self):
        client, session = _make_client()
        session.request.return_value = _make_response(
            json_data={"data": {"ciphertext": "vault:v1:x"}}
        )
        client.encrypt("mykey", "hi", context={"org": "1"})
        assert "context" in session.request.call_args.kwargs["json"]

    def test_decrypt_raises_due_to_transit_limitation(self):
        client, _ = _make_client()
        with pytest.raises(Exception, match="key_id"):
            client.decrypt("vault:v1:abc")

    def test_decrypt_with_key_round_trips_base64(self):
        client, session = _make_client()
        pt_b64 = base64.b64encode(b"secret-data").decode()
        session.request.return_value = _make_response(
            json_data={"data": {"plaintext": pt_b64}}
        )
        out = client.decrypt_with_key("mykey", "vault:v1:abc")
        assert out["plaintext"] == "secret-data"
        assert out["key_id"] == "mykey"

    def test_generate_data_key(self):
        client, session = _make_client()
        session.request.return_value = _make_response(
            json_data={"data": {"plaintext": "pk", "ciphertext": "ck"}}
        )
        out = client.generate_data_key("mykey", key_spec="AES_256")
        assert out["plaintext_key"] == "pk"
        assert out["ciphertext_key"] == "ck"
        assert session.request.call_args.kwargs["json"]["bits"] == 256


class TestRotateAndConnection:
    def test_rotate_key(self):
        client, session = _make_client()
        session.request.return_value = _make_response(json_data={})
        result = client.rotate_key("mykey")
        # First call is the rotate POST (a subsequent get_key GET follows).
        method, url = session.request.call_args_list[0][0]
        assert method == "POST"
        assert url.endswith("/keys/mykey/rotate")
        assert result["key_id"] == "mykey"

    def test_test_connection_success(self):
        client, session = _make_client()
        session.request.return_value = _make_response(json_data={"data": {"keys": []}})
        assert client.test_connection() is True

    def test_test_connection_failure(self):
        client, session = _make_client()
        session.request.side_effect = requests.exceptions.ConnectionError("x")
        assert client.test_connection() is False
