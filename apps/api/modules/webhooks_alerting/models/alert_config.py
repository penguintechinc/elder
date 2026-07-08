"""Alert configuration model for per-OU incident alerting."""

# flake8: noqa: E501


import enum
from typing import Optional

from sqlalchemy import JSON, Column, Enum, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, relationship

from apps.api.models.base import Base, IDMixin, TimestampMixin


class AlertDestinationType(enum.Enum):
    """Types of alert destinations."""

    EMAIL = "email"
    WEBHOOK = "webhook"
    PAGERDUTY = "pagerduty"
    SLACK = "slack"


class AlertConfiguration(Base, IDMixin, TimestampMixin):
    """
    Per-organization alert configuration.

    Defines where incident alerts should be sent when an issue marked as incident
    is linked to an organization. Multiple destinations can be configured per organization.

    Supported Destinations:
    - Email: Send via SMTP to specified addresses
    - Webhook: HTTP POST to custom endpoint
    - PagerDuty: Trigger PagerDuty incident
    - Slack: Post to Slack channel
    """

    __tablename__ = "alert_configurations"

    # Organization association
    organization_id = Column(
        Integer,
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="Organization this alert configuration belongs to",
    )

    # Alert destination type
    destination_type = Column(
        Enum(AlertDestinationType),
        nullable=False,
        index=True,
        comment="Type of alert destination (email, webhook, pagerduty, slack)",
    )

    # Configuration name/description
    name = Column(
        String(255),
        nullable=False,
        comment="Friendly name for this alert configuration",
    )

    # Enabled flag
    enabled = Column(
        Integer,
        nullable=False,
        default=1,
        index=True,
        comment="Whether this alert configuration is active (0=disabled, 1=enabled)",
    )

    # Destination-specific configuration (JSON)
    # Email: {"to": ["email1@example.com", "email2@example.com"], "cc": [], "subject_prefix": "[INCIDENT]"}
    # Webhook: {"url": "https://hooks.example.com/incident", "headers": {"Authorization": "Bearer token"}, "method": "POST"}
    # PagerDuty: {"service_key": "xxx", "urgency": "high", "dedup_key_prefix": "elder"}
    # Slack: {"webhook_url": "https://hooks.slack.com/...", "channel": "#incidents"}
    config = Column(
        JSON,
        nullable=False,
        comment="Destination-specific configuration as JSON",
    )

    # Severity filter (optional - only alert for specific severities)
    # JSON array: ["high", "critical"] or null for all
    severity_filter = Column(
        JSON,
        nullable=True,
        comment="Optional filter: only alert for specific priority levels",
    )

    # Relationships
    organization: Mapped[Optional["Organization"]] = relationship(
        "Organization",
        back_populates="alert_configurations",
    )

    def __repr__(self):
        return f"<AlertConfiguration(id={self.id}, org={self.organization_id}, type={self.destination_type.value}, name='{self.name}')>"
