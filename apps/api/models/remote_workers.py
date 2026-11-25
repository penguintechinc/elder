"""SQLAlchemy model for the remote workers fleet registry."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
)

from apps.api.models.base import Base, IDMixin


class RemoteWorker(Base, IDMixin):
    """Registered remote or in-cluster worker (runworker / scanner) with liveness state."""

    __tablename__ = "remote_workers"
    __table_args__ = (
        Index("ix_remote_workers_type_cluster", "worker_type", "cluster"),
    )

    worker_id = Column(String(64), unique=True, nullable=False, index=True)
    tenant_id = Column(
        Integer,
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    worker_type = Column(String(32), nullable=False, index=True)
    name = Column(String(255), nullable=False)
    cluster = Column(String(128), nullable=True, index=True)
    token_hash = Column(String(64), nullable=True, index=True)
    token_prefix = Column(String(16), nullable=True)
    config_json = Column(JSON, nullable=True)
    capabilities_json = Column(JSON, nullable=True)
    hostname = Column(String(255), nullable=True)
    version = Column(String(64), nullable=True)
    is_enabled = Column(Boolean, nullable=False, default=True)
    last_heartbeat_at = Column(DateTime(timezone=True), nullable=True)
    revoked_at = Column(DateTime(timezone=True), nullable=True)
    created_by_identity_id = Column(
        Integer,
        ForeignKey("identities.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at = Column(
        DateTime(timezone=True),
        nullable=True,
        default=lambda: datetime.now(UTC),
    )
    updated_at = Column(
        DateTime(timezone=True),
        nullable=True,
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )
