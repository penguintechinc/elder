"""
Pydantic 2 response DTOs for the certificates management module.

Scopes `apps.api.models.security.Certificate` PyDAL rows to an explicit,
validated field set — security-audit fix for raw `.as_dict()` serialization
(see apps/api/modules/secrets/routes/certificates.py). Field set mirrors the
`Certificate` SQLAlchemy model 1:1 (no PII/secret-value fields on this table —
`private_key_secret_id` is a reference into `builtin_secrets`, never the key
material itself), so this is a contract fix, not a functional trim.
"""

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from penguin_libs.pydantic.base import ImmutableModel


class CertificateResponse(ImmutableModel):
    """A single certificate inventory record."""

    id: int
    tenant_id: int
    name: str
    external_id: str | None = None
    description: str | None = None
    organization_id: int
    creator: str
    cert_type: str
    common_name: str | None = None
    subject_alternative_names: list[Any] | dict[str, Any] | None = None
    organization_unit: str | None = None
    locality: str | None = None
    state_province: str | None = None
    country: str | None = None
    issuer_common_name: str | None = None
    issuer_organization: str | None = None
    key_algorithm: str | None = None
    key_size: int | None = None
    signature_algorithm: str | None = None
    issue_date: date
    expiration_date: date
    not_before: datetime | None = None
    not_after: datetime | None = None
    certificate_pem: str | None = None
    certificate_fingerprint_sha1: str | None = None
    certificate_fingerprint_sha256: str | None = None
    serial_number: str | None = None
    private_key_secret_id: int | None = None
    entities_using: list[Any] | dict[str, Any] | None = None
    services_using: list[Any] | dict[str, Any] | None = None
    file_path: str | None = None
    vault_path: str | None = None
    auto_renew: bool
    renewal_days_before: int | None = None
    last_renewed_at: datetime | None = None
    renewal_method: str | None = None
    acme_account_url: str | None = None
    acme_order_url: str | None = None
    acme_challenge_type: str | None = None
    is_revoked: bool
    revoked_at: datetime | None = None
    revocation_reason: str | None = None
    validation_type: str | None = None
    ct_log_status: str | None = None
    ocsp_must_staple: bool | None = None
    cost_annual: Decimal | None = None
    purchase_date: date | None = None
    vendor: str | None = None
    notes: str | None = None
    tags: list[Any] | dict[str, Any] | None = None
    custom_metadata: dict[str, Any] | None = None
    status: str
    is_active: bool
    created_by_id: int | None = None
    updated_by_id: int | None = None
    village_id: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class CertificateListResponse(ImmutableModel):
    """Paginated list of certificates."""

    items: list[CertificateResponse]
    total: int
    page: int
    per_page: int
    pages: int
