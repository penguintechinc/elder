"""
Regression tests: license policy endpoints must expose an exact field set via
`@validate_response` DTOs (license_policies.py already used LicensePolicyDTO
internally — this locks that contract in with quart-schema enforcement
instead of a bare `ApiResponse.success/created(dto.to_dict())`).

security-audit: "0 @validate_response / raw ORM serialization — no response
schemas".
"""

from apps.api.models.pydantic.license_policy import (
    LicensePolicyDTO,
    LicensePolicyListResponse,
)


class TestLicensePolicyDTOFieldSet:
    def test_exact_field_set(self):
        assert set(LicensePolicyDTO.model_fields) == {
            "id",
            "tenant_id",
            "organization_id",
            "village_id",
            "name",
            "description",
            "allowed_licenses",
            "denied_licenses",
            "action",
            "is_active",
            "created_at",
            "updated_at",
        }


class TestLicensePolicyListResponseFieldSet:
    def test_exact_field_set(self):
        assert set(LicensePolicyListResponse.model_fields) == {
            "items",
            "total",
            "page",
            "per_page",
            "pages",
        }


class TestLicensePoliciesRoutesUseValidateResponse:
    def test_all_handlers_decorated(self):
        from quart_schema.validation import QUART_SCHEMA_RESPONSE_ATTRIBUTE

        from apps.api.modules.sbom.routes import license_policies as mod

        for name in ("list_policies", "create_policy", "get_policy", "update_policy"):
            func = getattr(mod, name)
            schemas = getattr(func, QUART_SCHEMA_RESPONSE_ATTRIBUTE, None)
            assert schemas, f"{name} is missing @validate_response wiring"
