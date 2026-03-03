"""Create base tables (consolidated migration for all core Elder tables)

Revision ID: 011
Revises: 010
Create Date: 2026-03-02
"""

revision = '011'
down_revision = '010'
branch_labels = None
depends_on = None

import sqlalchemy as sa
from alembic import op


def upgrade():
    # =========================================================================
    # LEVEL 0 — no dependencies
    # =========================================================================

    # 1. tenants
    op.create_table(
        'tenants',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('name', sa.String(255), nullable=False, unique=True),
        sa.Column('slug', sa.String(255), nullable=False, unique=True),
        sa.Column('display_name', sa.String(255), nullable=True),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default=sa.text('true')),
        sa.Column('settings', sa.JSON(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=True, server_default=sa.text('now()')),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True, server_default=sa.text('now()')),
    )

    # =========================================================================
    # LEVEL 1 — depends on tenants or has no FKs
    # =========================================================================

    # 2. identities
    op.create_table(
        'identities',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('tenant_id', sa.Integer(), sa.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=True),
        sa.Column('external_id', sa.String(255), nullable=True),
        sa.Column('provider', sa.String(255), nullable=True),
        sa.Column('type', sa.String(64), nullable=True),
        sa.Column('name', sa.String(255), nullable=True),
        sa.Column('email', sa.String(255), nullable=True),
        sa.Column('username', sa.String(255), nullable=True),
        sa.Column('display_name', sa.String(255), nullable=True),
        sa.Column('avatar_url', sa.String(1024), nullable=True),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default=sa.text('true')),
        sa.Column('is_service_account', sa.Boolean(), nullable=False, server_default=sa.text('false')),
        sa.Column('metadata', sa.JSON(), nullable=True),
        sa.Column('last_seen_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=True, server_default=sa.text('now()')),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True, server_default=sa.text('now()')),
    )

    # 3. portal_users
    op.create_table(
        'portal_users',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('tenant_id', sa.Integer(), sa.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=True),
        sa.Column('email', sa.String(255), nullable=False, unique=True),
        sa.Column('password', sa.String(512), nullable=True),
        sa.Column('first_name', sa.String(255), nullable=True),
        sa.Column('last_name', sa.String(255), nullable=True),
        sa.Column('display_name', sa.String(255), nullable=True),
        sa.Column('avatar_url', sa.String(1024), nullable=True),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default=sa.text('true')),
        sa.Column('is_admin', sa.Boolean(), nullable=False, server_default=sa.text('false')),
        sa.Column('is_superadmin', sa.Boolean(), nullable=False, server_default=sa.text('false')),
        sa.Column('mfa_enabled', sa.Boolean(), nullable=False, server_default=sa.text('false')),
        sa.Column('mfa_secret', sa.String(255), nullable=True),
        sa.Column('last_login_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('password_changed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('email_verified', sa.Boolean(), nullable=False, server_default=sa.text('false')),
        sa.Column('email_verification_token', sa.String(255), nullable=True),
        sa.Column('password_reset_token', sa.String(255), nullable=True),
        sa.Column('password_reset_expires', sa.DateTime(timezone=True), nullable=True),
        sa.Column('preferences', sa.JSON(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=True, server_default=sa.text('now()')),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True, server_default=sa.text('now()')),
    )

    # 4. idp_configurations
    op.create_table(
        'idp_configurations',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('tenant_id', sa.Integer(), sa.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=True),
        sa.Column('name', sa.String(255), nullable=False),
        sa.Column('type', sa.String(64), nullable=False),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default=sa.text('true')),
        sa.Column('config', sa.JSON(), nullable=True),
        sa.Column('attribute_mappings', sa.JSON(), nullable=True),
        sa.Column('auto_provision', sa.Boolean(), nullable=False, server_default=sa.text('false')),
        sa.Column('auto_deprovision', sa.Boolean(), nullable=False, server_default=sa.text('false')),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=True, server_default=sa.text('now()')),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True, server_default=sa.text('now()')),
    )

    # 5. scim_configurations
    op.create_table(
        'scim_configurations',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('tenant_id', sa.Integer(), sa.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=True),
        sa.Column('name', sa.String(255), nullable=False),
        sa.Column('token', sa.String(512), nullable=True),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default=sa.text('true')),
        sa.Column('sync_users', sa.Boolean(), nullable=False, server_default=sa.text('true')),
        sa.Column('sync_groups', sa.Boolean(), nullable=False, server_default=sa.text('true')),
        sa.Column('last_sync_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=True, server_default=sa.text('now()')),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True, server_default=sa.text('now()')),
    )

    # 6. identity_groups
    op.create_table(
        'identity_groups',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('name', sa.String(255), nullable=False),
        sa.Column('display_name', sa.String(255), nullable=True),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('type', sa.String(64), nullable=True),
        sa.Column('external_id', sa.String(255), nullable=True),
        sa.Column('provider', sa.String(255), nullable=True),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default=sa.text('true')),
        sa.Column('owner_identity_id', sa.Integer(), nullable=True),
        sa.Column('owner_group_id', sa.Integer(), nullable=True),
        sa.Column('metadata', sa.JSON(), nullable=True),
        sa.Column('settings', sa.JSON(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=True, server_default=sa.text('now()')),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True, server_default=sa.text('now()')),
    )

    # 7. roles
    op.create_table(
        'roles',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('name', sa.String(255), nullable=False, unique=True),
        sa.Column('display_name', sa.String(255), nullable=True),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('is_system', sa.Boolean(), nullable=False, server_default=sa.text('false')),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default=sa.text('true')),
        sa.Column('scopes', sa.JSON(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=True, server_default=sa.text('now()')),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True, server_default=sa.text('now()')),
    )

    # 8. permissions
    op.create_table(
        'permissions',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('name', sa.String(255), nullable=False, unique=True),
        sa.Column('display_name', sa.String(255), nullable=True),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('resource', sa.String(255), nullable=True),
        sa.Column('action', sa.String(255), nullable=True),
        sa.Column('is_system', sa.Boolean(), nullable=False, server_default=sa.text('false')),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=True, server_default=sa.text('now()')),
    )

    # 9. sync_configs
    op.create_table(
        'sync_configs',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('name', sa.String(255), nullable=False),
        sa.Column('type', sa.String(64), nullable=False),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default=sa.text('true')),
        sa.Column('config', sa.JSON(), nullable=True),
        sa.Column('schedule', sa.String(255), nullable=True),
        sa.Column('last_run_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=True, server_default=sa.text('now()')),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True, server_default=sa.text('now()')),
    )

    # 10. discovery_jobs
    op.create_table(
        'discovery_jobs',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('name', sa.String(255), nullable=False),
        sa.Column('type', sa.String(64), nullable=False),
        sa.Column('status', sa.String(64), nullable=True),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default=sa.text('true')),
        sa.Column('config', sa.JSON(), nullable=True),
        sa.Column('schedule', sa.String(255), nullable=True),
        sa.Column('last_run_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('next_run_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=True, server_default=sa.text('now()')),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True, server_default=sa.text('now()')),
    )

    # 11. backup_jobs
    op.create_table(
        'backup_jobs',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('name', sa.String(255), nullable=False),
        sa.Column('type', sa.String(64), nullable=False),
        sa.Column('status', sa.String(64), nullable=True),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default=sa.text('true')),
        sa.Column('config', sa.JSON(), nullable=True),
        sa.Column('schedule', sa.String(255), nullable=True),
        sa.Column('retention_days', sa.Integer(), nullable=True),
        sa.Column('last_run_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('next_run_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=True, server_default=sa.text('now()')),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True, server_default=sa.text('now()')),
    )

    # 12. audit_retention_policies
    op.create_table(
        'audit_retention_policies',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('name', sa.String(255), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('retention_days', sa.Integer(), nullable=False),
        sa.Column('event_types', sa.JSON(), nullable=True),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default=sa.text('true')),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=True, server_default=sa.text('now()')),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True, server_default=sa.text('now()')),
    )

    # =========================================================================
    # LEVEL 2 — depends on Level 1
    # =========================================================================

    # 13. portal_user_org_assignments
    op.create_table(
        'portal_user_org_assignments',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('portal_user_id', sa.Integer(), sa.ForeignKey('portal_users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('organization_id', sa.Integer(), nullable=True),  # FK set after organizations table
        sa.Column('role', sa.String(64), nullable=True),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default=sa.text('true')),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=True, server_default=sa.text('now()')),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True, server_default=sa.text('now()')),
    )

    # 14. backups
    op.create_table(
        'backups',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('backup_job_id', sa.Integer(), sa.ForeignKey('backup_jobs.id', ondelete='CASCADE'), nullable=True),
        sa.Column('status', sa.String(64), nullable=True),
        sa.Column('size_bytes', sa.BigInteger(), nullable=True),
        sa.Column('location', sa.String(1024), nullable=True),
        sa.Column('checksum', sa.String(255), nullable=True),
        sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('error_message', sa.Text(), nullable=True),
        sa.Column('metadata', sa.JSON(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=True, server_default=sa.text('now()')),
    )

    # 15. identity_group_memberships
    op.create_table(
        'identity_group_memberships',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('identity_id', sa.Integer(), sa.ForeignKey('identities.id', ondelete='CASCADE'), nullable=False),
        sa.Column('group_id', sa.Integer(), sa.ForeignKey('identity_groups.id', ondelete='CASCADE'), nullable=False),
        sa.Column('role', sa.String(64), nullable=True),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default=sa.text('true')),
        sa.Column('joined_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('added_by_id', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=True, server_default=sa.text('now()')),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True, server_default=sa.text('now()')),
    )

    # 16. group_access_requests
    op.create_table(
        'group_access_requests',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('group_id', sa.Integer(), sa.ForeignKey('identity_groups.id', ondelete='CASCADE'), nullable=False),
        sa.Column('requester_id', sa.Integer(), sa.ForeignKey('identities.id', ondelete='CASCADE'), nullable=False),
        sa.Column('status', sa.String(64), nullable=True),
        sa.Column('reason', sa.Text(), nullable=True),
        sa.Column('requested_role', sa.String(64), nullable=True),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=True, server_default=sa.text('now()')),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True, server_default=sa.text('now()')),
    )

    # 17. group_access_approvals
    op.create_table(
        'group_access_approvals',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('request_id', sa.Integer(), sa.ForeignKey('group_access_requests.id', ondelete='CASCADE'), nullable=False),
        sa.Column('approver_id', sa.Integer(), sa.ForeignKey('identities.id', ondelete='CASCADE'), nullable=False),
        sa.Column('decision', sa.String(64), nullable=True),
        sa.Column('comment', sa.Text(), nullable=True),
        sa.Column('decided_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=True, server_default=sa.text('now()')),
    )

    # 18. access_reviews
    op.create_table(
        'access_reviews',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('group_id', sa.Integer(), sa.ForeignKey('identity_groups.id', ondelete='CASCADE'), nullable=True),
        sa.Column('name', sa.String(255), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('status', sa.String(64), nullable=True),
        sa.Column('due_date', sa.Date(), nullable=True),
        sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_by_id', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=True, server_default=sa.text('now()')),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True, server_default=sa.text('now()')),
    )

    # 19. access_review_items
    op.create_table(
        'access_review_items',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('review_id', sa.Integer(), sa.ForeignKey('access_reviews.id', ondelete='CASCADE'), nullable=False),
        sa.Column('membership_id', sa.Integer(), sa.ForeignKey('identity_group_memberships.id', ondelete='CASCADE'), nullable=True),
        sa.Column('identity_id', sa.Integer(), sa.ForeignKey('identities.id', ondelete='CASCADE'), nullable=True),
        sa.Column('decision', sa.String(64), nullable=True),
        sa.Column('comment', sa.Text(), nullable=True),
        sa.Column('reviewed_by_id', sa.Integer(), nullable=True),
        sa.Column('reviewed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=True, server_default=sa.text('now()')),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True, server_default=sa.text('now()')),
    )

    # 20. access_review_assignments
    op.create_table(
        'access_review_assignments',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('review_id', sa.Integer(), sa.ForeignKey('access_reviews.id', ondelete='CASCADE'), nullable=False),
        sa.Column('reviewer_id', sa.Integer(), sa.ForeignKey('identities.id', ondelete='CASCADE'), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=True, server_default=sa.text('now()')),
    )

    # 21. role_permissions
    op.create_table(
        'role_permissions',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('role_id', sa.Integer(), sa.ForeignKey('roles.id', ondelete='CASCADE'), nullable=False),
        sa.Column('permission_id', sa.Integer(), sa.ForeignKey('permissions.id', ondelete='CASCADE'), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=True, server_default=sa.text('now()')),
    )

    # 22. user_roles
    op.create_table(
        'user_roles',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('identity_id', sa.Integer(), sa.ForeignKey('identities.id', ondelete='CASCADE'), nullable=False),
        sa.Column('role_id', sa.Integer(), sa.ForeignKey('roles.id', ondelete='CASCADE'), nullable=False),
        sa.Column('granted_by_id', sa.Integer(), nullable=True),
        sa.Column('granted_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=True, server_default=sa.text('now()')),
    )

    # 23. api_keys
    op.create_table(
        'api_keys',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('identity_id', sa.Integer(), sa.ForeignKey('identities.id', ondelete='CASCADE'), nullable=False),
        sa.Column('name', sa.String(255), nullable=False),
        sa.Column('key_hash', sa.String(512), nullable=False),
        sa.Column('key_prefix', sa.String(16), nullable=True),
        sa.Column('scopes', sa.JSON(), nullable=True),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default=sa.text('true')),
        sa.Column('last_used_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=True, server_default=sa.text('now()')),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True, server_default=sa.text('now()')),
    )

    # 24. sync_mappings
    op.create_table(
        'sync_mappings',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('sync_config_id', sa.Integer(), sa.ForeignKey('sync_configs.id', ondelete='CASCADE'), nullable=False),
        sa.Column('source_type', sa.String(255), nullable=True),
        sa.Column('source_field', sa.String(255), nullable=True),
        sa.Column('target_type', sa.String(255), nullable=True),
        sa.Column('target_field', sa.String(255), nullable=True),
        sa.Column('transform', sa.String(255), nullable=True),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default=sa.text('true')),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=True, server_default=sa.text('now()')),
    )

    # 25. sync_history
    op.create_table(
        'sync_history',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('sync_config_id', sa.Integer(), sa.ForeignKey('sync_configs.id', ondelete='CASCADE'), nullable=False),
        sa.Column('status', sa.String(64), nullable=True),
        sa.Column('records_processed', sa.Integer(), nullable=True),
        sa.Column('records_created', sa.Integer(), nullable=True),
        sa.Column('records_updated', sa.Integer(), nullable=True),
        sa.Column('records_deleted', sa.Integer(), nullable=True),
        sa.Column('records_failed', sa.Integer(), nullable=True),
        sa.Column('error_message', sa.Text(), nullable=True),
        sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=True, server_default=sa.text('now()')),
    )

    # 26. discovery_history
    op.create_table(
        'discovery_history',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('discovery_job_id', sa.Integer(), sa.ForeignKey('discovery_jobs.id', ondelete='CASCADE'), nullable=False),
        sa.Column('status', sa.String(64), nullable=True),
        sa.Column('resources_discovered', sa.Integer(), nullable=True),
        sa.Column('resources_created', sa.Integer(), nullable=True),
        sa.Column('resources_updated', sa.Integer(), nullable=True),
        sa.Column('resources_deleted', sa.Integer(), nullable=True),
        sa.Column('error_message', sa.Text(), nullable=True),
        sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('metadata', sa.JSON(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=True, server_default=sa.text('now()')),
    )

    # 27. saved_searches
    op.create_table(
        'saved_searches',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('identity_id', sa.Integer(), sa.ForeignKey('identities.id', ondelete='CASCADE'), nullable=False),
        sa.Column('name', sa.String(255), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('query', sa.Text(), nullable=True),
        sa.Column('filters', sa.JSON(), nullable=True),
        sa.Column('is_public', sa.Boolean(), nullable=False, server_default=sa.text('false')),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=True, server_default=sa.text('now()')),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True, server_default=sa.text('now()')),
    )

    # 28. organizations
    op.create_table(
        'organizations',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('tenant_id', sa.Integer(), sa.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=True),
        sa.Column('parent_id', sa.Integer(), sa.ForeignKey('organizations.id', ondelete='CASCADE'), nullable=True),
        sa.Column('name', sa.String(255), nullable=False),
        sa.Column('slug', sa.String(255), nullable=True),
        sa.Column('display_name', sa.String(255), nullable=True),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('type', sa.String(64), nullable=True),
        sa.Column('cloud_provider', sa.String(64), nullable=True),
        sa.Column('cloud_account_id', sa.String(255), nullable=True),
        sa.Column('region', sa.String(255), nullable=True),
        sa.Column('owner_identity_id', sa.Integer(), sa.ForeignKey('identities.id', ondelete='CASCADE'), nullable=True),
        sa.Column('owner_group_id', sa.Integer(), sa.ForeignKey('identity_groups.id', ondelete='CASCADE'), nullable=True),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default=sa.text('true')),
        sa.Column('settings', sa.JSON(), nullable=True),
        sa.Column('tags', sa.JSON(), nullable=True),
        sa.Column('metadata', sa.JSON(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=True, server_default=sa.text('now()')),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True, server_default=sa.text('now()')),
    )

    # 29. audit_logs
    op.create_table(
        'audit_logs',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('identity_id', sa.Integer(), sa.ForeignKey('identities.id', ondelete='CASCADE'), nullable=True),
        sa.Column('event_type', sa.String(255), nullable=False),
        sa.Column('resource_type', sa.String(255), nullable=True),
        sa.Column('resource_id', sa.String(255), nullable=True),
        sa.Column('action', sa.String(255), nullable=True),
        sa.Column('status', sa.String(64), nullable=True),
        sa.Column('ip_address', sa.String(64), nullable=True),
        sa.Column('user_agent', sa.Text(), nullable=True),
        sa.Column('request_id', sa.String(255), nullable=True),
        sa.Column('details', sa.JSON(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=True, server_default=sa.text('now()')),
    )

    # =========================================================================
    # LEVEL 3 — depends on Level 2
    # =========================================================================

    # 30. networking_resources
    op.create_table(
        'networking_resources',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('organization_id', sa.Integer(), sa.ForeignKey('organizations.id', ondelete='CASCADE'), nullable=True),
        sa.Column('parent_id', sa.Integer(), sa.ForeignKey('networking_resources.id', ondelete='CASCADE'), nullable=True),
        sa.Column('name', sa.String(255), nullable=True),
        sa.Column('type', sa.String(64), nullable=False),
        sa.Column('sub_type', sa.String(64), nullable=True),
        sa.Column('external_id', sa.String(255), nullable=True),
        sa.Column('cloud_provider', sa.String(64), nullable=True),
        sa.Column('region', sa.String(255), nullable=True),
        sa.Column('cidr', sa.String(64), nullable=True),
        sa.Column('ip_address', sa.String(64), nullable=True),
        sa.Column('is_public', sa.Boolean(), nullable=True),
        sa.Column('status', sa.String(64), nullable=True),
        sa.Column('tags', sa.JSON(), nullable=True),
        sa.Column('metadata', sa.JSON(), nullable=True),
        sa.Column('last_seen_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=True, server_default=sa.text('now()')),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True, server_default=sa.text('now()')),
    )

    # 31. entities
    op.create_table(
        'entities',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('organization_id', sa.Integer(), sa.ForeignKey('organizations.id', ondelete='CASCADE'), nullable=True),
        sa.Column('parent_id', sa.Integer(), sa.ForeignKey('entities.id', ondelete='CASCADE'), nullable=True),
        sa.Column('name', sa.String(255), nullable=True),
        sa.Column('type', sa.String(64), nullable=False),
        sa.Column('sub_type', sa.String(64), nullable=True),
        sa.Column('external_id', sa.String(255), nullable=True),
        sa.Column('cloud_provider', sa.String(64), nullable=True),
        sa.Column('region', sa.String(255), nullable=True),
        sa.Column('status', sa.String(64), nullable=True),
        sa.Column('is_managed', sa.Boolean(), nullable=True),
        sa.Column('tags', sa.JSON(), nullable=True),
        sa.Column('metadata', sa.JSON(), nullable=True),
        sa.Column('last_seen_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=True, server_default=sa.text('now()')),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True, server_default=sa.text('now()')),
    )

    # 32. labels
    op.create_table(
        'labels',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('organization_id', sa.Integer(), sa.ForeignKey('organizations.id', ondelete='CASCADE'), nullable=True),
        sa.Column('name', sa.String(255), nullable=False),
        sa.Column('color', sa.String(32), nullable=True),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=True, server_default=sa.text('now()')),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True, server_default=sa.text('now()')),
    )

    # 33. projects
    op.create_table(
        'projects',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('organization_id', sa.Integer(), sa.ForeignKey('organizations.id', ondelete='CASCADE'), nullable=True),
        sa.Column('name', sa.String(255), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('status', sa.String(64), nullable=True),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default=sa.text('true')),
        sa.Column('settings', sa.JSON(), nullable=True),
        sa.Column('created_by_id', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=True, server_default=sa.text('now()')),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True, server_default=sa.text('now()')),
    )

    # 34. milestones
    op.create_table(
        'milestones',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('organization_id', sa.Integer(), sa.ForeignKey('organizations.id', ondelete='CASCADE'), nullable=True),
        sa.Column('project_id', sa.Integer(), sa.ForeignKey('projects.id', ondelete='CASCADE'), nullable=True),
        sa.Column('name', sa.String(255), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('status', sa.String(64), nullable=True),
        sa.Column('due_date', sa.Date(), nullable=True),
        sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_by_id', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=True, server_default=sa.text('now()')),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True, server_default=sa.text('now()')),
    )

    # 35. secret_providers
    op.create_table(
        'secret_providers',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('organization_id', sa.Integer(), sa.ForeignKey('organizations.id', ondelete='CASCADE'), nullable=True),
        sa.Column('name', sa.String(255), nullable=False),
        sa.Column('type', sa.String(64), nullable=False),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default=sa.text('true')),
        sa.Column('config', sa.JSON(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=True, server_default=sa.text('now()')),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True, server_default=sa.text('now()')),
    )

    # 36. key_providers
    op.create_table(
        'key_providers',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('organization_id', sa.Integer(), sa.ForeignKey('organizations.id', ondelete='CASCADE'), nullable=True),
        sa.Column('name', sa.String(255), nullable=False),
        sa.Column('type', sa.String(64), nullable=False),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default=sa.text('true')),
        sa.Column('config', sa.JSON(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=True, server_default=sa.text('now()')),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True, server_default=sa.text('now()')),
    )

    # 37. iam_providers
    op.create_table(
        'iam_providers',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('organization_id', sa.Integer(), sa.ForeignKey('organizations.id', ondelete='CASCADE'), nullable=True),
        sa.Column('name', sa.String(255), nullable=False),
        sa.Column('type', sa.String(64), nullable=False),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default=sa.text('true')),
        sa.Column('config', sa.JSON(), nullable=True),
        sa.Column('last_sync_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=True, server_default=sa.text('now()')),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True, server_default=sa.text('now()')),
    )

    # 38. google_workspace_providers
    op.create_table(
        'google_workspace_providers',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('organization_id', sa.Integer(), sa.ForeignKey('organizations.id', ondelete='CASCADE'), nullable=True),
        sa.Column('name', sa.String(255), nullable=False),
        sa.Column('domain', sa.String(255), nullable=True),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default=sa.text('true')),
        sa.Column('credentials', sa.JSON(), nullable=True),
        sa.Column('scopes', sa.JSON(), nullable=True),
        sa.Column('last_sync_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=True, server_default=sa.text('now()')),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True, server_default=sa.text('now()')),
    )

    # 39. cloud_accounts
    op.create_table(
        'cloud_accounts',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('organization_id', sa.Integer(), sa.ForeignKey('organizations.id', ondelete='CASCADE'), nullable=True),
        sa.Column('name', sa.String(255), nullable=False),
        sa.Column('provider', sa.String(64), nullable=False),
        sa.Column('account_id', sa.String(255), nullable=True),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default=sa.text('true')),
        sa.Column('credentials', sa.JSON(), nullable=True),
        sa.Column('regions', sa.JSON(), nullable=True),
        sa.Column('last_sync_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=True, server_default=sa.text('now()')),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True, server_default=sa.text('now()')),
    )

    # 40. webhooks
    op.create_table(
        'webhooks',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('organization_id', sa.Integer(), sa.ForeignKey('organizations.id', ondelete='CASCADE'), nullable=True),
        sa.Column('name', sa.String(255), nullable=False),
        sa.Column('url', sa.String(1024), nullable=False),
        sa.Column('secret', sa.String(512), nullable=True),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default=sa.text('true')),
        sa.Column('events', sa.JSON(), nullable=True),
        sa.Column('headers', sa.JSON(), nullable=True),
        sa.Column('last_triggered_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=True, server_default=sa.text('now()')),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True, server_default=sa.text('now()')),
    )

    # 41. notification_rules
    op.create_table(
        'notification_rules',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('organization_id', sa.Integer(), sa.ForeignKey('organizations.id', ondelete='CASCADE'), nullable=True),
        sa.Column('name', sa.String(255), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default=sa.text('true')),
        sa.Column('event_types', sa.JSON(), nullable=True),
        sa.Column('conditions', sa.JSON(), nullable=True),
        sa.Column('channels', sa.JSON(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=True, server_default=sa.text('now()')),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True, server_default=sa.text('now()')),
    )

    # =========================================================================
    # LEVEL 4 — depends on Level 3
    # =========================================================================

    # 42. network_entity_mappings
    op.create_table(
        'network_entity_mappings',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('networking_resource_id', sa.Integer(), sa.ForeignKey('networking_resources.id', ondelete='CASCADE'), nullable=False),
        sa.Column('entity_id', sa.Integer(), sa.ForeignKey('entities.id', ondelete='CASCADE'), nullable=False),
        sa.Column('mapping_type', sa.String(64), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=True, server_default=sa.text('now()')),
    )

    # 43. network_topology
    op.create_table(
        'network_topology',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('source_id', sa.Integer(), sa.ForeignKey('networking_resources.id', ondelete='CASCADE'), nullable=False),
        sa.Column('target_id', sa.Integer(), sa.ForeignKey('networking_resources.id', ondelete='CASCADE'), nullable=False),
        sa.Column('relationship_type', sa.String(64), nullable=True),
        sa.Column('metadata', sa.JSON(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=True, server_default=sa.text('now()')),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True, server_default=sa.text('now()')),
    )

    # 44. builtin_secrets
    op.create_table(
        'builtin_secrets',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('organization_id', sa.Integer(), sa.ForeignKey('organizations.id', ondelete='CASCADE'), nullable=True),
        sa.Column('name', sa.String(255), nullable=False),
        sa.Column('type', sa.String(64), nullable=True),
        sa.Column('value', sa.Text(), nullable=True),
        sa.Column('is_encrypted', sa.Boolean(), nullable=False, server_default=sa.text('false')),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=True, server_default=sa.text('now()')),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True, server_default=sa.text('now()')),
    )

    # 45. dependencies
    op.create_table(
        'dependencies',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('tenant_id', sa.Integer(), sa.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=True),
        sa.Column('source_type', sa.String(64), nullable=False),
        sa.Column('source_id', sa.Integer(), nullable=False),
        sa.Column('target_type', sa.String(64), nullable=False),
        sa.Column('target_id', sa.Integer(), nullable=False),
        sa.Column('relationship_type', sa.String(64), nullable=True),
        sa.Column('metadata', sa.JSON(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=True, server_default=sa.text('now()')),
    )

    # 46. sync_conflicts
    op.create_table(
        'sync_conflicts',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('sync_mapping_id', sa.Integer(), sa.ForeignKey('sync_mappings.id', ondelete='CASCADE'), nullable=False),
        sa.Column('resolved_by_id', sa.Integer(), sa.ForeignKey('identities.id', ondelete='CASCADE'), nullable=True),
        sa.Column('resource_type', sa.String(255), nullable=True),
        sa.Column('resource_id', sa.String(255), nullable=True),
        sa.Column('conflict_data', sa.JSON(), nullable=True),
        sa.Column('resolution', sa.String(64), nullable=True),
        sa.Column('resolved_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=True, server_default=sa.text('now()')),
    )

    # 47. secrets
    op.create_table(
        'secrets',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('secret_provider_id', sa.Integer(), sa.ForeignKey('secret_providers.id', ondelete='CASCADE'), nullable=True),
        sa.Column('organization_id', sa.Integer(), sa.ForeignKey('organizations.id', ondelete='CASCADE'), nullable=True),
        sa.Column('parent_id', sa.Integer(), sa.ForeignKey('secrets.id', ondelete='CASCADE'), nullable=True),
        sa.Column('name', sa.String(255), nullable=False),
        sa.Column('path', sa.String(1024), nullable=True),
        sa.Column('type', sa.String(64), nullable=True),
        sa.Column('external_id', sa.String(255), nullable=True),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('tags', sa.JSON(), nullable=True),
        sa.Column('rotation_enabled', sa.Boolean(), nullable=False, server_default=sa.text('false')),
        sa.Column('rotation_days', sa.Integer(), nullable=True),
        sa.Column('last_rotated_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('last_accessed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=True, server_default=sa.text('now()')),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True, server_default=sa.text('now()')),
    )

    # 48. crypto_keys
    op.create_table(
        'crypto_keys',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('key_provider_id', sa.Integer(), sa.ForeignKey('key_providers.id', ondelete='CASCADE'), nullable=True),
        sa.Column('organization_id', sa.Integer(), sa.ForeignKey('organizations.id', ondelete='CASCADE'), nullable=True),
        sa.Column('name', sa.String(255), nullable=False),
        sa.Column('external_id', sa.String(255), nullable=True),
        sa.Column('type', sa.String(64), nullable=True),
        sa.Column('algorithm', sa.String(64), nullable=True),
        sa.Column('key_size', sa.Integer(), nullable=True),
        sa.Column('status', sa.String(64), nullable=True),
        sa.Column('rotation_enabled', sa.Boolean(), nullable=False, server_default=sa.text('false')),
        sa.Column('rotation_days', sa.Integer(), nullable=True),
        sa.Column('last_rotated_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('tags', sa.JSON(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=True, server_default=sa.text('now()')),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True, server_default=sa.text('now()')),
    )

    # 49. webhook_deliveries
    op.create_table(
        'webhook_deliveries',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('webhook_id', sa.Integer(), sa.ForeignKey('webhooks.id', ondelete='CASCADE'), nullable=False),
        sa.Column('event_type', sa.String(255), nullable=True),
        sa.Column('status', sa.String(64), nullable=True),
        sa.Column('http_status', sa.Integer(), nullable=True),
        sa.Column('request_payload', sa.JSON(), nullable=True),
        sa.Column('response_body', sa.Text(), nullable=True),
        sa.Column('error_message', sa.Text(), nullable=True),
        sa.Column('attempt_count', sa.Integer(), nullable=True),
        sa.Column('delivered_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=True, server_default=sa.text('now()')),
    )

    # =========================================================================
    # LEVEL 5 — depends on Level 4
    # =========================================================================

    # 50. secret_access_log
    op.create_table(
        'secret_access_log',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('secret_id', sa.Integer(), sa.ForeignKey('secrets.id', ondelete='CASCADE'), nullable=False),
        sa.Column('identity_id', sa.Integer(), sa.ForeignKey('identities.id', ondelete='CASCADE'), nullable=True),
        sa.Column('action', sa.String(64), nullable=True),
        sa.Column('ip_address', sa.String(64), nullable=True),
        sa.Column('user_agent', sa.Text(), nullable=True),
        sa.Column('metadata', sa.JSON(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=True, server_default=sa.text('now()')),
    )

    # 51. key_access_log
    op.create_table(
        'key_access_log',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('crypto_key_id', sa.Integer(), sa.ForeignKey('crypto_keys.id', ondelete='CASCADE'), nullable=False),
        sa.Column('identity_id', sa.Integer(), sa.ForeignKey('identities.id', ondelete='CASCADE'), nullable=True),
        sa.Column('action', sa.String(64), nullable=True),
        sa.Column('ip_address', sa.String(64), nullable=True),
        sa.Column('user_agent', sa.Text(), nullable=True),
        sa.Column('metadata', sa.JSON(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=True, server_default=sa.text('now()')),
    )

    # 52. software
    op.create_table(
        'software',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('tenant_id', sa.Integer(), sa.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=True),
        sa.Column('organization_id', sa.Integer(), sa.ForeignKey('organizations.id', ondelete='CASCADE'), nullable=True),
        sa.Column('identity_id', sa.Integer(), sa.ForeignKey('identities.id', ondelete='CASCADE'), nullable=True),
        sa.Column('name', sa.String(255), nullable=False),
        sa.Column('type', sa.String(64), nullable=True),
        sa.Column('version', sa.String(255), nullable=True),
        sa.Column('image_name', sa.String(512), nullable=True),
        sa.Column('image_tag', sa.String(255), nullable=True),
        sa.Column('image_digest', sa.String(255), nullable=True),
        sa.Column('registry', sa.String(512), nullable=True),
        sa.Column('external_id', sa.String(255), nullable=True),
        sa.Column('status', sa.String(64), nullable=True),
        sa.Column('tags', sa.JSON(), nullable=True),
        sa.Column('metadata', sa.JSON(), nullable=True),
        sa.Column('last_seen_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=True, server_default=sa.text('now()')),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True, server_default=sa.text('now()')),
    )

    # 53. services
    op.create_table(
        'services',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('tenant_id', sa.Integer(), sa.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=True),
        sa.Column('organization_id', sa.Integer(), sa.ForeignKey('organizations.id', ondelete='CASCADE'), nullable=True),
        sa.Column('identity_id', sa.Integer(), sa.ForeignKey('identities.id', ondelete='CASCADE'), nullable=True),
        sa.Column('name', sa.String(255), nullable=False),
        sa.Column('type', sa.String(64), nullable=True),
        sa.Column('sub_type', sa.String(64), nullable=True),
        sa.Column('external_id', sa.String(255), nullable=True),
        sa.Column('namespace', sa.String(255), nullable=True),
        sa.Column('cluster', sa.String(255), nullable=True),
        sa.Column('endpoint', sa.String(1024), nullable=True),
        sa.Column('port', sa.Integer(), nullable=True),
        sa.Column('protocol', sa.String(64), nullable=True),
        sa.Column('status', sa.String(64), nullable=True),
        sa.Column('tags', sa.JSON(), nullable=True),
        sa.Column('metadata', sa.JSON(), nullable=True),
        sa.Column('last_seen_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=True, server_default=sa.text('now()')),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True, server_default=sa.text('now()')),
    )

    # 54. ipam_prefixes
    op.create_table(
        'ipam_prefixes',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('tenant_id', sa.Integer(), sa.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=True),
        sa.Column('organization_id', sa.Integer(), sa.ForeignKey('organizations.id', ondelete='CASCADE'), nullable=True),
        sa.Column('parent_id', sa.Integer(), sa.ForeignKey('ipam_prefixes.id', ondelete='CASCADE'), nullable=True),
        sa.Column('prefix', sa.String(64), nullable=False),
        sa.Column('prefix_length', sa.Integer(), nullable=True),
        sa.Column('family', sa.Integer(), nullable=True),
        sa.Column('status', sa.String(64), nullable=True),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('tags', sa.JSON(), nullable=True),
        sa.Column('is_pool', sa.Boolean(), nullable=False, server_default=sa.text('false')),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=True, server_default=sa.text('now()')),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True, server_default=sa.text('now()')),
    )

    # 55. ipam_addresses
    op.create_table(
        'ipam_addresses',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('tenant_id', sa.Integer(), sa.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=True),
        sa.Column('prefix_id', sa.Integer(), sa.ForeignKey('ipam_prefixes.id', ondelete='CASCADE'), nullable=True),
        sa.Column('parent_id', sa.Integer(), sa.ForeignKey('ipam_addresses.id', ondelete='CASCADE'), nullable=True),
        sa.Column('address', sa.String(64), nullable=False),
        sa.Column('family', sa.Integer(), nullable=True),
        sa.Column('status', sa.String(64), nullable=True),
        sa.Column('assigned_to_type', sa.String(64), nullable=True),
        sa.Column('assigned_to_id', sa.Integer(), nullable=True),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('tags', sa.JSON(), nullable=True),
        sa.Column('dns_name', sa.String(255), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=True, server_default=sa.text('now()')),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True, server_default=sa.text('now()')),
    )

    # 56. ipam_vlans
    op.create_table(
        'ipam_vlans',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('tenant_id', sa.Integer(), sa.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=True),
        sa.Column('organization_id', sa.Integer(), sa.ForeignKey('organizations.id', ondelete='CASCADE'), nullable=True),
        sa.Column('vid', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(255), nullable=True),
        sa.Column('status', sa.String(64), nullable=True),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('tags', sa.JSON(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=True, server_default=sa.text('now()')),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True, server_default=sa.text('now()')),
    )

    # 57. certificates
    op.create_table(
        'certificates',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('tenant_id', sa.Integer(), sa.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=True),
        sa.Column('organization_id', sa.Integer(), sa.ForeignKey('organizations.id', ondelete='CASCADE'), nullable=True),
        sa.Column('identity_id', sa.Integer(), sa.ForeignKey('identities.id', ondelete='CASCADE'), nullable=True),
        sa.Column('builtin_secret_id', sa.Integer(), sa.ForeignKey('builtin_secrets.id', ondelete='CASCADE'), nullable=True),
        sa.Column('name', sa.String(255), nullable=False),
        sa.Column('common_name', sa.String(255), nullable=True),
        sa.Column('subject_alt_names', sa.JSON(), nullable=True),
        sa.Column('serial_number', sa.String(255), nullable=True),
        sa.Column('thumbprint', sa.String(255), nullable=True),
        sa.Column('issuer', sa.String(512), nullable=True),
        sa.Column('type', sa.String(64), nullable=True),
        sa.Column('status', sa.String(64), nullable=True),
        sa.Column('is_ca', sa.Boolean(), nullable=False, server_default=sa.text('false')),
        sa.Column('issued_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('auto_renew', sa.Boolean(), nullable=False, server_default=sa.text('false')),
        sa.Column('tags', sa.JSON(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=True, server_default=sa.text('now()')),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True, server_default=sa.text('now()')),
    )

    # 58. data_stores
    op.create_table(
        'data_stores',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('tenant_id', sa.Integer(), sa.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=True),
        sa.Column('organization_id', sa.Integer(), sa.ForeignKey('organizations.id', ondelete='CASCADE'), nullable=True),
        sa.Column('identity_id', sa.Integer(), sa.ForeignKey('identities.id', ondelete='CASCADE'), nullable=True),
        sa.Column('crypto_key_id', sa.Integer(), sa.ForeignKey('crypto_keys.id', ondelete='CASCADE'), nullable=True),
        sa.Column('portal_user_id', sa.Integer(), sa.ForeignKey('portal_users.id', ondelete='CASCADE'), nullable=True),
        sa.Column('name', sa.String(255), nullable=False),
        sa.Column('type', sa.String(64), nullable=False),
        sa.Column('sub_type', sa.String(64), nullable=True),
        sa.Column('external_id', sa.String(255), nullable=True),
        sa.Column('cloud_provider', sa.String(64), nullable=True),
        sa.Column('region', sa.String(255), nullable=True),
        sa.Column('endpoint', sa.String(1024), nullable=True),
        sa.Column('size_bytes', sa.BigInteger(), nullable=True),
        sa.Column('is_encrypted', sa.Boolean(), nullable=True),
        sa.Column('is_public', sa.Boolean(), nullable=True),
        sa.Column('status', sa.String(64), nullable=True),
        sa.Column('tags', sa.JSON(), nullable=True),
        sa.Column('metadata', sa.JSON(), nullable=True),
        sa.Column('last_seen_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=True, server_default=sa.text('now()')),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True, server_default=sa.text('now()')),
    )

    # 59. data_store_labels
    op.create_table(
        'data_store_labels',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('data_store_id', sa.Integer(), sa.ForeignKey('data_stores.id', ondelete='CASCADE'), nullable=False),
        sa.Column('label_id', sa.Integer(), sa.ForeignKey('labels.id', ondelete='CASCADE'), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=True, server_default=sa.text('now()')),
    )

    # 60. sbom_components
    op.create_table(
        'sbom_components',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('tenant_id', sa.Integer(), sa.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=True),
        sa.Column('name', sa.String(255), nullable=False),
        sa.Column('version', sa.String(255), nullable=True),
        sa.Column('type', sa.String(64), nullable=True),
        sa.Column('purl', sa.String(1024), nullable=True),
        sa.Column('cpe', sa.String(1024), nullable=True),
        sa.Column('license', sa.String(255), nullable=True),
        sa.Column('licenses', sa.JSON(), nullable=True),
        sa.Column('supplier', sa.String(255), nullable=True),
        sa.Column('hashes', sa.JSON(), nullable=True),
        sa.Column('metadata', sa.JSON(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=True, server_default=sa.text('now()')),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True, server_default=sa.text('now()')),
    )

    # 61. sbom_scans
    op.create_table(
        'sbom_scans',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('tenant_id', sa.Integer(), sa.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=True),
        sa.Column('builtin_secret_id', sa.Integer(), sa.ForeignKey('builtin_secrets.id', ondelete='CASCADE'), nullable=True),
        sa.Column('name', sa.String(255), nullable=True),
        sa.Column('target', sa.String(1024), nullable=True),
        sa.Column('target_type', sa.String(64), nullable=True),
        sa.Column('status', sa.String(64), nullable=True),
        sa.Column('format', sa.String(64), nullable=True),
        sa.Column('component_count', sa.Integer(), nullable=True),
        sa.Column('vulnerability_count', sa.Integer(), nullable=True),
        sa.Column('error_message', sa.Text(), nullable=True),
        sa.Column('scanned_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=True, server_default=sa.text('now()')),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True, server_default=sa.text('now()')),
    )

    # 62. vulnerabilities
    op.create_table(
        'vulnerabilities',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('tenant_id', sa.Integer(), sa.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=True),
        sa.Column('cve_id', sa.String(64), nullable=True, unique=True),
        sa.Column('title', sa.String(512), nullable=True),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('severity', sa.String(32), nullable=True),
        sa.Column('cvss_score', sa.Numeric(4, 1), nullable=True),
        sa.Column('cvss_vector', sa.String(255), nullable=True),
        sa.Column('cwes', sa.JSON(), nullable=True),
        sa.Column('references', sa.JSON(), nullable=True),
        sa.Column('published_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('modified_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=True, server_default=sa.text('now()')),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True, server_default=sa.text('now()')),
    )

    # 63. component_vulnerabilities
    op.create_table(
        'component_vulnerabilities',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('tenant_id', sa.Integer(), sa.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=True),
        sa.Column('component_id', sa.Integer(), sa.ForeignKey('sbom_components.id', ondelete='CASCADE'), nullable=False),
        sa.Column('vulnerability_id', sa.Integer(), sa.ForeignKey('vulnerabilities.id', ondelete='CASCADE'), nullable=False),
        sa.Column('fixed_by_id', sa.Integer(), sa.ForeignKey('identities.id', ondelete='CASCADE'), nullable=True),
        sa.Column('status', sa.String(64), nullable=True),
        sa.Column('fixed_version', sa.String(255), nullable=True),
        sa.Column('suppressed', sa.Boolean(), nullable=False, server_default=sa.text('false')),
        sa.Column('suppression_reason', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=True, server_default=sa.text('now()')),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True, server_default=sa.text('now()')),
    )

    # 64. license_policies
    op.create_table(
        'license_policies',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('tenant_id', sa.Integer(), sa.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=True),
        sa.Column('organization_id', sa.Integer(), sa.ForeignKey('organizations.id', ondelete='CASCADE'), nullable=True),
        sa.Column('name', sa.String(255), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('allowed_licenses', sa.JSON(), nullable=True),
        sa.Column('denied_licenses', sa.JSON(), nullable=True),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default=sa.text('true')),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=True, server_default=sa.text('now()')),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True, server_default=sa.text('now()')),
    )

    # 65. sbom_scan_schedules
    op.create_table(
        'sbom_scan_schedules',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('tenant_id', sa.Integer(), sa.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=True),
        sa.Column('name', sa.String(255), nullable=False),
        sa.Column('target', sa.String(1024), nullable=True),
        sa.Column('target_type', sa.String(64), nullable=True),
        sa.Column('schedule', sa.String(255), nullable=True),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default=sa.text('true')),
        sa.Column('last_run_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('next_run_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=True, server_default=sa.text('now()')),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True, server_default=sa.text('now()')),
    )

    # 66. resource_costs
    op.create_table(
        'resource_costs',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('organization_id', sa.Integer(), sa.ForeignKey('organizations.id', ondelete='CASCADE'), nullable=True),
        sa.Column('identity_id', sa.Integer(), sa.ForeignKey('identities.id', ondelete='CASCADE'), nullable=True),
        sa.Column('resource_type', sa.String(64), nullable=False),
        sa.Column('resource_id', sa.String(255), nullable=False),
        sa.Column('provider', sa.String(64), nullable=True),
        sa.Column('service', sa.String(255), nullable=True),
        sa.Column('region', sa.String(255), nullable=True),
        sa.Column('currency', sa.String(8), nullable=True),
        sa.Column('daily_cost', sa.Numeric(18, 6), nullable=True),
        sa.Column('monthly_cost', sa.Numeric(18, 6), nullable=True),
        sa.Column('tags', sa.JSON(), nullable=True),
        sa.Column('period_start', sa.Date(), nullable=True),
        sa.Column('period_end', sa.Date(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=True, server_default=sa.text('now()')),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True, server_default=sa.text('now()')),
    )

    # 67. cost_history
    op.create_table(
        'cost_history',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('resource_cost_id', sa.Integer(), sa.ForeignKey('resource_costs.id', ondelete='CASCADE'), nullable=False),
        sa.Column('date', sa.Date(), nullable=False),
        sa.Column('amount', sa.Numeric(18, 6), nullable=True),
        sa.Column('currency', sa.String(8), nullable=True),
        sa.Column('metadata', sa.JSON(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=True, server_default=sa.text('now()')),
    )

    # 68. cost_sync_jobs
    op.create_table(
        'cost_sync_jobs',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('organization_id', sa.Integer(), sa.ForeignKey('organizations.id', ondelete='CASCADE'), nullable=True),
        sa.Column('provider', sa.String(64), nullable=False),
        sa.Column('status', sa.String(64), nullable=True),
        sa.Column('period_start', sa.Date(), nullable=True),
        sa.Column('period_end', sa.Date(), nullable=True),
        sa.Column('records_synced', sa.Integer(), nullable=True),
        sa.Column('error_message', sa.Text(), nullable=True),
        sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=True, server_default=sa.text('now()')),
    )


def downgrade():
    # Drop in reverse dependency order
    op.drop_table('cost_sync_jobs')
    op.drop_table('cost_history')
    op.drop_table('resource_costs')
    op.drop_table('sbom_scan_schedules')
    op.drop_table('license_policies')
    op.drop_table('component_vulnerabilities')
    op.drop_table('vulnerabilities')
    op.drop_table('sbom_scans')
    op.drop_table('sbom_components')
    op.drop_table('data_store_labels')
    op.drop_table('data_stores')
    op.drop_table('certificates')
    op.drop_table('ipam_vlans')
    op.drop_table('ipam_addresses')
    op.drop_table('ipam_prefixes')
    op.drop_table('services')
    op.drop_table('software')
    op.drop_table('key_access_log')
    op.drop_table('secret_access_log')
    op.drop_table('webhook_deliveries')
    op.drop_table('crypto_keys')
    op.drop_table('secrets')
    op.drop_table('sync_conflicts')
    op.drop_table('dependencies')
    op.drop_table('builtin_secrets')
    op.drop_table('network_topology')
    op.drop_table('network_entity_mappings')
    op.drop_table('notification_rules')
    op.drop_table('webhooks')
    op.drop_table('cloud_accounts')
    op.drop_table('google_workspace_providers')
    op.drop_table('iam_providers')
    op.drop_table('key_providers')
    op.drop_table('secret_providers')
    op.drop_table('milestones')
    op.drop_table('projects')
    op.drop_table('labels')
    op.drop_table('entities')
    op.drop_table('networking_resources')
    op.drop_table('audit_logs')
    op.drop_table('organizations')
    op.drop_table('saved_searches')
    op.drop_table('discovery_history')
    op.drop_table('sync_history')
    op.drop_table('sync_mappings')
    op.drop_table('api_keys')
    op.drop_table('user_roles')
    op.drop_table('role_permissions')
    op.drop_table('access_review_assignments')
    op.drop_table('access_review_items')
    op.drop_table('access_reviews')
    op.drop_table('group_access_approvals')
    op.drop_table('group_access_requests')
    op.drop_table('identity_group_memberships')
    op.drop_table('backups')
    op.drop_table('portal_user_org_assignments')
    op.drop_table('audit_retention_policies')
    op.drop_table('backup_jobs')
    op.drop_table('discovery_jobs')
    op.drop_table('sync_configs')
    op.drop_table('permissions')
    op.drop_table('roles')
    op.drop_table('identity_groups')
    op.drop_table('scim_configurations')
    op.drop_table('idp_configurations')
    op.drop_table('portal_users')
    op.drop_table('identities')
    op.drop_table('tenants')
