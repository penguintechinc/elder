"""Unit tests for the SAML Service Provider assertion processing.

The real python3-saml (OneLogin) classes are mocked so that no crypto, IdP or
network access is required. Tests cover:

* A well-formed, authenticated response yields the expected NameID,
  attributes, session index and identity result.
* A response that fails validation (OneLogin reports errors) is rejected.
* JIT provisioning creates/maps an identity on first login, and honors the
  ``jit_provisioning_enabled`` flag and attribute->role mapping.
"""

from unittest.mock import MagicMock, patch

MODULE = "apps.api.services.sso.saml_service"

from apps.api.services.sso.saml_service import SAMLService  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _saml_idp_config(**overrides) -> dict:
    config = {
        "id": 1,
        "tenant_id": 1,
        "idp_type": "saml",
        "name": "Test IdP",
        "entity_id": "https://idp.example.com/metadata",
        "sso_url": "https://idp.example.com/sso",
        "slo_url": "https://idp.example.com/slo",
        "certificate": "MIIFAKECERT",
        "attribute_mappings": {},
        "jit_provisioning_enabled": True,
        "default_role": "reader",
    }
    config.update(overrides)
    return config


def _mock_auth(
    *,
    errors=None,
    authenticated=True,
    nameid="jane.doe@example.com",
    attributes=None,
    session_index="idx-123",
):
    """Build a MagicMock standing in for OneLogin_Saml2_Auth."""
    auth = MagicMock()
    auth.process_response.return_value = None
    auth.get_errors.return_value = errors or []
    auth.get_last_error_reason.return_value = "boom" if errors else None
    auth.is_authenticated.return_value = authenticated
    auth.get_nameid.return_value = nameid
    auth.get_attributes.return_value = attributes if attributes is not None else {}
    auth.get_session_index.return_value = session_index
    return auth


def _mock_db_no_user():
    """A current_app.db whose portal_users lookups return no existing user."""
    db = MagicMock()
    query = MagicMock()
    db.return_value = query
    query.select.return_value.first.return_value = None
    db.portal_users.insert.return_value = 42
    return db


# ---------------------------------------------------------------------------
# process_saml_response — success path
# ---------------------------------------------------------------------------
class TestProcessSamlResponseSuccess:
    def test_authenticated_response_returns_identity(self):
        attributes = {"email": ["jane.doe@example.com"], "name": ["Jane Doe"]}
        auth = _mock_auth(attributes=attributes)
        db = _mock_db_no_user()
        app = MagicMock()
        app.db = db

        with (
            patch.object(
                SAMLService, "get_idp_config", return_value=_saml_idp_config()
            ),
            patch(f"{MODULE}.OneLogin_Saml2_Auth", return_value=auth),
            patch(f"{MODULE}.current_app", app),
        ):
            result = SAMLService.process_saml_response(
                tenant_id=1,
                saml_response="b64",
                relay_state="/home",
                base_url="https://elder.test",
            )

        assert "error" not in result
        assert result["email"] == "jane.doe@example.com"
        assert result["tenant_id"] == 1
        assert result["name_id"] == "jane.doe@example.com"
        assert result["session_index"] == "idx-123"
        assert result["attributes"] == attributes
        # New user -> JIT provisioned
        assert result["jit_provisioned"] is True
        assert result["id"] == 42
        db.portal_users.insert.assert_called_once()

    def test_email_falls_back_to_nameid(self):
        auth = _mock_auth(attributes={}, nameid="bob@example.com")
        db = _mock_db_no_user()
        app = MagicMock()
        app.db = db

        with (
            patch.object(
                SAMLService, "get_idp_config", return_value=_saml_idp_config()
            ),
            patch(f"{MODULE}.OneLogin_Saml2_Auth", return_value=auth),
            patch(f"{MODULE}.current_app", app),
        ):
            result = SAMLService.process_saml_response(
                1, "b64", base_url="https://elder.test"
            )

        assert result.get("email") == "bob@example.com"


