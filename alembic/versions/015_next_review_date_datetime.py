"""Convert next_review_date and last_review_date to DateTime columns.

Revision ID: 015
Revises: 014
Create Date: 2026-07-08

The next_review_date and last_review_date columns were incorrectly defined as
VARCHAR(255). They are timestamps used in datetime comparisons and must be
DateTime(timezone=True) to support proper PostgreSQL queries.

Fresh database (no existing data), so this is a simple type conversion.
"""

import sqlalchemy as sa
from alembic import op

revision = "015"
down_revision = "014"
branch_labels = None
depends_on = None


def upgrade():
    """
    Convert next_review_date and last_review_date from VARCHAR to TIMESTAMP.

    Changes:
    1. Alter identity_groups.last_review_date: VARCHAR(255) -> TIMESTAMP WITH TIME ZONE
    2. Alter identity_groups.next_review_date: VARCHAR(255) -> TIMESTAMP WITH TIME ZONE
    """
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    tables = inspector.get_table_names()

    if "identity_groups" not in tables:
        return

    columns = [c["name"] for c in inspector.get_columns("identity_groups")]

    # Alter last_review_date column
    if "last_review_date" in columns:
        op.alter_column(
            "identity_groups",
            "last_review_date",
            existing_type=sa.String(255),
            type_=sa.DateTime(timezone=True),
            nullable=True,
        )

    # Alter next_review_date column
    if "next_review_date" in columns:
        op.alter_column(
            "identity_groups",
            "next_review_date",
            existing_type=sa.String(255),
            type_=sa.DateTime(timezone=True),
            nullable=True,
        )


def downgrade():
    """Revert columns back to VARCHAR(255)."""
    op.alter_column(
        "identity_groups",
        "last_review_date",
        existing_type=sa.DateTime(timezone=True),
        type_=sa.String(255),
        nullable=True,
    )
    op.alter_column(
        "identity_groups",
        "next_review_date",
        existing_type=sa.DateTime(timezone=True),
        type_=sa.String(255),
        nullable=True,
    )
