"""Unit tests for GCPSecretManagerClient with the Secret Manager SDK mocked at the boundary."""

# flake8: noqa: E501

import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from google.api_core import exceptions as gex

from apps.api.services.secrets import gcp_client as gcp_mod
from apps.api.services.secrets.base import (
    InvalidSecretConfigException,
    SecretAccessDeniedException,
    SecretAlreadyExistsException,
    SecretNotFoundException,
    SecretProviderException,
)
from apps.api.services.secrets.gcp_client import GCPSecretManagerClient

CREDS = "apps.api.services.secrets.gcp_client.resolve_gcp_credentials"
PROJECT = "proj-1"


def _namespace(ctor):
    """Fake ``secretmanager`` module: recording request types and a client ctor.

    Using a local namespace keeps the tests independent of any other test that
    replaces ``google.cloud.secretmanager`` in ``sys.modules``.
    """
    return SimpleNamespace(
        SecretManagerServiceClient=ctor,
        ListSecretsRequest=lambda **kw: SimpleNamespace(**kw),
        ListSecretVersionsRequest=lambda **kw: SimpleNamespace(**kw),
    )


@pytest.fixture
def sdk():
    """Mocked SecretManagerServiceClient instance."""
    return MagicMock()


@pytest.fixture
def client(sdk):
    """GCPSecretManagerClient wired to the mocked SDK client (default credentials)."""
    with (
        patch(CREDS, return_value=None),
        patch.object(gcp_mod, "secretmanager", _namespace(MagicMock(return_value=sdk))),
    ):
        yield GCPSecretManagerClient({"project_id": PROJECT})


def _version(name, data=b"plain", create_time="t1"):
    """Fake access_secret_version response."""
    return SimpleNamespace(
        name=name, payload=SimpleNamespace(data=data), create_time=create_time
    )


class TestInit:
    """Construction and config validation."""

    def test_missing_project_rejected(self):
        """A config without project_id raises InvalidSecretConfigException."""
        with pytest.raises(InvalidSecretConfigException, match="project_id"):
            GCPSecretManagerClient({})

    def test_default_credentials(self, sdk):
        """Without resolved credentials the SDK client is built with defaults."""
        with (
            patch(CREDS, return_value=None),
            patch.object(
                gcp_mod,
                "secretmanager",
                _namespace(ctor := MagicMock(return_value=sdk)),
            ),
        ):
            c = GCPSecretManagerClient({"project_id": PROJECT})
        ctor.assert_called_once_with()
        assert c.client is sdk and c.project_id == PROJECT

    def test_explicit_credentials_passed(self, sdk):
        """Resolved credentials are handed to the SDK client constructor."""
        cred = object()
        with (
            patch(CREDS, return_value=cred),
            patch.object(
                gcp_mod,
                "secretmanager",
                _namespace(ctor := MagicMock(return_value=sdk)),
            ),
        ):
            GCPSecretManagerClient({"project_id": PROJECT})
        ctor.assert_called_once_with(credentials=cred)

    def test_init_failure_wrapped(self):
        """Credential resolution errors surface as SecretProviderException."""
        with patch(CREDS, side_effect=RuntimeError("bad creds")):
            with pytest.raises(SecretProviderException, match="Failed to initialize"):
                GCPSecretManagerClient({"project_id": PROJECT})


class TestConnection:
    """test_connection result mapping."""

    def test_success(self, client, sdk):
        """A successful list returns True and requests the project parent."""
        sdk.list_secrets.return_value = iter([])
        assert client.test_connection() is True
        request = sdk.list_secrets.call_args.kwargs["request"]
        assert request.parent == f"projects/{PROJECT}" and request.page_size == 1

    @pytest.mark.parametrize("exc", [gex.PermissionDenied("no"), RuntimeError("net")])
    def test_failures_return_false(self, client, sdk, exc):
        """Permission and generic errors both yield False."""
        sdk.list_secrets.side_effect = exc
        assert client.test_connection() is False


