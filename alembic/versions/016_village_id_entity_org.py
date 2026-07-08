"""Add village_id column to Entity and Organization models (Phase 2)

Revision ID: 016
Revises: 015
Create Date: 2026-07-08
"""

import sqlalchemy as sa
from alembic import op

revision = "016"
down_revision = "015"
branch_labels = None
depends_on = None


def upgrade():
    """Add village_id column to entities and organizations tables."""
    # Add village_id column to entities table
    op.add_column(
        "entities",
        sa.Column(
            "village_id",
            sa.String(32),
            nullable=True,
            unique=True,
        ),
    )
    op.create_index("ix_entities_village_id", "entities", ["village_id"], unique=True)

    # Add village_id column to organizations table
    op.add_column(
        "organizations",
        sa.Column(
            "village_id",
            sa.String(32),
            nullable=True,
            unique=True,
        ),
    )
    op.create_index(
        "ix_organizations_village_id", "organizations", ["village_id"], unique=True
    )


def downgrade():
    """Remove village_id column from entities and organizations tables."""
    # Drop village_id column and index from organizations table
    op.drop_index("ix_organizations_village_id", table_name="organizations")
    op.drop_column("organizations", "village_id")

    # Drop village_id column and index from entities table
    op.drop_index("ix_entities_village_id", table_name="entities")
    op.drop_column("entities", "village_id")
