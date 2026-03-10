# flake8: noqa: E501
"""Auth provider, SCIM, audit retention, backup models."""

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, JSON, String, Text

from apps.api.models.base import Base, IDMixin, TimestampMixin


class IDPConfiguration(Base, IDMixin, TimestampMixin):
    """SSO/SAML/OIDC identity provider configuration."""

    __tablename__ = "idp_configurations"

    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=True)
    idp_type = Column(String(50), nullable=False)
    name = Column(String(255), nullable=False)
    # SAML fields
    entity_id = Column(String(512), nullable=True)
    metadata_url = Column(String(1024), nullable=True)
    sso_url = Column(String(1024), nullable=True)
    slo_url = Column(String(1024), nullable=True)
    certificate = Column(Text, nullable=True)
    # OIDC fields
    oidc_client_id = Column(String(512), nullable=True)
    oidc_client_secret = Column(String(512), nullable=True)
    oidc_issuer_url = Column(String(1024), nullable=True)
    oidc_scopes = Column(String(512), nullable=True)
    oidc_response_type = Column(String(50), nullable=True)
    oidc_token_endpoint_auth_method = Column(String(100), nullable=True)
    # Common fields
    attribute_mappings = Column(JSON, nullable=True)
    jit_provisioning_enabled = Column(Boolean, nullable=False)
    default_role = Column(String(50), nullable=True)
    is_active = Column(Boolean, nullable=False)


class SCIMConfiguration(Base, IDMixin, TimestampMixin):
    """SCIM provisioning configuration."""

    __tablename__ = "scim_configurations"

    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=False)
    endpoint_url = Column(String(1024), nullable=False)
    bearer_token = Column(String(512), nullable=False)
    sync_groups = Column(Boolean, nullable=False)
    is_active = Column(Boolean, nullable=False)
    last_sync_at = Column(DateTime(timezone=True), nullable=True)


class AuditRetentionPolicy(Base, IDMixin, TimestampMixin):
    """Audit log retention policy per resource type."""

    __tablename__ = "audit_retention_policies"

    resource_type = Column(String(50), nullable=False, unique=True)
    retention_days = Column(Integer, nullable=False)
    enabled = Column(Boolean, nullable=False)


class Backup(Base, IDMixin):
    """Backup run records."""

    __tablename__ = "backups"

    job_id = Column(Integer, ForeignKey("backup_jobs.id"), nullable=False)
    filename = Column(String(255), nullable=False)
    file_path = Column(String(512), nullable=True)
    file_size = Column(Integer, nullable=True)
    record_count = Column(Integer, nullable=True)
    status = Column(String(50), nullable=False)
    error_message = Column(Text, nullable=True)
    started_at = Column(DateTime(timezone=True), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    duration_seconds = Column(Integer, nullable=True)
    s3_url = Column(String(1024), nullable=True)
    s3_key = Column(String(512), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=True)


class BackupJob(Base, IDMixin, TimestampMixin):
    """Backup job configuration."""

    __tablename__ = "backup_jobs"

    name = Column(String(255), nullable=False)
    schedule = Column(String(100), nullable=False)
    retention_days = Column(Integer, nullable=False)
    enabled = Column(Boolean, nullable=False)
    last_run_at = Column(DateTime(timezone=True), nullable=True)
    s3_enabled = Column(Boolean, nullable=False)
    s3_endpoint = Column(String(255), nullable=True)
    s3_bucket = Column(String(255), nullable=True)
    s3_region = Column(String(50), nullable=True)
    s3_access_key = Column(String(255), nullable=True)
    s3_secret_key = Column(String(255), nullable=True)
    s3_prefix = Column(String(255), nullable=True)
