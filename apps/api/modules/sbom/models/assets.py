"""SBOM asset models: Service and Software."""

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    Date,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
)

from apps.api.models.base import Base, IDMixin, TimestampMixin


class Service(Base, IDMixin, TimestampMixin):
    """Microservice tracking."""

    __tablename__ = "services"

    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=False)
    name = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    organization_id = Column(Integer, ForeignKey("organizations.id"), nullable=False)
    domains = Column(JSON, nullable=True)
    paths = Column(JSON, nullable=True)
    poc_identity_id = Column(Integer, ForeignKey("identities.id"), nullable=True)
    language = Column(String(50), nullable=True)
    deployment_method = Column(String(50), nullable=True)
    deployment_type = Column(String(100), nullable=True)
    is_public = Column(Boolean, nullable=False)
    port = Column(Integer, nullable=True)
    health_endpoint = Column(String(255), nullable=True)
    repository_url = Column(String(1024), nullable=True)
    documentation_url = Column(String(1024), nullable=True)
    sla_uptime = Column(Numeric(precision=5, scale=2), nullable=True)
    sla_response_time_ms = Column(Integer, nullable=True)
    notes = Column(Text, nullable=True)
    tags = Column(JSON, nullable=True)
    status = Column(String(50), nullable=True)
    village_id = Column(String(32), unique=True, nullable=True)


class Software(Base, IDMixin, TimestampMixin):
    """Software inventory tracking."""

    __tablename__ = "software"

    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=False)
    name = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    organization_id = Column(Integer, ForeignKey("organizations.id"), nullable=False)
    purchasing_poc_id = Column(Integer, ForeignKey("identities.id"), nullable=True)
    license_url = Column(String(1024), nullable=True)
    version = Column(String(100), nullable=True)
    business_purpose = Column(Text, nullable=True)
    software_type = Column(String(50), nullable=False)
    seats = Column(Integer, nullable=True)
    cost_monthly = Column(Numeric(precision=10, scale=2), nullable=True)
    renewal_date = Column(Date, nullable=True)
    vendor = Column(String(255), nullable=True)
    support_contact = Column(String(255), nullable=True)
    notes = Column(Text, nullable=True)
    tags = Column(JSON, nullable=True)
    is_active = Column(Boolean, nullable=False)
    village_id = Column(String(32), unique=True, nullable=True)
