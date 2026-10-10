"""Unit tests for the Infisical secrets client.

HTTP is mocked at the ``requests`` module level and the session factory is
patched, so no live Infisical instance is required. Covers config validation,
the HTTP status→exception contract, KV-vs-plain secret parsing, delete, version
lookup, and the pure timestamp/JSON helpers.
"""

import json
from unittest.mock import MagicMock, patch

import pytest
from requests.exceptions import HTTPError, RequestException

from apps.api.services.secrets.base import (
    InvalidSecretConfigException,
    SecretAccessDeniedException,
    SecretNotFoundException,
    SecretProviderException,
)
from apps.api.services.secrets.infisical_client import InfisicalClient

MODULE = "apps.api.services.secrets.infisical_client"


def _http_error(status_code, text="err"):
    resp = MagicMock()
    resp.status_code = status_code
    resp.text = text
    return HTTPError(response=resp)


def _ok_response(json_data):
    resp = MagicMock()
    resp.raise_for_status = MagicMock()
    resp.json.return_value = json_data
    return resp


def _err_response(status_code, text="err"):
    resp = MagicMock()
    resp.raise_for_status = MagicMock(side_effect=_http_error(status_code, text))
    return resp


def _make_client(config=None):
    cfg = {
        "host": "https://app.infisical.com",
        "service_token": "st.a.b.c",
        "environment": "prod",
    }
    if config:
        cfg.update(config)
    session = MagicMock()
    session.headers = {"Authorization": "Bearer st.a.b.c"}
    with patch(
        "apps.api.common.providers.infisical.create_infisical_session",
        return_value=(session, "https://app.infisical.com/api/v3"),
    ):
        return InfisicalClient(cfg)


class TestValidateConfig:
    def test_missing_fields_raise(self):
        with pytest.raises(InvalidSecretConfigException, match="Missing required"):
            with patch(
                "apps.api.common.providers.infisical.create_infisical_session",
                return_value=(MagicMock(headers={}), "x"),
            ):
                InfisicalClient({"host": "h"})  # no service_token/environment

    def test_init_sets_attributes(self):
        client = _make_client({"secret_path": "/app", "workspace_id": "ws1"})
        assert client.environment == "prod"
        assert client.secret_path == "/app"
        assert client.workspace_id == "ws1"
        assert client.api_base.endswith("/api/v3")


class TestTestConnection:
    def test_success(self):
        client = _make_client()
        with patch(f"{MODULE}.requests.get", return_value=_ok_response({})):
            assert client.test_connection() is True

    def test_401_returns_false(self):
        client = _make_client()
        with patch(f"{MODULE}.requests.get", return_value=_err_response(401)):
            assert client.test_connection() is False

    def test_other_http_error_returns_false(self):
        client = _make_client()
        with patch(f"{MODULE}.requests.get", return_value=_err_response(500)):
            assert client.test_connection() is False


class TestGetSecret:
    def test_plain_value(self):
        client = _make_client()
        resp = _ok_response(
            {
                "secret": {
                    "secretKey": "API_KEY",
                    "secretValue": "plain-token",
                    "version": 2,
                    "createdAt": "2026-01-01T00:00:00Z",
                }
            }
        )
        with patch(f"{MODULE}.requests.get", return_value=resp):
            sv = client.get_secret("app/API_KEY")
        assert sv.value == "plain-token"
        assert sv.is_kv is False
        assert sv.version == "2"
        assert sv.created_at is not None

    def test_json_value_becomes_kv(self):
        client = _make_client()
        resp = _ok_response(
            {
                "secret": {
                    "secretKey": "DB",
                    "secretValue": json.dumps({"user": "u", "pass": "p"}),
                    "version": 1,
                }
            }
        )
        with patch(f"{MODULE}.requests.get", return_value=resp):
            sv = client.get_secret("DB")
        assert sv.is_kv is True
        assert sv.kv_pairs == {"user": "u", "pass": "p"}
        assert sv.value is None

    @pytest.mark.parametrize(
        "status,exc",
        [
            (404, SecretNotFoundException),
            (401, SecretAccessDeniedException),
            (403, SecretAccessDeniedException),
            (500, SecretProviderException),
        ],
    )
    def test_http_errors_map(self, status, exc):
        client = _make_client()
        with patch(f"{MODULE}.requests.get", return_value=_err_response(status)):
            with pytest.raises(exc):
                client.get_secret("app/x")

    def test_request_exception_wrapped(self):
        client = _make_client()
        with patch(
            f"{MODULE}.requests.get", side_effect=RequestException("conn reset")
        ):
            with pytest.raises(SecretProviderException, match="request error"):
                client.get_secret("app/x")


class TestDeleteSecret:
    def test_success(self):
        client = _make_client()
        with patch(f"{MODULE}.requests.delete", return_value=_ok_response({})):
            assert client.delete_secret("app/x") is True

    def test_not_found(self):
        client = _make_client()
        with patch(f"{MODULE}.requests.delete", return_value=_err_response(404)):
            with pytest.raises(SecretNotFoundException):
                client.delete_secret("app/x")


class TestVersionsAndHelpers:
    def test_get_secret_versions_returns_current(self):
        client = _make_client()
        resp = _ok_response(
            {"secret": {"secretKey": "x", "secretValue": "v", "version": 7}}
        )
        with patch(f"{MODULE}.requests.get", return_value=resp):
            assert client.get_secret_versions("x") == ["7"]

    @pytest.mark.parametrize(
        "ts,ok",
        [
            ("2026-01-01T00:00:00Z", True),
            ("not-a-date", False),
            (None, False),
        ],
    )
    def test_parse_timestamp(self, ts, ok):
        result = InfisicalClient._parse_timestamp(ts)
        assert (result is not None) is ok

    @pytest.mark.parametrize(
        "value,expected",
        [
            ('{"a": 1}', True),
            ("[1,2,3]", False),
            ("plain", False),
            ("123", False),
        ],
    )
    def test_is_json_dict(self, value, expected):
        assert InfisicalClient._is_json_dict(value) is expected
