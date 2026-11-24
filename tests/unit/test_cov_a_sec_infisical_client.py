"""Additional InfisicalClient tests: list/create/update/delete, workspace/version params, errors.

Complements test_secrets_infisical_client.py; HTTP is mocked at the ``requests``
module and the session factory is patched (the true external boundary).
"""

# flake8: noqa: E501

from unittest.mock import MagicMock, patch

import pytest
from requests.exceptions import HTTPError, RequestException

from apps.api.services.secrets.base import (
    SecretAccessDeniedException,
    SecretAlreadyExistsException,
    SecretNotFoundException,
    SecretProviderException,
)
from apps.api.services.secrets.infisical_client import InfisicalClient

MOD = "apps.api.services.secrets.infisical_client"
API = "https://app.infisical.com/api/v3"


def _http_error(status, text="err"):
    """Build an HTTPError carrying a response with the given status."""
    resp = MagicMock()
    resp.status_code = status
    resp.text = text
    return HTTPError(response=resp)


def _ok(data):
    """Response mock that succeeds and returns ``data`` as JSON."""
    r = MagicMock()
    r.json.return_value = data
    return r


def _fail(status):
    """Response mock whose raise_for_status raises an HTTPError."""
    r = MagicMock()
    r.raise_for_status.side_effect = _http_error(status)
    return r


def _make(**cfg):
    """Build an InfisicalClient with the session factory patched."""
    config = {
        "host": "https://app.infisical.com/",
        "service_token": "-".join(["fake", "tok"]),
        "environment": "prod",
    }
    config.update(cfg)
    session = MagicMock()
    session.headers = {"Authorization": "Bearer x"}
    with patch(
        "apps.api.common.providers.infisical.create_infisical_session",
        return_value=(session, API),
    ):
        return InfisicalClient(config)


@pytest.fixture
def ws_client():
    """Client configured with a workspace id and a custom secret path."""
    return _make(workspace_id="ws-1", secret_path="/app/")


@pytest.fixture
def client():
    """Client without workspace id (default path)."""
    return _make()


class TestInit:
    """Initialisation failures."""

    def test_session_factory_failure_wrapped(self):
        """A failing session factory raises SecretProviderException."""
        with patch(
            "apps.api.common.providers.infisical.create_infisical_session",
            side_effect=RuntimeError("tls"),
        ):
            with pytest.raises(SecretProviderException, match="Failed to initialize"):
                InfisicalClient({"host": "h", "service_token": "t", "environment": "e"})


class TestConnection:
    """test_connection params and generic failures."""

    def test_sends_workspace_id(self, ws_client):
        """The workspace id is included in the probe request when configured."""
        with patch(f"{MOD}.requests.get", return_value=_ok({})) as get:
            assert ws_client.test_connection() is True
        params = get.call_args.kwargs["params"]
        assert params == {
            "environment": "prod",
            "secretPath": "/app/",
            "workspaceId": "ws-1",
        }

    def test_generic_exception_false(self, client):
        """A non-HTTP exception yields False."""
        with patch(f"{MOD}.requests.get", side_effect=RuntimeError("dns")):
            assert client.test_connection() is False


class TestGetSecretParams:
    """get_secret request construction and extra error branches."""

    def test_workspace_version_and_nested_path(self, ws_client):
        """workspaceId/version params and path splitting are applied."""
        payload = {"secret": {"secretKey": "pw", "secretValue": "v", "version": 4}}
        with patch(f"{MOD}.requests.get", return_value=_ok(payload)) as get:
            sv = ws_client.get_secret("/team/db/pw", version="4")
        assert get.call_args.args[0] == f"{API}/secrets/raw/pw"
        assert get.call_args.kwargs["params"] == {
            "environment": "prod",
            "secretPath": "/team/db",
            "workspaceId": "ws-1",
            "version": "4",
        }
        assert sv.version == "4" and sv.metadata["secret_path"] == "/team/db"

    def test_non_string_value_tolerated(self, client):
        """A null secretValue is handled (TypeError path) and returned as-is."""
        payload = {"secret": {"secretKey": "k", "secretValue": None}}
        with patch(f"{MOD}.requests.get", return_value=_ok(payload)):
            sv = client.get_secret("k")
        assert sv.value is None and sv.is_kv is False

    def test_generic_exception_wrapped(self, client):
        """A non-request exception (bad JSON body) is wrapped as provider error."""
        bad = MagicMock()
        bad.json.side_effect = ValueError("not json")
        with patch(f"{MOD}.requests.get", return_value=bad):
            with pytest.raises(SecretProviderException, match="Unexpected error"):
                client.get_secret("k")


