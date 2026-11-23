"""SQLAlchemy model for S3-backed async batch CSV import jobs."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import (
    JSON,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)

from apps.api.models.base import Base, IDMixin


class CsvImportJob(Base, IDMixin):
    """Tracks an asynchronous S3-backed CSV bulk import job."""

    __tablename__ = "csv_import_jobs"
    __table_args__ = (
        Index("ix_csv_import_jobs_tenant_status", "tenant_id", "status"),
        Index("ix_csv_import_jobs_status_created", "status", "created_at"),
    )

    job_id = Column(String(64), unique=True, nullable=False, index=True)
    tenant_id = Column(
        Integer,
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    resource = Column(String(64), nullable=False, index=True)
    filename = Column(String(255), nullable=False, server_default="")
    storage_backend = Column(String(32), nullable=False, server_default="s3")
    storage_key = Column(String(512), nullable=True)
    file_sha256 = Column(String(64), nullable=True)
    file_size_bytes = Column(Integer, nullable=True)
    status = Column(String(32), nullable=False, server_default="pending", index=True)
    total_rows = Column(Integer, nullable=False, server_default="0")
    imported_count = Column(Integer, nullable=False, server_default="0")
    skipped_count = Column(Integer, nullable=False, server_default="0")
    errors_json = Column(JSON, nullable=True)
    error_message = Column(Text, nullable=True)
    created_by_identity_id = Column(
        Integer,
        ForeignKey("identities.id", ondelete="SET NULL"),
        nullable=True,
    )
    worker_id = Column(String(128), nullable=True)
    started_at = Column(DateTime(timezone=True), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(
        DateTime(timezone=True),
        nullable=True,
        default=lambda: datetime.now(UTC),
    )
