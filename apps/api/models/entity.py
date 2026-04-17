"""Entity model for tracking infrastructure and organizational resources."""

# flake8: noqa: E501


from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    ForeignKey,
    Integer,
    String,
    DateTime,
)
from sqlalchemy.orm import Mapped, relationship

from apps.api.models.base import Base, IDMixin, TimestampMixin


class Entity(Base, IDMixin, TimestampMixin):
    """
    Entity model for tracking various infrastructure and organizational resources.

    Schema matches Alembic migration 011_create_base_tables.
    """

    __tablename__ = "entities"

    organization_id = Column(
        Integer,
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    parent_id = Column(
        Integer,
        ForeignKey("entities.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    name = Column(String(255), nullable=True, index=True)
    type = Column(String(64), nullable=False, index=True)
    sub_type = Column(String(64), nullable=True, index=True)
    external_id = Column(String(255), nullable=True)
    cloud_provider = Column(String(64), nullable=True)
    region = Column(String(255), nullable=True)
    status = Column(String(64), nullable=True)
    is_managed = Column(Boolean, nullable=True)
    tags = Column(JSON, nullable=True)
    entity_metadata = Column("metadata", JSON, nullable=True)
    last_seen_at = Column(DateTime(timezone=True), nullable=True)

    organization: Mapped["Organization"] = relationship(
        "Organization",
        back_populates="entities",
        foreign_keys=[organization_id],
    )
    parent: Mapped["Entity"] = relationship(
        "Entity",
        remote_side="Entity.id",
        backref="children",
        foreign_keys=[parent_id],
    )

    def __repr__(self) -> str:
        """String representation."""
        return f"<Entity(id={self.id}, name='{self.name}', type='{self.type}')>"
