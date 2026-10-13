"""Add is_incident flag to issues table

Revision ID: 002
Revises: 001
Create Date: 2025-10-25 00:00:00.000000

"""

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision = "002"
down_revision = "001_enterprise"
branch_labels = None
depends_on = None


def upgrade():
    """Add is_incident column to issues table for incident tracking and Alertmanager integration."""
    # Add is_incident column with default value of 0 (not an incident)
    op.add_column(
        "issues",
        sa.Column(
            "is_incident",
            sa.Integer(),
            nullable=False,
            server_default="0",
            comment="Whether this issue is marked as an incident (0=no, 1=yes)",
        ),
    )

    # Add index for faster querying of incident issues
    op.create_index(
        op.f("ix_issues_is_incident"), "issues", ["is_incident"], unique=False
    )


def downgrade():
    """Remove is_incident column from issues table."""
    op.drop_index(op.f("ix_issues_is_incident"), table_name="issues")
    op.drop_column("issues", "is_incident")
