"""Role-Based Access Control (RBAC) models."""

# flake8: noqa: E501


import enum
from typing import List, Optional

from sqlalchemy import Column, Enum, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, relationship

from apps.api.models.base import Base, IDMixin, TimestampMixin


class RoleScope(enum.Enum):
    """Scope of role assignment."""

    GLOBAL = "global"  # Role applies globally across all organizations
    ORGANIZATION = "organization"  # Role applies only to specific organization


class Role(Base, IDMixin, TimestampMixin):
    """
    Role model for RBAC.

    Predefined roles:
    - super_admin: Full system access
    - org_admin: Full access within organization
    - editor: Can create and edit entities
    - viewer: Read-only access
    """

    __tablename__ = "roles"

    name = Column(String(100), nullable=False, unique=True, index=True)
    description = Column(String(512), nullable=True)

    # Relationships
    permissions: Mapped[List["RolePermission"]] = relationship(
        "RolePermission",
        back_populates="role",
        cascade="all, delete-orphan",
    )

    user_roles: Mapped[List["UserRole"]] = relationship(
        "UserRole",
        back_populates="role",
        cascade="all, delete-orphan",
    )

    def __repr__(self) -> str:
        """String representation of role."""
        return f"<Role(id={self.id}, name='{self.name}')>"

    def has_permission(self, permission_name: str) -> bool:
        """
        Check if role has a specific permission.

        Args:
            permission_name: Name of the permission

        Returns:
            True if role has permission, False otherwise
        """
        return any(rp.permission.name == permission_name for rp in self.permissions)

    def get_permission_names(self) -> List[str]:
        """
        Get all permission names for this role.

        Returns:
            List of permission names
        """
        return [rp.permission.name for rp in self.permissions]


class Permission(Base, IDMixin, TimestampMixin):
    """
    Permission model for RBAC.

    Permissions follow pattern: {action}_{resource}
    Examples:
    - create_entity
    - edit_entity
    - delete_entity
    - view_entity
    - create_organization
    - edit_organization
    - delete_organization
    - manage_users
    - manage_roles
    """

    __tablename__ = "permissions"

    name = Column(
        String(100),
        nullable=False,
        unique=True,
        index=True,
        comment="Permission name (e.g., create_entity, edit_organization)",
    )
    description = Column(String(512), nullable=True)

    resource_type = Column(
        String(50),
        nullable=False,
        index=True,
        comment="Resource type (entity, organization, user, role, etc.)",
    )

    action = Column(
        String(50),
        nullable=False,
        index=True,
        comment="Action (create, edit, delete, view, manage)",
    )

    # Relationships
    roles: Mapped[List["RolePermission"]] = relationship(
        "RolePermission",
        back_populates="permission",
        cascade="all, delete-orphan",
    )

    def __repr__(self) -> str:
        """String representation of permission."""
        return f"<Permission(id={self.id}, name='{self.name}')>"


class RolePermission(Base, IDMixin, TimestampMixin):
    """
    Many-to-many relationship between roles and permissions.
    """

    __tablename__ = "role_permissions"

    role_id = Column(
        Integer,
        ForeignKey("roles.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    permission_id = Column(
        Integer,
        ForeignKey("permissions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # Unique constraint to prevent duplicate role-permission pairs
    __table_args__ = (
        UniqueConstraint("role_id", "permission_id", name="uix_role_permission"),
    )

    # Relationships
    role: Mapped["Role"] = relationship(
        "Role",
        back_populates="permissions",
    )

    permission: Mapped["Permission"] = relationship(
        "Permission",
        back_populates="roles",
    )

    def __repr__(self) -> str:
        """String representation of role-permission relationship."""
        return f"<RolePermission(role_id={self.role_id}, permission_id={self.permission_id})>"


class UserRole(Base, IDMixin, TimestampMixin):
    """
    Assignment of roles to users with optional organization scope.
    """

    __tablename__ = "user_roles"

    identity_id = Column(
        Integer,
        ForeignKey("identities.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    role_id = Column(
        Integer,
        ForeignKey("roles.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # Scope
    scope = Column(
        Enum(RoleScope),
        nullable=False,
        default=RoleScope.GLOBAL,
        index=True,
        comment="Scope of role assignment (global or organization)",
    )

    # scope_id: PyDAL uses scope_id for scoped resource ID (e.g., organization ID)
    scope_id = Column(
        Integer,
        nullable=True,
        index=True,
        comment="Scoped resource ID (e.g., organization ID) for scoped role assignments",
    )

    organization_id = Column(
        Integer,
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
        comment="Organization scope (null for global scope)",
    )

    # Unique constraint to prevent duplicate user-role-org assignments
    __table_args__ = (
        UniqueConstraint(
            "identity_id",
            "role_id",
            "organization_id",
            name="uix_user_role_org",
        ),
    )

    # Relationships
    identity: Mapped["Identity"] = relationship(
        "Identity",
        back_populates="roles",
    )

    role: Mapped["Role"] = relationship(
        "Role",
        back_populates="user_roles",
    )

    organization: Mapped[Optional["Organization"]] = relationship(
        "Organization",
        backref="user_roles",
    )

    def __repr__(self) -> str:
        """String representation of user role assignment."""
        return (
            f"<UserRole(identity_id={self.identity_id}, "
            f"role_id={self.role_id}, "
            f"scope={self.scope.value}, "
            f"org_id={self.organization_id})>"
        )

    def is_global(self) -> bool:
        """Check if role is global scope."""
        return self.scope == RoleScope.GLOBAL

    def is_org_scoped(self) -> bool:
        """Check if role is organization-scoped."""
        return self.scope == RoleScope.ORGANIZATION

    def applies_to_organization(self, organization_id: int) -> bool:
        """
        Check if this role assignment applies to a specific organization.

        Args:
            organization_id: Organization ID to check

        Returns:
            True if role applies to the organization
        """
        if self.is_global():
            return True

        return self.organization_id == organization_id
