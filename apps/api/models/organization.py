"""Organization model for hierarchical organizational structure."""

# flake8: noqa: E501


from typing import List, Optional

from sqlalchemy import Column, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, relationship

from apps.api.models.base import Base, IDMixin, TimestampMixin


class Organization(Base, IDMixin, TimestampMixin):
    """
    Hierarchical organization model.

    Represents organizational structures like Company → Department → Teams.
    Each level can be associated with LDAP/SAML groups and has ownership.
    """

    __tablename__ = "organizations"

    # v2.2.0: Multi-tenancy support
    tenant_id = Column(
        Integer,
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        default=1,
        index=True,
    )

    # Core fields
    name = Column(String(255), nullable=False, index=True)
    description = Column(Text, nullable=True)

    # Organization type (v2.2.0)
    organization_type = Column(
        String(50),
        nullable=False,
        default="organization",
        index=True,
        comment="Type: department, organization, team, collection, other",
    )

    # village_id for cross-system reference
    village_id = Column(String(32), unique=True, nullable=True, index=True)

    # Hierarchical relationship
    parent_id = Column(
        Integer,
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )

    # LDAP/SAML integration
    ldap_dn = Column(
        String(512), nullable=True, index=True, comment="LDAP Distinguished Name"
    )
    saml_group = Column(
        String(255), nullable=True, index=True, comment="SAML group identifier"
    )

    # Ownership
    owner_identity_id = Column(
        Integer,
        ForeignKey("identities.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
        comment="Owner identity (POC)",
    )
    owner_group_id = Column(
        Integer,
        ForeignKey("identity_groups.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
        comment="Owner group/team",
    )

    # Relationships
    parent: Mapped[Optional["Organization"]] = relationship(
        "Organization",
        remote_side="Organization.id",
        backref="children",
        foreign_keys=[parent_id],
    )

    owner: Mapped[Optional["Identity"]] = relationship(
        "Identity",
        foreign_keys=[owner_identity_id],
        backref="owned_organizations",
    )

    owner_group: Mapped[Optional["IdentityGroup"]] = relationship(
        "IdentityGroup",
        foreign_keys=[owner_group_id],
        backref="owned_organizations",
    )

    entities: Mapped[List["Entity"]] = relationship(
        "Entity",
        back_populates="organization",
        cascade="all, delete-orphan",
    )

    alert_configurations: Mapped[List["AlertConfiguration"]] = relationship(
        "AlertConfiguration",
        back_populates="organization",
        cascade="all, delete-orphan",
    )

    def __repr__(self) -> str:
        """String representation of organization."""
        return f"<Organization(id={self.id}, name='{self.name}', parent_id={self.parent_id})>"

    def get_hierarchy_path(self) -> List["Organization"]:
        """
        Get the full hierarchy path from root to this organization.

        Returns:
            List of organizations from root to current
        """
        path = [self]
        current = self.parent
        while current:
            path.insert(0, current)
            current = current.parent
        return path

    def get_hierarchy_string(self, separator: str = " → ") -> str:
        """
        Get the hierarchy as a human-readable string.

        Args:
            separator: String to separate hierarchy levels

        Returns:
            Formatted hierarchy string
        """
        path = self.get_hierarchy_path()
        return separator.join(org.name for org in path)

    def get_all_children(self, recursive: bool = True) -> List["Organization"]:
        """
        Get all child organizations.

        Args:
            recursive: If True, get all descendants; if False, only direct children

        Returns:
            List of child organizations
        """
        if not recursive:
            return list(self.children)

        all_children = []
        for child in self.children:
            all_children.append(child)
            all_children.extend(child.get_all_children(recursive=True))

        return all_children

    @property
    def depth(self) -> int:
        """
        Get the depth of this organization in the hierarchy.

        Returns:
            Depth level (0 for root organizations)
        """
        return len(self.get_hierarchy_path()) - 1
