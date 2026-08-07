"""Projects tenant_id: add column + FK + index, backfill from organization

Revision ID: 031
Revises: 030
Create Date: 2026-08-06
"""

import sqlalchemy as sa

from alembic import op

revision = "031"
down_revision = "030"
branch_labels = None
depends_on = None


def upgrade():
    """Add nullable tenant_id + index + FK to projects, then backfill (idempotent)."""
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    columns = [c["name"] for c in inspector.get_columns("projects")]

    if "tenant_id" not in columns:
        op.add_column("projects", sa.Column("tenant_id", sa.Integer(), nullable=True))
        op.create_index("ix_projects_tenant_id", "projects", ["tenant_id"])
        op.create_foreign_key(
            "fk_projects_tenant",
            "projects",
            "tenants",
            ["tenant_id"],
            ["id"],
            ondelete="CASCADE",
        )

    # Backfill tenant_id from the owning organization (projects.organization_id
    # is NOT NULL, so every project has a direct org link). Safe to re-run:
    # only touches NULL rows.
    op.execute("""
        UPDATE projects p SET tenant_id = o.tenant_id
        FROM organizations o
        WHERE p.organization_id = o.id AND p.tenant_id IS NULL
        """)
    # Any remaining unmatched (orphaned organization_id or org with no
    # tenant) -> tenant 1 (single-tenant default) so nothing is orphaned.
    op.execute("UPDATE projects SET tenant_id = 1 WHERE tenant_id IS NULL")


def downgrade():
    op.drop_constraint("fk_projects_tenant", "projects", type_="foreignkey")
    op.drop_index("ix_projects_tenant_id", table_name="projects")
    op.drop_column("projects", "tenant_id")
