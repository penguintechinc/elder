# flake8: noqa: E501
"""Infrastructure models: networking, services, software, data stores, costs."""

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


class NetworkingResource(Base, IDMixin, TimestampMixin):
    """Networking resources (subnets, VPCs, firewalls, etc.)."""

    __tablename__ = "networking_resources"

    name = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    network_type = Column(String(50), nullable=False)
    organization_id = Column(Integer, ForeignKey("organizations.id"), nullable=False)
    parent_id = Column(Integer, ForeignKey("networking_resources.id"), nullable=True)
    region = Column(String(100), nullable=True)
    location = Column(String(255), nullable=True)
    poc = Column(String(255), nullable=True)
    organizational_unit = Column(String(255), nullable=True)
    attributes = Column(JSON, nullable=True)
    status_metadata = Column(JSON, nullable=True)
    tags = Column(JSON, nullable=True)
    is_active = Column(Boolean, nullable=True)


class NetworkEntityMapping(Base, IDMixin, TimestampMixin):
    """Links networking resources to entities."""

    __tablename__ = "network_entity_mappings"

    network_id = Column(Integer, ForeignKey("networking_resources.id"), nullable=False)
    entity_id = Column(Integer, ForeignKey("entities.id"), nullable=False)
    relationship_type = Column(String(50), nullable=False)
    extra_metadata = Column("metadata", JSON, nullable=True)


class NetworkTopology(Base, IDMixin, TimestampMixin):
    """Network topology connections between networking resources."""

    __tablename__ = "network_topology"

    source_network_id = Column(
        Integer, ForeignKey("networking_resources.id"), nullable=False
    )
    target_network_id = Column(
        Integer, ForeignKey("networking_resources.id"), nullable=False
    )
    connection_type = Column(String(50), nullable=False)
    bandwidth = Column(String(50), nullable=True)
    latency = Column(String(50), nullable=True)
    extra_metadata = Column("metadata", JSON, nullable=True)


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


class DataStore(Base, IDMixin, TimestampMixin):
    """Data inventory management."""

    __tablename__ = "data_stores"

    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=False)
    organization_id = Column(Integer, ForeignKey("organizations.id"), nullable=True)
    village_id = Column(String(32), unique=True, nullable=True)
    name = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    storage_type = Column(String(50), nullable=True)
    storage_provider = Column(String(100), nullable=True)
    location_region = Column(String(50), nullable=True)
    location_physical = Column(String(255), nullable=True)
    data_classification = Column(String(20), nullable=True)
    encryption_at_rest = Column(Boolean, nullable=True)
    encryption_in_transit = Column(Boolean, nullable=True)
    encryption_key_id = Column(Integer, ForeignKey("crypto_keys.id"), nullable=True)
    retention_days = Column(Integer, nullable=True)
    backup_enabled = Column(Boolean, nullable=True)
    backup_frequency = Column(String(50), nullable=True)
    access_control_type = Column(String(20), nullable=True)
    poc_identity_id = Column(Integer, ForeignKey("identities.id"), nullable=True)
    compliance_frameworks = Column(JSON, nullable=True)
    contains_pii = Column(Boolean, nullable=True)
    contains_phi = Column(Boolean, nullable=True)
    contains_pci = Column(Boolean, nullable=True)
    size_bytes = Column(Integer, nullable=True)
    last_access_audit = Column(DateTime(timezone=True), nullable=True)
    extra_metadata = Column("metadata", JSON, nullable=True)
    created_by = Column(Integer, ForeignKey("portal_users.id"), nullable=True)
    is_active = Column(Boolean, nullable=True)


class DataStoreLabel(Base, IDMixin):
    """Labels applied to data stores."""

    __tablename__ = "data_store_labels"

    data_store_id = Column(Integer, ForeignKey("data_stores.id"), nullable=False)
    label_id = Column(Integer, ForeignKey("labels.id"), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=True)


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
