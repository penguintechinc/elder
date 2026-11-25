"""DSAR (Data Subject Access Request) activity log.

Every self-service statutory-rights action (apps/api/api/v1/privacy.py) and
Enterprise bulk-admin action (apps/api/api/v1/privacy_admin.py) writes one row
here. This is the "admin dashboard/list of requests" convenience layer's data
source -- listing it is Enterprise-gated (see privacy_admin.py), but writing
to it happens on every tier so an Enterprise upgrade never loses history.
"""

from sqlalchemy import (
    JSON,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    SmallInteger,
    String,
    func,
)

from apps.api.models.base import Base, IDMixin, TenantScopedMixin, TimestampMixin


class DsarRequest(Base, IDMixin, TenantScopedMixin, TimestampMixin):
    """Record of a single DSAR action taken by or on behalf of a data subject."""

    __tablename__ = "dsar_requests"

    # Nullable since migration 054: a portal user (portal_users, the second
    # human-identity table) is a data subject too and has no identities row,
    # so exactly one of identity_id / portal_user_id is set per request.
    identity_id = Column(
        Integer,
        ForeignKey("identities.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    portal_user_id = Column(
        Integer,
        ForeignKey("portal_users.id", ondelete="CASCADE"),
        nullable=True,
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


class PrivacyConsentRecord(Base, IDMixin, TenantScopedMixin):
    """Append-only consent / lawful-basis ledger entry for one data subject.

    A withdrawal timestamp alone cannot prove what was consented to, when, or
    on which lawful basis (GDPR Art. 7(1)); every grant, withdrawal and
    CCPA/CPRA sale-or-sharing opt-out appends one row here and rows are never
    updated. Codes are the int enums in apps/api/services/privacy/inventory.py
    (SubjectType, ConsentPurpose, ConsentState, LawfulBasis).
    """

    __tablename__ = "privacy_consent_records"

    public_id = Column(String(36), nullable=False, unique=True, index=True)
    subject_type = Column(SmallInteger, nullable=False)
    subject_id = Column(Integer, nullable=False, index=True)
    purpose = Column(SmallInteger, nullable=False)
    state = Column(SmallInteger, nullable=False)
    lawful_basis = Column(SmallInteger, nullable=False)
    # server_default: penguin-dal inserts never apply an ORM-side default.
    created_at = Column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class PrivacyErasureReceipt(Base, IDMixin, TenantScopedMixin):
    """PII-free receipt of one right-to-erasure run (what was erased/retained, why).

    Written before the sweep starts (status in-progress) and finalised after
    the subject row is anonymised, so an interrupted erasure is visible and
    resumable. `receipt` holds only table/column names, row counts, action
    codes and legal-basis text -- never a subject's personal data.
    """

    __tablename__ = "privacy_erasure_receipts"

    public_id = Column(String(36), nullable=False, unique=True, index=True)
    subject_type = Column(SmallInteger, nullable=False)
    subject_id = Column(Integer, nullable=False, index=True)
    requested_by_id = Column(Integer, nullable=True)
    status = Column(SmallInteger, nullable=False, server_default="1")
    receipt = Column(JSON, nullable=True)
    created_at = Column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    completed_at = Column(DateTime(timezone=True), nullable=True)
