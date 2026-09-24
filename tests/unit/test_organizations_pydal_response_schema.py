"""
Regression tests: Organization Unit (OU) endpoints
(apps/api/modules/infrastructure/routes/organizations_pydal.py — the LIVE
/api/v1/organizations blueprint) must expose an exact field set via
`@validate_response` DTOs, not raw `.as_dict()`/`asdict()` serialization.

security-audit: "0 @validate_response / raw ORM serialization — no response
schemas".
"""

import dataclasses

from apps.api.models.dataclasses import OrganizationDTO
from apps.api.modules.infrastructure.routes.organizations_pydal import (
    OrganizationListResponse,
)


def _field_names(dc) -> set[str]:
    return {f.name for f in dataclasses.fields(dc)}


class TestOrganizationDTOFieldSet:
    def test_exact_field_set(self):
        assert _field_names(OrganizationDTO) == {
            "id",
            "name",
            "description",
            "type",
            "parent_id",
            "owner_identity_id",
            "owner_group_id",
            "created_at",
            "updated_at",
            "slug",
            "tenant_id",
            "display_name",
            "cloud_provider",
            "cloud_account_id",
            "region",
            "is_active",
            "settings",
            "tags",
            "metadata",
        }


class TestOrganizationListResponseFieldSet:
    def test_exact_field_set(self):
        assert _field_names(OrganizationListResponse) == {
            "items",
            "total",
            "page",
            "per_page",
            "pages",
        }


class TestOrganizationsPydalRoutesUseValidateResponse:
    def test_all_handlers_decorated(self):
        from quart_schema.validation import QUART_SCHEMA_RESPONSE_ATTRIBUTE

        from apps.api.modules.infrastructure.routes import organizations_pydal as mod

        for name in (
            "list_organizations",
            "create_organization",
            "get_organization",
            "update_organization",
            "get_organization_children",
        ):
            func = getattr(mod, name)
            schemas = getattr(func, QUART_SCHEMA_RESPONSE_ATTRIBUTE, None)
            assert schemas, f"{name} is missing @validate_response wiring"
