# flake8: noqa: E501
"""Secrets management models."""

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, JSON, String, Text

from apps.api.models.base import Base, IDMixin, TimestampMixin


class SecretProvider(Base, IDMixin, TimestampMixin):
    """External secret provider configuration."""

    __tablename__ = "secret_providers"

    name = Column(String(255), nullable=False)
    provider = Column(String(50), nullable=False)
    config_json = Column(JSON, nullable=False)
    organization_id = Column(Integer, ForeignKey("organizations.id"), nullable=False)
    enabled = Column(Boolean, nullable=False)
    last_sync_at = Column(DateTime(timezone=True), nullable=True)


class Secret(Base, IDMixin, TimestampMixin):
    """Secrets from external providers."""

    __tablename__ = "secrets"

    name = Column(String(255), nullable=False)
    provider_id = Column(Integer, ForeignKey("secret_providers.id"), nullable=False)
    provider_path = Column(String(512), nullable=False)
    secret_type = Column(String(50), nullable=False)
    is_kv = Column(Boolean, nullable=False)
    organization_id = Column(Integer, ForeignKey("organizations.id"), nullable=False)
    parent_id = Column(Integer, ForeignKey("secrets.id"), nullable=True)
    extra_metadata = Column('metadata', JSON, nullable=True)
    last_synced_at = Column(DateTime(timezone=True), nullable=True)


class SecretAccessLog(Base, IDMixin):
    """Audit log for secret access."""

    __tablename__ = "secret_access_log"

    secret_id = Column(Integer, ForeignKey("secrets.id"), nullable=False)
    identity_id = Column(Integer, ForeignKey("identities.id"), nullable=False)
    action = Column(String(50), nullable=False)
    masked = Column(Boolean, nullable=False)
    accessed_at = Column(DateTime(timezone=True), nullable=False)


class BuiltinSecret(Base, IDMixin, TimestampMixin):
    """In-app encrypted secret storage."""

    __tablename__ = "builtin_secrets"

    name = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    organization_id = Column(Integer, ForeignKey("organizations.id"), nullable=False)
    secret_value = Column(String(255), nullable=True)
    secret_json = Column(JSON, nullable=True)
    secret_type = Column(String(50), nullable=False)
    tags = Column(JSON, nullable=True)
    is_active = Column(Boolean, nullable=False)
    expires_at = Column(DateTime(timezone=True), nullable=True)


class APIKey(Base, IDMixin, TimestampMixin):
    """API keys for identity authentication."""

    __tablename__ = "api_keys"

    identity_id = Column(Integer, ForeignKey("identities.id"), nullable=False)
    name = Column(String(255), nullable=False)
    key_hash = Column(String(255), nullable=False)
    prefix = Column(String(20), nullable=False)
    last_used_at = Column(DateTime(timezone=True), nullable=True)
    expires_at = Column(DateTime(timezone=True), nullable=True)
    is_active = Column(Boolean, nullable=False)
