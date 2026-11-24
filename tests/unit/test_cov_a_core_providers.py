"""Unit tests for the shared cloud-provider session/client factories.

Covers ``apps/api/common/providers/{aws,gcp,vault,infisical}.py``. The cloud
SDK entry points (boto3 session, google service-account loaders) are mocked at
the boundary; ``requests.Session`` objects are real and inspected directly.
All credential-looking values are built at runtime.
"""

import json
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

from apps.api.common.providers import aws, gcp, infisical, vault


def _fake(*parts):
    """Build a throwaway credential-like value at runtime."""
    return "-".join(["fake", *parts, uuid4().hex[:6]])


class TestAwsFactory:
    """create_aws_session_and_client builds the boto3 session/client from config."""

    def _call(self, config, service="kms"):
        """Invoke the factory with boto3 patched; return (result, session ctor, session)."""
        with patch.object(aws, "boto3") as boto3:
            session = boto3.session.Session.return_value
            result = aws.create_aws_session_and_client(config, service)
        return result, boto3.session.Session, session

    def test_region_only_uses_ambient_credentials(self):
        """Without explicit keys only the region is passed (IAM role path)."""
        result, ctor, session = self._call({"region": "eu-west-1"})
        ctor.assert_called_once_with(region_name="eu-west-1")
        session.client.assert_called_once_with("kms")
        assert result is session.client.return_value

    def test_explicit_credentials_and_session_token(self):
        """Static keys plus a session token are forwarded to the session."""
        key, secret, token = _fake("id"), _fake("sec"), _fake("tok")
        _, ctor, _ = self._call(
            {
                "region": "us-east-1",
                "access_key_id": key,
                "secret_access_key": secret,
                "session_token": token,
            }
        )
        ctor.assert_called_once_with(
            region_name="us-east-1",
            aws_access_key_id=key,
            aws_secret_access_key=secret,
            aws_session_token=token,
        )

    def test_explicit_credentials_without_token(self):
        """Static keys without a token do not add aws_session_token."""
        key, secret = _fake("id"), _fake("sec")
        _, ctor, _ = self._call(
            {"region": "us-east-1", "access_key_id": key, "secret_access_key": secret}
        )
        assert "aws_session_token" not in ctor.call_args.kwargs
        assert ctor.call_args.kwargs["aws_access_key_id"] == key

    def test_partial_credentials_are_ignored(self):
        """An access key without its secret is not passed to boto3."""
        _, ctor, _ = self._call({"region": "us-east-1", "access_key_id": _fake("id")})
        ctor.assert_called_once_with(region_name="us-east-1")

    def test_endpoint_url_passed_to_client(self):
        """A custom endpoint (LocalStack) is forwarded to session.client."""
        _, _, session = self._call(
            {"region": "us-east-1", "endpoint_url": "http://localstack:4566"},
            service="secretsmanager",
        )
        session.client.assert_called_once_with(
            "secretsmanager", endpoint_url="http://localstack:4566"
        )

    def test_missing_region_raises_wrapped_error(self):
        """A config without region fails with the service name in the message."""
        with pytest.raises(Exception, match="Failed to initialize AWS kms client"):
            self._call({})

    def test_boto_failure_is_wrapped(self):
        """A boto3 failure surfaces as a wrapped initialization error."""
        with patch.object(aws, "boto3") as boto3:
            boto3.session.Session.side_effect = RuntimeError("no profile")
            with pytest.raises(Exception, match="AWS kms client: no profile"):
                aws.create_aws_session_and_client({"region": "x"}, "kms")


class TestGcpCredentials:
    """resolve_gcp_credentials picks JSON, file, or default credentials."""

    @pytest.mark.parametrize("as_string", [True, False])
    def test_credentials_json_dict_or_string(self, as_string):
        """credentials_json is accepted as a dict or a JSON string."""
        info = {"type": "service_account", "project_id": _fake("proj")}
        cfg = {"credentials_json": json.dumps(info) if as_string else info}
        with patch.object(gcp, "service_account") as sa:
            creds = gcp.resolve_gcp_credentials(cfg)
        sa.Credentials.from_service_account_info.assert_called_once_with(info)
        assert creds is sa.Credentials.from_service_account_info.return_value

    def test_credentials_file(self):
        """credentials_file is loaded through the service-account file loader."""
        with patch.object(gcp, "service_account") as sa:
            creds = gcp.resolve_gcp_credentials({"credentials_file": "/tmp/sa.json"})
        sa.Credentials.from_service_account_file.assert_called_once_with("/tmp/sa.json")
        assert creds is sa.Credentials.from_service_account_file.return_value

    def test_json_takes_precedence_over_file(self):
        """When both are present the inline JSON wins and the file is not read."""
        with patch.object(gcp, "service_account") as sa:
            gcp.resolve_gcp_credentials(
                {"credentials_json": {"a": 1}, "credentials_file": "/tmp/sa.json"}
            )
        sa.Credentials.from_service_account_file.assert_not_called()

    def test_no_credentials_returns_none(self):
        """No credential keys means application-default credentials (None)."""
        assert gcp.resolve_gcp_credentials({"project_id": "p"}) is None

    def test_invalid_json_is_wrapped(self):
        """Malformed credentials_json raises a wrapped resolution error."""
        with pytest.raises(Exception, match="Failed to resolve GCP credentials"):
            gcp.resolve_gcp_credentials({"credentials_json": "{not json"})