class TestGetSecret:
    """get_secret parsing and error mapping."""

    def _wire(self, sdk, data):
        """Configure sdk responses for a get_secret call."""
        sdk.access_secret_version.return_value = _version(
            f"projects/{PROJECT}/secrets/s/versions/7", data=data, create_time="v-t"
        )
        sdk.get_secret.return_value = SimpleNamespace(
            create_time="s-t", labels={"env": "prod"}, replication="auto"
        )

    def test_plain_value(self, client, sdk):
        """A plain payload is returned with version, labels and timestamps."""
        self._wire(sdk, b"hello")
        sv = client.get_secret("s")
        sdk.access_secret_version.assert_called_once_with(
            request={"name": f"projects/{PROJECT}/secrets/s/versions/latest"}
        )
        assert sv.value == "hello" and sv.version == "7" and sv.is_kv is False
        assert sv.created_at == "s-t" and sv.updated_at == "v-t"
        assert sv.metadata["labels"] == {"env": "prod"}

    def test_json_object_is_kv(self, client, sdk):
        """A JSON object payload becomes kv_pairs with no raw value."""
        self._wire(sdk, json.dumps({"a": "b"}).encode())
        sv = client.get_secret("s")
        assert sv.is_kv is True and sv.kv_pairs == {"a": "b"} and sv.value is None

    def test_json_scalar_stays_plain_and_no_labels(self, client, sdk):
        """A JSON non-object stays a plain value; empty labels become {}."""
        self._wire(sdk, b"42")
        sdk.get_secret.return_value.labels = None
        sv = client.get_secret("s")
        assert sv.value == "42" and sv.is_kv is False and sv.metadata["labels"] == {}

    def test_explicit_version_and_full_name(self, client, sdk):
        """A version and a full resource name are used verbatim."""
        self._wire(sdk, b"x")
        client.get_secret("projects/other/secrets/z", version="3")
        sdk.access_secret_version.assert_called_once_with(
            request={"name": "projects/other/secrets/z/versions/3"}
        )

    @pytest.mark.parametrize(
        "exc,expected",
        [
            (gex.NotFound("x"), SecretNotFoundException),
            (gex.PermissionDenied("x"), SecretAccessDeniedException),
            (RuntimeError("x"), SecretProviderException),
        ],
    )
    def test_error_mapping(self, client, sdk, exc, expected):
        """SDK errors map to the matching provider exception."""
        sdk.access_secret_version.side_effect = exc
        with pytest.raises(expected):
            client.get_secret("s")


class TestListSecrets:
    """list_secrets filtering and KV detection."""

    def _secret(self, name, labels=None):
        """Fake list_secrets item."""
        return SimpleNamespace(
            name=f"projects/{PROJECT}/secrets/{name}",
            create_time="t",
            labels=labels,
            replication="r",
        )

    def test_lists_with_kv_detection(self, client, sdk):
        """KV is detected via the latest version; access failures mean not KV."""
        sdk.list_secrets.return_value = iter(
            [self._secret("a", {"x": "y"}), self._secret("b"), self._secret("c")]
        )
        payloads = {
            "a": _version("v", data=b'{"k": 1}'),
            "b": _version("v", data=b"plain"),
        }

        def _access(request):
            name = request["name"].split("/")[3]
            if name not in payloads:
                raise gex.PermissionDenied("nope")
            return payloads[name]

        sdk.access_secret_version.side_effect = _access
        res = client.list_secrets()
        assert [s.path for s in res] == ["a", "b", "c"]
        assert [s.is_kv for s in res] == [True, False, False]
        assert (
            res[0].metadata["labels"] == {"x": "y"} and res[1].metadata["labels"] == {}
        )

    def test_prefix_filters_client_side(self, client, sdk):
        """The prefix is sent as a filter and non-matching names are dropped."""
        sdk.list_secrets.return_value = iter(
            [self._secret("app-one"), self._secret("other")]
        )
        sdk.access_secret_version.return_value = _version("v", data=b"x")
        res = client.list_secrets(prefix="app")
        assert [s.path for s in res] == ["app-one"]
        assert sdk.list_secrets.call_args.kwargs["request"].filter == "name:app*"

    @pytest.mark.parametrize(
        "exc,expected",
        [
            (gex.PermissionDenied("x"), SecretAccessDeniedException),
            (RuntimeError("x"), SecretProviderException),
        ],
    )
    def test_error_mapping(self, client, sdk, exc, expected):
        """Listing errors map to access-denied or provider exceptions."""
        sdk.list_secrets.side_effect = exc
        with pytest.raises(expected):
            client.list_secrets()


