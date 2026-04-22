"""Add missing columns for subscriptions and access reviews

Revision ID: 012
Revises: 011
Create Date: 2026-04-14
"""

revision = '012'
down_revision = '010'
branch_labels = None
depends_on = None

import sqlalchemy as sa
from alembic import op


def upgrade():
    """Add subscription_tier and review_enabled columns."""
    # Add subscription_tier to tenants table
    op.add_column(
        'tenants',
        sa.Column(
            'subscription_tier',
            sa.String(64),
            nullable=False,
            server_default='enterprise'
        )
    )

    # Add review-related columns to identity_groups table
    op.add_column(
        'identity_groups',
        sa.Column(
            'review_enabled',
            sa.Boolean(),
            nullable=False,
            server_default=sa.text('false')
        )
    )
    op.add_column(
        'identity_groups',
        sa.Column(
            'next_review_date',
            sa.DateTime(timezone=True),
            nullable=True
        )
    )
    op.add_column(
        'identity_groups',
        sa.Column(
            'last_review_date',
            sa.DateTime(timezone=True),
            nullable=True
        )
    )
    op.add_column(
        'identity_groups',
        sa.Column(
            'review_due_days',
            sa.Integer(),
            nullable=True,
            server_default='14'
        )
    )
    op.add_column(
        'identity_groups',
        sa.Column(
            'review_auto_apply',
            sa.Boolean(),
            nullable=False,
            server_default=sa.text('false')
        )
    )
    op.add_column(
        'identity_groups',
        sa.Column(
            'tenant_id',
            sa.Integer(),
            sa.ForeignKey('tenants.id', ondelete='CASCADE'),
            nullable=True
        )
    )


def downgrade():
    """Remove added columns."""
    op.drop_column('tenants', 'subscription_tier')
    op.drop_constraint('identity_groups_tenant_id_fkey', 'identity_groups', type_='foreignkey')
    op.drop_column('identity_groups', 'tenant_id')
    op.drop_column('identity_groups', 'review_auto_apply')
    op.drop_column('identity_groups', 'review_due_days')
    op.drop_column('identity_groups', 'last_review_date')
    op.drop_column('identity_groups', 'next_review_date')
    op.drop_column('identity_groups', 'review_enabled')
