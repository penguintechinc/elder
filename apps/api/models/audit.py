"""Audit log model for tracking all system changes."""

# flake8: noqa: E501


from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import JSON, Boolean, Column, DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, relationship

from apps.api.models.base import Base, IDMixin


class AuditLog(Base, IDMixin):
    """
    Audit log for tracking all system changes and access.

    Schema matches PyDAL pydal_models.py audit_logs table exactly.
    PyDAL handles all runtime queries; this model is for create_all() only.
    """

    __tablename__ = "audit_logs"

    # Actor (who performed the action)
    identity_id = Column(
        Integer,
        ForeignKey("identities.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
        comment="Identity that performed the action",
    )

    # PyDAL uses action_name (string), not an Enum
    action_name = Column(
        String(50),
        nullable=True,
        index=True,
        comment="Action performed (e.g. create, update, delete, login)",
    )

    resource_type = Column(
        String(50),
        nullable=True,
        index=True,
        comment="Type of resource affected",
    )

    resource_id = Column(
        Integer,
        nullable=True,
        index=True,
        comment="ID of the affected resource",
    )

    # PyDAL uses 'details' (JSON) for change context
    details = Column(
        JSON,
        nullable=True,
        comment="Details and context of the action (JSON)",
    )

    # Request context
    ip_address = Column(
        String(45),
        nullable=True,
        index=True,
        comment="IP address of requester (IPv4 or IPv6)",
    )

    user_agent = Column(
        String(512),
        nullable=True,
        comment="User agent string",
    )

    # Result
    success = Column(
        Boolean,
        nullable=True,
        default=True,
        comment="Whether action succeeded",
    )

    created_at = Column(
        DateTime(timezone=True),
        nullable=True,
        comment="When the action occurred",
    )

    # Relationships
    identity: Mapped[Optional["Identity"]] = relationship(
        "Identity",
        backref="audit_logs",
    )

    def __repr__(self) -> str:
        """String representation of audit log entry."""
        return (
            f"<AuditLog(id={self.id}, "
            f"action_name={self.action_name}, "
            f"resource_type={self.resource_type}, "
            f"resource_id={self.resource_id})>"
        )
