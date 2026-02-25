"""Add issue milestone and project link tables.

Revision ID: 009
Revises: 008
Create Date: 2026-02-20

These two junction tables depend on both 'issues' (Alembic-managed, migration 001)
and 'milestones'/'projects' (PyDAL base tables). Because they straddle the
PyDAL/Alembic boundary, they are excluded from PyDAL auto-creation and are
instead created here, after both PyDAL base tables and the enterprise tables
from migration 001 already exist.
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '009'
down_revision = '008'
branch_labels = None
depends_on = None


def upgrade():
    """Create issue milestone and project link tables."""

    # issue_milestone_links — links issues to milestones (many-to-many)
    op.create_table(
        'issue_milestone_links',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('issue_id', sa.Integer(), nullable=False),
        sa.Column('milestone_id', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['issue_id'], ['issues.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['milestone_id'], ['milestones.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('issue_id', 'milestone_id', name='uix_issue_milestone'),
    )
    op.create_index('ix_issue_milestone_links_issue_id', 'issue_milestone_links', ['issue_id'])
    op.create_index('ix_issue_milestone_links_milestone_id', 'issue_milestone_links', ['milestone_id'])

    # issue_project_links — links issues to projects (many-to-many)
    op.create_table(
        'issue_project_links',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('issue_id', sa.Integer(), nullable=False),
        sa.Column('project_id', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['issue_id'], ['issues.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['project_id'], ['projects.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('issue_id', 'project_id', name='uix_issue_project'),
    )
    op.create_index('ix_issue_project_links_issue_id', 'issue_project_links', ['issue_id'])
    op.create_index('ix_issue_project_links_project_id', 'issue_project_links', ['project_id'])


def downgrade():
    """Drop issue milestone and project link tables."""
    op.drop_index('ix_issue_project_links_project_id', table_name='issue_project_links')
    op.drop_index('ix_issue_project_links_issue_id', table_name='issue_project_links')
    op.drop_table('issue_project_links')

    op.drop_index('ix_issue_milestone_links_milestone_id', table_name='issue_milestone_links')
    op.drop_index('ix_issue_milestone_links_issue_id', table_name='issue_milestone_links')
    op.drop_table('issue_milestone_links')
