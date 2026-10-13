"""Add issue_type enum to issues table.

Revision ID: 004
Revises: 003
Create Date: 2025-10-25
"""

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision = "004"
down_revision = "003"
branch_labels = None
depends_on = None


def upgrade():
    """Add issue_type column with enum type."""
    # Create the enum type
    op.execute("""
        CREATE TYPE issuetype AS ENUM (
            'operations',
            'code',
            'config',
            'security',
            'architecture',
            'process',
            'approval',
            'feature',
            'bug',
            'other'
        )
    """)

    # Add the column with default 'other'
    op.execute("""
        ALTER TABLE issues
        ADD COLUMN issue_type issuetype NOT NULL DEFAULT 'other'
    """)

    # Create index on issue_type
    op.create_index("ix_issues_issue_type", "issues", ["issue_type"])


def downgrade():
    """Remove issue_type column and enum type."""
    op.drop_index("ix_issues_issue_type", table_name="issues")
    op.drop_column("issues", "issue_type")
    op.execute("DROP TYPE issuetype")
