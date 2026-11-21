"""Tenant module toggle model for Phase-2 module management.

Tracks which modules are enabled per tenant and stores module-specific settings.
Schema authority for the tenant_modules table (SQLAlchemy/Alembic).
penguin-dal handles runtime queries via reflection.
"""

# flake8: noqa: E501

from sqlalchemy import JSON, Boolean, Column, Integer, String, UniqueConstraint

from apps.api.models.base import Base, IDMixin, TenantScopedMixin, TimestampMixin


class TenantModule(Base, IDMixin, TimestampMixin, TenantScopedMixin):
    """
    Tenant-specific module enablement state.

    Tracks whether each module is enabled for a specific tenant and stores
    module-specific configuration/settings as JSON.
    """

    __tablename__ = "tenant_modules"

    module_name = Column(
        String(64),
        nullable=False,
        index=True,
        comment="Module name (e.g., 'infrastructure', 'sbom', 'issues')",
    )

    enabled = Column(
        Boolean,
        nullable=False,
        default=True,
        comment="Whether this module is enabled for the tenant",
    )

    settings = Column(
        JSON,
        nullable=True,
        comment="Module-specific configuration and settings (JSON)",
    )

    # Unique constraint: one row per tenant+module pair
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "module_name",
            name="uq_tenant_module_name",
        ),
    )

    def __repr__(self) -> str:
        """String representation of tenant module."""
        return (
            f"<TenantModule(tenant_id={self.tenant_id}, "
            f"module_name={self.module_name}, "
            f"enabled={self.enabled})>"
        )
