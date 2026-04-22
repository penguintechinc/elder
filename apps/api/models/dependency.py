"""Dependency model for tracking relationships between resources."""

# flake8: noqa: E501


from sqlalchemy import JSON, Column, ForeignKey, Integer, String

from apps.api.models.base import Base, IDMixin, TimestampMixin


class Dependency(Base, IDMixin, TimestampMixin):
    """
    Dependency relationship between any two resources.

    Schema matches Alembic migration 011_create_base_tables.
    Uses polymorphic source/target pattern (source_type/source_id).
    """

    __tablename__ = "dependencies"

    tenant_id = Column(
        Integer,
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    source_type = Column(String(64), nullable=False, index=True)
    source_id = Column(Integer, nullable=False, index=True)
    target_type = Column(String(64), nullable=False, index=True)
    target_id = Column(Integer, nullable=False, index=True)
    dependency_type = Column(String(64), nullable=True, index=True)
    village_id = Column(String(32), unique=True, nullable=True, index=True)
    dep_metadata = Column("metadata", JSON, nullable=True)

    def __repr__(self) -> str:
        """String representation of dependency."""
        return (
            f"<Dependency(id={self.id}, "
            f"source={self.source_type}/{self.source_id}, "
            f"target={self.target_type}/{self.target_id}, "
            f"type={self.dependency_type})>"
        )