# ---------------------------------------------------------------------------
# process_saml_response — rejection paths
# ---------------------------------------------------------------------------
class TestProcessSamlResponseRejection:
    def test_no_idp_configured(self):
        with patch.object(SAMLService, "get_idp_config", return_value=None):
            result = SAMLService.process_saml_response(1, "b64", base_url="https://x")
        assert "error" in result

    def test_non_saml_idp_rejected(self):
        with patch.object(
            SAMLService,
            "get_idp_config",
            return_value=_saml_idp_config(idp_type="oidc"),
        ):
            result = SAMLService.process_saml_response(1, "b64", base_url="https://x")
        assert result["error"] == "Configured IdP is not a SAML provider"

    def test_missing_certificate_rejected(self):
        with patch.object(
            SAMLService,
            "get_idp_config",
            return_value=_saml_idp_config(certificate=None),
        ):
            result = SAMLService.process_saml_response(1, "b64", base_url="https://x")
        assert "certificate" in result["error"].lower()

    def test_validation_errors_rejected(self):
        auth = _mock_auth(errors=["invalid_response_signature"], authenticated=False)
        with (
            patch.object(
                SAMLService, "get_idp_config", return_value=_saml_idp_config()
            ),
            patch(f"{MODULE}.OneLogin_Saml2_Auth", return_value=auth),
        ):
            result = SAMLService.process_saml_response(1, "b64", base_url="https://x")

        assert result["error"] == "SAML validation failed"
        assert result["details"] == ["invalid_response_signature"]
        assert result["reason"] == "boom"

    def test_not_authenticated_rejected(self):
        auth = _mock_auth(errors=[], authenticated=False)
        with (
            patch.object(
                SAMLService, "get_idp_config", return_value=_saml_idp_config()
            ),
            patch(f"{MODULE}.OneLogin_Saml2_Auth", return_value=auth),
        ):
            result = SAMLService.process_saml_response(1, "b64", base_url="https://x")
        assert "error" in result
        assert "authenticate" in result["error"].lower()

    def test_malformed_response_raises_is_caught(self):
        auth = _mock_auth()
        auth.process_response.side_effect = Exception("bad xml")
        app = MagicMock()
        with (
            patch.object(
                SAMLService, "get_idp_config", return_value=_saml_idp_config()
            ),
            patch(f"{MODULE}.OneLogin_Saml2_Auth", return_value=auth),
            patch(f"{MODULE}.current_app", app),
        ):
            result = SAMLService.process_saml_response(1, "b64", base_url="https://x")
        assert result["error"] == "Failed to process SAML response"
        assert "bad xml" in result["detail"]

    def test_no_email_in_assertion_rejected(self):
        auth = _mock_auth(attributes={}, nameid="not-an-email")
        with (
            patch.object(
                SAMLService, "get_idp_config", return_value=_saml_idp_config()
            ),
            patch(f"{MODULE}.OneLogin_Saml2_Auth", return_value=auth),
        ):
            result = SAMLService.process_saml_response(1, "b64", base_url="https://x")
        assert result["error"] == "No email found in SAML assertion"


# ---------------------------------------------------------------------------
# JIT provisioning
# ---------------------------------------------------------------------------
class TestJitProvisioning:
    def test_jit_disabled_and_no_user_rejected(self):
        auth = _mock_auth(attributes={"email": ["new@example.com"]})
        db = _mock_db_no_user()
        app = MagicMock()
        app.db = db

        with (
            patch.object(
                SAMLService,
                "get_idp_config",
                return_value=_saml_idp_config(jit_provisioning_enabled=False),
            ),
            patch(f"{MODULE}.OneLogin_Saml2_Auth", return_value=auth),
            patch(f"{MODULE}.current_app", app),
        ):
            result = SAMLService.process_saml_response(1, "b64", base_url="https://x")

        assert "error" in result
        db.portal_users.insert.assert_not_called()

    def test_existing_user_is_matched_not_recreated(self):
        auth = _mock_auth(attributes={"email": ["jane.doe@example.com"]})
        existing = MagicMock()
        existing.id = 7
        existing.email = "jane.doe@example.com"
        existing.tenant_role = "maintainer"
        existing.global_role = None

        db = MagicMock()
        db.return_value.select.return_value.first.return_value = existing
        app = MagicMock()
        app.db = db

        with (
            patch.object(
                SAMLService, "get_idp_config", return_value=_saml_idp_config()
            ),
            patch(f"{MODULE}.OneLogin_Saml2_Auth", return_value=auth),
            patch(f"{MODULE}.current_app", app),
        ):
            result = SAMLService.process_saml_response(1, "b64", base_url="https://x")

        assert result["id"] == 7
        assert result["tenant_role"] == "maintainer"
        assert result.get("jit_provisioned") is not True
        db.portal_users.insert.assert_not_called()

    def test_jit_creates_user_with_mapped_role(self):
        # role attribute "idp-admins" maps to Elder "admin"
        config = _saml_idp_config(
            attribute_mappings={
                "email": "email",
                "full_name": "displayName",
                "role": "groups",
                "role_map": {"idp-admins": "admin"},
            }
        )
        attributes = {
            "email": ["new.admin@example.com"],
            "displayName": ["New Admin"],
            "groups": ["idp-admins"],
        }
        db = _mock_db_no_user()
        app = MagicMock()
        app.db = db

        # jit_provision_user uses current_app.db directly; patch it.
        with patch(f"{MODULE}.current_app", app):
            result = SAMLService.jit_provision_user(
                tenant_id=1,
                email="new.admin@example.com",
                idp_config=config,
                attributes=attributes,
            )

        assert result["jit_provisioned"] is True
        assert result["tenant_role"] == "admin"
        assert result["email"] == "new.admin@example.com"
        # full_name list was flattened to a scalar on insert
        _, kwargs = db.portal_users.insert.call_args
        assert kwargs["full_name"] == "New Admin"
        assert kwargs["tenant_role"] == "admin"

    def test_unknown_role_falls_back_to_default(self):
        config = _saml_idp_config(
            attribute_mappings={"role": "groups"}, default_role="reader"
        )
        attributes = {"groups": ["unmapped-group"]}
        assert SAMLService._map_role(attributes, config) == "reader"

    def test_mapped_role_passthrough_when_valid(self):
        config = _saml_idp_config(attribute_mappings={"role": "groups"})
        attributes = {"groups": ["maintainer"]}
        assert SAMLService._map_role(attributes, config) == "maintainer"