class TestGcpClientFactory:
    """create_gcp_client passes resolved credentials to the client factory."""

    def test_with_credentials(self):
        """Resolved credentials are handed to the factory and returned."""
        factory = MagicMock()
        with patch.object(gcp, "service_account") as sa:
            client, creds = gcp.create_gcp_client(
                {"project_id": "p", "credentials_json": {"a": 1}}, factory
            )
        expected = sa.Credentials.from_service_account_info.return_value
        factory.assert_called_once_with(credentials=expected)
        assert (client, creds) == (factory.return_value, expected)

    def test_default_credentials(self):
        """Without credentials the factory is called bare and creds is None."""
        factory = MagicMock()
        client, creds = gcp.create_gcp_client({}, factory)
        factory.assert_called_once_with()
        assert (client, creds) == (factory.return_value, None)

    def test_factory_failure_is_wrapped(self):
        """A client-construction failure is wrapped with context."""
        factory = MagicMock(side_effect=RuntimeError("quota"))
        with pytest.raises(Exception, match="Failed to initialize GCP client: quota"):
            gcp.create_gcp_client({}, factory)


class TestVaultSession:
    """create_vault_session sets auth headers, TLS verification and mTLS."""

    def test_token_and_default_verify(self):
        """Token header is set and TLS verification defaults to True."""
        token = _fake("vault")
        session = vault.create_vault_session({"url": "https://v", "token": token})
        assert session.headers["X-Vault-Token"] == token
        assert session.headers["Content-Type"] == "application/json"
        assert "X-Vault-Namespace" not in session.headers
        assert session.verify is True
        assert session.cert is None

    def test_missing_token_sends_empty_header(self):
        """A config without a token yields an empty token header, not a crash."""
        session = vault.create_vault_session({"url": "https://v"})
        assert session.headers["X-Vault-Token"] == ""

    def test_namespace_header(self):
        """The Enterprise namespace is sent when configured."""
        session = vault.create_vault_session({"token": "t", "namespace": "team-a"})
        assert session.headers["X-Vault-Namespace"] == "team-a"

    @pytest.mark.parametrize(
        "config, expected",
        [
            ({"verify_tls": "/etc/ca.pem"}, "/etc/ca.pem"),
            ({"verify_tls": "/etc/ca.pem", "ca_cert": "/other.pem"}, "/etc/ca.pem"),
            ({"verify_tls": True, "ca_cert": "/etc/ca2.pem"}, "/etc/ca2.pem"),
            ({"ca_cert": "/etc/ca3.pem"}, "/etc/ca3.pem"),
            ({"verify_tls": False, "ca_cert": "/etc/ca4.pem"}, False),
            ({"verify_tls": False}, False),
            ({"verify_tls": True}, True),
        ],
    )
    def test_tls_verification_resolution(self, config, expected):
        """verify_tls path > ca_cert (when verifying) > boolean flag."""
        session = vault.create_vault_session({"token": "t", **config})
        assert session.verify == expected

    @pytest.mark.parametrize(
        "config, expected",
        [
            ({"client_cert": "/c.pem", "client_key": "/k.pem"}, ("/c.pem", "/k.pem")),
            ({"client_cert": "/c.pem"}, None),
            ({"client_key": "/k.pem"}, None),
        ],
    )
    def test_mtls_requires_cert_and_key(self, config, expected):
        """mTLS is configured only when both cert and key are present."""
        session = vault.create_vault_session({"token": "t", **config})
        assert session.cert == expected

    def test_session_failure_is_wrapped(self):
        """A failure creating the HTTP session is wrapped with context."""
        with patch.object(vault.requests, "Session", side_effect=RuntimeError("x")):
            with pytest.raises(Exception, match="Failed to initialize Vault session"):
                vault.create_vault_session({"token": "t"})


class TestInfisicalSession:
    """create_infisical_session builds the bearer session and API base URL."""

    def test_defaults(self):
        """Default host and v3 API are used with the service token as bearer."""
        token = _fake("inf")
        session, base = infisical.create_infisical_session({"service_token": token})
        assert base == "https://app.infisical.com/api/v3"
        assert session.headers["Authorization"] == f"Bearer {token}"
        assert session.headers["Content-Type"] == "application/json"

    def test_custom_host_version_and_token_fallback(self):
        """Trailing slashes are trimmed; `token` is used when no service_token."""
        token = _fake("inf")
        session, base = infisical.create_infisical_session(
            {"host": "https://inf.example.test//", "token": token, "api_version": "v4"}
        )
        assert base == "https://inf.example.test/api/v4"
        assert session.headers["Authorization"] == f"Bearer {token}"

    def test_service_token_preferred_over_token(self):
        """service_token wins when both token keys are supplied."""
        primary, other = _fake("svc"), _fake("tok")
        session, _ = infisical.create_infisical_session(
            {"service_token": primary, "token": other}
        )
        assert session.headers["Authorization"] == f"Bearer {primary}"

    @pytest.mark.parametrize("config", [{}, {"service_token": ""}, {"token": None}])
    def test_missing_token_raises(self, config):
        """No usable token is rejected with a wrapped, explicit error."""
        with pytest.raises(Exception, match="service_token or token is required"):
            infisical.create_infisical_session(config)
