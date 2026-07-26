"""Add external_id to domain tables for discovery edge resolution

Revision ID: 024
Revises: 023
Create Date: 2026-07-26
"""

import sqlalchemy as sa

from alembic import op

revision = "024"
down_revision = "023"
branch_labels = None
depends_on = None

_TABLES = [
    "networking_resources",
    "services",
    "software",
    "data_stores",
    "certificates",
]


def upgrade():
    """Add nullable external_id + index to each domain table (idempotent)."""
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    for table in _TABLES:
        columns = [c["name"] for c in inspector.get_columns(table)]
        if "external_id" not in columns:
            op.add_column(
                table, sa.Column("external_id", sa.String(255), nullable=True)
            )
            op.create_index(f"ix_{table}_external_id", table, ["external_id"])


def downgrade():
    for table in _TABLES:
        op.drop_index(f"ix_{table}_external_id", table_name=table)
        op.drop_column(table, "external_id")
