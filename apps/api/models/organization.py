"""Organization model for hierarchical organizational structure."""

# flake8: noqa: E501


from typing import List, Optional

from sqlalchemy import JSON, Boolean, Column, ForeignKey, Integer, String, Text, text
from sqlalchemy.orm import Mapped, relationship

from apps.api.models.base import Base, IDMixin, TimestampMixin


class Organization(Base, IDMixin, TimestampMixin):
    """
    Hierarchical organization model.

    Schema matches Alembic migration 011_create_base_tables.
    """

    __tablename__ = "organizations"

    tenant_id = Column(
        Integer,
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    parent_id = Column(
        Integer,
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    name = Column(String(255), nullable=False, index=True)
    slug = Column(String(255), nullable=True)
    display_name = Column(String(255), nullable=True)
    description = Column(Text, nullable=True)
    type = Column(String(64), nullable=True, index=True)
    cloud_provider = Column(String(64), nullable=True)
    cloud_account_id = Column(String(255), nullable=True)
    region = Column(String(255), nullable=True)
    owner_identity_id = Column(
        Integer,
        ForeignKey("identities.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    owner_group_id = Column(
        Integer,
        ForeignKey("identity_groups.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    is_active = Column(Boolean, nullable=False, server_default=text("true"))
    settings = Column(JSON, nullable=True)
    tags = Column(JSON, nullable=True)
    org_metadata = Column('metadata', JSON, nullable=True)

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

    entities: Mapped[List["Entity"]] = relationship(
        "Entity",
        back_populates="organization",
        cascade="all, delete-orphan",
    )

    def __repr__(self) -> str:
        """String representation."""
        return f"<Organization(id={self.id}, name='{self.name}')>"
