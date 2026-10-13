"""Identity metadata: add universal metadata JSON bag to identities

Revision ID: 033
Revises: 032
Create Date: 2026-08-06
"""

import sqlalchemy as sa

from alembic import op

revision = "033"
down_revision = "032"
branch_labels = None
depends_on = None


def upgrade():
    """Add nullable metadata JSON column to identities (idempotent)."""
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    columns = [c["name"] for c in inspector.get_columns("identities")]

    if "metadata" not in columns:
        op.add_column("identities", sa.Column("metadata", sa.JSON(), nullable=True))


def downgrade():
    op.drop_column("identities", "metadata")
