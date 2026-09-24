"""
Regression tests: built-in secrets endpoints must expose an exact, explicit
field set via `@validate_response` DTOs — not raw `__dict__` serialization.

security-audit: "0 @validate_response / raw ORM serialization — no response
schemas". Fails loudly if a future change widens any of these DTOs to leak
an extra field.
"""

from apps.api.models.pydantic.secrets import (
    DeleteSecretResponse,
    SecretConnectionTestResponse,
    SecretMetadataListResponse,
    SecretMetadataResponse,
    SecretValueResponse,
)


class TestSecretMetadataResponseFieldSet:
    """list_secrets / create_secret / update_secret response shape."""

    def test_exact_field_set(self):
        assert set(SecretMetadataResponse.model_fields) == {
            "name",
            "path",
            "is_kv",
            "version",
            "created_at",
            "updated_at",
            "metadata",
        }

    def test_rejects_unknown_fields(self):
        """extra='forbid' (ImmutableModel) blocks field creep at construction time."""
        import pytest
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            SecretMetadataResponse(
                name="db-password",
                path="db-password",
                is_kv=False,
                unexpected_field="leaked",
            )


class TestSecretMetadataListResponseFieldSet:
    def test_exact_field_set(self):
        assert set(SecretMetadataListResponse.model_fields) == {"secrets"}


class TestSecretValueResponseFieldSet:
    """get_secret response shape."""

    def test_exact_field_set(self):
        assert set(SecretValueResponse.model_fields) == {
            "name",
            "value",
            "is_masked",
            "is_kv",
            "kv_pairs",
            "version",
            "created_at",
            "updated_at",
            "metadata",
        }


class TestDeleteSecretResponseFieldSet:
    def test_exact_field_set(self):
        assert set(DeleteSecretResponse.model_fields) == {"success", "path"}


class TestSecretConnectionTestResponseFieldSet:
    def test_exact_field_set(self):
        assert set(SecretConnectionTestResponse.model_fields) == {
            "success",
            "provider",
        }


class TestBuiltinSecretsRoutesUseValidateResponse:
    """
    Wiring check: every route in builtin_secrets.py that returns secret data
    must carry @validate_response (not a bare jsonify(<raw dict>)).
    """

    def test_all_handlers_decorated(self):
        from quart_schema.validation import QUART_SCHEMA_RESPONSE_ATTRIBUTE

        from apps.api.modules.secrets.routes import builtin_secrets as mod

        for name in (
            "list_secrets",
            "get_secret",
            "create_secret",
            "update_secret",
            "delete_secret",
            "test_connection",
        ):
            func = getattr(mod, name)
            schemas = getattr(func, QUART_SCHEMA_RESPONSE_ATTRIBUTE, None)
            assert schemas, f"{name} is missing @validate_response wiring"
