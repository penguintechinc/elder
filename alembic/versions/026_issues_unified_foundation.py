"""Issues unified foundation: tenant_id + backfill from owning organization

Revision ID: 026
Revises: 025
Create Date: 2026-08-06
"""

import sqlalchemy as sa

from alembic import op

revision = "026"
down_revision = "025"
branch_labels = None
depends_on = None


def upgrade():
    """Add nullable tenant_id + index + FK to issues, then backfill (idempotent)."""
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    columns = [c["name"] for c in inspector.get_columns("issues")]

    if "tenant_id" not in columns:
        op.add_column("issues", sa.Column("tenant_id", sa.Integer(), nullable=True))
        op.create_index("ix_issues_tenant_id", "issues", ["tenant_id"])
        op.create_foreign_key(
            "fk_issues_tenant",
            "issues",
            "tenants",
            ["tenant_id"],
            ["id"],
            ondelete="CASCADE",
        )

    # Backfill tenant_id from the owning organization (issues.resource_id when
    # resource_type='organization'). Safe to re-run: only touches NULL rows.
    op.execute(
        """
        UPDATE issues i SET tenant_id = o.tenant_id
        FROM organizations o
        WHERE i.resource_type = 'organization' AND i.resource_id = o.id AND i.tenant_id IS NULL
        """
    )
    # Any remaining unmatched (e.g. resource_type='entity') -> tenant 1
    # (single-tenant default) so nothing is orphaned.
    op.execute("UPDATE issues SET tenant_id = 1 WHERE tenant_id IS NULL")


def downgrade():
    op.drop_constraint("fk_issues_tenant", "issues", type_="foreignkey")
    op.drop_index("ix_issues_tenant_id", table_name="issues")
    op.drop_column("issues", "tenant_id")
