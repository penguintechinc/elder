# flake8: noqa: E501
"""Project, milestone, label, issue linkage, and saved search models."""

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
)

from apps.api.models.base import Base, IDMixin, TimestampMixin


class Project(Base, IDMixin, TimestampMixin):
    """Project management."""

    __tablename__ = "projects"

    name = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    status = Column(String(50), nullable=False)
    organization_id = Column(Integer, ForeignKey("organizations.id"), nullable=False)
    start_date = Column(Date, nullable=True)
    end_date = Column(Date, nullable=True)
    village_id = Column(String(32), unique=True, nullable=True)


class Milestone(Base, IDMixin, TimestampMixin):
    """Project milestones."""

    __tablename__ = "milestones"

    title = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    status = Column(String(50), nullable=False)
    organization_id = Column(Integer, ForeignKey("organizations.id"), nullable=False)
    project_id = Column(Integer, ForeignKey("projects.id"), nullable=True)
    due_date = Column(Date, nullable=True)
    closed_at = Column(DateTime(timezone=True), nullable=True)
    village_id = Column(String(32), unique=True, nullable=True)


class Label(Base, IDMixin, TimestampMixin):
    """Generic labels for cross-resource labeling."""

    __tablename__ = "labels"

    name = Column(String(100), nullable=False)
    color = Column(String(7), nullable=True)
    description = Column(String(512), nullable=True)
    organization_id = Column(Integer, ForeignKey("organizations.id"), nullable=False)


class SavedSearch(Base, IDMixin):
    """User-saved search queries."""

    __tablename__ = "saved_searches"

    name = Column(String(255), nullable=False)
    query = Column(Text, nullable=False)
    filters = Column(JSON, nullable=True)
    identity_id = Column(Integer, ForeignKey("identities.id"), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=True)
