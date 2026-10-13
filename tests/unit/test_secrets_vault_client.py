"""Unit tests for the Hashicorp Vault secrets client.

The Vault HTTP session is mocked (via ``create_vault_session``), so no live
Vault is required. Covers config validation, the ``_make_request`` status-code
contract, KV v1/v2 path building, and the get/list/create/update/delete/
versions operations plus their error paths.
"""

from unittest.mock import MagicMock, patch

import pytest
import requests

from apps.api.services.secrets.base import (
    InvalidSecretConfigException,
    SecretAccessDeniedException,
    SecretAlreadyExistsException,
    SecretNotFoundException,
    SecretProviderException,
)
from apps.api.services.secrets.vault_client import HashicorpVaultClient


def _make_response(status_code=200, json_data=None, text="{}"):
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_data if json_data is not None else {}
    resp.text = text
    return resp


def _make_client(config=None, kv_version="2"):
    """Build a client with a mocked Vault session."""
    cfg = {
        "url": "https://vault.example.com:8200",
        "token": "s.test",
        "kv_version": kv_version,
    }
    if config:
        cfg.update(config)
    fake_session = MagicMock()
    with patch(
        "apps.api.common.providers.vault.create_vault_session",
        return_value=fake_session,
    ):
        client = HashicorpVaultClient(cfg)
    return client, fake_session


class TestValidateConfig:
    def test_missing_fields_raise(self):
        with pytest.raises(InvalidSecretConfigException, match="Missing required"):
            with patch(
                "apps.api.common.providers.vault.create_vault_session",
                return_value=MagicMock(),
            ):
                HashicorpVaultClient({"url": "https://v:8200"})  # no token

    def test_bad_url_scheme_raises(self):
        with pytest.raises(InvalidSecretConfigException, match="must start with"):
            with patch(
                "apps.api.common.providers.vault.create_vault_session",
                return_value=MagicMock(),
            ):
                HashicorpVaultClient({"url": "vault:8200", "token": "s.x"})

    def test_init_sets_defaults(self):
        client, _ = _make_client()
        assert client.base_url == "https://vault.example.com:8200"
        assert client.mount_point == "secret"
        assert client.kv_version == "2"


class TestMakeRequest:
    @pytest.mark.parametrize(
        "status,exc",
        [
            (404, SecretNotFoundException),
            (403, SecretAccessDeniedException),
            (409, SecretAlreadyExistsException),
        ],
    )
    def test_status_codes_map_to_exceptions(self, status, exc):
        client, session = _make_client()
        session.request.return_value = _make_response(status_code=status)
        with pytest.raises(exc):
            client._make_request("GET", "/v1/secret/data/x")

    def test_generic_4xx_raises_provider_exception(self):
        client, session = _make_client()
        session.request.return_value = _make_response(
            status_code=500,
            json_data={"errors": ["internal boom"]},
            text='{"errors":["internal boom"]}',
        )
        with pytest.raises(SecretProviderException, match="internal boom"):
            client._make_request("GET", "/v1/secret/data/x")

    def test_success_returns_json(self):
        client, session = _make_client()
        session.request.return_value = _make_response(json_data={"ok": True})
        assert client._make_request("GET", "/v1/x") == {"ok": True}

    def test_empty_body_returns_empty_dict(self):
        client, session = _make_client()
        session.request.return_value = _make_response(text="")
        assert client._make_request("GET", "/v1/x") == {}

    def test_request_exception_wrapped(self):
        client, session = _make_client()
        session.request.side_effect = requests.exceptions.ConnectionError("down")
        with pytest.raises(SecretProviderException, match="request failed"):
            client._make_request("GET", "/v1/x")


class TestKvPath:
    def test_v2_paths(self):
        client, _ = _make_client(kv_version="2")
        assert client._get_kv_path("foo", "data") == "/v1/secret/data/foo"
        assert client._get_kv_path("foo", "metadata") == "/v1/secret/metadata/foo"
        assert client._get_kv_path("foo", "destroy") == "/v1/secret/destroy/foo"
        assert client._get_kv_path("/foo", "delete") == "/v1/secret/data/foo"

    def test_v1_path(self):
        client, _ = _make_client(kv_version="1")
        assert client._get_kv_path("foo") == "/v1/secret/foo"


class TestTestConnection:
    def test_success(self):
        client, session = _make_client()
        session.request.return_value = _make_response(json_data={"initialized": True})
        assert client.test_connection() is True

    def test_failure_returns_false(self):
        client, session = _make_client()
        session.request.side_effect = requests.exceptions.ConnectionError("x")
        assert client.test_connection() is False


