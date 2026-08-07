"""Create hd_intake_forms table (configurable public/internal intake forms).

Intake forms are the CRM-facing entry point into the unified Issues model:
a submission creates an Issue (issue_type=support by default), distinct from
HdTicketForm (018) which creates an hd_tickets row. Slug is GLOBALLY unique
(mirrors hd_ticket_forms.slug, see 018) because the public resolution route
(/api/v1/intake/<slug>) carries no tenant component.

Revision ID: 036
Revises: 035
Create Date: 2026-08-07
"""

import sqlalchemy as sa

from alembic import op

revision = "036"
down_revision = "035"
branch_labels = None
depends_on = None


def upgrade():
    """Create hd_intake_forms with FKs, indexes, and the global slug uniqueness constraint (idempotent)."""
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    if inspector.has_table("hd_intake_forms"):
        return

    op.create_table(
        "hd_intake_forms",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("village_id", sa.String(32), nullable=True, unique=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("slug", sa.String(255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "fields", sa.JSON(), nullable=False, comment="Field spec array"
        ),
        sa.Column(
            "issue_type",
            sa.String(30),
            nullable=False,
            server_default="support",
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
        sa.Column(
            "is_public", sa.Boolean(), nullable=False, server_default="0"
        ),
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
        # Global slug uniqueness — public URL /api/v1/intake/<slug> has no
        # tenant component, so slugs must not collide across tenants (see
        # model docstring and hd_ticket_forms in 018).
        sa.UniqueConstraint("slug", name="uq_intake_form_slug"),
    )
    op.create_index(
        "ix_hd_intake_forms_tenant_id", "hd_intake_forms", ["tenant_id"]
    )
    op.create_index(
        "ix_hd_intake_forms_organization_id", "hd_intake_forms", ["organization_id"]
    )


def downgrade():
    """Drop hd_intake_forms (idempotent)."""
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    if not inspector.has_table("hd_intake_forms"):
        return

    op.drop_index("ix_hd_intake_forms_organization_id", table_name="hd_intake_forms")
    op.drop_index("ix_hd_intake_forms_tenant_id", table_name="hd_intake_forms")
    op.drop_table("hd_intake_forms")
