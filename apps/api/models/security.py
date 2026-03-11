# flake8: noqa: E501
"""Security models: vulnerabilities, SBOM, certificates, crypto keys, license policies."""

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
)

from apps.api.models.base import Base, IDMixin, TimestampMixin


class Vulnerability(Base, IDMixin, TimestampMixin):
    """CVE/vulnerability database."""

    __tablename__ = "vulnerabilities"

    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=False)
    cve_id = Column(String(50), unique=True, nullable=False)
    aliases = Column(JSON, nullable=True)
    severity = Column(String(20), nullable=False)
    cvss_score = Column(Numeric(precision=3, scale=1), nullable=True)
    cvss_vector = Column(String(100), nullable=True)
    title = Column(String(512), nullable=True)
    description = Column(Text, nullable=True)
    affected_packages = Column(JSON, nullable=True)
    fixed_versions = Column(JSON, nullable=True)
    references = Column(JSON, nullable=True)
    published_at = Column(DateTime(timezone=True), nullable=True)
    modified_at = Column(DateTime(timezone=True), nullable=True)
    source = Column(String(50), nullable=True)
    is_active = Column(Boolean, nullable=False)
    nvd_last_sync = Column(DateTime(timezone=True), nullable=True)
    village_id = Column(String(32), unique=True, nullable=True)


class ComponentVulnerability(Base, IDMixin, TimestampMixin):
    """Links SBOM components to vulnerabilities."""

    __tablename__ = "component_vulnerabilities"

    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=False)
    component_id = Column(Integer, ForeignKey("sbom_components.id"), nullable=False)
    vulnerability_id = Column(Integer, ForeignKey("vulnerabilities.id"), nullable=False)
    status = Column(String(20), nullable=False)
    remediation_notes = Column(Text, nullable=True)
    remediated_at = Column(DateTime(timezone=True), nullable=True)
    remediated_by_id = Column(Integer, ForeignKey("identities.id"), nullable=True)


class SBOMScan(Base, IDMixin):
    """SBOM scan job tracking."""

    __tablename__ = "sbom_scans"

    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=False)
    parent_type = Column(String(50), nullable=False)
    parent_id = Column(Integer, nullable=False)
    scan_type = Column(String(50), nullable=False)
    status = Column(String(20), nullable=False)
    repository_url = Column(String(1024), nullable=True)
    repository_branch = Column(String(255), nullable=True)
    credential_type = Column(String(50), nullable=True)
    credential_id = Column(Integer, ForeignKey("builtin_secrets.id"), nullable=True)
    credential_mapping = Column(JSON, nullable=True)
    commit_hash = Column(String(64), nullable=True)
    files_scanned = Column(JSON, nullable=True)
    components_found = Column(Integer, nullable=True)
    components_added = Column(Integer, nullable=True)
    components_updated = Column(Integer, nullable=True)
    components_removed = Column(Integer, nullable=True)
    error_message = Column(Text, nullable=True)
    scan_duration_ms = Column(Integer, nullable=True)
    started_at = Column(DateTime(timezone=True), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=True)
    village_id = Column(String(32), unique=True, nullable=True)


class SBOMComponent(Base, IDMixin, TimestampMixin):
    """Software Bill of Materials component tracking."""

    __tablename__ = "sbom_components"

    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=False)
    parent_type = Column(String(50), nullable=False)
    parent_id = Column(Integer, nullable=False)
    name = Column(String(255), nullable=False)
    version = Column(String(100), nullable=True)
    purl = Column(String(512), nullable=True)
    package_type = Column(String(50), nullable=False)
    scope = Column(String(20), nullable=True)
    direct = Column(Boolean, nullable=False)
    license_id = Column(String(100), nullable=True)
    license_name = Column(String(255), nullable=True)
    license_url = Column(String(1024), nullable=True)
    source_file = Column(String(255), nullable=True)
    repository_url = Column(String(1024), nullable=True)
    homepage_url = Column(String(1024), nullable=True)
    description = Column(Text, nullable=True)
    hash_sha256 = Column(String(64), nullable=True)
    hash_sha512 = Column(String(128), nullable=True)
    extra_metadata = Column("metadata", JSON, nullable=True)
    is_active = Column(Boolean, nullable=False)
    village_id = Column(String(32), unique=True, nullable=True)


class SBOMScanSchedule(Base, IDMixin, TimestampMixin):
    """Periodic SBOM scan configuration."""

    __tablename__ = "sbom_scan_schedules"

    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=False)
    parent_type = Column(String(50), nullable=False)
    parent_id = Column(Integer, nullable=False)
    schedule_cron = Column(String(100), nullable=False)
    is_active = Column(Boolean, nullable=False)
    last_run_at = Column(DateTime(timezone=True), nullable=True)
    next_run_at = Column(DateTime(timezone=True), nullable=True)
    credential_type = Column(String(50), nullable=True)
    credential_id = Column(Integer, nullable=True)
    credential_mapping = Column(JSON, nullable=True)
    village_id = Column(String(32), unique=True, nullable=True)


