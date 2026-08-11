"""Add default root organization for initial setup.

Revision ID: 005
Revises: 004
Create Date: 2025-10-25
"""

import sqlalchemy as sa
from sqlalchemy.sql import column, table

from alembic import op

# revision identifiers, used by Alembic.
revision = "005"
down_revision = "004"
branch_labels = None
depends_on = None


def upgrade():
    """Add default root organization if none exists."""
    # Use raw SQL to insert default organization if organizations table is empty
    # This ensures idempotency - won't fail if organization already exists
    op.execute("""
        INSERT INTO organizations (id, name, description, created_at, updated_at)
        SELECT 1, 'Default Organization', 'Default root organization', NOW(), NOW()
        WHERE NOT EXISTS (SELECT 1 FROM organizations WHERE id = 1)
    """)

    # Reset the sequence if needed to ensure next auto-generated ID is correct
    op.execute("""
        SELECT setval(
            pg_get_serial_sequence('organizations', 'id'),
            GREATEST(
                (SELECT MAX(id) FROM organizations),
                1
            )
        )
    """)


def downgrade():
    """Remove default root organization.

    WARNING: This will only remove the default organization if it has no children
    and no associated entities. Use with caution in production environments.
    """
    op.execute("""
        DELETE FROM organizations
        WHERE id = 1
        AND name = 'Default Organization'
        AND parent_id IS NULL
        AND NOT EXISTS (SELECT 1 FROM organizations WHERE parent_id = 1)
        AND NOT EXISTS (SELECT 1 FROM entities WHERE organization_id = 1)
    """)
