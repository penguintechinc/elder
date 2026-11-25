"""
Regression tests: certificate endpoints must expose an exact, explicit field
set via `@validate_response` DTOs — not raw `.as_dict()` serialization.

security-audit: "0 @validate_response / raw ORM serialization — no response
schemas".
"""

from apps.api.models.pydantic.certificate import (
    CertificateListResponse,
    CertificateResponse,
)

EXPECTED_CERTIFICATE_FIELDS = {
    "id",
    "tenant_id",
    "name",
    "external_id",
    "description",
    "organization_id",
    "creator",
    "cert_type",
    "common_name",
    "subject_alternative_names",
    "organization_unit",
    "locality",
    "state_province",
    "country",
    "issuer_common_name",
    "issuer_organization",
    "key_algorithm",
    "key_size",
    "signature_algorithm",
    "issue_date",
    "expiration_date",
    "not_before",
    "not_after",
    "certificate_pem",
    "certificate_fingerprint_sha1",
    "certificate_fingerprint_sha256",
    "serial_number",
    "private_key_secret_id",
    "entities_using",
    "services_using",
    "file_path",
    "vault_path",
    "auto_renew",
    "renewal_days_before",
    "last_renewed_at",
    "renewal_method",
    "acme_account_url",
    "acme_order_url",
    "acme_challenge_type",
    "is_revoked",
    "revoked_at",
    "revocation_reason",
    "validation_type",
    "ct_log_status",
    "ocsp_must_staple",
    "cost_annual",
    "purchase_date",
    "vendor",
    "notes",
    "tags",
    "custom_metadata",
    "status",
    "is_active",
    "created_by_id",
    "updated_by_id",
    "village_id",
    "created_at",
    "updated_at",
}


class TestCertificateResponseFieldSet:
    def test_exact_field_set(self):
        assert set(CertificateResponse.model_fields) == EXPECTED_CERTIFICATE_FIELDS

    def test_no_raw_private_key_material_field(self):
        """Only a reference id into builtin_secrets — never the key itself."""
        assert "private_key" not in CertificateResponse.model_fields
        assert "private_key_pem" not in CertificateResponse.model_fields


class TestCertificateListResponseFieldSet:
    def test_exact_field_set(self):
        assert set(CertificateListResponse.model_fields) == {
            "items",
            "total",
            "page",
            "per_page",
            "pages",
        }


class TestCertificatesRoutesUseValidateResponse:
    """Wiring check: list/create/get/update handlers carry @validate_response."""

    def test_all_handlers_decorated(self):
        from quart_schema.validation import QUART_SCHEMA_RESPONSE_ATTRIBUTE

        from apps.api.modules.secrets.routes import certificates as mod

        for name in (
            "list_certificates",
            "create_certificate",
            "get_certificate",
            "update_certificate",
        ):
            func = getattr(mod, name)
            schemas = getattr(func, QUART_SCHEMA_RESPONSE_ATTRIBUTE, None)
            assert schemas, f"{name} is missing @validate_response wiring"
