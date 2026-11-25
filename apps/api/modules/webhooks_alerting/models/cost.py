# flake8: noqa: E501
"""Cost tracking models."""

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
)

from apps.api.models.base import Base, IDMixin, TimestampMixin


class ResourceCost(Base, IDMixin, TimestampMixin):
    """Cost tracking per resource."""

    __tablename__ = "resource_costs"

    resource_type = Column(String(50), nullable=False)
    resource_id = Column(Integer, nullable=False)
    organization_id = Column(Integer, ForeignKey("organizations.id"), nullable=False)
    cost_to_date = Column(Numeric(precision=12, scale=2), nullable=True)
    cost_ytd = Column(Numeric(precision=12, scale=2), nullable=True)
    cost_mtd = Column(Numeric(precision=12, scale=2), nullable=True)
    estimated_monthly_cost = Column(Numeric(precision=12, scale=2), nullable=True)
    currency = Column(String(3), nullable=True)
    cost_provider = Column(String(50), nullable=True)
    recommendations = Column(JSON, nullable=True)
    created_by_identity_id = Column(Integer, ForeignKey("identities.id"), nullable=True)
    resource_created_at = Column(DateTime(timezone=True), nullable=True)
    last_synced_at = Column(DateTime(timezone=True), nullable=True)


class CostHistory(Base, IDMixin):
    """Daily cost snapshots for trending."""

    __tablename__ = "cost_history"

    resource_cost_id = Column(Integer, ForeignKey("resource_costs.id"), nullable=False)
    snapshot_date = Column(Date, nullable=False)
    cost_amount = Column(Numeric(precision=12, scale=2), nullable=False)
    usage_quantity = Column(Numeric(precision=12, scale=4), nullable=True)
    usage_unit = Column(String(50), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=True)


class CostSyncJob(Base, IDMixin):
    """Scheduled cost provider sync jobs."""

    __tablename__ = "cost_sync_jobs"

    name = Column(String(255), nullable=False)
    provider = Column(String(50), nullable=False)
    organization_id = Column(Integer, ForeignKey("organizations.id"), nullable=False)
    config_json = Column(JSON, nullable=False)
    schedule_interval = Column(Integer, nullable=True)
    enabled = Column(Boolean, nullable=True)
    last_run_at = Column(DateTime(timezone=True), nullable=True)
    next_run_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=True)