class Certificate(Base, IDMixin, TimestampMixin):
    """Certificate lifecycle management."""

    __tablename__ = "certificates"

    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=False)
    name = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    organization_id = Column(Integer, ForeignKey("organizations.id"), nullable=False)
    creator = Column(String(100), nullable=False)
    cert_type = Column(String(50), nullable=False)
    common_name = Column(String(255), nullable=True)
    subject_alternative_names = Column(JSON, nullable=True)
    organization_unit = Column(String(255), nullable=True)
    locality = Column(String(100), nullable=True)
    state_province = Column(String(100), nullable=True)
    country = Column(String(2), nullable=True)
    issuer_common_name = Column(String(255), nullable=True)
    issuer_organization = Column(String(255), nullable=True)
    key_algorithm = Column(String(50), nullable=True)
    key_size = Column(Integer, nullable=True)
    signature_algorithm = Column(String(100), nullable=True)
    issue_date = Column(Date, nullable=False)
    expiration_date = Column(Date, nullable=False)
    not_before = Column(DateTime(timezone=True), nullable=True)
    not_after = Column(DateTime(timezone=True), nullable=True)
    certificate_pem = Column(Text, nullable=True)
    certificate_fingerprint_sha1 = Column(String(64), nullable=True)
    certificate_fingerprint_sha256 = Column(String(64), nullable=True)
    serial_number = Column(String(255), nullable=True)
    private_key_secret_id = Column(
        Integer, ForeignKey("builtin_secrets.id"), nullable=True
    )
    entities_using = Column(JSON, nullable=True)
    services_using = Column(JSON, nullable=True)
    file_path = Column(String(1024), nullable=True)
    vault_path = Column(String(512), nullable=True)
    auto_renew = Column(Boolean, nullable=False)
    renewal_days_before = Column(Integer, nullable=True)
    last_renewed_at = Column(DateTime(timezone=True), nullable=True)
    renewal_method = Column(String(50), nullable=True)
    acme_account_url = Column(String(512), nullable=True)
    acme_order_url = Column(String(512), nullable=True)
    acme_challenge_type = Column(String(50), nullable=True)
    is_revoked = Column(Boolean, nullable=False)
    revoked_at = Column(DateTime(timezone=True), nullable=True)
    revocation_reason = Column(String(100), nullable=True)
    validation_type = Column(String(50), nullable=True)
    ct_log_status = Column(String(50), nullable=True)
    ocsp_must_staple = Column(Boolean, nullable=True)
    cost_annual = Column(Numeric(precision=10, scale=2), nullable=True)
    purchase_date = Column(Date, nullable=True)
    vendor = Column(String(255), nullable=True)
    notes = Column(Text, nullable=True)
    tags = Column(JSON, nullable=True)
    custom_metadata = Column(JSON, nullable=True)
    status = Column(String(50), nullable=False)
    is_active = Column(Boolean, nullable=False)
    created_by_id = Column(Integer, ForeignKey("identities.id"), nullable=True)
    updated_by_id = Column(Integer, ForeignKey("identities.id"), nullable=True)
    village_id = Column(String(32), unique=True, nullable=True)


class CryptoKey(Base, IDMixin, TimestampMixin):
    """Cryptographic key management."""

    __tablename__ = "crypto_keys"

    name = Column(String(255), nullable=False)
    key_provider_id = Column(Integer, ForeignKey("key_providers.id"), nullable=False)
    provider_key_id = Column(String(512), nullable=False)
    provider_key_arn = Column(String(512), nullable=True)
    key_hash = Column(String(255), nullable=False)
    key_type = Column(String(50), nullable=True)
    key_state = Column(String(50), nullable=True)
    organization_id = Column(Integer, ForeignKey("organizations.id"), nullable=False)
    metadata_json = Column(JSON, nullable=True)
    last_synced_at = Column(DateTime(timezone=True), nullable=True)


class KeyProvider(Base, IDMixin, TimestampMixin):
    """Key management provider configuration."""

    __tablename__ = "key_providers"

    name = Column(String(255), nullable=False)
    provider = Column(String(50), nullable=False)
    config_json = Column(JSON, nullable=False)
    organization_id = Column(Integer, ForeignKey("organizations.id"), nullable=False)
    enabled = Column(Boolean, nullable=False)
    last_sync_at = Column(DateTime(timezone=True), nullable=True)


class KeyAccessLog(Base, IDMixin):
    """Audit log for cryptographic key access."""

    __tablename__ = "key_access_log"

    key_id = Column(Integer, ForeignKey("crypto_keys.id"), nullable=False)
    identity_id = Column(Integer, ForeignKey("identities.id"), nullable=True)
    action = Column(String(50), nullable=True)
    operation = Column(String(50), nullable=True)
    metadata_json = Column(JSON, nullable=True)
    accessed_at = Column(DateTime(timezone=True), nullable=False)


class LicensePolicy(Base, IDMixin, TimestampMixin):
    """License compliance rules."""

    __tablename__ = "license_policies"

    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=False)
    organization_id = Column(Integer, ForeignKey("organizations.id"), nullable=False)
    name = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    allowed_licenses = Column(JSON, nullable=True)
    denied_licenses = Column(JSON, nullable=True)
    action = Column(String(10), nullable=True)
    is_active = Column(Boolean, nullable=False)
    credential_type = Column(String(50), nullable=True)
    credential_id = Column(Integer, nullable=True)
    credential_mapping = Column(JSON, nullable=True)
    village_id = Column(String(32), unique=True, nullable=True)
