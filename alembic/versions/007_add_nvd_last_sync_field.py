"""Add nvd_last_sync field to vulnerabilities table.

Revision ID: 007
Revises: 006
Create Date: 2025-12-17

This migration adds a field to track when NVD data was last synced for each
vulnerability. This enables daily NVD enrichment without repeatedly querying
for the same CVEs.
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


# revision identifiers, used by Alembic.
revision = '007'
down_revision = '006'
branch_labels = None
depends_on = None


def upgrade():
    """Add nvd_last_sync field to vulnerabilities table."""
    bind = op.get_bind()
    inspector = inspect(bind)
    existing = {c['name'] for c in inspector.get_columns('vulnerabilities')}
    if 'nvd_last_sync' not in existing:
        op.add_column('vulnerabilities', sa.Column('nvd_last_sync', sa.DateTime(timezone=True), nullable=True))


def downgrade():
    """Remove nvd_last_sync field."""
    op.drop_column('vulnerabilities', 'nvd_last_sync')
