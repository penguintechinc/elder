"""Helpdesk module models - tickets, SLA, email, teams, forms, CRM."""

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)

from apps.api.models.base import Base, IDMixin, TenantScopedMixin, TimestampMixin, VillageIDMixin


class HdTicket(Base, IDMixin, TenantScopedMixin, VillageIDMixin, TimestampMixin):
    """Support ticket model."""

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
    requester_identity_id = Column(
        Integer,
        ForeignKey("identities.id", ondelete="RESTRICT"),
        nullable=False,
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


class HdEmailAccount(Base, IDMixin, TenantScopedMixin, TimestampMixin):
    """SMTP/IMAP/Gmail integrations."""

    __tablename__ = "hd_email_accounts"

    display_name = Column(String(255), nullable=True)
    email_address = Column(String(255), nullable=False)
    provider = Column(
        String(20),
        nullable=False,
        comment="smtp_imap or gmail_api",
    )
    smtp_host = Column(String(255), nullable=True)
    smtp_port = Column(Integer, nullable=True)
    smtp_mode = Column(String(20), nullable=True, comment="ssl or starttls")
    smtp_username = Column(String(255), nullable=True)
    smtp_password_ref = Column(String(255), nullable=True, comment="penguin-sal reference")
    imap_host = Column(String(255), nullable=True)
    imap_port = Column(Integer, default=993, nullable=True)
    imap_username = Column(String(255), nullable=True)
    imap_password_ref = Column(String(255), nullable=True, comment="penguin-sal reference")
    gmail_credentials_ref = Column(String(255), nullable=True, comment="penguin-sal reference")
    gmail_token_ref = Column(String(255), nullable=True, comment="penguin-sal reference")
    gmail_watch_expiry = Column(DateTime(timezone=True), nullable=True)
    is_default = Column(Boolean, default=False, nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)
    last_polled_at = Column(DateTime(timezone=True), nullable=True)


class HdEmailLog(Base, IDMixin, TimestampMixin):
    """Email send/receive history."""

    __tablename__ = "hd_email_logs"

    hd_email_account_id = Column(
        Integer,
        ForeignKey("hd_email_accounts.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    direction = Column(
        String(10),
        nullable=False,
        comment="inbound or outbound",
    )
    message_id = Column(String(255), nullable=False, unique=True)
    in_reply_to = Column(String(255), nullable=True)
    from_addr = Column(String(255), nullable=True)
    to_addr = Column(Text, nullable=True)
    subject = Column(String(500), nullable=True)
    hd_ticket_id = Column(
        Integer,
        ForeignKey("hd_tickets.id", ondelete="SET NULL"),
        nullable=True,
    )


class HdTicketForm(Base, IDMixin, TenantScopedMixin, TimestampMixin):
    """Custom ticket submission forms with optional CAPTCHA."""

    __tablename__ = "hd_ticket_forms"

    name = Column(String(255), nullable=False)
    slug = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    is_default = Column(Boolean, default=False, nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)
    captcha_provider = Column(
        String(20),
        default="none",
        nullable=False,
        comment="none, turnstile, recaptcha",
    )
    captcha_site_key = Column(String(255), nullable=True)
    captcha_secret_ref = Column(String(255), nullable=True, comment="penguin-sal reference")
    fields = Column(JSON, nullable=False, server_default="{}")

    __table_args__ = (UniqueConstraint("tenant_id", "slug", name="uq_tenant_form_slug"),)


class HdCompany(Base, IDMixin, TenantScopedMixin, VillageIDMixin, TimestampMixin):
    """CRM company / account record."""

    __tablename__ = "hd_companies"

    name = Column(String(255), nullable=False)
    domain = Column(String(255), nullable=True)
    industry = Column(String(100), nullable=True)
    size = Column(String(50), nullable=True, comment="startup, smb, mid-market, enterprise")
    website = Column(String(500), nullable=True)
    notes = Column(Text, nullable=True)


class HdContact(Base, IDMixin, TenantScopedMixin, VillageIDMixin, TimestampMixin):
    """Individual CRM contact, optionally linked to a Company and Identity."""

    __tablename__ = "hd_contacts"

    hd_company_id = Column(
        Integer,
        ForeignKey("hd_companies.id", ondelete="SET NULL"),
        nullable=True,
    )
    identity_id = Column(
        Integer,
        ForeignKey("identities.id", ondelete="SET NULL"),
        nullable=True,
        comment="Optional link to identities table",
    )
    first_name = Column(String(100), nullable=True)
    last_name = Column(String(100), nullable=True)
    email = Column(String(255), nullable=False, index=True)
    phone = Column(String(50), nullable=True)
    job_title = Column(String(200), nullable=True)
    notes = Column(Text, nullable=True)


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