class TestListSecrets:
    """list_secrets behaviour."""

    def test_lists_filters_and_kv(self, ws_client):
        """Prefix filter, KV detection, path building and workspace param."""
        payload = {
            "secrets": [
                {"secretKey": "app-a", "secretValue": '{"k": 1}', "version": 2},
                {"secretKey": "app-b", "secretValue": "plain"},
                {"secretKey": "app-c", "secretValue": None},
                {"secretKey": "zzz", "secretValue": "x"},
            ]
        }
        with patch(f"{MOD}.requests.get", return_value=_ok(payload)) as get:
            res = ws_client.list_secrets(prefix="app-")
        assert get.call_args.kwargs["params"]["workspaceId"] == "ws-1"
        assert [s.name for s in res] == ["app-a", "app-b", "app-c"]
        assert [s.is_kv for s in res] == [True, False, False]
        assert res[0].path == "/app/app-a" and res[0].version == "2"
        assert res[1].version == "1"

    def test_no_prefix_no_workspace(self, client):
        """Without a prefix everything is listed; empty payload gives []."""
        with patch(f"{MOD}.requests.get", return_value=_ok({})) as get:
            assert client.list_secrets() == []
        assert "workspaceId" not in get.call_args.kwargs["params"]

    @pytest.mark.parametrize(
        "status,exc",
        [
            (401, SecretAccessDeniedException),
            (403, SecretAccessDeniedException),
            (500, SecretProviderException),
        ],
    )
    def test_http_errors(self, client, status, exc):
        """HTTP statuses map to the matching exception."""
        with patch(f"{MOD}.requests.get", return_value=_fail(status)):
            with pytest.raises(exc):
                client.list_secrets()

    def test_request_and_unexpected_errors(self, client):
        """Transport errors and unexpected errors are wrapped."""
        with patch(f"{MOD}.requests.get", side_effect=RequestException("net")):
            with pytest.raises(SecretProviderException, match="request error"):
                client.list_secrets()
        with patch(f"{MOD}.requests.get", side_effect=RuntimeError("x")):
            with pytest.raises(SecretProviderException, match="Unexpected error"):
                client.list_secrets()


class TestCreate:
    """create_secret behaviour."""

    def test_create_with_metadata_and_workspace(self, ws_client):
        """Comment/type metadata and workspace id are posted; result is parsed."""
        resp = _ok(
            {
                "secret": {
                    "version": 1,
                    "createdAt": "2026-01-01T00:00:00Z",
                    "workspace": "ws-1",
                    "environment": "prod",
                }
            }
        )
        with patch(f"{MOD}.requests.post", return_value=resp) as post:
            res = ws_client.create_secret(
                "/db/pw", '{"a": 1}', {"comment": "c", "type": "personal"}
            )
        body = post.call_args.kwargs["json"]
        assert body == {
            "environment": "prod",
            "secretPath": "/db",
            "secretValue": '{"a": 1}',
            "type": "personal",
            "workspaceId": "ws-1",
            "secretComment": "c",
        }
        assert res.is_kv is True and res.name == "pw" and res.created_at is not None

    def test_create_minimal(self, client):
        """Without metadata/workspace only the base payload is sent."""
        with patch(f"{MOD}.requests.post", return_value=_ok({})) as post:
            res = client.create_secret("pw", "v")
        assert post.call_args.kwargs["json"]["type"] == "shared"
        assert "workspaceId" not in post.call_args.kwargs["json"]
        assert res.is_kv is False and res.version == "1"

    @pytest.mark.parametrize(
        "status,exc",
        [
            (409, SecretAlreadyExistsException),
            (401, SecretAccessDeniedException),
            (403, SecretAccessDeniedException),
            (500, SecretProviderException),
        ],
    )
    def test_http_errors(self, client, status, exc):
        """HTTP statuses map to specific exceptions on create."""
        with patch(f"{MOD}.requests.post", return_value=_fail(status)):
            with pytest.raises(exc):
                client.create_secret("pw", "v")

    def test_request_and_unexpected_errors(self, client):
        """Transport and unexpected errors are wrapped on create."""
        with patch(f"{MOD}.requests.post", side_effect=RequestException("net")):
            with pytest.raises(SecretProviderException, match="request error"):
                client.create_secret("pw", "v")
        with patch(f"{MOD}.requests.post", side_effect=RuntimeError("x")):
            with pytest.raises(SecretProviderException, match="Unexpected error"):
                client.create_secret("pw", "v")


