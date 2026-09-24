"""
Regression tests: identity endpoints must expose an exact, explicit field set
via `@validate_response` DTOs (identities.py already used IdentityDTO/
IdentityGroupDTO internally — this locks that contract in with quart-schema
enforcement instead of a bare `jsonify(asdict(...))`).

security-audit: "0 @validate_response / raw ORM serialization — no response
schemas".
"""

import dataclasses

from apps.api.api.v1.identities import IdentityGroupListResponse, IdentityListResponse
from apps.api.models.dataclasses import IdentityDTO, IdentityGroupDTO


def _field_names(dc) -> set[str]:
    return {f.name for f in dataclasses.fields(dc)}


class TestIdentityDTOFieldSet:
    def test_exact_field_set(self):
        assert _field_names(IdentityDTO) == {
            "id",
            "username",
            "email",
            "created_at",
            "updated_at",
            "identity_type",
            "tenant_id",
            "external_id",
            "provider",
            "full_name",
            "display_name",
            "avatar_url",
            "is_active",
            "is_service_account",
            "metadata",
            "last_seen_at",
        }

    def test_no_password_or_credential_fields(self):
        names = _field_names(IdentityDTO)
        assert not any("password" in n or "secret" in n or "hash" in n for n in names)


class TestIdentityGroupDTOFieldSet:
    def test_exact_field_set(self):
        assert _field_names(IdentityGroupDTO) == {
            "id",
            "name",
            "description",
            "ldap_dn",
            "saml_group",
            "is_active",
            "created_at",
            "updated_at",
        }


class TestIdentityListResponseFieldSet:
    def test_exact_field_set(self):
        assert _field_names(IdentityListResponse) == {
            "items",
            "total",
            "page",
            "per_page",
            "pages",
        }


class TestIdentityGroupListResponseFieldSet:
    def test_exact_field_set(self):
        assert _field_names(IdentityGroupListResponse) == {
            "items",
            "total",
            "page",
            "per_page",
            "pages",
        }


class TestIdentitiesRoutesUseValidateResponse:
    """Wiring check: every model-returning handler carries @validate_response."""

    def test_all_handlers_decorated(self):
        from quart_schema.validation import QUART_SCHEMA_RESPONSE_ATTRIBUTE

        from apps.api.api.v1 import identities as mod

        for name in (
            "list_identities",
            "create_identity",
            "get_identity",
            "update_identity",
            "list_groups",
            "create_group",
            "get_group",
            "update_group",
        ):
            func = getattr(mod, name)
            schemas = getattr(func, QUART_SCHEMA_RESPONSE_ATTRIBUTE, None)
            assert schemas, f"{name} is missing @validate_response wiring"
