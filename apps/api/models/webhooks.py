# flake8: noqa: E501
"""Webhook and notification rule models."""

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, JSON, String, Text

from apps.api.models.base import Base, IDMixin, TimestampMixin


class Webhook(Base, IDMixin, TimestampMixin):
    """Outbound webhook configuration."""

    __tablename__ = "webhooks"

    name = Column(String(255), nullable=False)
    url = Column(String(2048), nullable=False)
    secret = Column(String(255), nullable=True)
    events = Column(JSON, nullable=False)
    enabled = Column(Boolean, nullable=False)
    organization_id = Column(Integer, ForeignKey("organizations.id"), nullable=False)


class WebhookDelivery(Base, IDMixin):
    """Webhook delivery audit records."""

    __tablename__ = "webhook_deliveries"

    webhook_id = Column(Integer, ForeignKey("webhooks.id"), nullable=False)
    event_type = Column(String(100), nullable=False)
    payload = Column(JSON, nullable=False)
    response_status = Column(Integer, nullable=True)
    response_body = Column(Text, nullable=True)
    delivered_at = Column(DateTime(timezone=True), nullable=False)
    success = Column(Boolean, nullable=False)


class NotificationRule(Base, IDMixin, TimestampMixin):
    """Notification rules for various channels."""

    __tablename__ = "notification_rules"

    name = Column(String(255), nullable=False)
    channel = Column(String(50), nullable=False)
    events = Column(JSON, nullable=False)
    config_json = Column(JSON, nullable=False)
    enabled = Column(Boolean, nullable=False)
    organization_id = Column(Integer, ForeignKey("organizations.id"), nullable=False)
