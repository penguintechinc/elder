# flake8: noqa: E501
"""IP Address Management (IPAM) models."""

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


class IPAMPrefix(Base, IDMixin, TimestampMixin):
    """CIDR prefix/network management."""

    __tablename__ = "ipam_prefixes"

    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=False)
    prefix = Column(String(50), nullable=False)
    description = Column(Text, nullable=True)
    organization_id = Column(Integer, ForeignKey("organizations.id"), nullable=False)
    parent_id = Column(Integer, ForeignKey("ipam_prefixes.id"), nullable=True)
    vlan_id = Column(Integer, nullable=True)
    vrf = Column(String(100), nullable=True)
    status = Column(String(50), nullable=True)
    role = Column(String(100), nullable=True)
    is_pool = Column(Boolean, nullable=False)
    site = Column(String(255), nullable=True)
    region = Column(String(100), nullable=True)
    tags = Column(JSON, nullable=True)
    village_id = Column(String(32), unique=True, nullable=True)


class IPAMAddress(Base, IDMixin, TimestampMixin):
    """Individual IP address tracking."""

    __tablename__ = "ipam_addresses"

    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=False)
    address = Column(String(50), nullable=False)
    prefix_id = Column(Integer, ForeignKey("ipam_prefixes.id"), nullable=False)
    dns_name = Column(String(255), nullable=True)
    description = Column(Text, nullable=True)
    status = Column(String(50), nullable=True)
    assigned_object_type = Column(String(50), nullable=True)
    assigned_object_id = Column(Integer, nullable=True)
    nat_inside_id = Column(Integer, ForeignKey("ipam_addresses.id"), nullable=True)
    tags = Column(JSON, nullable=True)
    village_id = Column(String(32), unique=True, nullable=True)


class IPAMVLAN(Base, IDMixin, TimestampMixin):
    """VLAN management."""

    __tablename__ = "ipam_vlans"

    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=False)
    vid = Column(Integer, nullable=False)
    name = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    organization_id = Column(Integer, ForeignKey("organizations.id"), nullable=False)
    status = Column(String(50), nullable=True)
    role = Column(String(100), nullable=True)
    site = Column(String(255), nullable=True)
    tags = Column(JSON, nullable=True)
    village_id = Column(String(32), unique=True, nullable=True)
