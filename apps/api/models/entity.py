"""Entity model for tracking infrastructure and organizational resources."""

# flake8: noqa: E501


import enum
import secrets
from typing import List, Optional

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    Column,
    Enum,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, relationship

from apps.api.models.base import Base, IDMixin, TimestampMixin


class EntityType(enum.Enum):
    """Types of entities that can be tracked."""

    DATACENTER = "datacenter"
    VPC = "vpc"
    SUBNET = "subnet"
    COMPUTE = "compute"  # Laptops, Servers, VMs
    NETWORK = "network"  # VPNs, Proxies, Routers, Load Balancers
    USER = "user"  # Human and non-human users
    SECURITY_ISSUE = "security_issue"  # Vulnerabilities, CVEs, etc.


class Entity(Base, IDMixin, TimestampMixin):
    """
    Entity model for tracking various infrastructure and organizational resources.

    Uses single-table inheritance with entity_type discriminator for different entity types.
    Type-specific attributes stored in JSONB metadata field.
    """

    __tablename__ = "entities"

    # Unique 64-bit identifier for lookups
    unique_id = Column(
        BigInteger,
        unique=True,
        nullable=False,
        index=True,
        comment="Unique 64-bit identifier for public lookups",
    )

    # Core fields
    name = Column(String(255), nullable=False, index=True)
    description = Column(Text, nullable=True)

    # Type discriminator
    entity_type = Column(
        Enum(EntityType, values_callable=lambda e: [x.value for x in e]),
        nullable=False,
        index=True,
        comment="Type of entity (datacenter, vpc, subnet, compute, network, user, security_issue)",
    )

    # Sub-type (e.g., router, server, database)
    sub_type = Column(
        String(50),
        nullable=True,
        index=True,
        comment="Sub-type within entity_type (router, server, database, etc.)",
    )

    # Hierarchical parent
    parent_id = Column(
        Integer,
        ForeignKey("entities.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )

    # Status tracking (v1.2.1)
    status_metadata = Column(
        JSON,
        nullable=True,
        comment="{status: Running|Stopped|Deleted|Creating|Error, timestamp: epoch64}",
    )

    # Default metadata template for this sub_type
    default_metadata = Column(
        JSON,
        nullable=True,
        comment="Default metadata template for this sub_type",
    )

    # Tags
    tags = Column(
        JSON,
        nullable=True,
        default=list,
        comment="List of string tags",
    )

    # Active flag
    is_active = Column(Boolean, nullable=True, default=True)

    # village_id for cross-system reference
    village_id = Column(String(32), unique=True, nullable=True, index=True)

    # Organization relationship
    organization_id = Column(
        Integer,
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # Ownership
    owner_identity_id = Column(
        Integer,
        ForeignKey("identities.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
        comment="Owner identity",
    )

    # Type-specific metadata stored as JSONB
    # Examples:
    # - datacenter: {"location": "US-East-1", "provider": "AWS", "region": "us-east-1"}
    # - compute: {"hostname": "web-01", "ip": "10.0.1.5", "os": "Ubuntu 22.04", "cpu": 4, "memory_gb": 16}
    # - network: {"device_type": "load_balancer", "ip": "10.0.1.10", "ports": [80, 443]}
    # - security_issue: {"cve": "CVE-2024-1234", "severity": "high", "cvss_score": 8.5}
    attributes = Column(
        JSON,
        nullable=True,
        default=dict,
        comment="Type-specific attributes stored as JSON",
    )

    # Relationships
    organization: Mapped["Organization"] = relationship(
        "Organization",
        back_populates="entities",
    )

    owner: Mapped[Optional["Identity"]] = relationship(
        "Identity",
        foreign_keys=[owner_identity_id],
        backref="owned_entities",
    )

    # Dependencies where this entity is the source (this entity depends on targets)
    outgoing_dependencies: Mapped[List["Dependency"]] = relationship(
        "Dependency",
        foreign_keys="Dependency.source_entity_id",
        back_populates="source_entity",
        cascade="all, delete-orphan",
    )

    # Dependencies where this entity is the target (sources depend on this entity)
    incoming_dependencies: Mapped[List["Dependency"]] = relationship(
        "Dependency",
        foreign_keys="Dependency.target_entity_id",
        back_populates="target_entity",
        cascade="all, delete-orphan",
    )

    def __init__(self, **kwargs):
        """Initialize entity with auto-generated unique_id."""
        if "unique_id" not in kwargs:
            # Generate unique 64-bit identifier
            kwargs["unique_id"] = self._generate_unique_id()
        super().__init__(**kwargs)

    @staticmethod
    def _generate_unique_id() -> int:
        """Generate a unique 64-bit identifier."""
        # Generate a random 64-bit positive integer
        return secrets.randbits(63)  # 63 bits to ensure positive

    def __repr__(self) -> str:
        """String representation of entity."""
        return f"<Entity(id={self.id}, unique_id={self.unique_id}, name='{self.name}', type={self.entity_type.value})>"

    @property
    def type_display(self) -> str:
        """Get human-readable entity type."""
        type_names = {
            EntityType.DATACENTER: "Datacenter",
            EntityType.VPC: "VPC",
            EntityType.SUBNET: "Subnet",
            EntityType.COMPUTE: "Compute Device",
            EntityType.NETWORK: "Network Device",
            EntityType.USER: "User",
            EntityType.SECURITY_ISSUE: "Security Issue",
        }
        return type_names.get(self.entity_type, self.entity_type.value)

    def get_all_dependencies(self, depth: int = -1) -> List["Entity"]:
        """
        Get all entities that this entity depends on, recursively.

        Args:
            depth: Maximum depth to traverse (-1 for unlimited)

        Returns:
            List of dependent entities
        """
        if depth == 0:
            return []

        dependencies = []
        for dep in self.outgoing_dependencies:
            target = dep.target_entity
            if target and target not in dependencies:
                dependencies.append(target)
                if depth != 1:
                    next_depth = depth - 1 if depth > 0 else -1
                    dependencies.extend(target.get_all_dependencies(depth=next_depth))

        return dependencies

    def get_all_dependents(self, depth: int = -1) -> List["Entity"]:
        """
        Get all entities that depend on this entity, recursively.

        Args:
            depth: Maximum depth to traverse (-1 for unlimited)

        Returns:
            List of dependent entities
        """
        if depth == 0:
            return []

        dependents = []
        for dep in self.incoming_dependencies:
            source = dep.source_entity
            if source and source not in dependents:
                dependents.append(source)
                if depth != 1:
                    next_depth = depth - 1 if depth > 0 else -1
                    dependents.extend(source.get_all_dependents(depth=next_depth))

        return dependents

    def get_metadata_field(self, field: str, default: any = None) -> any:
        """
        Safely get a field from metadata.

        Args:
            field: Field name to retrieve
            default: Default value if field doesn't exist

        Returns:
            Field value or default
        """
        if not self.attributes:
            return default
        return self.attributes.get(field, default)

    def set_metadata_field(self, field: str, value: any) -> None:
        """
        Set a field in metadata.

        Args:
            field: Field name to set
            value: Value to set
        """
        if self.attributes is None:
            self.attributes = {}
        self.attributes[field] = value
