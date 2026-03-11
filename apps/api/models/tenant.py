# flake8: noqa: E501
"""Tenant, portal user, and org assignment models."""

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
)

from apps.api.models.base import Base, IDMixin, TimestampMixin


class Tenant(Base, IDMixin, TimestampMixin):
    """Multi-tenancy foundation table."""

    __tablename__ = "tenants"

    name = Column(String(255), nullable=False)
    slug = Column(String(100), nullable=False, unique=True)
    domain = Column(String(255), nullable=True)
    subscription_tier = Column(String(50), nullable=True)
    license_key = Column(String(255), nullable=True)
    settings = Column(JSON, nullable=True)
    feature_flags = Column(JSON, nullable=True)
    data_retention_days = Column(Integer, nullable=True)
    storage_quota_gb = Column(Integer, nullable=True)
    is_active = Column(Boolean, nullable=False)
    village_id = Column(String(32), unique=True, nullable=True)


class PortalUser(Base, IDMixin, TimestampMixin):
    """Enterprise portal user management."""

    __tablename__ = "portal_users"

    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=False)
    email = Column(String(255), nullable=False)
    password_hash = Column(String(255), nullable=True)
    mfa_secret = Column(String(255), nullable=True)
    mfa_backup_codes = Column(JSON, nullable=True)
    global_role = Column(String(50), nullable=True)
    tenant_role = Column(String(50), nullable=True)
    full_name = Column(String(255), nullable=True)
    is_active = Column(Boolean, nullable=False)
    email_verified = Column(Boolean, nullable=False)
    last_login_at = Column(DateTime(timezone=True), nullable=True)
    failed_login_attempts = Column(Integer, nullable=True)
    locked_until = Column(DateTime(timezone=True), nullable=True)
    password_changed_at = Column(DateTime(timezone=True), nullable=True)


class PortalUserOrgAssignment(Base, IDMixin):
    """Portal user to organization role assignments."""

    __tablename__ = "portal_user_org_assignments"

    portal_user_id = Column(Integer, ForeignKey("portal_users.id"), nullable=False)
    organization_id = Column(Integer, nullable=False)
    role = Column(String(50), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=True)
