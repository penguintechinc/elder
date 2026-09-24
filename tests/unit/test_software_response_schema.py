"""
Regression tests: software tracking endpoints must expose an exact field set
via `@validate_response` DTOs, not raw `.as_dict()` serialization.

security-audit: "0 @validate_response / raw ORM serialization — no response
schemas".
"""

from apps.api.models.pydantic.software import SoftwareDTO, SoftwareListResponse


class TestSoftwareDTOFieldSet:
    def test_exact_field_set(self):
        assert set(SoftwareDTO.model_fields) == {
            "id",
            "tenant_id",
            "name",
            "description",
            "organization_id",
            "purchasing_poc_id",
            "license_url",
            "version",
            "business_purpose",
            "software_type",
            "seats",
            "cost_monthly",
            "renewal_date",
            "vendor",
            "support_contact",
            "notes",
            "tags",
            "is_active",
            "created_at",
            "updated_at",
            "village_id",
        }


class TestSoftwareListResponseFieldSet:
    def test_exact_field_set(self):
        assert set(SoftwareListResponse.model_fields) == {
            "items",
            "total",
            "page",
            "per_page",
            "pages",
        }


class TestSoftwareRoutesUseValidateResponse:
    def test_all_handlers_decorated(self):
        from quart_schema.validation import QUART_SCHEMA_RESPONSE_ATTRIBUTE

        from apps.api.modules.sbom.routes import software as mod

        for name in (
            "list_software",
            "create_software",
            "get_software",
            "update_software",
        ):
            func = getattr(mod, name)
            schemas = getattr(func, QUART_SCHEMA_RESPONSE_ATTRIBUTE, None)
            assert schemas, f"{name} is missing @validate_response wiring"