class TestGetSecret:
    def test_kv2_returns_kv_pairs_and_version(self):
        client, session = _make_client(kv_version="2")
        session.request.return_value = _make_response(
            json_data={
                "data": {
                    "data": {"username": "admin", "password": "p"},
                    "metadata": {
                        "version": 3,
                        "created_time": "2026-01-01T00:00:00Z",
                        "destroyed": False,
                    },
                }
            }
        )
        sv = client.get_secret("app/db")
        assert sv.is_kv is True
        assert sv.kv_pairs == {"username": "admin", "password": "p"}
        assert sv.version == "3"
        assert sv.created_at is not None

    def test_kv1_returns_data(self):
        client, session = _make_client(kv_version="1")
        session.request.return_value = _make_response(json_data={"data": {"k": "v"}})
        sv = client.get_secret("app/db")
        assert sv.kv_pairs == {"k": "v"}
        assert sv.version == "1"

    def test_not_found_propagates(self):
        client, session = _make_client()
        session.request.return_value = _make_response(status_code=404)
        with pytest.raises(SecretNotFoundException):
            client.get_secret("missing")


class TestCreateUpdate:
    def test_create_kv2_parses_json_value_and_sets_cas(self):
        client, session = _make_client(kv_version="2")
        session.request.return_value = _make_response(
            json_data={"data": {"version": 1, "created_time": "2026-01-01T00:00:00Z"}}
        )
        meta = client.create_secret("app/x", '{"k": "v"}')
        assert meta.version == "1"
        # Payload wraps data and sets cas=0 for create-only.
        payload = session.request.call_args.kwargs["json"]
        assert payload["data"] == {"k": "v"}
        assert payload["options"]["cas"] == 0

    def test_create_non_json_value_wrapped(self):
        client, session = _make_client(kv_version="2")
        session.request.return_value = _make_response(
            json_data={"data": {"version": 1}}
        )
        client.create_secret("app/x", "plain-string")
        payload = session.request.call_args.kwargs["json"]
        assert payload["data"] == {"value": "plain-string"}

    def test_create_already_exists_propagates(self):
        client, session = _make_client(kv_version="2")
        session.request.return_value = _make_response(status_code=409)
        with pytest.raises(SecretAlreadyExistsException):
            client.create_secret("app/x", "v")

    def test_update_kv1_payload_is_raw(self):
        client, session = _make_client(kv_version="1")
        session.request.return_value = _make_response(json_data={})
        client.update_secret("app/x", '{"k": "v"}')
        payload = session.request.call_args.kwargs["json"]
        assert payload == {"k": "v"}


class TestDeleteAndVersions:
    def test_delete_kv1_is_permanent(self):
        client, session = _make_client(kv_version="1")
        session.request.return_value = _make_response(json_data={})
        assert client.delete_secret("app/x") is True
        method, url = session.request.call_args[0]
        assert method == "DELETE"

    def test_get_versions_kv1(self):
        client, _ = _make_client(kv_version="1")
        assert client.get_secret_versions("app/x") == ["1"]

    def test_get_versions_kv2_filters_destroyed(self):
        client, session = _make_client(kv_version="2")
        session.request.return_value = _make_response(
            json_data={
                "data": {
                    "versions": {
                        "1": {"destroyed": True},
                        "2": {"deletion_time": "2026-01-01T00:00:00Z"},
                        "3": {"destroyed": False},
                        "4": {"destroyed": False},
                    }
                }
            }
        )
        versions = client.get_secret_versions("app/x")
        assert versions == ["4", "3"]  # destroyed/deleted filtered, desc order

    def test_delete_kv2_soft_delete_uses_latest_version(self):
        client, session = _make_client(kv_version="2")
        # get_secret_versions is called internally; make metadata then delete succeed.
        session.request.side_effect = [
            _make_response(
                json_data={"data": {"versions": {"5": {"destroyed": False}}}}
            ),
            _make_response(json_data={}),
        ]
        assert client.delete_secret("app/x", force=False) is True


class TestListSecrets:
    def test_list_kv2_skips_directories(self):
        client, session = _make_client(kv_version="2")
        session.request.side_effect = [
            _make_response(json_data={"data": {"keys": ["a", "sub/"]}}),
            _make_response(
                json_data={
                    "data": {
                        "current_version": 2,
                        "created_time": "2026-01-01T00:00:00Z",
                    }
                }
            ),
        ]
        metas = client.list_secrets()
        assert len(metas) == 1
        assert metas[0].name == "a"
        assert metas[0].version == "2"

    def test_list_empty_path_not_found_returns_empty(self):
        client, session = _make_client(kv_version="2")
        session.request.return_value = _make_response(status_code=404)
        assert client.list_secrets() == []
