"""Create helpdesk module schema — 12 tables for tickets, SLA, email, teams, forms, CRM.

Revision ID: 018
Revises: 017
Create Date: 2026-07-08

Phase 3a: Re-author Ruffled helpdesk schema as Elder helpdesk module.
- hd_tickets, hd_ticket_messages, hd_ticket_attachments
- hd_sla_policies, hd_canned_responses
- hd_email_accounts, hd_email_logs
- hd_ticket_forms
- hd_companies, hd_contacts
- hd_teams, hd_team_members

Tenant ref: Integer FK to tenants.id (not uuid).
User/actor refs: *_identity_id Integer FK to identities.id (from Ruffled uuid).
village_id: VillageIDMixin on hd_tickets, hd_companies, hd_contacts only.
"""

import sqlalchemy as sa
from alembic import op

revision = "018"
down_revision = "017"
branch_labels = None
depends_on = None


def upgrade():
    """Create 12 helpdesk tables with FKs, indexes, unique constraints."""

    # hd_sla_policies — standalone, no dependencies
    op.create_table(
        "hd_sla_policies",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("priority", sa.String(20), nullable=False),
        sa.Column("first_response_hours", sa.Integer(), nullable=False),
        sa.Column("resolution_hours", sa.Integer(), nullable=False),
        sa.Column(
            "business_hours_only", sa.Boolean(), nullable=False, server_default="1"
        ),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="1"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_hd_sla_policies_tenant_id", "hd_sla_policies", ["tenant_id"])

    # hd_teams — no FK to other hd_ tables initially
    op.create_table(
        "hd_teams",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("village_id", sa.String(32), nullable=True, unique=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=True,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=True,
            server_default=sa.func.now(),
            onupdate=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_hd_teams_tenant_id", "hd_teams", ["tenant_id"])

    # hd_tickets — FK to hd_teams, hd_sla_policies, identities
    op.create_table(
        "hd_tickets",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("village_id", sa.String(32), nullable=True, unique=True),
        sa.Column("subject", sa.String(500), nullable=False),
        sa.Column(
            "status",
            sa.String(30),
            nullable=False,
            server_default="new",
            comment="new, open, pending, on_hold, resolved, closed",
        ),
        sa.Column(
            "priority",
            sa.String(20),
            nullable=False,
            server_default="medium",
            comment="low, medium, high, urgent, critical",
        ),
        sa.Column(
            "channel",
            sa.String(20),
            nullable=False,
            server_default="web",
            comment="web, email, api",
        ),
        # Requester is an internal identity OR an external CRM contact (public
        # form submissions); both nullable, FK to hd_contacts added after that
        # table is created below.
        sa.Column("requester_identity_id", sa.Integer(), nullable=True),
        sa.Column("requester_contact_id", sa.Integer(), nullable=True),
        sa.Column("assignee_identity_id", sa.Integer(), nullable=True),
        sa.Column("hd_team_id", sa.Integer(), nullable=True),
        sa.Column("category", sa.String(100), nullable=True),
        sa.Column("tags", sa.JSON(), nullable=True, comment="JSON array of tags"),
        sa.Column("hd_sla_policy_id", sa.Integer(), nullable=True),
        sa.Column("sla_breach_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("first_response_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=True,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=True,
            server_default=sa.func.now(),
            onupdate=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["requester_identity_id"], ["identities.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["assignee_identity_id"], ["identities.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(["hd_team_id"], ["hd_teams.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(
            ["hd_sla_policy_id"], ["hd_sla_policies.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_hd_tickets_tenant_id", "hd_tickets", ["tenant_id"])
    op.create_index(
        "ix_hd_tickets_requester_identity_id", "hd_tickets", ["requester_identity_id"]
    )
    op.create_index(
        "ix_hd_tickets_assignee_identity_id", "hd_tickets", ["assignee_identity_id"]
    )
    op.create_index("ix_hd_tickets_hd_team_id", "hd_tickets", ["hd_team_id"])
    op.create_index(
        "ix_hd_tickets_hd_sla_policy_id", "hd_tickets", ["hd_sla_policy_id"]
    )

    # hd_ticket_messages — FK to hd_tickets, identities
    op.create_table(
        "hd_ticket_messages",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("hd_ticket_id", sa.Integer(), nullable=False),
        sa.Column("sender_identity_id", sa.Integer(), nullable=False),
        sa.Column(
            "message_type",
            sa.String(20),
            nullable=False,
            comment="reply, note, system",
        ),
        sa.Column("body_text", sa.Text(), nullable=True),
        sa.Column("body_html", sa.Text(), nullable=True),
        sa.Column("is_internal", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column(
            "email_message_id",
            sa.String(255),
            nullable=True,
            comment="RFC 2822 Message-ID",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=True,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=True,
            server_default=sa.func.now(),
            onupdate=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(
            ["hd_ticket_id"], ["hd_tickets.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["sender_identity_id"], ["identities.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_hd_ticket_messages_hd_ticket_id", "hd_ticket_messages", ["hd_ticket_id"]
    )

    # hd_ticket_attachments — FK to hd_tickets, hd_ticket_messages
    op.create_table(
        "hd_ticket_attachments",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("hd_ticket_id", sa.Integer(), nullable=False),
        sa.Column("hd_message_id", sa.Integer(), nullable=True),
        sa.Column("filename", sa.String(255), nullable=False),
        sa.Column("content_type", sa.String(100), nullable=True),
        sa.Column("size_bytes", sa.Integer(), nullable=True),
        sa.Column("storage_path", sa.String(500), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=True,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=True,
            server_default=sa.func.now(),
            onupdate=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(
            ["hd_ticket_id"], ["hd_tickets.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["hd_message_id"], ["hd_ticket_messages.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_hd_ticket_attachments_hd_ticket_id",
        "hd_ticket_attachments",
        ["hd_ticket_id"],
    )

    # hd_canned_responses — FK to identities
    op.create_table(
        "hd_canned_responses",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("body_html", sa.Text(), nullable=False),
        sa.Column("category", sa.String(100), nullable=True),
        sa.Column("created_by_identity_id", sa.Integer(), nullable=True),
        sa.Column("is_shared", sa.Boolean(), nullable=False, server_default="1"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=True,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=True,
            server_default=sa.func.now(),
            onupdate=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["created_by_identity_id"], ["identities.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_hd_canned_responses_tenant_id", "hd_canned_responses", ["tenant_id"]
    )

    # hd_email_accounts — FK to identities (optional)
    op.create_table(
        "hd_email_accounts",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("display_name", sa.String(255), nullable=True),
        sa.Column("email_address", sa.String(255), nullable=False),
        sa.Column(
            "provider",
            sa.String(20),
            nullable=False,
            comment="smtp_imap or gmail_api",
        ),
        sa.Column("smtp_host", sa.String(255), nullable=True),
        sa.Column("smtp_port", sa.Integer(), nullable=True),
        sa.Column("smtp_mode", sa.String(20), nullable=True, comment="ssl or starttls"),
        sa.Column("smtp_username", sa.String(255), nullable=True),
        sa.Column(
            "smtp_password_ref",
            sa.String(255),
            nullable=True,
            comment="penguin-sal reference",
        ),
        sa.Column("imap_host", sa.String(255), nullable=True),
        sa.Column("imap_port", sa.Integer(), nullable=True, server_default="993"),
        sa.Column("imap_username", sa.String(255), nullable=True),
        sa.Column(
            "imap_password_ref",
            sa.String(255),
            nullable=True,
            comment="penguin-sal reference",
        ),
        sa.Column(
            "gmail_credentials_ref",
            sa.String(255),
            nullable=True,
            comment="penguin-sal reference",
        ),
        sa.Column(
            "gmail_token_ref",
            sa.String(255),
            nullable=True,
            comment="penguin-sal reference",
        ),
        sa.Column("gmail_watch_expiry", sa.DateTime(timezone=True), nullable=True),
        sa.Column("is_default", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="1"),
        sa.Column("last_polled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=True,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=True,
            server_default=sa.func.now(),
            onupdate=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_hd_email_accounts_tenant_id", "hd_email_accounts", ["tenant_id"]
    )

    # hd_email_logs — FK to hd_email_accounts, hd_tickets
    op.create_table(
        "hd_email_logs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("hd_email_account_id", sa.Integer(), nullable=False),
        sa.Column(
            "direction",
            sa.String(10),
            nullable=False,
            comment="inbound or outbound",
        ),
        sa.Column("message_id", sa.String(255), nullable=False, unique=True),
        sa.Column("in_reply_to", sa.String(255), nullable=True),
        sa.Column("from_addr", sa.String(255), nullable=True),
        sa.Column("to_addr", sa.Text(), nullable=True),
        sa.Column("subject", sa.String(500), nullable=True),
        sa.Column("hd_ticket_id", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=True,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=True,
            server_default=sa.func.now(),
            onupdate=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(
            ["hd_email_account_id"], ["hd_email_accounts.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["hd_ticket_id"], ["hd_tickets.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_hd_email_logs_hd_email_account_id", "hd_email_logs", ["hd_email_account_id"]
    )

    # hd_ticket_forms — FK to identities (optional)
    op.create_table(
        "hd_ticket_forms",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("slug", sa.String(255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("is_default", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="1"),
        sa.Column(
            "captcha_provider",
            sa.String(20),
            nullable=False,
            server_default="none",
            comment="none, turnstile, recaptcha",
        ),
        sa.Column("captcha_site_key", sa.String(255), nullable=True),
        sa.Column(
            "captcha_secret_ref",
            sa.String(255),
            nullable=True,
            comment="penguin-sal reference",
        ),
        sa.Column("fields", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=True,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=True,
            server_default=sa.func.now(),
            onupdate=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        # Global slug uniqueness — public URL /public/<slug> has no tenant
        # component, so slugs must not collide across tenants (see model).
        sa.UniqueConstraint("slug", name="uq_form_slug"),
    )
    op.create_index("ix_hd_ticket_forms_tenant_id", "hd_ticket_forms", ["tenant_id"])

    # hd_companies — FK to identities (optional for notes author, but stored as properties not FK)
    op.create_table(
        "hd_companies",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("village_id", sa.String(32), nullable=True, unique=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("domain", sa.String(255), nullable=True),
        sa.Column("industry", sa.String(100), nullable=True),
        sa.Column(
            "size",
            sa.String(50),
            nullable=True,
            comment="startup, smb, mid-market, enterprise",
        ),
        sa.Column("website", sa.String(500), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=True,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=True,
            server_default=sa.func.now(),
            onupdate=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_hd_companies_tenant_id", "hd_companies", ["tenant_id"])

    # hd_contacts — FK to hd_companies, identities
    op.create_table(
        "hd_contacts",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("village_id", sa.String(32), nullable=True, unique=True),
        sa.Column("hd_company_id", sa.Integer(), nullable=True),
        sa.Column(
            "identity_id",
            sa.Integer(),
            nullable=True,
            comment="Optional link to identities table",
        ),
        sa.Column("first_name", sa.String(100), nullable=True),
        sa.Column("last_name", sa.String(100), nullable=True),
        sa.Column("email", sa.String(255), nullable=False),
        sa.Column("phone", sa.String(50), nullable=True),
        sa.Column("job_title", sa.String(200), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=True,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=True,
            server_default=sa.func.now(),
            onupdate=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["hd_company_id"], ["hd_companies.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["identity_id"], ["identities.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_hd_contacts_tenant_id", "hd_contacts", ["tenant_id"])
    op.create_index("ix_hd_contacts_hd_company_id", "hd_contacts", ["hd_company_id"])
    op.create_index("ix_hd_contacts_email", "hd_contacts", ["email"])

    # hd_tickets.requester_contact_id FK — deferred until hd_contacts exists.
    op.create_foreign_key(
        "fk_hd_tickets_requester_contact_id",
        "hd_tickets",
        "hd_contacts",
        ["requester_contact_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_hd_tickets_requester_contact_id", "hd_tickets", ["requester_contact_id"]
    )

    # hd_team_members — M:N between hd_teams and identities
    op.create_table(
        "hd_team_members",
        sa.Column("hd_team_id", sa.Integer(), nullable=False, primary_key=True),
        sa.Column("identity_id", sa.Integer(), nullable=False, primary_key=True),
        sa.Column("role", sa.String(30), nullable=False, server_default="member"),
        sa.ForeignKeyConstraint(["hd_team_id"], ["hd_teams.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["identity_id"], ["identities.id"], ondelete="CASCADE"),
    )


def downgrade():
    """Drop all helpdesk tables in FK-safe order."""
    # Drop the deferred hd_tickets->hd_contacts FK before hd_contacts goes away.
    op.drop_constraint(
        "fk_hd_tickets_requester_contact_id", "hd_tickets", type_="foreignkey"
    )
    # Drop leaf tables first (no other hd_ tables depend on them)
    op.drop_table("hd_team_members")
    op.drop_table("hd_contacts")
    op.drop_table("hd_companies")
    op.drop_table("hd_ticket_forms")
    op.drop_table("hd_email_logs")
    op.drop_table("hd_email_accounts")
    op.drop_table("hd_ticket_attachments")
    op.drop_table("hd_ticket_messages")

    # Drop hd_tickets (depends on hd_teams, hd_sla_policies)
    op.drop_table("hd_tickets")

    # Drop independent tables
    op.drop_table("hd_canned_responses")
    op.drop_table("hd_teams")
    op.drop_table("hd_sla_policies")
