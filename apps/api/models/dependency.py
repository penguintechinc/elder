"""Dependency model for tracking relationships between entities."""

# flake8: noqa: E501


import enum
from datetime import datetime, timezone

from sqlalchemy import JSON, Column, DateTime, Enum, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, relationship

from apps.api.models.base import Base, IDMixin


class DependencyType(enum.Enum):
    """Types of dependencies between entities."""

    DEPENDS_ON = "depends_on"  # Source depends on target
    RELATED_TO = "related_to"  # General relationship
    PART_OF = "part_of"  # Source is part of target (e.g., VM part of VPC)


class Dependency(Base, IDMixin):
    """
    Dependency relationship between entities.

    Represents directed dependencies where source_entity depends on target_entity.
    """

    __tablename__ = "dependencies"

    # v2.2.0: Multi-tenancy support
    tenant_id = Column(
        Integer,
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        default=1,
        index=True,
    )

    # Polymorphic source/target (v2.x: PyDAL uses source_type/source_id pattern)
    source_type = Column(
        String(50),
        nullable=True,
        index=True,
        comment="Source resource type: entity, identity, project, milestone, issue, organization",
    )
    target_type = Column(
        String(50),
        nullable=True,
        index=True,
        comment="Target resource type: entity, identity, project, milestone, issue, organization",
    )

    # village_id for cross-system reference
    village_id = Column(String(32), unique=True, nullable=True, index=True)

    # Source and target entities
    source_entity_id = Column(
        Integer,
        ForeignKey("entities.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="Entity that has the dependency",
    )

    target_entity_id = Column(
        Integer,
        ForeignKey("entities.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="Entity that is depended upon",
    )

    # Dependency type
    dependency_type = Column(
        Enum(DependencyType),
        nullable=False,
        default=DependencyType.DEPENDS_ON,
        index=True,
        comment="Type of dependency relationship",
    )

    # Additional metadata
    dependency_metadata = Column(
        "metadata",  # Column name in database
        JSON,
        nullable=True,
        default=dict,
        comment="Additional dependency metadata (e.g., criticality, notes)",
    )

    # Plain integer source/target IDs (PyDAL pattern)
    source_id = Column(Integer, nullable=True)
    target_id = Column(Integer, nullable=True)

    # Timestamp
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        index=True,
    )

    updated_at = Column(
        DateTime(timezone=True),
        nullable=True,
    )

    # Relationships
    source_entity: Mapped["Entity"] = relationship(
        "Entity",
        foreign_keys=[source_entity_id],
        back_populates="outgoing_dependencies",
    )

    target_entity: Mapped["Entity"] = relationship(
        "Entity",
        foreign_keys=[target_entity_id],
        back_populates="incoming_dependencies",
    )

    def __repr__(self) -> str:
        """String representation of dependency."""
        return (
            f"<Dependency(id={self.id}, "
            f"source={self.source_entity_id}, "
            f"target={self.target_entity_id}, "
            f"type={self.dependency_type.value})>"
        )

    @property
    def type_display(self) -> str:
        """Get human-readable dependency type."""
        type_names = {
            DependencyType.DEPENDS_ON: "Depends On",
            DependencyType.RELATED_TO: "Related To",
            DependencyType.PART_OF: "Part Of",
        }
        return type_names.get(self.dependency_type, self.dependency_type.value)

    def get_criticality(self) -> str:
        """
        Get the criticality level of this dependency.

        Returns:
            Criticality level (high, medium, low) from metadata or 'medium' as default
        """
        if not self.dependency_metadata:
            return "medium"
        return self.dependency_metadata.get("criticality", "medium")

    def set_criticality(self, level: str) -> None:
        """
        Set the criticality level of this dependency.

        Args:
            level: Criticality level (high, medium, low)
        """
        if self.dependency_metadata is None:
            self.dependency_metadata = {}
        self.dependency_metadata["criticality"] = level
