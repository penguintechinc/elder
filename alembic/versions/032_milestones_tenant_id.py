"""Milestones tenant_id: add column + FK + index, backfill from organization

Revision ID: 032
Revises: 031
Create Date: 2026-08-06
"""

import sqlalchemy as sa

from alembic import op

revision = "032"
down_revision = "031"
branch_labels = None
depends_on = None


def upgrade():
    """Add nullable tenant_id + index + FK to milestones, then backfill (idempotent)."""
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    columns = [c["name"] for c in inspector.get_columns("milestones")]

    if "tenant_id" not in columns:
        op.add_column("milestones", sa.Column("tenant_id", sa.Integer(), nullable=True))
        op.create_index("ix_milestones_tenant_id", "milestones", ["tenant_id"])
        op.create_foreign_key(
            "fk_milestones_tenant",
            "milestones",
            "tenants",
            ["tenant_id"],
            ["id"],
            ondelete="CASCADE",
        )

    # Backfill tenant_id from the owning organization (milestones.organization_id
    # is NOT NULL, so every milestone has a direct org link). Safe to re-run:
    # only touches NULL rows.
    op.execute("""
        UPDATE milestones m SET tenant_id = o.tenant_id
        FROM organizations o
        WHERE m.organization_id = o.id AND m.tenant_id IS NULL
        """)
    # Any remaining unmatched (orphaned organization_id or org with no
    # tenant) -> tenant 1 (single-tenant default) so nothing is orphaned.
    op.execute("UPDATE milestones SET tenant_id = 1 WHERE tenant_id IS NULL")


def downgrade():
    op.drop_constraint("fk_milestones_tenant", "milestones", type_="foreignkey")
    op.drop_index("ix_milestones_tenant_id", table_name="milestones")
    op.drop_column("milestones", "tenant_id")
