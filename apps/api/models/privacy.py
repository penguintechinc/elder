"""DSAR (Data Subject Access Request) activity log.

Every self-service statutory-rights action (apps/api/api/v1/privacy.py) and
Enterprise bulk-admin action (apps/api/api/v1/privacy_admin.py) writes one row
here. This is the "admin dashboard/list of requests" convenience layer's data
source -- listing it is Enterprise-gated (see privacy_admin.py), but writing
to it happens on every tier so an Enterprise upgrade never loses history.
"""

from sqlalchemy import Column, ForeignKey, Integer, String

from apps.api.models.base import Base, IDMixin, TenantScopedMixin, TimestampMixin


class DsarRequest(Base, IDMixin, TenantScopedMixin, TimestampMixin):
    """Record of a single DSAR action taken by or on behalf of a data subject."""

    __tablename__ = "dsar_requests"

    identity_id = Column(
        Integer,
        ForeignKey("identities.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    request_type = Column(
        String(32),
        nullable=False,
        comment="One of: access, erasure, consent_opt_out, consent_opt_in, bulk_erasure",
    )
    # server_default (not just default=) matches alembic/versions/041_dsar_
    # privacy_fields.py -- runtime inserts go through penguin-dal, not the
    # SQLAlchemy ORM session, so a Python-side Column default is never
    # applied.
    status = Column(String(20), nullable=False, server_default="completed")
    requested_by_identity_id = Column(
        Integer,
        nullable=True,
        comment="Actor identity id -- equals identity_id for self-service, "
        "the admin's id for Enterprise bulk actions",
    )
