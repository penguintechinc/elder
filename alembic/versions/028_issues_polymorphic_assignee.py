"""Polymorphic issue assignee: add assignee_type, drop single-table assignee FK

issues.assignee_id (issues foundation task 4) becomes a polymorphic
reference disambiguated by the new assignee_type column ('identity' ->
identities.id, 'org_unit' -> organizations.id). A column can only carry a
DB-level FK to one table, so any pre-existing FK from assignee_id to
identities.id (created by an earlier create_all() run, back when the
SQLAlchemy model still declared it) is dropped — it would otherwise reject
every org_unit assignment whose id doesn't coincidentally also exist in
identities.

Revision ID: 028
Revises: 027
Create Date: 2026-08-06
"""

import sqlalchemy as sa

from alembic import op

revision = "028"
down_revision = "027"
branch_labels = None
depends_on = None


def upgrade():
    """Add nullable assignee_type; drop assignee_id's single-table FK, if present."""
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    columns = [c["name"] for c in inspector.get_columns("issues")]

    if "assignee_type" not in columns:
        op.add_column(
            "issues", sa.Column("assignee_type", sa.String(16), nullable=True)
        )
        op.create_index("ix_issues_assignee_type", "issues", ["assignee_type"])

    for fk in inspector.get_foreign_keys("issues"):
        if fk.get("constrained_columns") == ["assignee_id"]:
            op.drop_constraint(fk["name"], "issues", type_="foreignkey")


def downgrade():
    """Drop assignee_type; do not restore the assignee_id -> identities FK.

    Restoring the FK is not attempted: any org_unit-assigned row (assignee_id
    pointing at organizations.id) would violate it immediately.
    """
    op.drop_index("ix_issues_assignee_type", table_name="issues")
    op.drop_column("issues", "assignee_type")
