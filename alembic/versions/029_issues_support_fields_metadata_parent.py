"""Issues: support fields + universal metadata bag + parent self-FK

Adds the nullable support/helpdesk columns (channel, category,
requester_contact_id, hd_sla_policy_id, sla_breach_at, first_response_at,
resolved_at), the universal `metadata` JSON bag (mapped in the SQLAlchemy
model as issue_metadata since `metadata` is a reserved declarative
attribute name), and parent_issue_id, a self-referential FK enabling
sub-tasks.

requester_contact_id references identities.id as a placeholder until the
Plan 03 CRM contacts table exists; ON DELETE SET NULL keeps issues intact
if the referenced identity is removed.

Revision ID: 029
Revises: 028
Create Date: 2026-08-06
"""

import sqlalchemy as sa

from alembic import op

revision = "029"
down_revision = "028"
branch_labels = None
depends_on = None


def upgrade():
    """Add support columns, metadata JSON bag, and parent_issue_id (idempotent)."""
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    columns = [c["name"] for c in inspector.get_columns("issues")]

    if "channel" not in columns:
        op.add_column("issues", sa.Column("channel", sa.String(20), nullable=True))

    if "category" not in columns:
        op.add_column("issues", sa.Column("category", sa.String(100), nullable=True))

    if "requester_contact_id" not in columns:
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

    if "hd_sla_policy_id" not in columns:
        op.add_column(
            "issues", sa.Column("hd_sla_policy_id", sa.Integer(), nullable=True)
        )

    if "sla_breach_at" not in columns:
        op.add_column(
            "issues",
            sa.Column("sla_breach_at", sa.DateTime(timezone=True), nullable=True),
        )

    if "first_response_at" not in columns:
        op.add_column(
            "issues",
            sa.Column("first_response_at", sa.DateTime(timezone=True), nullable=True),
        )

    if "resolved_at" not in columns:
        op.add_column(
            "issues",
            sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        )

    if "metadata" not in columns:
        op.add_column("issues", sa.Column("metadata", sa.JSON(), nullable=True))

    if "parent_issue_id" not in columns:
        op.add_column(
            "issues", sa.Column("parent_issue_id", sa.Integer(), nullable=True)
        )
        op.create_index("ix_issues_parent_issue_id", "issues", ["parent_issue_id"])
        op.create_foreign_key(
            "fk_issues_parent_issue",
            "issues",
            "issues",
            ["parent_issue_id"],
            ["id"],
            ondelete="SET NULL",
        )


def downgrade():
    """Drop parent_issue_id, metadata, and the support/SLA columns."""
    op.drop_constraint("fk_issues_parent_issue", "issues", type_="foreignkey")
    op.drop_index("ix_issues_parent_issue_id", table_name="issues")
    op.drop_column("issues", "parent_issue_id")

    op.drop_column("issues", "metadata")

    op.drop_column("issues", "resolved_at")
    op.drop_column("issues", "first_response_at")
    op.drop_column("issues", "sla_breach_at")
    op.drop_column("issues", "hd_sla_policy_id")

    op.drop_constraint("fk_issues_requester_contact", "issues", type_="foreignkey")
    op.drop_index("ix_issues_requester_contact_id", table_name="issues")
    op.drop_column("issues", "requester_contact_id")

    op.drop_column("issues", "category")
    op.drop_column("issues", "channel")
