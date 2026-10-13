"""
Regression tests: discovery sync endpoints must expose an exact field set via
`@validate_response` DTOs — not raw `.as_dict()` serialization.

security-audit: "0 @validate_response / raw ORM serialization — no response
schemas". This module additionally fixes a genuine credential leak: the
sync_configs table's `webhook_secret` and `config_json` (which holds
third-party platform tokens, e.g. GitHub `api_token`) were previously
returned in full to any discovery:read-scoped caller via `.as_dict()`.
"""

from types import SimpleNamespace

from apps.api.models.pydantic.sync import (
    SyncConfigEnvelopeResponse,
    SyncConfigListResponse,
    SyncConfigResponse,
    SyncConflictEnvelopeResponse,
    SyncConflictResponse,
    redact_config_json,
)


def _fake_row(**kwargs):
    """PyDAL Row stand-in exposing .as_dict()."""
    return SimpleNamespace(as_dict=lambda: dict(kwargs))


class TestSyncConfigResponseFieldSet:
    def test_exact_field_set(self):
        assert set(SyncConfigResponse.model_fields) == {
            "id",
            "name",
            "platform",
            "enabled",
            "sync_interval",
            "batch_fallback_enabled",
            "batch_size",
            "two_way_create",
            "webhook_enabled",
            "has_webhook_secret",
            "last_sync_at",
            "last_batch_sync_at",
            "config_json",
            "created_at",
            "updated_at",
        }

    def test_never_exposes_raw_webhook_secret(self):
        assert "webhook_secret" not in SyncConfigResponse.model_fields

    def test_from_row_converts_secret_to_boolean(self):
        row = _fake_row(
            id=1,
            name="gh-sync",
            platform="github",
            enabled=True,
            sync_interval=300,
            batch_fallback_enabled=True,
            batch_size=100,
            two_way_create=False,
            webhook_enabled=True,
            webhook_secret="super-secret-value",
            last_sync_at=None,
            last_batch_sync_at=None,
            config_json={"api_token": "ghp_leak_me_not", "elder_organization_id": 7},
            created_at=None,
            updated_at=None,
        )
        dto = SyncConfigResponse.from_row(row)
        assert dto.has_webhook_secret is True
        assert dto.config_json["api_token"] == "***REDACTED***"
        assert dto.config_json["elder_organization_id"] == 7
        assert "super-secret-value" not in dto.model_dump_json()

    def test_from_row_no_secret_configured(self):
        row = _fake_row(
            id=2,
            name="jira-sync",
            platform="jira",
            enabled=False,
            sync_interval=600,
            batch_fallback_enabled=False,
            batch_size=50,
            two_way_create=True,
            webhook_enabled=False,
            webhook_secret=None,
            last_sync_at=None,
            last_batch_sync_at=None,
            config_json=None,
            created_at=None,
            updated_at=None,
        )
        dto = SyncConfigResponse.from_row(row)
        assert dto.has_webhook_secret is False


class TestRedactConfigJson:
    def test_redacts_known_credential_keys(self):
        redacted = redact_config_json(
            {"api_token": "abc", "access_token": "def", "elder_organization_id": 1}
        )
        assert redacted["api_token"] == "***REDACTED***"
        assert redacted["access_token"] == "***REDACTED***"
        assert redacted["elder_organization_id"] == 1

    def test_passthrough_for_empty(self):
        assert redact_config_json(None) is None
        assert redact_config_json({}) == {}


class TestSyncConfigListResponseFieldSet:
    def test_exact_field_set(self):
        assert set(SyncConfigListResponse.model_fields) == {"configs"}


class TestSyncConfigEnvelopeResponseFieldSet:
    def test_exact_field_set(self):
        assert set(SyncConfigEnvelopeResponse.model_fields) == {"config"}


class TestSyncConflictResponseFieldSet:
    def test_exact_field_set(self):
        assert set(SyncConflictResponse.model_fields) == {
            "id",
            "mapping_id",
            "conflict_type",
            "elder_data",
            "external_data",
            "resolution_strategy",
            "resolved",
            "resolved_at",
            "resolved_by_id",
            "created_at",
        }


class TestSyncConflictEnvelopeResponseFieldSet:
    def test_exact_field_set(self):
        assert set(SyncConflictEnvelopeResponse.model_fields) == {"conflict"}


class TestSyncRoutesUseValidateResponse:
    def test_all_handlers_decorated(self):
        from quart_schema.validation import QUART_SCHEMA_RESPONSE_ATTRIBUTE

        from apps.api.modules.discovery.routes import sync as mod

        for name in (
            "list_sync_configs",
            "create_sync_config",
            "get_sync_config",
            "update_sync_config",
            "resolve_conflict",
        ):
            func = getattr(mod, name)
            schemas = getattr(func, QUART_SCHEMA_RESPONSE_ATTRIBUTE, None)
            assert schemas, f"{name} is missing @validate_response wiring"
