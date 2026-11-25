"""
Regression tests: entity/organization metadata field endpoints must expose an
exact field set via `@validate_response` DTOs, not raw `.as_dict()`.

security-audit: "0 @validate_response / raw ORM serialization — no response
schemas". Also fixes a stale-DTO trap: `MetadataFieldDTO` (unused by these
routes) claims `key`/`value` fields that don't exist on `metadata_fields` —
the real columns are `field_key`/`field_value`.
"""

from apps.api.models.pydantic.metadata import MetadataFieldResponse


class TestMetadataFieldResponseFieldSet:
    def test_exact_field_set(self):
        assert set(MetadataFieldResponse.model_fields) == {
            "id",
            "resource_type",
            "resource_id",
            "field_key",
            "field_type",
            "field_value",
            "is_system",
            "created_by_id",
            "village_id",
            "created_at",
            "updated_at",
        }

    def test_uses_real_column_names_not_stale_dto_names(self):
        names = set(MetadataFieldResponse.model_fields)
        assert "field_key" in names and "key" not in names
        assert "field_value" in names and "value" not in names


class TestMetadataRoutesUseValidateResponse:
    def test_all_handlers_decorated(self):
        from quart_schema.validation import QUART_SCHEMA_RESPONSE_ATTRIBUTE

        from apps.api.modules.issues.routes import metadata as mod

        for name in (
            "create_entity_metadata",
            "update_entity_metadata",
            "create_organization_metadata",
            "update_organization_metadata",
        ):
            func = getattr(mod, name)
            schemas = getattr(func, QUART_SCHEMA_RESPONSE_ATTRIBUTE, None)
            assert schemas, f"{name} is missing @validate_response wiring"