class TestCreateUpdate:
    """create_secret and update_secret."""

    def test_create_with_labels(self, client, sdk):
        """Create builds the secret with labels, then adds the first version."""
        sdk.create_secret.return_value = SimpleNamespace(
            name=f"projects/{PROJECT}/secrets/n", create_time="ct"
        )
        sdk.add_secret_version.return_value = _version(
            f"projects/{PROJECT}/secrets/n/versions/1"
        )
        res = client.create_secret("n", '{"a": 1}', {"labels": {"k": "v"}})
        req = sdk.create_secret.call_args.kwargs["request"]
        assert req["secret_id"] == "n" and req["secret"]["labels"] == {"k": "v"}
        vreq = sdk.add_secret_version.call_args.kwargs["request"]
        assert vreq["payload"]["data"] == b'{"a": 1}'
        assert res.version == "1" and res.is_kv is True

    def test_create_without_metadata(self, client, sdk):
        """Without metadata no labels are sent."""
        sdk.create_secret.return_value = SimpleNamespace(
            name="projects/p/secrets/n", create_time="ct"
        )
        sdk.add_secret_version.return_value = _version(
            "projects/p/secrets/n/versions/1"
        )
        client.create_secret("n", "plain")
        assert "labels" not in sdk.create_secret.call_args.kwargs["request"]["secret"]

    @pytest.mark.parametrize(
        "exc,expected",
        [
            (gex.AlreadyExists("x"), SecretAlreadyExistsException),
            (gex.PermissionDenied("x"), SecretAccessDeniedException),
            (RuntimeError("x"), SecretProviderException),
        ],
    )
    def test_create_error_mapping(self, client, sdk, exc, expected):
        """Create maps SDK errors to provider exceptions."""
        sdk.create_secret.side_effect = exc
        with pytest.raises(expected):
            client.create_secret("n", "v")

    def test_update_adds_version(self, client, sdk):
        """Update adds a new version and reports its id."""
        sdk.add_secret_version.return_value = _version(
            f"projects/{PROJECT}/secrets/n/versions/9", create_time="ut"
        )
        res = client.update_secret("n", "new")
        req = sdk.add_secret_version.call_args.kwargs["request"]
        assert req["parent"] == f"projects/{PROJECT}/secrets/n"
        assert res.version == "9" and res.updated_at == "ut" and res.is_kv is False

    @pytest.mark.parametrize(
        "exc,expected",
        [
            (gex.NotFound("x"), SecretNotFoundException),
            (gex.PermissionDenied("x"), SecretAccessDeniedException),
            (RuntimeError("x"), SecretProviderException),
        ],
    )
    def test_update_error_mapping(self, client, sdk, exc, expected):
        """Update maps SDK errors to provider exceptions."""
        sdk.add_secret_version.side_effect = exc
        with pytest.raises(expected):
            client.update_secret("n", "v")


class TestDeleteVersionsHelpers:
    """delete_secret, get_secret_versions and helpers."""

    def test_delete(self, client, sdk):
        """Delete issues the delete request for the formatted name."""
        assert client.delete_secret("n") is True
        sdk.delete_secret.assert_called_once_with(
            request={"name": f"projects/{PROJECT}/secrets/n"}
        )

    @pytest.mark.parametrize(
        "exc,expected",
        [
            (gex.NotFound("x"), SecretNotFoundException),
            (gex.PermissionDenied("x"), SecretAccessDeniedException),
            (RuntimeError("x"), SecretProviderException),
        ],
    )
    def test_delete_error_mapping(self, client, sdk, exc, expected):
        """Delete maps SDK errors to provider exceptions."""
        sdk.delete_secret.side_effect = exc
        with pytest.raises(expected):
            client.delete_secret("n")

    def test_versions(self, client, sdk):
        """Version ids are the last path component of each version name."""
        sdk.list_secret_versions.return_value = iter(
            [
                SimpleNamespace(name="a/b/versions/1"),
                SimpleNamespace(name="a/b/versions/2"),
            ]
        )
        assert client.get_secret_versions("n") == ["1", "2"]
        assert (
            sdk.list_secret_versions.call_args.kwargs["request"].parent
            == f"projects/{PROJECT}/secrets/n"
        )

    @pytest.mark.parametrize(
        "exc,expected",
        [
            (gex.NotFound("x"), SecretNotFoundException),
            (gex.PermissionDenied("x"), SecretAccessDeniedException),
            (RuntimeError("x"), SecretProviderException),
        ],
    )
    def test_versions_error_mapping(self, client, sdk, exc, expected):
        """Version listing maps SDK errors to provider exceptions."""
        sdk.list_secret_versions.side_effect = exc
        with pytest.raises(expected):
            client.get_secret_versions("n")

    @pytest.mark.parametrize(
        "given,expected",
        [
            ("s", f"projects/{PROJECT}/secrets/s"),
            ("projects/x/secrets/y", "projects/x/secrets/y"),
        ],
    )
    def test_format_secret_name(self, client, given, expected):
        """Short ids are expanded; full resource names are kept."""
        assert client._format_secret_name(given) == expected

    @pytest.mark.parametrize(
        "value,expected",
        [('{"a": 1}', True), ("[1]", False), ("x", False), (None, False)],
    )
    def test_is_json_dict(self, value, expected):
        """_is_json_dict is True only for JSON objects."""
        assert GCPSecretManagerClient._is_json_dict(value) is expected
