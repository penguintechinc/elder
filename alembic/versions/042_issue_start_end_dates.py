"""Add start_date and end_date columns to issues table.

Revision ID: 042
Revises: 041
Create Date: 2026-11-19
"""

import sqlalchemy as sa

from alembic import op

revision = "042"
down_revision = "041"
branch_labels = None
depends_on = None


def upgrade():
    """Add start_date and end_date to issues (idempotent)."""
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    issue_columns = [c["name"] for c in inspector.get_columns("issues")]
    if "start_date" not in issue_columns:
        op.add_column(
            "issues",
            sa.Column("start_date", sa.DateTime(timezone=True), nullable=True),
        )
    if "end_date" not in issue_columns:
        op.add_column(
            "issues",
            sa.Column("end_date", sa.DateTime(timezone=True), nullable=True),
        )


def downgrade():
    """Drop start_date and end_date from issues (idempotent)."""
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    issue_columns = [c["name"] for c in inspector.get_columns("issues")]
    if "end_date" in issue_columns:
        op.drop_column("issues", "end_date")
    if "start_date" in issue_columns:
        op.drop_column("issues", "start_date")