class TestUpdate:
    """update_secret behaviour."""

    def test_update_success(self, ws_client):
        """PATCH payload includes workspace id; response timestamps are parsed."""
        resp = _ok({"secret": {"version": 3, "updatedAt": "2026-02-02T00:00:00Z"}})
        with patch(f"{MOD}.requests.patch", return_value=resp) as patch_:
            res = ws_client.update_secret("/db/pw", '{"x": 1}')
        assert patch_.call_args.kwargs["json"]["workspaceId"] == "ws-1"
        assert res.version == "3" and res.is_kv is True and res.updated_at is not None

    def test_update_without_workspace(self, client):
        """Without a workspace id the payload omits it."""
        with patch(f"{MOD}.requests.patch", return_value=_ok({})) as patch_:
            client.update_secret("pw", "v")
        assert "workspaceId" not in patch_.call_args.kwargs["json"]

    @pytest.mark.parametrize(
        "status,exc",
        [
            (404, SecretNotFoundException),
            (401, SecretAccessDeniedException),
            (403, SecretAccessDeniedException),
            (500, SecretProviderException),
        ],
    )
    def test_http_errors(self, client, status, exc):
        """HTTP statuses map to specific exceptions on update."""
        with patch(f"{MOD}.requests.patch", return_value=_fail(status)):
            with pytest.raises(exc):
                client.update_secret("pw", "v")

    def test_request_and_unexpected_errors(self, client):
        """Transport and unexpected errors are wrapped on update."""
        with patch(f"{MOD}.requests.patch", side_effect=RequestException("net")):
            with pytest.raises(SecretProviderException, match="request error"):
                client.update_secret("pw", "v")
        with patch(f"{MOD}.requests.patch", side_effect=RuntimeError("x")):
            with pytest.raises(SecretProviderException, match="Unexpected error"):
                client.update_secret("pw", "v")


class TestDelete:
    """delete_secret behaviour not covered by the base tests."""

    def test_workspace_param(self, ws_client):
        """Delete sends workspaceId and the split path."""
        with patch(f"{MOD}.requests.delete", return_value=_ok({})) as d:
            assert ws_client.delete_secret("/a/b") is True
        assert d.call_args.kwargs["params"] == {
            "environment": "prod",
            "secretPath": "/a",
            "workspaceId": "ws-1",
        }

    @pytest.mark.parametrize(
        "status,exc",
        [
            (401, SecretAccessDeniedException),
            (403, SecretAccessDeniedException),
            (500, SecretProviderException),
        ],
    )
    def test_http_errors(self, client, status, exc):
        """Remaining HTTP statuses map to specific exceptions on delete."""
        with patch(f"{MOD}.requests.delete", return_value=_fail(status)):
            with pytest.raises(exc):
                client.delete_secret("pw")

    def test_request_and_unexpected_errors(self, client):
        """Transport and unexpected errors are wrapped on delete."""
        with patch(f"{MOD}.requests.delete", side_effect=RequestException("net")):
            with pytest.raises(SecretProviderException, match="request error"):
                client.delete_secret("pw")
        with patch(f"{MOD}.requests.delete", side_effect=RuntimeError("x")):
            with pytest.raises(SecretProviderException, match="Unexpected error"):
                client.delete_secret("pw")


class TestVersions:
    """get_secret_versions error wrapping."""

    def test_error_wrapped(self, client):
        """Lookup failures are wrapped with the versions message."""
        with patch(f"{MOD}.requests.get", return_value=_fail(404)):
            with pytest.raises(SecretProviderException, match="Error getting versions"):
                client.get_secret_versions("pw")
