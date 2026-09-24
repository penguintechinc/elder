"""
Regression tests: audit retention policy endpoints must expose an exact field
set via `@validate_response` DTOs, not raw `.as_dict()` serialization.

security-audit: "0 @validate_response / raw ORM serialization — no response
schemas".
"""

from apps.api.models.pydantic.audit import (
    AuditRetentionPolicyListResponse,
    AuditRetentionPolicyResponse,
)


class TestAuditRetentionPolicyResponseFieldSet:
    def test_exact_field_set(self):
        assert set(AuditRetentionPolicyResponse.model_fields) == {
            "id",
            "resource_type",
            "retention_days",
            "enabled",
            "created_at",
            "updated_at",
        }

    def test_no_nonexistent_columns_leaked(self):
        """description/event_types are referenced by the route's insert/update
        kwargs but do not exist on the audit_retention_policies table."""
        assert "description" not in AuditRetentionPolicyResponse.model_fields
        assert "event_types" not in AuditRetentionPolicyResponse.model_fields


class TestAuditRetentionPolicyListResponseFieldSet:
    def test_exact_field_set(self):
        assert set(AuditRetentionPolicyListResponse.model_fields) == {
            "policies",
            "count",
        }


class TestAuditRoutesUseValidateResponse:
    def test_all_handlers_decorated(self):
        from quart_schema.validation import QUART_SCHEMA_RESPONSE_ATTRIBUTE

        from apps.api.api.v1 import audit as mod

        for name in (
            "list_retention_policies",
            "get_retention_policy",
            "create_retention_policy",
            "update_retention_policy",
        ):
            func = getattr(mod, name)
            schemas = getattr(func, QUART_SCHEMA_RESPONSE_ATTRIBUTE, None)
            assert schemas, f"{name} is missing @validate_response wiring"
