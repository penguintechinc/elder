# flake8: noqa: E501
"""SQLAlchemy models for the AI Resources module."""

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
)

from apps.api.models.base import (
    Base,
    IDMixin,
    TenantScopedMixin,
    TimestampMixin,
    VillageIDMixin,
)


class AIResource(Base, IDMixin, TenantScopedMixin, VillageIDMixin, TimestampMixin):
    """AI resource inventory (LLMs, AI Gateways, MCP, Code Mode, and UTCP Servers)."""

    __tablename__ = "ai_resources"

    name = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    organization_id = Column(Integer, ForeignKey("organizations.id"), nullable=False)
    resource_type = Column(String(50), nullable=False, index=True)
    provider = Column(String(255), nullable=True)
    model_name = Column(String(255), nullable=True)
    endpoint_url = Column(String(1024), nullable=True)
    status = Column(String(50), nullable=False, default="pending_review", index=True)
    agreement_level = Column(String(50), nullable=True)
    plan_info = Column(String(255), nullable=True)
    budget_monthly = Column(Numeric(precision=12, scale=2), nullable=True)
    cost_per_10m_tokens = Column(Numeric(precision=12, scale=4), nullable=True)
    seats = Column(Integer, nullable=True)
    parent_system_id = Column(Integer, ForeignKey("entities.id"), nullable=True)
    owner_id = Column(Integer, ForeignKey("identities.id"), nullable=True)
    notes = Column(Text, nullable=True)
    tags = Column(JSON, nullable=True)
    extra_metadata = Column("metadata", JSON, nullable=True)
    is_active = Column(Boolean, nullable=False, default=True)
