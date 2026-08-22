"""Drop helpdesk customer-relations schema (moved to Waddles); helpdesk is now internal-only.

Removes the customer/public/community half of the helpdesk module — CRM
(hd_companies, hd_contacts), public intake forms (hd_ticket_forms,
hd_intake_forms), and customer email intake (hd_email_accounts,
hd_email_logs) — plus the two contact FKs that referenced them
(hd_tickets.requester_contact_id, issues.requester_contact_id). Elder keeps
internal ticketing only (hd_tickets, hd_ticket_messages,
hd_ticket_attachments, hd_sla_policies, hd_canned_responses, hd_teams,
hd_team_members). See
docs/superpowers/specs/2026-08-21-helpdesk-internal-reframe-phase3.md.

Downgrade recreates the dropped tables/columns with their original schema
(from migrations 018, 029, 036) — structure only; any data that lived in
them is not restored.

Revision ID: 040
Revises: 039
Create Date: 2026-08-21
"""

import sqlalchemy as sa

from alembic import op

revision = "040"
down_revision = "039"
branch_labels = None
depends_on = None


def _has_column(
    inspector: sa.engine.reflection.Inspector, table: str, column: str
) -> bool:
    """True if `table` exists and has `column` (drop guards, cross-DB safe)."""
    if not inspector.has_table(table):
        return False
    return any(col["name"] == column for col in inspector.get_columns(table))


def upgrade():
    """Drop the customer-relations tables + contact FK columns (idempotent, FK-safe order)."""
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    # 1. Drop the two contact FK columns first so nothing references hd_contacts.
    #    Dropping a column cascades its FK constraint + index (Postgres).
    if _has_column(inspector, "hd_tickets", "requester_contact_id"):
        op.drop_column("hd_tickets", "requester_contact_id")
    if _has_column(inspector, "issues", "requester_contact_id"):
        op.drop_column("issues", "requester_contact_id")

    # 2. Drop tables in FK-safe order (children before parents).
    #    hd_email_logs -> hd_email_accounts; hd_contacts -> hd_companies.
    for table in (
        "hd_email_logs",
        "hd_email_accounts",
        "hd_ticket_forms",
        "hd_contacts",
        "hd_companies",
        "hd_intake_forms",
    ):
        if inspector.has_table(table):
            op.drop_table(table)


def downgrade():
    """Recreate the dropped tables + contact FK columns (structure only)."""
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    # hd_companies (parent of hd_contacts)
    if not inspector.has_table("hd_companies"):
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

    # hd_contacts (FK -> hd_companies, identities)
    if not inspector.has_table("hd_contacts"):
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
        op.create_index(
            "ix_hd_contacts_hd_company_id", "hd_contacts", ["hd_company_id"]
        )
        op.create_index("ix_hd_contacts_email", "hd_contacts", ["email"])

    # hd_email_accounts (parent of hd_email_logs)
    if not inspector.has_table("hd_email_accounts"):
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
            sa.Column(
                "smtp_mode", sa.String(20), nullable=True, comment="ssl or starttls"
            ),
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

    # hd_email_logs (FK -> hd_email_accounts, hd_tickets)
    if not inspector.has_table("hd_email_logs"):
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
            "ix_hd_email_logs_hd_email_account_id",
            "hd_email_logs",
            ["hd_email_account_id"],
        )

    # hd_ticket_forms (standalone, global slug uniqueness)
    if not inspector.has_table("hd_ticket_forms"):
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
            sa.UniqueConstraint("slug", name="uq_form_slug"),
        )
        op.create_index(
            "ix_hd_ticket_forms_tenant_id", "hd_ticket_forms", ["tenant_id"]
        )

    # hd_intake_forms (FK -> organizations; global slug uniqueness)
    if not inspector.has_table("hd_intake_forms"):
        op.create_table(
            "hd_intake_forms",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("tenant_id", sa.Integer(), nullable=False),
            sa.Column("village_id", sa.String(32), nullable=True, unique=True),
            sa.Column("name", sa.String(255), nullable=False),
            sa.Column("slug", sa.String(255), nullable=False),
            sa.Column("description", sa.Text(), nullable=True),
            sa.Column("fields", sa.JSON(), nullable=False, comment="Field spec array"),
            sa.Column(
                "issue_type", sa.String(30), nullable=False, server_default="support"
            ),
            sa.Column("default_assignee_type", sa.String(16), nullable=True),
            sa.Column("default_assignee_id", sa.Integer(), nullable=True),
            sa.Column(
                "organization_id",
                sa.Integer(),
                nullable=True,
                comment="Owning org for issues created from this form; falls back "
                "to the tenant's root org when null",
            ),
            sa.Column("is_public", sa.Boolean(), nullable=False, server_default="0"),
            sa.Column(
                "captcha_required", sa.Boolean(), nullable=False, server_default="0"
            ),
            sa.Column("is_active", sa.Boolean(), nullable=False, server_default="1"),
            sa.Column("metadata", sa.JSON(), nullable=True),
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
                ["organization_id"], ["organizations.id"], ondelete="SET NULL"
            ),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("slug", name="uq_intake_form_slug"),
        )
        op.create_index(
            "ix_hd_intake_forms_tenant_id", "hd_intake_forms", ["tenant_id"]
        )
        op.create_index(
            "ix_hd_intake_forms_organization_id", "hd_intake_forms", ["organization_id"]
        )

    # Re-add hd_tickets.requester_contact_id (col + index + FK -> hd_contacts)
    if not _has_column(inspector, "hd_tickets", "requester_contact_id"):
        op.add_column(
            "hd_tickets", sa.Column("requester_contact_id", sa.Integer(), nullable=True)
        )
        op.create_index(
            "ix_hd_tickets_requester_contact_id", "hd_tickets", ["requester_contact_id"]
        )
        op.create_foreign_key(
            "fk_hd_tickets_requester_contact_id",
            "hd_tickets",
            "hd_contacts",
            ["requester_contact_id"],
            ["id"],
            ondelete="SET NULL",
        )

    # Re-add issues.requester_contact_id (col + index + FK -> identities)
    if not _has_column(inspector, "issues", "requester_contact_id"):
        op.add_column(
            "issues", sa.Column("requester_contact_id", sa.Integer(), nullable=True)
        )
        op.create_index(
            "ix_issues_requester_contact_id", "issues", ["requester_contact_id"]
        )
        op.create_foreign_key(
            "fk_issues_requester_contact",
            "issues",
            "identities",
            ["requester_contact_id"],
            ["id"],
            ondelete="SET NULL",
        )
