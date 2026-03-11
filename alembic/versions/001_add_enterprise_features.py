"""Add enterprise features: resource roles, issues, metadata

Revision ID: 001_enterprise
Revises:
Create Date: 2025-01-23 10:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = '001_enterprise'
down_revision = '011'
branch_label = None
depends_on = None


def upgrade():
    """Add enterprise feature tables."""

    # ========================================================================
    # Resource Roles Table
    # ========================================================================
    op.create_table(
        'resource_roles',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('identity_id', sa.Integer(), nullable=False, comment='Identity who has this role'),
        sa.Column('resource_type', sa.Enum('ENTITY', 'ORGANIZATION', name='resourcetype'), nullable=False, comment='Type of resource (entity or organization)'),
        sa.Column('resource_id', sa.Integer(), nullable=False, comment='ID of the entity or organization'),
        sa.Column('role_type', sa.Enum('MAINTAINER', 'OPERATOR', 'VIEWER', name='resourceroletype'), nullable=False, comment='Role level (maintainer, operator, viewer)'),
        sa.Column('granted_by_id', sa.Integer(), nullable=True, comment='Identity who granted this role'),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['identity_id'], ['identities.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['granted_by_id'], ['identities.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('identity_id', 'resource_type', 'resource_id', 'role_type', name='uix_resource_role')
    )
    op.create_index(op.f('ix_resource_roles_identity_id'), 'resource_roles', ['identity_id'], unique=False)
    op.create_index(op.f('ix_resource_roles_resource_type'), 'resource_roles', ['resource_type'], unique=False)
    op.create_index(op.f('ix_resource_roles_resource_id'), 'resource_roles', ['resource_id'], unique=False)

    # ========================================================================
    # Issues Tables
    # ========================================================================

    # Issue labels table
    op.create_table(
        'issue_labels',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('name', sa.String(length=50), nullable=False, comment="Label name (e.g., 'bug', 'enhancement')"),
        sa.Column('color', sa.String(length=7), nullable=False, comment="Hex color code for label (e.g., '#ff0000')"),
        sa.Column('description', sa.Text(), nullable=True, comment='Label description'),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('name')
    )
    op.create_index(op.f('ix_issue_labels_name'), 'issue_labels', ['name'], unique=True)

    # Issues table
    op.create_table(
        'issues',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('resource_type', sa.String(length=20), nullable=False, comment='Type of resource (entity or organization)'),
        sa.Column('resource_id', sa.Integer(), nullable=False, comment='ID of the entity or organization'),
        sa.Column('title', sa.String(length=255), nullable=False, comment='Issue title'),
        sa.Column('description', sa.Text(), nullable=True, comment='Detailed issue description (supports Markdown)'),
        sa.Column('status', sa.Enum('OPEN', 'IN_PROGRESS', 'CLOSED', 'RESOLVED', name='issuestatus'), nullable=False, comment='Current issue status'),
        sa.Column('priority', sa.Enum('LOW', 'MEDIUM', 'HIGH', 'CRITICAL', name='issuepriority'), nullable=False, comment='Issue priority level'),
        sa.Column('created_by_id', sa.Integer(), nullable=True, comment='User who created this issue'),
        sa.Column('assigned_to_id', sa.Integer(), nullable=True, comment='User assigned to this issue'),
        sa.Column('due_date', sa.DateTime(timezone=True), nullable=True, comment='Issue due date'),
        sa.Column('closed_at', sa.DateTime(timezone=True), nullable=True, comment='When the issue was closed'),
        sa.Column('closed_by_id', sa.Integer(), nullable=True, comment='User who closed this issue'),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['created_by_id'], ['identities.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['assigned_to_id'], ['identities.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['closed_by_id'], ['identities.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_issues_resource_type'), 'issues', ['resource_type'], unique=False)
    op.create_index(op.f('ix_issues_resource_id'), 'issues', ['resource_id'], unique=False)
    op.create_index(op.f('ix_issues_title'), 'issues', ['title'], unique=False)
    op.create_index(op.f('ix_issues_status'), 'issues', ['status'], unique=False)
    op.create_index(op.f('ix_issues_priority'), 'issues', ['priority'], unique=False)
    op.create_index(op.f('ix_issues_created_by_id'), 'issues', ['created_by_id'], unique=False)
    op.create_index(op.f('ix_issues_assigned_to_id'), 'issues', ['assigned_to_id'], unique=False)

    # Issue label assignments (many-to-many)
    op.create_table(
        'issue_label_assignments',
        sa.Column('issue_id', sa.Integer(), nullable=False),
        sa.Column('label_id', sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(['issue_id'], ['issues.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['label_id'], ['issue_labels.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('issue_id', 'label_id')
    )

    # Issue comments table
    op.create_table(
        'issue_comments',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('issue_id', sa.Integer(), nullable=False, comment='Issue this comment belongs to'),
        sa.Column('author_id', sa.Integer(), nullable=True, comment='User who wrote this comment'),
        sa.Column('content', sa.Text(), nullable=False, comment='Comment content (supports Markdown)'),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['issue_id'], ['issues.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['author_id'], ['identities.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_issue_comments_issue_id'), 'issue_comments', ['issue_id'], unique=False)
    op.create_index(op.f('ix_issue_comments_author_id'), 'issue_comments', ['author_id'], unique=False)

    # Issue entity links table
    op.create_table(
        'issue_entity_links',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('issue_id', sa.Integer(), nullable=False, comment='Issue'),
        sa.Column('entity_id', sa.Integer(), nullable=False, comment='Entity'),
        sa.Column('link_type', sa.Enum('RELATED', 'BLOCKS', 'BLOCKED_BY', 'FIXES', name='issuelinktype'), nullable=False, comment='Type of relationship'),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['issue_id'], ['issues.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['entity_id'], ['entities.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_issue_entity_links_issue_id'), 'issue_entity_links', ['issue_id'], unique=False)
    op.create_index(op.f('ix_issue_entity_links_entity_id'), 'issue_entity_links', ['entity_id'], unique=False)

    # ========================================================================
    # Metadata Table
    # ========================================================================
    op.create_table(
        'metadata_fields',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('resource_type', sa.String(length=20), nullable=False, comment='Type of resource (entity or organization)'),
        sa.Column('resource_id', sa.Integer(), nullable=False, comment='ID of the entity or organization'),
        sa.Column('field_key', sa.String(length=100), nullable=False, comment='Metadata field key/name'),
        sa.Column('field_type', sa.Enum('STRING', 'NUMBER', 'DATE', 'BOOLEAN', 'JSON', name='metadatafieldtype'), nullable=False, comment='Data type of this field'),
        sa.Column('field_value', sa.Text(), nullable=False, comment='JSON-encoded field value'),
        sa.Column('is_system', sa.Boolean(), nullable=False, comment='System metadata cannot be deleted or modified by users'),
        sa.Column('created_by_id', sa.Integer(), nullable=True, comment='User who created this metadata field'),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['created_by_id'], ['identities.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('resource_type', 'resource_id', 'field_key', name='uix_metadata_field')
    )
    op.create_index(op.f('ix_metadata_fields_resource_type'), 'metadata_fields', ['resource_type'], unique=False)
    op.create_index(op.f('ix_metadata_fields_resource_id'), 'metadata_fields', ['resource_id'], unique=False)
    op.create_index(op.f('ix_metadata_fields_field_key'), 'metadata_fields', ['field_key'], unique=False)
    op.create_index(op.f('ix_metadata_fields_created_by_id'), 'metadata_fields', ['created_by_id'], unique=False)

    # ========================================================================
    # Default Issue Labels
    # ========================================================================
    op.execute("""
        INSERT INTO issue_labels (name, color, description, created_at, updated_at)
        VALUES
            ('bug', '#d73a4a', 'Something isn''t working', NOW(), NOW()),
            ('enhancement', '#a2eeef', 'New feature or request', NOW(), NOW()),
            ('documentation', '#0075ca', 'Improvements or additions to documentation', NOW(), NOW()),
            ('question', '#d876e3', 'Further information is requested', NOW(), NOW()),
            ('wontfix', '#ffffff', 'This will not be worked on', NOW(), NOW()),
            ('duplicate', '#cfd3d7', 'This issue or pull request already exists', NOW(), NOW()),
            ('good first issue', '#7057ff', 'Good for newcomers', NOW(), NOW()),
            ('help wanted', '#008672', 'Extra attention is needed', NOW(), NOW()),
            ('invalid', '#e4e669', 'This doesn''t seem right', NOW(), NOW()),
            ('security', '#ee0701', 'Security-related issue', NOW(), NOW())
    """)


def downgrade():
    """Remove enterprise feature tables."""

    # Drop tables in reverse order (respecting foreign keys)
    op.drop_table('metadata_fields')
    op.drop_table('issue_entity_links')
    op.drop_table('issue_comments')
    op.drop_table('issue_label_assignments')
    op.drop_table('issues')
    op.drop_table('issue_labels')
    op.drop_table('resource_roles')

    # Drop enum types
    op.execute('DROP TYPE IF EXISTS metadatafieldtype')
    op.execute('DROP TYPE IF EXISTS issuelinktype')
    op.execute('DROP TYPE IF EXISTS issuepriority')
    op.execute('DROP TYPE IF EXISTS issuestatus')
    op.execute('DROP TYPE IF EXISTS resourceroletype')
    op.execute('DROP TYPE IF EXISTS resourcetype')
