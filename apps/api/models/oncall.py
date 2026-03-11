# flake8: noqa: E501
"""On-call rotation models."""

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


class OnCallRotation(Base, IDMixin, TimestampMixin):
    """On-call rotation configuration."""

    __tablename__ = "on_call_rotations"

    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=False)
    village_id = Column(String(32), unique=True, nullable=True)
    name = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    is_active = Column(Boolean, nullable=False)
    scope_type = Column(String(50), nullable=False)
    organization_id = Column(Integer, ForeignKey("organizations.id"), nullable=True)
    service_id = Column(Integer, ForeignKey("services.id"), nullable=True)
    schedule_type = Column(String(50), nullable=False)
    rotation_length_days = Column(Integer, nullable=True)
    rotation_start_date = Column(Date, nullable=True)
    schedule_cron = Column(String(255), nullable=True)
    handoff_timezone = Column(String(100), nullable=True)
    shift_split = Column(Boolean, nullable=True)
    shift_config = Column(JSON, nullable=True)


class OnCallRotationParticipant(Base, IDMixin, TimestampMixin):
    """People in an on-call rotation."""

    __tablename__ = "on_call_rotation_participants"

    rotation_id = Column(Integer, ForeignKey("on_call_rotations.id"), nullable=False)
    identity_id = Column(Integer, ForeignKey("identities.id"), nullable=False)
    order_index = Column(Integer, nullable=False)
    is_active = Column(Boolean, nullable=False)
    start_date = Column(Date, nullable=True)
    end_date = Column(Date, nullable=True)
    notification_email = Column(String(255), nullable=True)
    notification_phone = Column(String(50), nullable=True)
    notification_slack = Column(String(255), nullable=True)


class OnCallShift(Base, IDMixin):
    """Historical record of who was on-call."""

    __tablename__ = "on_call_shifts"

    rotation_id = Column(Integer, ForeignKey("on_call_rotations.id"), nullable=False)
    identity_id = Column(Integer, ForeignKey("identities.id"), nullable=False)
    shift_start = Column(DateTime(timezone=True), nullable=False)
    shift_end = Column(DateTime(timezone=True), nullable=False)
    is_override = Column(Boolean, nullable=False)
    override_id = Column(Integer, ForeignKey("on_call_overrides.id"), nullable=True)
    alerts_received = Column(Integer, nullable=True)
    incidents_created = Column(Integer, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=True)


class OnCallOverride(Base, IDMixin):
    """Temporary on-call substitutions."""

    __tablename__ = "on_call_overrides"

    rotation_id = Column(Integer, ForeignKey("on_call_rotations.id"), nullable=False)
    original_identity_id = Column(Integer, ForeignKey("identities.id"), nullable=False)
    override_identity_id = Column(Integer, ForeignKey("identities.id"), nullable=False)
    start_datetime = Column(DateTime(timezone=True), nullable=False)
    end_datetime = Column(DateTime(timezone=True), nullable=False)
    reason = Column(String(512), nullable=True)
    created_by_id = Column(Integer, ForeignKey("identities.id"), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=True)


class OnCallNotification(Base, IDMixin):
    """Notification audit trail for on-call events."""

    __tablename__ = "on_call_notifications"

    rotation_id = Column(Integer, ForeignKey("on_call_rotations.id"), nullable=True)
    identity_id = Column(Integer, ForeignKey("identities.id"), nullable=True)
    notification_type = Column(String(50), nullable=False)
    channel = Column(String(50), nullable=False)
    subject = Column(String(512), nullable=True)
    message = Column(Text, nullable=True)
    extra_metadata = Column("metadata", JSON, nullable=True)
    status = Column(String(50), nullable=True)
    error_message = Column(Text, nullable=True)
    sent_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=True)


class OnCallEscalationPolicy(Base, IDMixin, TimestampMixin):
    """Backup contacts and escalation rules for rotations."""

    __tablename__ = "on_call_escalation_policies"

    rotation_id = Column(Integer, ForeignKey("on_call_rotations.id"), nullable=False)
    level = Column(Integer, nullable=False)
    escalation_type = Column(String(50), nullable=False)
    identity_id = Column(Integer, ForeignKey("identities.id"), nullable=True)
    group_id = Column(Integer, ForeignKey("identity_groups.id"), nullable=True)
    escalation_delay_minutes = Column(Integer, nullable=True)
    notification_channels = Column(JSON, nullable=True)
