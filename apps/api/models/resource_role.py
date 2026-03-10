"""Resource-level role model for Elder enterprise features."""

# flake8: noqa: E501


import enum
from typing import List, Optional

from sqlalchemy import Column, Enum, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, relationship

from apps.api.models.base import Base, IDMixin, TimestampMixin


class ResourceType(enum.Enum):
    """Type of resource for role assignment."""

    ENTITY = "entity"
    ORGANIZATION = "organization"


class ResourceRoleType(enum.Enum):
    """Resource-level role types with different permission levels."""

    MAINTAINER = "maintainer"  # Full CRUD, can manage roles
    OPERATOR = "operator"  # Operational control, limited mutations
    VIEWER = "viewer"  # Read access, can create issues/comments


class ResourceRole(Base, IDMixin, TimestampMixin):
    """
    Resource-level role assignment model.

    Assigns Maintainer/Operator/Viewer roles to identities for specific resources.
    Enables fine-grained permission control at the entity or organization level.

    Permission Matrix:
    - **Maintainer**: Full CRUD on issues/metadata, can manage roles on this resource
    - **Operator**: Create/close issues (not delete/edit), add comments/labels, metadata read-only
    - **Viewer**: View issues, create new issues, add comments, metadata read-only
    """

    __tablename__ = "resource_roles"

    # Group that has this role (PyDAL has group_id alongside identity_id)
    group_id = Column(
        Integer,
        ForeignKey("identity_groups.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
        comment="Identity group with this role (alternative to identity_id)",
    )

    # village_id for cross-system reference
    village_id = Column(String(32), unique=True, nullable=True, index=True)

    # Identity who has this role
    identity_id = Column(
        Integer,
        ForeignKey("identities.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="Identity who has this role",
    )

    # Resource being granted access to
    resource_type = Column(
        Enum(ResourceType),
        nullable=False,
        index=True,
        comment="Type of resource (entity or organization)",
    )

    resource_id = Column(
        Integer,
        nullable=False,
        index=True,
        comment="ID of the entity or organization",
    )

    # Role type
    role_type = Column(
        Enum(ResourceRoleType),
        nullable=False,
        comment="Role level (maintainer, operator, viewer)",
    )

    # Plain string role column (PyDAL expects this column name)
    role = Column(String(50), nullable=True)

    # Who granted this role
    granted_by_id = Column(
        Integer,
        ForeignKey("identities.id", ondelete="SET NULL"),
        nullable=True,
        comment="Identity who granted this role",
    )

    # Relationships
    identity: Mapped["Identity"] = relationship(
        "Identity",
        foreign_keys=[identity_id],
        backref="resource_roles",
    )

    group: Mapped[Optional["IdentityGroup"]] = relationship(
        "IdentityGroup",
        foreign_keys=[group_id],
        backref="resource_roles",
    )

    granted_by: Mapped[Optional["Identity"]] = relationship(
        "Identity",
        foreign_keys=[granted_by_id],
        backref="granted_roles",
    )

    # Ensure one role per identity per resource
    __table_args__ = (
        UniqueConstraint(
            "identity_id",
            "resource_type",
            "resource_id",
            "role_type",
            name="uix_resource_role",
        ),
    )

    def __repr__(self) -> str:
        """String representation of resource role."""
        return f"<ResourceRole(id={self.id}, identity_id={self.identity_id}, resource_type={self.resource_type.value}, resource_id={self.resource_id}, role={self.role_type.value})>"

    @classmethod
    def get_user_role(
        cls, identity_id: int, resource_type: ResourceType, resource_id: int
    ) -> Optional["ResourceRole"]:
        """
        Get user's role for a specific resource.

        Args:
            identity_id: Identity to check
            resource_type: Type of resource (entity or organization)
            resource_id: ID of resource

        Returns:
            ResourceRole if assigned, None otherwise
        """
        from shared.database import db

        return (
            db.session.query(cls)
            .filter_by(
                identity_id=identity_id,
                resource_type=resource_type,
                resource_id=resource_id,
            )
            .first()
        )

    @classmethod
    def check_permission(
        cls,
        identity_id: int,
        resource_type: ResourceType,
        resource_id: int,
        required_role: ResourceRoleType,
    ) -> bool:
        """
        Check if identity has required role level on resource.

        Role hierarchy: viewer < operator < maintainer
        If user has maintainer, they implicitly have operator and viewer permissions.

        Args:
            identity_id: Identity to check
            resource_type: Type of resource
            resource_id: ID of resource
            required_role: Minimum role level required

        Returns:
            True if identity has sufficient role level
        """
        role = cls.get_user_role(identity_id, resource_type, resource_id)

        if not role:
            return False

        # Role hierarchy levels
        role_levels = {
            ResourceRoleType.VIEWER: 1,
            ResourceRoleType.OPERATOR: 2,
            ResourceRoleType.MAINTAINER: 3,
        }

        user_level = role_levels.get(role.role_type, 0)
        required_level = role_levels.get(required_role, 99)

        return user_level >= required_level

    @classmethod
    def get_users_with_role(
        cls,
        resource_type: ResourceType,
        resource_id: int,
        role_type: Optional[ResourceRoleType] = None,
    ) -> List["ResourceRole"]:
        """
        Get all users with roles on a specific resource.

        Args:
            resource_type: Type of resource
            resource_id: ID of resource
            role_type: Optional filter by specific role type

        Returns:
            List of ResourceRole assignments
        """
        from shared.database import db

        query = db.session.query(cls).filter_by(
            resource_type=resource_type,
            resource_id=resource_id,
        )

        if role_type:
            query = query.filter_by(role_type=role_type)

        return query.all()


# Extension methods for Entity and Organization models


def get_entity_role(entity_id: int, identity_id: int) -> Optional[ResourceRole]:
    """Get user's role for an entity."""
    return ResourceRole.get_user_role(identity_id, ResourceType.ENTITY, entity_id)


def get_organization_role(
    organization_id: int, identity_id: int
) -> Optional[ResourceRole]:
    """Get user's role for an organization."""
    return ResourceRole.get_user_role(
        identity_id, ResourceType.ORGANIZATION, organization_id
    )


def check_entity_permission(
    entity_id: int, identity_id: int, required_role: ResourceRoleType
) -> bool:
    """Check if user has required role on entity."""
    return ResourceRole.check_permission(
        identity_id, ResourceType.ENTITY, entity_id, required_role
    )


def check_organization_permission(
    organization_id: int, identity_id: int, required_role: ResourceRoleType
) -> bool:
    """Check if user has required role on organization."""
    return ResourceRole.check_permission(
        identity_id, ResourceType.ORGANIZATION, organization_id, required_role
    )


def get_entity_users_with_role(
    entity_id: int, role_type: Optional[ResourceRoleType] = None
) -> List[ResourceRole]:
    """Get all users with roles on an entity."""
    return ResourceRole.get_users_with_role(ResourceType.ENTITY, entity_id, role_type)


def get_organization_users_with_role(
    organization_id: int, role_type: Optional[ResourceRoleType] = None
) -> List[ResourceRole]:
    """Get all users with roles on an organization."""
    return ResourceRole.get_users_with_role(
        ResourceType.ORGANIZATION, organization_id, role_type
    )
