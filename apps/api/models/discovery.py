# flake8: noqa: E501
"""Discovery, cloud account, sync, and IAM provider models."""

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, JSON, String, Text

from apps.api.models.base import Base, IDMixin, TimestampMixin


class CloudAccount(Base, IDMixin, TimestampMixin):
    """Cloud provider account for auto-discovery."""

    __tablename__ = "cloud_accounts"

    name = Column(String(255), nullable=False)
    provider = Column(String(50), nullable=False)
    credentials_json = Column(JSON, nullable=False)
    organization_id = Column(Integer, ForeignKey("organizations.id"), nullable=False)
    enabled = Column(Boolean, nullable=False)


class DiscoveryJob(Base, IDMixin, TimestampMixin):
    """Discovery job configuration."""

    __tablename__ = "discovery_jobs"

    name = Column(String(255), nullable=False)
    provider = Column(String(50), nullable=False)
    config_json = Column(JSON, nullable=False)
    schedule_interval = Column(Integer, nullable=False)
    enabled = Column(Boolean, nullable=False)
    credential_type = Column(String(50), nullable=True)
    credential_id = Column(Integer, nullable=True)
    credential_mapping = Column(JSON, nullable=True)
    last_run_at = Column(DateTime(timezone=True), nullable=True)
    next_run_at = Column(DateTime(timezone=True), nullable=True)


class DiscoveryHistory(Base, IDMixin):
    """Discovery job run history."""

    __tablename__ = "discovery_history"

    job_id = Column(Integer, ForeignKey("discovery_jobs.id"), nullable=False)
    started_at = Column(DateTime(timezone=True), nullable=False)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    status = Column(String(50), nullable=False)
    entities_discovered = Column(Integer, nullable=False)
    entities_created = Column(Integer, nullable=False)
    entities_updated = Column(Integer, nullable=False)
    error_message = Column(Text, nullable=True)
    results_json = Column(JSON, nullable=True)


class SyncConfig(Base, IDMixin, TimestampMixin):
    """Sync configuration for external platforms."""

    __tablename__ = "sync_configs"

    name = Column(String(255), nullable=False, unique=True)
    platform = Column(String(50), nullable=False)
    enabled = Column(Boolean, nullable=False)
    sync_interval = Column(Integer, nullable=False)
    batch_fallback_enabled = Column(Boolean, nullable=False)
    batch_size = Column(Integer, nullable=False)
    two_way_create = Column(Boolean, nullable=False)
    webhook_enabled = Column(Boolean, nullable=False)
    webhook_secret = Column(String(255), nullable=True)
    last_sync_at = Column(DateTime(timezone=True), nullable=True)
    last_batch_sync_at = Column(DateTime(timezone=True), nullable=True)
    config_json = Column(JSON, nullable=True)


class SyncHistory(Base, IDMixin):
    """Sync run history records."""

    __tablename__ = "sync_history"

    sync_config_id = Column(Integer, ForeignKey("sync_configs.id"), nullable=False)
    correlation_id = Column(String(36), nullable=True)
    sync_type = Column(String(50), nullable=False)
    items_synced = Column(Integer, nullable=False)
    items_failed = Column(Integer, nullable=False)
    started_at = Column(DateTime(timezone=True), nullable=False)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    success = Column(Boolean, nullable=False)
    error_message = Column(Text, nullable=True)
    sync_metadata = Column(JSON, nullable=True)


class SyncConflict(Base, IDMixin):
    """Sync conflict records for manual resolution."""

    __tablename__ = "sync_conflicts"

    mapping_id = Column(Integer, ForeignKey("sync_mappings.id"), nullable=False)
    conflict_type = Column(String(50), nullable=False)
    elder_data = Column(JSON, nullable=False)
    external_data = Column(JSON, nullable=False)
    resolution_strategy = Column(String(50), nullable=True)
    resolved = Column(Boolean, nullable=False)
    resolved_at = Column(DateTime(timezone=True), nullable=True)
    resolved_by_id = Column(Integer, ForeignKey("identities.id"), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=True)


class SyncMapping(Base, IDMixin, TimestampMixin):
    """Maps Elder resources to external platform IDs."""

    __tablename__ = "sync_mappings"

    elder_type = Column(String(50), nullable=False)
    elder_id = Column(Integer, nullable=False)
    external_platform = Column(String(50), nullable=False)
    external_id = Column(String(255), nullable=False)
    sync_config_id = Column(Integer, ForeignKey("sync_configs.id"), nullable=False)
    sync_status = Column(String(50), nullable=True)
    sync_method = Column(String(50), nullable=True)
    last_synced_at = Column(DateTime(timezone=True), nullable=True)
    elder_updated_at = Column(DateTime(timezone=True), nullable=True)
    external_updated_at = Column(DateTime(timezone=True), nullable=True)


class GoogleWorkspaceProvider(Base, IDMixin, TimestampMixin):
    """Google Workspace provider configuration."""

    __tablename__ = "google_workspace_providers"

    name = Column(String(255), nullable=False)
    domain = Column(String(255), nullable=False)
    admin_email = Column(String(255), nullable=False)
    credentials_json = Column(JSON, nullable=False)
    organization_id = Column(Integer, ForeignKey("organizations.id"), nullable=False)
    enabled = Column(Boolean, nullable=False)
    last_sync_at = Column(DateTime(timezone=True), nullable=True)


class IAMProvider(Base, IDMixin, TimestampMixin):
    """IAM provider configuration for unified IAM model."""

    __tablename__ = "iam_providers"

    name = Column(String(255), nullable=False)
    provider_type = Column(String(50), nullable=False)
    config_json = Column(JSON, nullable=False)
    organization_id = Column(Integer, ForeignKey("organizations.id"), nullable=False)
    enabled = Column(Boolean, nullable=False)
    last_sync_at = Column(DateTime(timezone=True), nullable=True)
