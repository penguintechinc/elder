"""Add missing authentication fields to portal_users

Revision ID: 013
Revises: 012
Create Date: 2026-04-14
"""

revision = '013'
down_revision = '012'
branch_labels = None
depends_on = None

import sqlalchemy as sa
from alembic import op


def upgrade():
    """Add password_hash and global_role columns to portal_users."""
    # Check if column already exists before adding (idempotent)
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    columns = [c['name'] for c in inspector.get_columns('portal_users')]
    
    if 'password_hash' not in columns:
        op.add_column(
            'portal_users',
            sa.Column(
                'password_hash',
                sa.String(255),
                nullable=True
            )
        )
    
    if 'global_role' not in columns:
        op.add_column(
            'portal_users',
            sa.Column(
                'global_role',
                sa.String(50),
                nullable=True
            )
        )
    
    # Copy password to password_hash for backward compatibility
    op.execute('UPDATE portal_users SET password_hash = password WHERE password_hash IS NULL')


def downgrade():
    """Remove password_hash and global_role columns."""
    op.drop_column('portal_users', 'password_hash')
    op.drop_column('portal_users', 'global_role')
