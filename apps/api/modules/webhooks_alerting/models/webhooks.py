# flake8: noqa: E501
"""Webhook and notification rule models."""

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
)

from apps.api.models.base import Base, IDMixin, TimestampMixin, VillageIDMixin


class Webhook(Base, IDMixin, VillageIDMixin, TimestampMixin):
    """Tenant-scoped outbound webhook configuration.

    Reconciled to the real (already-deployed) migration-011 schema
    (`is_active`/`events`/`headers`, not the `enabled`/`events_json`/
    `headers_json` this model previously drifted to) and extended (037) with
    tenant_id, village_id, additive assignment-event filters, and a
    universal metadata bag.
    """

    __tablename__ = "webhooks"

    tenant_id = Column(
        Integer,
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
        comment="Tenant this webhook belongs to",
    )
    organization_id = Column(
        Integer, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=True
    )
    name = Column(String(255), nullable=False)
    url = Column(String(1024), nullable=False)
    secret = Column(String(512), nullable=True)
    is_active = Column(Boolean, nullable=False, default=True)
    events = Column(
        JSON, nullable=True, comment="Subscribed event types, e.g. ['issue.assigned']"
    )
    headers = Column(
        JSON, nullable=True, comment="Custom headers sent with every delivery"
    )
    filter_issue_type = Column(
        String(30),
        nullable=True,
        comment="Optional issue_type filter for issue.assigned (e.g. SUPPORT); unset matches any",
    )
    filter_assignee_type = Column(
        String(16),
        nullable=True,
        comment="Optional assignee_type filter for issue.assigned ('identity' or 'org_unit')",
    )
    filter_assignee_id = Column(
        Integer,
        nullable=True,
        comment="Optional assignee_id filter for issue.assigned",
    )
    # `metadata` is reserved on SQLAlchemy declarative models (Base.metadata),
    # so the Python attribute is named webhook_metadata while the DB column
    # stays `metadata` (same pattern as Issue.issue_metadata).
    webhook_metadata = Column(
        "metadata", JSON, nullable=True, comment="Universal free-form JSON metadata bag"
    )
    last_triggered_at = Column(DateTime(timezone=True), nullable=True)


class WebhookDelivery(Base, IDMixin):
    """Webhook delivery audit record — one row per delivery attempt.

    Reconciled to the real migration-011 schema: `status`/`http_status`/
    `request_payload`/`error_message`/`attempt_count`, no `success` boolean,
    no `duration_ms`, no `updated_at` (this table is insert-then-update-in-
    place, never re-timestamped as "updated").
    """

    __tablename__ = "webhook_deliveries"

    webhook_id = Column(
        Integer, ForeignKey("webhooks.id", ondelete="CASCADE"), nullable=False
    )
    event_type = Column(String(255), nullable=True)
    status = Column(String(64), nullable=True, comment="'success' or 'failed'")
    http_status = Column(Integer, nullable=True)
    request_payload = Column(JSON, nullable=True)
    response_body = Column(Text, nullable=True)
    error_message = Column(Text, nullable=True)
    attempt_count = Column(Integer, nullable=True)
    delivered_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=True)


class NotificationRule(Base, IDMixin, TimestampMixin):
    """Notification rules for various channels.

    NOTE: this model also drifts from its migration-011 schema (`event_types`/
    `conditions`/`channels`/`is_active`, not `events`/`config_json`/`enabled`)
    but notification_rules is not on any path this plan touches (assignment
    webhooks are delivered exclusively through `webhooks`/`webhook_deliveries`)
    — left unchanged here; reconciling it is a separate, tracked follow-up.
    """

    __tablename__ = "notification_rules"

    name = Column(String(255), nullable=False)
    channel = Column(String(50), nullable=False)
    events = Column(JSON, nullable=False)
    config_json = Column(JSON, nullable=False)
    enabled = Column(Boolean, nullable=False)
    organization_id = Column(Integer, ForeignKey("organizations.id"), nullable=False)
