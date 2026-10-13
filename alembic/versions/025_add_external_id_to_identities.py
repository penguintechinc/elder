"""Add external_id to identities table for IAM discovery edge resolution

Revision ID: 025
Revises: 024
Create Date: 2026-08-04
"""

import sqlalchemy as sa

from alembic import op

revision = "025"
down_revision = "024"
branch_labels = None
depends_on = None


def upgrade():
    """Add nullable external_id + index to identities table (idempotent)."""
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    columns = [c["name"] for c in inspector.get_columns("identities")]
    if "external_id" not in columns:
        op.add_column(
            "identities", sa.Column("external_id", sa.String(255), nullable=True)
        )
        op.create_index("ix_identities_external_id", "identities", ["external_id"])


def downgrade():
    op.drop_index("ix_identities_external_id", table_name="identities")
    op.drop_column("identities", "external_id")
