# flake8: noqa: E501
"""Access review and group access request models."""

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, String, Text

from apps.api.models.base import Base, IDMixin, TimestampMixin


class AccessReview(Base, IDMixin, TimestampMixin):
    """Periodic group membership access reviews."""

    __tablename__ = "access_reviews"

    tenant_id = Column(Integer, nullable=False)
    group_id = Column(Integer, ForeignKey("identity_groups.id"), nullable=False)
    review_period_start = Column(DateTime(timezone=True), nullable=False)
    review_period_end = Column(DateTime(timezone=True), nullable=False)
    due_date = Column(DateTime(timezone=True), nullable=False)
    status = Column(String(20), nullable=False)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    completed_by_id = Column(Integer, nullable=True)
    total_members = Column(Integer, nullable=True)
    members_reviewed = Column(Integer, nullable=True)
    members_kept = Column(Integer, nullable=True)
    members_removed = Column(Integer, nullable=True)
    auto_apply_decisions = Column(Boolean, nullable=False)
    village_id = Column(String(32), unique=True, nullable=True)


class AccessReviewItem(Base, IDMixin):
    """Individual member review items within an access review."""

    __tablename__ = "access_review_items"

    tenant_id = Column(Integer, nullable=False)
    review_id = Column(Integer, ForeignKey("access_reviews.id"), nullable=False)
    membership_id = Column(Integer, ForeignKey("identity_group_memberships.id"), nullable=False)
    identity_id = Column(Integer, ForeignKey("identities.id"), nullable=False)
    decision = Column(String(20), nullable=True)
    justification = Column(Text, nullable=True)
    new_expiration = Column(DateTime(timezone=True), nullable=True)
    reviewed_by_id = Column(Integer, nullable=True)
    reviewed_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=True)


class AccessReviewAssignment(Base, IDMixin):
    """Tracks who is assigned to perform an access review."""

    __tablename__ = "access_review_assignments"

    tenant_id = Column(Integer, nullable=False)
    review_id = Column(Integer, ForeignKey("access_reviews.id"), nullable=False)
    reviewer_identity_id = Column(Integer, ForeignKey("identities.id"), nullable=False)
    assigned_at = Column(DateTime(timezone=True), nullable=True)
    completed = Column(Boolean, nullable=False)
    completed_at = Column(DateTime(timezone=True), nullable=True)


class GroupAccessRequest(Base, IDMixin, TimestampMixin):
    """Requests for group membership access."""

    __tablename__ = "group_access_requests"

    tenant_id = Column(Integer, nullable=False)
    group_id = Column(Integer, ForeignKey("identity_groups.id"), nullable=False)
    requester_id = Column(Integer, ForeignKey("identities.id"), nullable=False)
    status = Column(String(20), nullable=True)
    reason = Column(Text, nullable=True)
    expires_at = Column(DateTime(timezone=True), nullable=True)
    decided_at = Column(DateTime(timezone=True), nullable=True)
    decided_by_id = Column(Integer, nullable=True)
    decision_comment = Column(Text, nullable=True)
    village_id = Column(String(32), unique=True, nullable=True)


class GroupAccessApproval(Base, IDMixin):
    """Individual approval decisions for group access requests."""

    __tablename__ = "group_access_approvals"

    tenant_id = Column(Integer, nullable=False)
    request_id = Column(Integer, ForeignKey("group_access_requests.id"), nullable=False)
    approver_id = Column(Integer, ForeignKey("identities.id"), nullable=False)
    decision = Column(String(20), nullable=False)
    comment = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=True)
