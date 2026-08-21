"""Helpdesk module models — internal ticketing: tickets, SLA, canned responses, teams.

Scope: INTERNAL helpdesk only (employees/contractors). The customer-relations
half (CRM companies/contacts, public intake forms + CAPTCHA, customer email
intake) was removed and handed off to Waddles — see
docs/superpowers/specs/2026-08-21-helpdesk-internal-reframe-phase3.md.
"""

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

from apps.api.models.base import (
    Base,
    IDMixin,
    TenantScopedMixin,
    TimestampMixin,
    VillageIDMixin,
)


class HdTicket(Base, IDMixin, TenantScopedMixin, VillageIDMixin, TimestampMixin):
    """Internal support ticket model."""

    __tablename__ = "hd_tickets"

    subject = Column(String(500), nullable=False)
    status = Column(
        String(30),
        default="new",
        nullable=False,
        comment="new, open, pending, on_hold, resolved, closed",
    )
    priority = Column(
        String(20),
        default="medium",
        nullable=False,
        comment="low, medium, high, urgent, critical",
    )
    channel = Column(
        String(20),
        default="web",
        nullable=False,
        comment="web, email, api",
    )
    # Requester is an internal identity (portal/agent-created). Nullable so a
    # system-created ticket without an explicit requester is still valid.
    requester_identity_id = Column(
        Integer,
        ForeignKey("identities.id", ondelete="RESTRICT"),
        nullable=True,
    )
    assignee_identity_id = Column(
        Integer,
        ForeignKey("identities.id", ondelete="SET NULL"),
        nullable=True,
    )
    hd_team_id = Column(
        Integer,
        ForeignKey("hd_teams.id", ondelete="SET NULL"),
        nullable=True,
    )
    category = Column(String(100), nullable=True)
    tags = Column(JSON, nullable=True, comment="JSON array of tags")
    hd_sla_policy_id = Column(
        Integer,
        ForeignKey("hd_sla_policies.id", ondelete="SET NULL"),
        nullable=True,
    )
    sla_breach_at = Column(DateTime(timezone=True), nullable=True)
    first_response_at = Column(DateTime(timezone=True), nullable=True)
    resolved_at = Column(DateTime(timezone=True), nullable=True)
    closed_at = Column(DateTime(timezone=True), nullable=True)


class HdTicketMessage(Base, IDMixin, TimestampMixin):
    """Messages within a ticket."""

    __tablename__ = "hd_ticket_messages"

    hd_ticket_id = Column(
        Integer,
        ForeignKey("hd_tickets.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    sender_identity_id = Column(
        Integer,
        ForeignKey("identities.id", ondelete="RESTRICT"),
        nullable=False,
    )
    message_type = Column(
        String(20),
        nullable=False,
        comment="reply, note, system",
    )
    body_text = Column(Text, nullable=True)
    body_html = Column(Text, nullable=True)
    is_internal = Column(Boolean, default=False, nullable=False)
    email_message_id = Column(String(255), nullable=True, comment="RFC 2822 Message-ID")


class HdTicketAttachment(Base, IDMixin, TimestampMixin):
    """Attachments in messages or tickets."""

    __tablename__ = "hd_ticket_attachments"

    hd_ticket_id = Column(
        Integer,
        ForeignKey("hd_tickets.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    hd_message_id = Column(
        Integer,
        ForeignKey("hd_ticket_messages.id", ondelete="CASCADE"),
        nullable=True,
    )
    filename = Column(String(255), nullable=False)
    content_type = Column(String(100), nullable=True)
    size_bytes = Column(Integer, nullable=True)
    storage_path = Column(String(500), nullable=False)


class HdSlaPolicy(Base, IDMixin, TenantScopedMixin):
    """Service Level Agreement policies."""

    __tablename__ = "hd_sla_policies"

    name = Column(String(255), nullable=False)
    priority = Column(String(20), nullable=False)
    first_response_hours = Column(Integer, nullable=False)
    resolution_hours = Column(Integer, nullable=False)
    business_hours_only = Column(Boolean, default=True, nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)


class HdCannedResponse(Base, IDMixin, TenantScopedMixin, TimestampMixin):
    """Pre-written response templates."""

    __tablename__ = "hd_canned_responses"

    title = Column(String(255), nullable=False)
    body_html = Column(Text, nullable=False)
    category = Column(String(100), nullable=True)
    created_by_identity_id = Column(
        Integer,
        ForeignKey("identities.id", ondelete="SET NULL"),
        nullable=True,
    )
    is_shared = Column(Boolean, default=True, nullable=False)


class HdTeam(Base, IDMixin, TenantScopedMixin, VillageIDMixin, TimestampMixin):
    """Support team."""

    __tablename__ = "hd_teams"

    name = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)


class HdTeamMember(Base):
    """M:N relationship between Team and Identity."""

    __tablename__ = "hd_team_members"

    hd_team_id = Column(
        Integer,
        ForeignKey("hd_teams.id", ondelete="CASCADE"),
        primary_key=True,
    )
    identity_id = Column(
        Integer,
        ForeignKey("identities.id", ondelete="CASCADE"),
        primary_key=True,
    )
    role = Column(String(30), default="member", nullable=False)
