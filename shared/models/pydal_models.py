"""PyDAL table definitions for Elder application.

This module defines all PyDAL database tables. Long lines are unavoidable
due to Field() definition syntax and are suppressed from linting.
"""

# flake8: noqa: E501

import datetime

from pydal import Field
from pydal.validators import *  # noqa: F401, F403

from shared.utils.village_id import generate_village_id


def define_all_tables(db):
    """Define all database tables using PyDAL.

    Tables are defined in dependency order to satisfy foreign key references.
    """

    # ==========================================
    # LEVEL 0: Tenant table (foundation for multi-tenancy)
    # ==========================================

    # Tenants table - v2.2.0: Enterprise multi-tenancy foundation
    db.define_table(
        "tenants",
        Field("name", "string", length=255, notnull=True, requires=IS_NOT_EMPTY()),
        Field("slug", "string", length=100, notnull=True, unique=True),
        Field("domain", "string", length=255),  # Custom domain for tenant
        Field(
            "subscription_tier",
            "string",
            length=50,
            default="community",
            requires=IS_IN_SET(["community", "professional", "enterprise"]),
        ),
        Field("license_key", "string", length=255),
        Field("settings", "json"),  # Tenant-specific settings
        Field("feature_flags", "json"),  # Feature flag overrides
        Field("data_retention_days", "integer", default=90),
        Field("storage_quota_gb", "integer", default=10),
        Field("is_active", "boolean", default=True, notnull=True),
        Field(
            "created_at",
            "datetime",
            default=lambda: datetime.datetime.now(datetime.timezone.utc),
        ),
        Field(
            "updated_at",
            "datetime",
            update=lambda: datetime.datetime.now(datetime.timezone.utc),
        ),
        Field(
            "village_id", "string", length=32, unique=True, default=generate_village_id
        ),
        migrate=False,
    )

    # ==========================================
    # LEVEL 1: Base tables with no dependencies
    # ==========================================

    # Identities table - must be first (referenced by many tables)
    db.define_table(
        "identities",
        # v2.2.0: Multi-tenancy support
        Field(
            "tenant_id",
            "reference tenants",
            default=1,
            notnull=True,
            ondelete="CASCADE",
        ),
        # v2.0.0: Identity type validation for IAM unification
        Field(
            "identity_type",
            "string",
            length=50,
            notnull=True,
            requires=IS_IN_SET(
                [
                    "employee",
                    "vendor",
                    "bot",
                    "serviceAccount",
                    "integration",
                    "otherHuman",
                    "other",
                ]
            ),
        ),
        Field(
            "username",
            "string",
            length=255,
            notnull=True,
            unique=True,
            requires=IS_NOT_EMPTY(),
        ),
        Field("email", "string", length=255, requires=IS_EMAIL()),
        Field("full_name", "string", length=255),
        Field(
            "organization_id", "integer"
        ),  # Integer field to avoid circular FK reference
        Field(
            "portal_role",
            "string",
            length=20,
            default="observer",
            notnull=True,
            requires=IS_IN_SET(["admin", "editor", "observer"]),
        ),  # Portal access level
        Field("auth_provider", "string", length=50, notnull=True),
        Field("auth_provider_id", "string", length=255),
        Field("password_hash", "string", length=255),
        Field("is_active", "boolean", default=True, notnull=True),
        Field("is_superuser", "boolean", default=False, notnull=True),
        Field("mfa_enabled", "boolean", default=False, notnull=True),
        Field("mfa_secret", "string", length=255),
        Field("last_login_at", "datetime"),
        Field(
            "created_at",
            "datetime",
            default=lambda: datetime.datetime.now(datetime.timezone.utc),
        ),
        Field(
            "updated_at",
            "datetime",
            update=lambda: datetime.datetime.now(datetime.timezone.utc),
        ),
        Field(
            "village_id", "string", length=32, unique=True, default=generate_village_id
        ),
        migrate=False,
    )

    # Portal Users table - v2.2.0: Enterprise portal user management
    db.define_table(
        "portal_users",
        Field("tenant_id", "reference tenants", notnull=True, ondelete="CASCADE"),
        Field("email", "string", length=255, notnull=True, requires=IS_EMAIL()),
        Field("password_hash", "string", length=255),
        Field("mfa_secret", "string", length=255),
        Field("mfa_backup_codes", "json"),  # Encrypted backup codes
        Field(
            "global_role",
            "string",
            length=50,
            requires=IS_EMPTY_OR(IS_IN_SET(["admin", "support"])),
        ),  # Platform-wide role
        Field(
            "tenant_role",
            "string",
            length=50,
            requires=IS_EMPTY_OR(IS_IN_SET(["admin", "maintainer", "reader"])),
        ),  # Tenant-wide role
        Field("full_name", "string", length=255),
        Field("is_active", "boolean", default=True, notnull=True),
        Field("email_verified", "boolean", default=False, notnull=True),
        Field("last_login_at", "datetime"),
        Field("failed_login_attempts", "integer", default=0),
        Field("locked_until", "datetime"),
        Field("password_changed_at", "datetime"),
        Field(
            "created_at",
            "datetime",
            default=lambda: datetime.datetime.now(datetime.timezone.utc),
        ),
        Field(
            "updated_at",
            "datetime",
            update=lambda: datetime.datetime.now(datetime.timezone.utc),
        ),
        migrate=False,
    )

    # IdP Configurations table - v2.2.0: SSO/SAML configuration, v3.0.0: OIDC support
    db.define_table(
        "idp_configurations",
        Field(
            "tenant_id", "reference tenants", ondelete="CASCADE"
        ),  # NULL for global IdP
        Field(
            "idp_type",
            "string",
            length=50,
            notnull=True,
            requires=IS_IN_SET(["saml", "oidc"]),
        ),
        Field("name", "string", length=255, notnull=True, requires=IS_NOT_EMPTY()),
        # SAML-specific fields
        Field("entity_id", "string", length=512),  # SAML Entity ID
        Field("metadata_url", "string", length=1024),  # IdP metadata URL
        Field("sso_url", "string", length=1024),  # SSO endpoint
        Field("slo_url", "string", length=1024),  # Single Logout endpoint
        Field("certificate", "text"),  # X.509 certificate
        # OIDC-specific fields (v3.0.0)
        Field("oidc_client_id", "string", length=512),  # OIDC Client ID
        Field(
            "oidc_client_secret", "string", length=512
        ),  # OIDC Client Secret (encrypted)
        Field(
            "oidc_issuer_url", "string", length=1024
        ),  # OIDC Issuer URL (.well-known/openid-configuration)
        Field(
            "oidc_scopes", "string", length=512, default="openid profile email"
        ),  # Space-separated scopes
        Field(
            "oidc_response_type", "string", length=50, default="code"
        ),  # OAuth2 response type
        Field(
            "oidc_token_endpoint_auth_method",
            "string",
            length=100,
            default="client_secret_basic",
        ),  # Token endpoint auth method
        # Common fields
        Field("attribute_mappings", "json"),  # Map IdP attributes to user fields
        Field("jit_provisioning_enabled", "boolean", default=True, notnull=True),
        Field(
            "default_role",
            "string",
            length=50,
            default="reader",
            requires=IS_IN_SET(["admin", "maintainer", "reader"]),
        ),
        Field("is_active", "boolean", default=True, notnull=True),
        Field(
            "created_at",
            "datetime",
            default=lambda: datetime.datetime.now(datetime.timezone.utc),
        ),
        Field(
            "updated_at",
            "datetime",
            update=lambda: datetime.datetime.now(datetime.timezone.utc),
        ),
        migrate=False,
    )

    # SCIM Configurations table - v2.2.0: SCIM provisioning
    db.define_table(
        "scim_configurations",
        Field("tenant_id", "reference tenants", notnull=True, ondelete="CASCADE"),
        Field("endpoint_url", "string", length=1024, notnull=True),
        Field("bearer_token", "string", length=512, notnull=True),
        Field("sync_groups", "boolean", default=True, notnull=True),
        Field("is_active", "boolean", default=True, notnull=True),
        Field("last_sync_at", "datetime"),
        Field(
            "created_at",
            "datetime",
            default=lambda: datetime.datetime.now(datetime.timezone.utc),
        ),
        Field(
            "updated_at",
            "datetime",
            update=lambda: datetime.datetime.now(datetime.timezone.utc),
        ),
        migrate=False,
    )

    # Identity Groups table - must be before organizations
    db.define_table(
        "identity_groups",
        Field("name", "string", length=255, notnull=True, requires=IS_NOT_EMPTY()),
        Field("description", "string", length=512),
        Field("ldap_dn", "string", length=512),
        Field("saml_group", "string", length=255),
        Field("is_active", "boolean", default=True, notnull=True),
        # Group ownership (Enterprise feature)
        Field("owner_identity_id", "integer"),  # Reference to identities (added later)
        Field("owner_group_id", "integer"),  # Self-reference to identity_groups
        # Approval workflow settings
        Field(
            "approval_mode", "string", length=20, default="any"
        ),  # any, all, threshold
        Field("approval_threshold", "integer", default=1),
        # Multi-provider configuration
        Field(
            "provider", "string", length=50, default="internal"
        ),  # internal, ldap, okta
        Field("provider_group_id", "string", length=512),  # Provider-specific group ID
        Field("sync_enabled", "boolean", default=False),
        # Access review configuration (Enterprise feature)
        Field("review_enabled", "boolean", default=False, notnull=True),
        Field(
            "review_interval_days", "integer", default=90
        ),  # 90=quarterly, 365=yearly
        Field("last_review_date", "datetime"),
        Field("next_review_date", "datetime"),
        Field("review_assignment_mode", "string", length=20, default="all_owners"),
        Field("review_due_days", "integer", default=14),  # Days to complete review
        Field("review_auto_apply", "boolean", default=True, notnull=True),
        Field(
            "created_at",
            "datetime",
            default=lambda: datetime.datetime.now(datetime.timezone.utc),
        ),
        Field(
            "updated_at",
            "datetime",
            update=lambda: datetime.datetime.now(datetime.timezone.utc),
        ),
        migrate=False,
    )

    # Roles table - no dependencies
    db.define_table(
        "roles",
        Field(
            "name",
            "string",
            length=100,
            notnull=True,
            unique=True,
            requires=IS_NOT_EMPTY(),
        ),
        Field("description", "string", length=512),
        Field(
            "created_at",
            "datetime",
            default=lambda: datetime.datetime.now(datetime.timezone.utc),
        ),
        Field(
            "updated_at",
            "datetime",
            update=lambda: datetime.datetime.now(datetime.timezone.utc),
        ),
        migrate=False,
    )

    # Permissions table - no dependencies
    db.define_table(
        "permissions",
        Field(
            "name",
            "string",
            length=100,
            notnull=True,
            unique=True,
            requires=IS_NOT_EMPTY(),
        ),
        Field("resource_type", "string", length=50, notnull=True),
        Field("action_name", "string", length=50, notnull=True),
        Field("description", "string", length=512),
        Field(
            "created_at",
            "datetime",
            default=lambda: datetime.datetime.now(datetime.timezone.utc),
        ),
        Field(
            "updated_at",
            "datetime",
            update=lambda: datetime.datetime.now(datetime.timezone.utc),
        ),
        migrate=False,
    )

    # Sync Configs table - no dependencies
    db.define_table(
        "sync_configs",
        Field(
            "name",
            "string",
            length=255,
            notnull=True,
            unique=True,
            requires=IS_NOT_EMPTY(),
        ),
        Field(
            "platform",
            "string",
            length=50,
            notnull=True,
            requires=IS_IN_SET(["github", "gitlab", "jira", "trello", "openproject"]),
        ),
        Field("enabled", "boolean", default=True, notnull=True),
        Field("sync_interval", "integer", default=300, notnull=True),  # seconds
        Field("batch_fallback_enabled", "boolean", default=True, notnull=True),
        Field("batch_size", "integer", default=100, notnull=True),
        Field("two_way_create", "boolean", default=False, notnull=True),
        Field("webhook_enabled", "boolean", default=True, notnull=True),
        Field("webhook_secret", "string", length=255),
        Field("last_sync_at", "datetime"),
        Field("last_batch_sync_at", "datetime"),
        Field(
            "config_json", "json"
        ),  # Platform-specific configuration (API tokens, URLs, etc.)
        Field(
            "created_at",
            "datetime",
            default=lambda: datetime.datetime.now(datetime.timezone.utc),
        ),
        Field(
            "updated_at",
            "datetime",
            update=lambda: datetime.datetime.now(datetime.timezone.utc),
        ),
        migrate=False,
    )

    # Discovery Jobs table - no dependencies (Phase 5: Cloud Auto-Discovery)
    db.define_table(
        "discovery_jobs",
        Field("name", "string", length=255, notnull=True, requires=IS_NOT_EMPTY()),
        Field(
            "provider",
            "string",
            length=50,
            notnull=True,
            requires=IS_IN_SET(
                [
                    "aws",
                    "gcp",
                    "azure",
                    "kubernetes",
                    "network",
                    "http_screenshot",
                    "banner",
                ]
            ),
        ),
        Field("config_json", "json", notnull=True),  # Provider-specific configuration
        Field("schedule_interval", "integer", default=3600, notnull=True),  # seconds
        Field("enabled", "boolean", default=True, notnull=True),
        # v2.0.0: Credential integration for discovery jobs
        Field(
            "credential_type",
            "string",
            length=50,
            requires=IS_IN_SET(["secret", "key", "builtin_secret", "static", "none"]),
        ),  # Type of credential
        Field(
            "credential_id", "integer"
        ),  # ID of the credential (secret_id, key_id, or builtin_secret_id)
        Field(
            "credential_mapping", "json"
        ),  # Maps credential keys to discovery config fields
        Field("last_run_at", "datetime"),
        Field("next_run_at", "datetime"),
        Field(
            "created_at",
            "datetime",
            default=lambda: datetime.datetime.now(datetime.timezone.utc),
        ),
        Field(
            "updated_at",
            "datetime",
            update=lambda: datetime.datetime.now(datetime.timezone.utc),
        ),
        migrate=False,
    )

    # Backup Jobs table - no dependencies (Phase 10: Advanced Search & Data Management)
    db.define_table(
        "backup_jobs",
        Field("name", "string", length=255, notnull=True, requires=IS_NOT_EMPTY()),
        Field("schedule", "string", length=100, notnull=True),  # Cron expression
        Field("retention_days", "integer", default=30, notnull=True),
        Field("enabled", "boolean", default=True, notnull=True),
        Field("last_run_at", "datetime"),
        # S3 Configuration (optional, per-job override of global S3 settings)
        Field("s3_enabled", "boolean", default=False, notnull=True),
        Field(
            "s3_endpoint", "string", length=255
        ),  # S3 endpoint URL (e.g., s3.amazonaws.com, minio.example.com)
        Field("s3_bucket", "string", length=255),  # S3 bucket name
        Field("s3_region", "string", length=50),  # S3 region (e.g., us-east-1)
        Field("s3_access_key", "string", length=255),  # S3 access key ID
        Field(
            "s3_secret_key", "string", length=255
        ),  # S3 secret access key (should be encrypted)
        Field(
            "s3_prefix", "string", length=255
        ),  # S3 key prefix (e.g., elder/backups/)
        Field(
            "created_at",
            "datetime",
            default=lambda: datetime.datetime.now(datetime.timezone.utc),
        ),
        Field(
            "updated_at",
            "datetime",
            update=lambda: datetime.datetime.now(datetime.timezone.utc),
        ),
        migrate=False,
    )

    # Audit Retention Policies table - no dependencies (Phase 8: Audit System Enhancement)
    db.define_table(
        "audit_retention_policies",
        Field("resource_type", "string", length=50, notnull=True, unique=True),
        Field("retention_days", "integer", default=90, notnull=True),
        Field("enabled", "boolean", default=True, notnull=True),
        Field(
            "created_at",
            "datetime",
            default=lambda: datetime.datetime.now(datetime.timezone.utc),
        ),
        Field(
            "updated_at",
            "datetime",
            update=lambda: datetime.datetime.now(datetime.timezone.utc),
        ),
        migrate=False,
    )

    # ==========================================
    # LEVEL 2: Tables with Level 1 dependencies
    # ==========================================

    # Portal User Org Assignments table (depends on: portal_users) - v2.2.0
    # Note: organization_id is integer to avoid circular reference, will be validated in app
    db.define_table(
        "portal_user_org_assignments",
        Field(
            "portal_user_id", "reference portal_users", notnull=True, ondelete="CASCADE"
        ),
        Field("organization_id", "integer", notnull=True),  # Reference to organizations
        Field(
            "role",
            "string",
            length=50,
            notnull=True,
            requires=IS_IN_SET(["admin", "maintainer", "reader"]),
        ),
        Field(
            "created_at",
            "datetime",
            default=lambda: datetime.datetime.now(datetime.timezone.utc),
        ),
        migrate=False,
    )

    # Backups table (depends on: backup_jobs) - Phase 10: Advanced Search & Data Management
    db.define_table(
        "backups",
        Field("job_id", "reference backup_jobs", notnull=True, ondelete="CASCADE"),
        Field("filename", "string", length=255, notnull=True),
        Field("file_path", "string", length=512),
        Field("file_size", "bigint"),  # Size in bytes
        Field("record_count", "integer", default=0),
        Field(
            "status",
            "string",
            length=50,
            notnull=True,
            default="pending",
            requires=IS_IN_SET(["pending", "running", "completed", "failed"]),
        ),
        Field("error_message", "text"),
        Field("started_at", "datetime"),
        Field("completed_at", "datetime"),
        Field("duration_seconds", "integer"),
        Field("s3_url", "string", length=1024),  # Full S3 URL if uploaded to S3
        Field("s3_key", "string", length=512),  # S3 object key for deletion/download
        Field(
            "created_at",
            "datetime",
            default=lambda: datetime.datetime.now(datetime.timezone.utc),
        ),
        migrate=False,
    )

    # Identity Group Memberships table (depends on: identities, identity_groups)
    db.define_table(
        "identity_group_memberships",
        Field("identity_id", "reference identities", notnull=True, ondelete="CASCADE"),
        Field(
            "group_id", "reference identity_groups", notnull=True, ondelete="CASCADE"
        ),
        # Expiration support (Enterprise feature)
        Field("expires_at", "datetime"),
        Field(
            "granted_via_request_id", "integer"
        ),  # Reference to group_access_requests
        # Provider sync tracking
        Field("provider_synced", "boolean", default=False),
        Field("provider_synced_at", "datetime"),
        Field("provider_member_id", "string", length=512),  # Provider-specific user ID
        Field(
            "created_at",
            "datetime",
            default=lambda: datetime.datetime.now(datetime.timezone.utc),
        ),
        migrate=False,
    )

    # Group Access Requests table (Enterprise feature - depends on: identity_groups, identities)
    db.define_table(
        "group_access_requests",
        Field("tenant_id", "integer", default=1, notnull=True),
        Field(
            "group_id", "reference identity_groups", notnull=True, ondelete="CASCADE"
        ),
        Field("requester_id", "reference identities", notnull=True, ondelete="CASCADE"),
        Field(
            "status", "string", length=20, default="pending"
        ),  # pending, approved, denied, expired, cancelled
        Field("reason", "text"),
        Field("expires_at", "datetime"),  # Requested membership expiration
        Field("decided_at", "datetime"),
        Field("decided_by_id", "integer"),  # Reference to identities
        Field("decision_comment", "text"),
        Field("village_id", "string", length=32, unique=True),
        Field(
            "created_at",
            "datetime",
            default=lambda: datetime.datetime.now(datetime.timezone.utc),
        ),
        Field(
            "updated_at",
            "datetime",
            update=lambda: datetime.datetime.now(datetime.timezone.utc),
        ),
        migrate=False,
    )

    # Group Access Approvals table (Enterprise feature - tracks individual approvals)
    db.define_table(
        "group_access_approvals",
        Field("tenant_id", "integer", default=1, notnull=True),
        Field(
            "request_id",
            "reference group_access_requests",
            notnull=True,
            ondelete="CASCADE",
        ),
        Field("approver_id", "reference identities", notnull=True, ondelete="CASCADE"),
        Field("decision", "string", length=20, notnull=True),  # approved, denied
        Field("comment", "text"),
        Field(
            "created_at",
            "datetime",
            default=lambda: datetime.datetime.now(datetime.timezone.utc),
        ),
        migrate=False,
    )

    # Access Reviews table (Enterprise feature - periodic membership reviews)
    db.define_table(
        "access_reviews",
        Field("tenant_id", "integer", default=1, notnull=True),
        Field(
            "group_id",
            "reference identity_groups",
            notnull=True,
            ondelete="CASCADE",
        ),
        Field("review_period_start", "datetime", notnull=True),
        Field("review_period_end", "datetime", notnull=True),
        Field("due_date", "datetime", notnull=True),
        Field(
            "status",
            "string",
            length=20,
            default="scheduled",
            requires=IS_IN_SET(["scheduled", "in_progress", "completed", "overdue"]),
        ),
        Field("completed_at", "datetime"),
        Field("completed_by_id", "integer"),
        Field("total_members", "integer", default=0),
        Field("members_reviewed", "integer", default=0),
        Field("members_kept", "integer", default=0),
        Field("members_removed", "integer", default=0),
        Field("auto_apply_decisions", "boolean", default=True, notnull=True),
        Field(
            "village_id", "string", length=32, unique=True, default=generate_village_id
        ),
        Field(
            "created_at",
            "datetime",
            default=lambda: datetime.datetime.now(datetime.timezone.utc),
        ),
        Field(
            "updated_at",
            "datetime",
            update=lambda: datetime.datetime.now(datetime.timezone.utc),
        ),
        migrate=False,
    )

    # Access Review Items table (tracks individual member reviews)
    db.define_table(
        "access_review_items",
        Field("tenant_id", "integer", default=1, notnull=True),
        Field(
            "review_id",
            "reference access_reviews",
            notnull=True,
            ondelete="CASCADE",
        ),
        Field(
            "membership_id",
            "reference identity_group_memberships",
            notnull=True,
        ),
        Field("identity_id", "reference identities", notnull=True),
        Field(
            "decision",
            "string",
            length=20,
            requires=IS_EMPTY_OR(IS_IN_SET(["keep", "remove", "extend"])),
        ),
        Field("justification", "text"),
        Field("new_expiration", "datetime"),
        Field("reviewed_by_id", "integer"),
        Field("reviewed_at", "datetime"),
        Field(
            "created_at",
            "datetime",
            default=lambda: datetime.datetime.now(datetime.timezone.utc),
        ),
        migrate=False,
    )

    # Access Review Assignments table (tracks who is assigned to review)
    db.define_table(
        "access_review_assignments",
        Field("tenant_id", "integer", default=1, notnull=True),
        Field(
            "review_id",
            "reference access_reviews",
            notnull=True,
            ondelete="CASCADE",
        ),
        Field(
            "reviewer_identity_id",
            "reference identities",
            notnull=True,
        ),
        Field(
            "assigned_at",
            "datetime",
            default=lambda: datetime.datetime.now(datetime.timezone.utc),
        ),
        Field("completed", "boolean", default=False, notnull=True),
        Field("completed_at", "datetime"),
        migrate=False,
    )

    # Role Permissions table (depends on: roles, permissions)
    db.define_table(
        "role_permissions",
        Field("role_id", "reference roles", notnull=True, ondelete="CASCADE"),
        Field(
            "permission_id", "reference permissions", notnull=True, ondelete="CASCADE"
        ),
        Field(
            "created_at",
            "datetime",
            default=lambda: datetime.datetime.now(datetime.timezone.utc),
        ),
        migrate=False,
    )

    # User Roles table (depends on: identities, roles)
    db.define_table(
        "user_roles",
        Field("identity_id", "reference identities", notnull=True, ondelete="CASCADE"),
        Field("role_id", "reference roles", notnull=True, ondelete="CASCADE"),
        Field("scope", "string", length=50, notnull=True),
        Field("scope_id", "integer"),
        Field(
            "created_at",
            "datetime",
            default=lambda: datetime.datetime.now(datetime.timezone.utc),
        ),
        migrate=False,
    )

    # API Keys table (depends on: identities)
    db.define_table(
        "api_keys",
        Field("identity_id", "reference identities", notnull=True, ondelete="CASCADE"),
        Field("name", "string", length=255, notnull=True, requires=IS_NOT_EMPTY()),
        Field(
            "key_hash", "string", length=255, notnull=True
        ),  # SHA256 hash of the API key
        Field(
            "prefix", "string", length=20, notnull=True
        ),  # First few chars for display (e.g., "elder_123...")
        Field("last_used_at", "datetime"),
        Field("expires_at", "datetime"),
        Field("is_active", "boolean", default=True, notnull=True),
        Field(
            "created_at",
            "datetime",
            default=lambda: datetime.datetime.now(datetime.timezone.utc),
        ),
        Field(
            "updated_at",
            "datetime",
            update=lambda: datetime.datetime.now(datetime.timezone.utc),
        ),
        migrate=False,
    )

    # Sync Mappings table (depends on: sync_configs)
    db.define_table(
        "sync_mappings",
        Field(
            "elder_type",
            "string",
            length=50,
            notnull=True,
            requires=IS_IN_SET(
                ["issue", "project", "milestone", "label", "organization"]
            ),
        ),
        Field("elder_id", "integer", notnull=True),
        Field(
            "external_platform",
            "string",
            length=50,
            notnull=True,
            requires=IS_IN_SET(["github", "gitlab", "jira", "trello", "openproject"]),
        ),
        Field("external_id", "string", length=255, notnull=True),
        Field(
            "sync_config_id", "reference sync_configs", notnull=True, ondelete="CASCADE"
        ),
        Field(
            "sync_status",
            "string",
            length=50,
            default="synced",
            requires=IS_IN_SET(["synced", "conflict", "error", "pending"]),
        ),
        Field(
            "sync_method",
            "string",
            length=50,
            default="webhook",
            requires=IS_IN_SET(["webhook", "poll", "batch", "manual"]),
        ),
        Field("last_synced_at", "datetime"),
        Field("elder_updated_at", "datetime"),
        Field("external_updated_at", "datetime"),
        Field(
            "created_at",
            "datetime",
            default=lambda: datetime.datetime.now(datetime.timezone.utc),
        ),
        Field(
            "updated_at",
            "datetime",
            update=lambda: datetime.datetime.now(datetime.timezone.utc),
        ),
        migrate=False,
    )

    # Sync History table (depends on: sync_configs, identities)
    db.define_table(
        "sync_history",
        Field(
            "sync_config_id", "reference sync_configs", notnull=True, ondelete="CASCADE"
        ),
        Field("correlation_id", "string", length=36),  # UUID for distributed tracing
        Field(
            "sync_type",
            "string",
            length=50,
            notnull=True,
            requires=IS_IN_SET(["webhook", "poll", "batch", "manual"]),
        ),
        Field("items_synced", "integer", default=0, notnull=True),
        Field("items_failed", "integer", default=0, notnull=True),
        Field(
            "started_at",
            "datetime",
            default=lambda: datetime.datetime.now(datetime.timezone.utc),
            notnull=True,
        ),
        Field("completed_at", "datetime"),
        Field("success", "boolean", default=True, notnull=True),
        Field("error_message", "text"),
        Field(
            "sync_metadata", "json"
        ),  # Additional sync details, platform-specific data
        migrate=False,
    )

    # Discovery History table (depends on: discovery_jobs) - Phase 5: Cloud Auto-Discovery
    db.define_table(
        "discovery_history",
        Field("job_id", "reference discovery_jobs", notnull=True, ondelete="CASCADE"),
        Field(
            "started_at",
            "datetime",
            default=lambda: datetime.datetime.now(datetime.timezone.utc),
            notnull=True,
        ),
        Field("completed_at", "datetime"),
        Field(
            "status",
            "string",
            length=50,
            notnull=True,
            default="running",
            requires=IS_IN_SET(
                ["running", "completed", "success", "failed", "partial"]
            ),
        ),
        Field("entities_discovered", "integer", default=0, notnull=True),
        Field("entities_created", "integer", default=0, notnull=True),
        Field("entities_updated", "integer", default=0, notnull=True),
        Field("error_message", "text"),
        Field("results_json", "json"),  # Scan results (for local scans)
        migrate=False,
    )

    # Saved Searches table (depends on: identities) - Phase 10: Advanced Search & Data Management
    db.define_table(
        "saved_searches",
        Field("name", "string", length=255, notnull=True, requires=IS_NOT_EMPTY()),
        Field("query", "text", notnull=True),
        Field("filters", "json"),
        Field("identity_id", "reference identities", notnull=True, ondelete="CASCADE"),
        Field(
            "created_at",
            "datetime",
            default=lambda: datetime.datetime.now(datetime.timezone.utc),
        ),
        migrate=False,
    )

    # Organizations table (depends on: identities, identity_groups, tenants)
    db.define_table(
        "organizations",
        # v2.2.0: Multi-tenancy support
        Field(
            "tenant_id",
            "reference tenants",
            default=1,
            notnull=True,
            ondelete="CASCADE",
        ),
        Field("name", "string", length=255, notnull=True, requires=IS_NOT_EMPTY()),
        Field("description", "text"),
        Field(
            "organization_type",
            "string",
            length=50,
            notnull=True,
            default="organization",
            requires=IS_IN_SET(
                ["department", "organization", "team", "collection", "other"]
            ),
        ),
        Field("parent_id", "reference organizations", ondelete="CASCADE"),
        Field("ldap_dn", "string", length=512),
        Field("saml_group", "string", length=255),
        Field("owner_identity_id", "reference identities", ondelete="SET NULL"),
        Field("owner_group_id", "reference identity_groups", ondelete="SET NULL"),
        Field(
            "created_at",
            "datetime",
            default=lambda: datetime.datetime.now(datetime.timezone.utc),
        ),
        Field(
            "updated_at",
            "datetime",
            update=lambda: datetime.datetime.now(datetime.timezone.utc),
        ),
        Field(
            "village_id", "string", length=32, unique=True, default=generate_village_id
        ),
        migrate=False,
    )

    # Resource Roles table (depends on: identities, identity_groups)
    db.define_table(
        "resource_roles",
        Field("identity_id", "reference identities", ondelete="CASCADE"),
        Field("group_id", "reference identity_groups", ondelete="CASCADE"),
        Field("role", "string", length=50, notnull=True),
        Field("resource_type", "string", length=50, notnull=True),
        Field("resource_id", "integer"),
        Field(
            "created_at",
            "datetime",
            default=lambda: datetime.datetime.now(datetime.timezone.utc),
        ),
        Field(
            "updated_at",
            "datetime",
            update=lambda: datetime.datetime.now(datetime.timezone.utc),
        ),
        Field(
            "village_id", "string", length=32, unique=True, default=generate_village_id
        ),
        migrate=False,
    )

    # Audit Logs table (depends on: identities)
    db.define_table(
        "audit_logs",
        Field("identity_id", "reference identities", ondelete="SET NULL"),
        Field("action_name", "string", length=50, notnull=True),
        Field("resource_type", "string", length=50, notnull=True),
        Field("resource_id", "integer"),
        Field("details", "json"),
        Field("success", "boolean", default=True, notnull=True),
        Field("ip_address", "string", length=45),
        Field("user_agent", "string", length=512),
        Field(
            "created_at",
            "datetime",
            default=lambda: datetime.datetime.now(datetime.timezone.utc),
        ),
        migrate=False,
    )

    # ==========================================
    # LEVEL 3: Tables with Level 2 dependencies
    # ==========================================

    # Networking Resources table (depends on: organizations) - v2.0.0: Dedicated Networking Model
    db.define_table(
        "networking_resources",
        Field("name", "string", length=255, notnull=True, requires=IS_NOT_EMPTY()),
        Field("description", "text"),
        Field(
            "network_type",
            "string",
            length=50,
            notnull=True,
            requires=IS_IN_SET(
                [
                    "subnet",
                    "firewall",
                    "proxy",
                    "router",
                    "switch",
                    "hub",
                    "tunnel",
                    "route_table",
                    "vrrf",
                    "vxlan",
                    "vlan",
                    "namespace",
                    "ingress",
                    "cni",
                    "load_balancer",
                    "other",
                ]
            ),
        ),
        Field(
            "organization_id",
            "reference organizations",
            notnull=True,
            ondelete="CASCADE",
        ),
        Field(
            "parent_id", "reference networking_resources", ondelete="CASCADE"
        ),  # Hierarchical networks
        # v2.0.0: New fields for network resources
        Field(
            "region", "string", length=100
        ),  # Geographic region (e.g., us-east-1, europe-west1)
        Field("location", "string", length=255),  # Physical location or datacenter
        Field("poc", "string", length=255),  # Point of contact (email, name, team)
        Field(
            "organizational_unit", "string", length=255
        ),  # OU or department ownership
        # Standard metadata fields
        Field("attributes", "json"),  # Type-specific metadata
        Field(
            "status_metadata", "json"
        ),  # Operational status {status: "Running|Stopped|Error", timestamp: epoch64}
        Field("tags", "list:string"),
        Field("is_active", "boolean", default=True),
        Field(
            "created_at",
            "datetime",
            default=lambda: datetime.datetime.now(datetime.timezone.utc),
        ),
        Field(
            "updated_at",
            "datetime",
            update=lambda: datetime.datetime.now(datetime.timezone.utc),
        ),
        migrate=False,
    )

    # Entities table (depends on: organizations)
    db.define_table(
        "entities",
        Field("name", "string", length=255, notnull=True, requires=IS_NOT_EMPTY()),
        Field("description", "text"),
        Field(
            "entity_type", "string", length=50, notnull=True
        ),  # network, compute, storage, datacenter, security
        Field(
            "sub_type", "string", length=50
        ),  # router, server, database, etc. (sub-type within entity_type)
        Field(
            "organization_id",
            "reference organizations",
            notnull=True,
            ondelete="CASCADE",
        ),
        Field("parent_id", "reference entities", ondelete="CASCADE"),
        Field("attributes", "json"),  # Type-specific metadata
        Field(
            "default_metadata", "json"
        ),  # Default metadata template for this sub_type
        Field("tags", "list:string"),
        Field("is_active", "boolean", default=True),
        # v1.2.1: Status tracking for sync operations (Running, Stopped, Deleted, Creating, Error)
        Field(
            "status_metadata", "json"
        ),  # {status: "Running|Stopped|Deleted|Creating|Error", timestamp: epoch64}
        Field(
            "created_at",
            "datetime",
            default=lambda: datetime.datetime.now(datetime.timezone.utc),
        ),
        Field(
            "updated_at",
            "datetime",
            update=lambda: datetime.datetime.now(datetime.timezone.utc),
        ),
        Field(
            "village_id", "string", length=32, unique=True, default=generate_village_id
        ),
        migrate=False,
    )

    # Issues table (depends on: identities, organizations)
    db.define_table(
        "issues",
        Field("title", "string", length=255, notnull=True, requires=IS_NOT_EMPTY()),
        Field("description", "text"),
        Field("status", "string", length=50, notnull=True, default="open"),
        Field("priority", "string", length=50, default="medium"),
        Field("issue_type", "string", length=50, default="other"),
        Field("reporter_id", "reference identities", notnull=True, ondelete="CASCADE"),
        Field("assignee_id", "reference identities", ondelete="SET NULL"),
        Field("organization_id", "reference organizations", ondelete="CASCADE"),
        Field(
            "is_incident", "integer", default=0, notnull=True
        ),  # Boolean: 0=false, 1=true
        Field("closed_at", "datetime"),
        Field(
            "created_at",
            "datetime",
            default=lambda: datetime.datetime.now(datetime.timezone.utc),
        ),
        Field(
            "updated_at",
            "datetime",
            update=lambda: datetime.datetime.now(datetime.timezone.utc),
        ),
        Field(
            "village_id", "string", length=32, unique=True, default=generate_village_id
        ),
        migrate=False,
    )

    # Issue Labels table - no dependencies
    db.define_table(
        "issue_labels",
        Field(
            "name",
            "string",
            length=100,
            notnull=True,
            unique=True,
            requires=IS_NOT_EMPTY(),
        ),
        Field("color", "string", length=7, default="#cccccc"),
        Field("description", "string", length=512),
        Field(
            "created_at",
            "datetime",
            default=lambda: datetime.datetime.now(datetime.timezone.utc),
        ),
        migrate=False,
    )

    # Labels table - Generic labels for any resource (depends on: organizations) - v2.4.0: Cross-resource labeling
    db.define_table(
        "labels",
        Field("name", "string", length=100, notnull=True, requires=IS_NOT_EMPTY()),
        Field("color", "string", length=7, default="#cccccc"),
        Field("description", "string", length=512),
        Field(
            "organization_id",
            "reference organizations",
            notnull=True,
            ondelete="CASCADE",
        ),
        Field(
            "created_at",
            "datetime",
            default=lambda: datetime.datetime.now(datetime.timezone.utc),
        ),
        Field(
            "updated_at",
            "datetime",
            update=lambda: datetime.datetime.now(datetime.timezone.utc),
        ),
        migrate=False,
    )

    # Projects table (depends on: organizations)
    db.define_table(
        "projects",
        Field("name", "string", length=255, notnull=True, requires=IS_NOT_EMPTY()),
        Field("description", "text"),
        Field("status", "string", length=50, notnull=True, default="active"),
        Field(
            "organization_id",
            "reference organizations",
            notnull=True,
            ondelete="CASCADE",
        ),
        Field("start_date", "date"),
        Field("end_date", "date"),
        Field(
            "created_at",
            "datetime",
            default=lambda: datetime.datetime.now(datetime.timezone.utc),
        ),
        Field(
            "updated_at",
            "datetime",
            update=lambda: datetime.datetime.now(datetime.timezone.utc),
        ),
        Field(
            "village_id", "string", length=32, unique=True, default=generate_village_id
        ),
        migrate=False,
    )

    # Milestones table (depends on: organizations, projects)
    db.define_table(
        "milestones",
        Field("title", "string", length=255, notnull=True, requires=IS_NOT_EMPTY()),
        Field("description", "text"),
        Field("status", "string", length=50, notnull=True, default="open"),
        Field(
            "organization_id",
            "reference organizations",
            notnull=True,
            ondelete="CASCADE",
        ),
        Field("project_id", "reference projects", ondelete="CASCADE"),
        Field("due_date", "date"),
        Field("closed_at", "datetime"),
        Field(
            "created_at",
            "datetime",
            default=lambda: datetime.datetime.now(datetime.timezone.utc),
        ),
        Field(
            "updated_at",
            "datetime",
            update=lambda: datetime.datetime.now(datetime.timezone.utc),
        ),
        Field(
            "village_id", "string", length=32, unique=True, default=generate_village_id
        ),
        migrate=False,
    )

    # Secret Providers table (depends on: organizations) - Phase 2: Secrets Management
    db.define_table(
        "secret_providers",
        Field("name", "string", length=255, notnull=True, requires=IS_NOT_EMPTY()),
        # v2.0.0: Added hashicorp_vault support
        Field(
            "provider",
            "string",
            length=50,
            notnull=True,
            requires=IS_IN_SET(
                [
                    "aws_secrets_manager",
                    "gcp_secret_manager",
                    "infisical",
                    "hashicorp_vault",
                ]
            ),
        ),
        Field("config_json", "json", notnull=True),  # Provider-specific configuration
        Field(
            "organization_id",
            "reference organizations",
            notnull=True,
            ondelete="CASCADE",
        ),
        Field("enabled", "boolean", default=True, notnull=True),
        Field("last_sync_at", "datetime"),
        Field(
            "created_at",
            "datetime",
            default=lambda: datetime.datetime.now(datetime.timezone.utc),
        ),
        Field(
            "updated_at",
            "datetime",
            update=lambda: datetime.datetime.now(datetime.timezone.utc),
        ),
        migrate=False,
    )

    # Key Providers table (depends on: organizations) - Phase 3: Keys Management
    db.define_table(
        "key_providers",
        Field("name", "string", length=255, notnull=True, requires=IS_NOT_EMPTY()),
        # v2.0.0: Added hashicorp_vault support
        Field(
            "provider",
            "string",
            length=50,
            notnull=True,
            requires=IS_IN_SET(["aws_kms", "gcp_kms", "infisical", "hashicorp_vault"]),
        ),
        Field("config_json", "json", notnull=True),  # Provider-specific configuration
        Field(
            "organization_id",
            "reference organizations",
            notnull=True,
            ondelete="CASCADE",
        ),
        Field("enabled", "boolean", default=True, notnull=True),
        Field("last_sync_at", "datetime"),
        Field(
            "created_at",
            "datetime",
            default=lambda: datetime.datetime.now(datetime.timezone.utc),
        ),
        Field(
            "updated_at",
            "datetime",
            update=lambda: datetime.datetime.now(datetime.timezone.utc),
        ),
        migrate=False,
    )

    # IAM Providers table (depends on: organizations) - v2.0.0: Unified IAM Model
    db.define_table(
        "iam_providers",
        Field("name", "string", length=255, notnull=True, requires=IS_NOT_EMPTY()),
        Field(
            "provider_type",
            "string",
            length=50,
            notnull=True,
            requires=IS_IN_SET(["aws_iam", "gcp_iam", "kubernetes", "azure_ad"]),
        ),
        Field("config_json", "json", notnull=True),  # Provider-specific configuration
        Field(
            "organization_id",
            "reference organizations",
            notnull=True,
            ondelete="CASCADE",
        ),
        Field("enabled", "boolean", default=True, notnull=True),
        Field("last_sync_at", "datetime"),
        Field(
            "created_at",
            "datetime",
            default=lambda: datetime.datetime.now(datetime.timezone.utc),
        ),
        Field(
            "updated_at",
            "datetime",
            update=lambda: datetime.datetime.now(datetime.timezone.utc),
        ),
        migrate=False,
    )

    # Google Workspace Providers table (depends on: organizations) - v2.0.0: Unified IAM Model
    db.define_table(
        "google_workspace_providers",
        Field("name", "string", length=255, notnull=True, requires=IS_NOT_EMPTY()),
        Field("domain", "string", length=255, notnull=True),  # Google Workspace domain
        Field(
            "admin_email", "string", length=255, notnull=True, requires=IS_EMAIL()
        ),  # Admin for delegation
        Field("credentials_json", "json", notnull=True),  # Service account credentials
        Field(
            "organization_id",
            "reference organizations",
            notnull=True,
            ondelete="CASCADE",
        ),
        Field("enabled", "boolean", default=True, notnull=True),
        Field("last_sync_at", "datetime"),
        Field(
            "created_at",
            "datetime",
            default=lambda: datetime.datetime.now(datetime.timezone.utc),
        ),
        Field(
            "updated_at",
            "datetime",
            update=lambda: datetime.datetime.now(datetime.timezone.utc),
        ),
        migrate=False,
    )

    # Cloud Accounts table (depends on: organizations) - Phase 5: Cloud Auto-Discovery
    db.define_table(
        "cloud_accounts",
        Field("name", "string", length=255, notnull=True, requires=IS_NOT_EMPTY()),
        Field(
            "provider",
            "string",
            length=50,
            notnull=True,
            requires=IS_IN_SET(["aws", "gcp", "azure", "kubernetes"]),
        ),
        Field("credentials_json", "json", notnull=True),  # Encrypted credentials
        Field(
            "organization_id",
            "reference organizations",
            notnull=True,
            ondelete="CASCADE",
        ),
        Field("enabled", "boolean", default=True, notnull=True),
        Field(
            "created_at",
            "datetime",
            default=lambda: datetime.datetime.now(datetime.timezone.utc),
        ),
        Field(
            "updated_at",
            "datetime",
            update=lambda: datetime.datetime.now(datetime.timezone.utc),
        ),
        migrate=False,
    )

    # Webhooks table (depends on: organizations) - Phase 9: Webhook & Notification System
    db.define_table(
        "webhooks",
        Field("name", "string", length=255, notnull=True, requires=IS_NOT_EMPTY()),
        Field("url", "string", length=2048, notnull=True, requires=IS_URL()),
        Field("secret", "string", length=255),  # HMAC secret for payload signing
        Field(
            "events", "list:string", notnull=True
        ),  # List of event types to trigger on
        Field("enabled", "boolean", default=True, notnull=True),
        Field(
            "organization_id",
            "reference organizations",
            notnull=True,
            ondelete="CASCADE",
        ),
        Field(
            "created_at",
            "datetime",
            default=lambda: datetime.datetime.now(datetime.timezone.utc),
        ),
        Field(
            "updated_at",
            "datetime",
            update=lambda: datetime.datetime.now(datetime.timezone.utc),
        ),
        migrate=False,
    )

    # Notification Rules table (depends on: organizations) - Phase 9: Webhook & Notification System
    db.define_table(
        "notification_rules",
        Field("name", "string", length=255, notnull=True, requires=IS_NOT_EMPTY()),
        Field(
            "channel",
            "string",
            length=50,
            notnull=True,
            requires=IS_IN_SET(["email", "slack", "teams", "pagerduty", "webhook"]),
        ),
        Field(
            "events", "list:string", notnull=True
        ),  # List of event types to trigger on
        Field("config_json", "json", notnull=True),  # Channel-specific configuration
        Field("enabled", "boolean", default=True, notnull=True),
        Field(
            "organization_id",
            "reference organizations",
            notnull=True,
            ondelete="CASCADE",
        ),
        Field(
            "created_at",
            "datetime",
            default=lambda: datetime.datetime.now(datetime.timezone.utc),
        ),
        Field(
            "updated_at",
            "datetime",
            update=lambda: datetime.datetime.now(datetime.timezone.utc),
        ),
        migrate=False,
    )

    # ==========================================
    # LEVEL 4: Tables with Level 3 dependencies
    # ==========================================

    # Network Entity Mappings table (depends on: networking_resources, entities) - v2.0.0
    db.define_table(
        "network_entity_mappings",
        Field(
            "network_id",
            "reference networking_resources",
            notnull=True,
            ondelete="CASCADE",
        ),
        Field("entity_id", "reference entities", notnull=True, ondelete="CASCADE"),
        Field(
            "relationship_type",
            "string",
            length=50,
            notnull=True,
            requires=IS_IN_SET(
                ["attached", "routed_through", "connected_to", "secured_by", "other"]
            ),
        ),
        Field("metadata", "json"),  # Additional relationship metadata
        Field(
            "created_at",
            "datetime",
            default=lambda: datetime.datetime.now(datetime.timezone.utc),
        ),
        Field(
            "updated_at",
            "datetime",
            update=lambda: datetime.datetime.now(datetime.timezone.utc),
        ),
        migrate=False,
    )

    # Network Topology table (depends on: networking_resources) - v2.0.0
    db.define_table(
        "network_topology",
        Field(
            "source_network_id",
            "reference networking_resources",
            notnull=True,
            ondelete="CASCADE",
        ),
        Field(
            "target_network_id",
            "reference networking_resources",
            notnull=True,
            ondelete="CASCADE",
        ),
        Field(
            "connection_type",
            "string",
            length=50,
            notnull=True,
            requires=IS_IN_SET(
                [
                    "peering",
                    "transit",
                    "vpn",
                    "direct_connect",
                    "routing",
                    "switching",
                    "other",
                ]
            ),
        ),
        Field("bandwidth", "string", length=50),  # e.g., "1Gbps", "10Gbps"
        Field("latency", "string", length=50),  # e.g., "5ms", "100ms"
        Field("metadata", "json"),  # Additional connection metadata
        Field(
            "created_at",
            "datetime",
            default=lambda: datetime.datetime.now(datetime.timezone.utc),
        ),
        Field(
            "updated_at",
            "datetime",
            update=lambda: datetime.datetime.now(datetime.timezone.utc),
        ),
        migrate=False,
    )

    # Built-in Secrets table (depends on: organizations) - v2.0.0: In-app secret storage with encryption
    db.define_table(
        "builtin_secrets",
        Field("name", "string", length=255, notnull=True, requires=IS_NOT_EMPTY()),
        Field("description", "text"),
        Field(
            "organization_id",
            "reference organizations",
            notnull=True,
            ondelete="CASCADE",
        ),
        # PyDAL password field automatically encrypts/hashes the value
        Field("secret_value", "password"),  # For simple string secrets (encrypted)
        # JSON field for structured secrets (needs manual encryption if sensitive)
        Field("secret_json", "json"),  # For complex JSON credentials
        Field(
            "secret_type",
            "string",
            length=50,
            notnull=True,
            default="password",
            requires=IS_IN_SET(
                [
                    "api_key",
                    "password",
                    "certificate",
                    "ssh_key",
                    "json_credential",
                    "other",
                ]
            ),
        ),
        Field("tags", "list:string"),
        Field("is_active", "boolean", default=True, notnull=True),
        Field("expires_at", "datetime"),
        Field(
            "created_at",
            "datetime",
            default=lambda: datetime.datetime.now(datetime.timezone.utc),
        ),
        Field(
            "updated_at",
            "datetime",
            update=lambda: datetime.datetime.now(datetime.timezone.utc),
        ),
        migrate=False,
    )

    # Dependencies table - polymorphic links between any resource types
    db.define_table(
        "dependencies",
        Field("tenant_id", "reference tenants", notnull=True, ondelete="CASCADE"),
        Field(
            "source_type",
            "string",
            length=50,
            notnull=True,
            requires=IS_IN_SET(
                ["entity", "identity", "project", "milestone", "issue", "organization"]
            ),
        ),
        Field("source_id", "integer", notnull=True),
        Field(
            "target_type",
            "string",
            length=50,
            notnull=True,
            requires=IS_IN_SET(
                ["entity", "identity", "project", "milestone", "issue", "organization"]
            ),
        ),
        Field("target_id", "integer", notnull=True),
        Field("dependency_type", "string", length=50, notnull=True),
        Field("metadata", "json"),
        Field(
            "created_at",
            "datetime",
            default=lambda: datetime.datetime.now(datetime.timezone.utc),
        ),
        Field(
            "updated_at",
            "datetime",
            update=lambda: datetime.datetime.now(datetime.timezone.utc),
        ),
        Field(
            "village_id", "string", length=32, unique=True, default=generate_village_id
        ),
        migrate=False,
    )

    # Issue Comments table (depends on: issues, identities)
    db.define_table(
        "issue_comments",
        Field("issue_id", "reference issues", notnull=True, ondelete="CASCADE"),
        Field("author_id", "reference identities", notnull=True, ondelete="CASCADE"),
        Field("content", "text", notnull=True, requires=IS_NOT_EMPTY()),
        Field(
            "created_at",
            "datetime",
            default=lambda: datetime.datetime.now(datetime.timezone.utc),
        ),
        Field(
            "updated_at",
            "datetime",
            update=lambda: datetime.datetime.now(datetime.timezone.utc),
        ),
        migrate=False,
    )

    # Issue Label Assignments table (depends on: issues, issue_labels)
    db.define_table(
        "issue_label_assignments",
        Field("issue_id", "reference issues", notnull=True, ondelete="CASCADE"),
        Field("label_id", "reference issue_labels", notnull=True, ondelete="CASCADE"),
        Field(
            "created_at",
            "datetime",
            default=lambda: datetime.datetime.now(datetime.timezone.utc),
        ),
        migrate=False,
    )

    # Issue Entity Links table (depends on: issues, entities)
    db.define_table(
        "issue_entity_links",
        Field("issue_id", "reference issues", notnull=True, ondelete="CASCADE"),
        Field("entity_id", "reference entities", notnull=True, ondelete="CASCADE"),
        Field(
            "created_at",
            "datetime",
            default=lambda: datetime.datetime.now(datetime.timezone.utc),
        ),
        migrate=False,
    )

    # Issue Milestone Links table (depends on: issues, milestones)
    db.define_table(
        "issue_milestone_links",
        Field("issue_id", "reference issues", notnull=True, ondelete="CASCADE"),
        Field("milestone_id", "reference milestones", notnull=True, ondelete="CASCADE"),
        Field(
            "created_at",
            "datetime",
            default=lambda: datetime.datetime.now(datetime.timezone.utc),
        ),
        migrate=False,
    )

    # Issue Project Links table (depends on: issues, projects)
    db.define_table(
        "issue_project_links",
        Field("issue_id", "reference issues", notnull=True, ondelete="CASCADE"),
        Field("project_id", "reference projects", notnull=True, ondelete="CASCADE"),
        Field(
            "created_at",
            "datetime",
            default=lambda: datetime.datetime.now(datetime.timezone.utc),
        ),
        migrate=False,
    )

    # Sync Conflicts table (depends on: sync_mappings, identities)
    db.define_table(
        "sync_conflicts",
        Field(
            "mapping_id", "reference sync_mappings", notnull=True, ondelete="CASCADE"
        ),
        Field(
            "conflict_type",
            "string",
            length=50,
            notnull=True,
            requires=IS_IN_SET(
                ["timestamp", "field_mismatch", "deleted_external", "deleted_local"]
            ),
        ),
        Field("elder_data", "json", notnull=True),  # Elder's version of the data
        Field(
            "external_data", "json", notnull=True
        ),  # External platform's version of the data
        Field(
            "resolution_strategy",
            "string",
            length=50,
            default="manual",
            requires=IS_IN_SET(["manual", "elder_wins", "external_wins", "merge"]),
        ),
        Field("resolved", "boolean", default=False, notnull=True),
        Field("resolved_at", "datetime"),
        Field("resolved_by_id", "reference identities", ondelete="SET NULL"),
        Field(
            "created_at",
            "datetime",
            default=lambda: datetime.datetime.now(datetime.timezone.utc),
        ),
        migrate=False,
    )

    # Metadata Fields table - uses generic resource_type/resource_id pattern
    db.define_table(
        "metadata_fields",
        Field("key", "string", length=255, notnull=True, requires=IS_NOT_EMPTY()),
        Field("value", "text"),
        Field("field_type", "string", length=50, notnull=True, default="string"),
        Field("is_system", "boolean", default=False, notnull=True),
        Field("resource_type", "string", length=50, notnull=True),
        Field("resource_id", "integer", notnull=True),
        Field(
            "created_at",
            "datetime",
            default=lambda: datetime.datetime.now(datetime.timezone.utc),
        ),
        Field(
            "updated_at",
            "datetime",
            update=lambda: datetime.datetime.now(datetime.timezone.utc),
        ),
        Field(
            "village_id", "string", length=32, unique=True, default=generate_village_id
        ),
        migrate=False,
    )

    # Secrets table (depends on: secret_providers, organizations) - Phase 2: Secrets Management
    db.define_table(
        "secrets",
        Field("name", "string", length=255, notnull=True, requires=IS_NOT_EMPTY()),
        Field(
            "provider_id",
            "reference secret_providers",
            notnull=True,
            ondelete="CASCADE",
        ),
        Field(
            "provider_path", "string", length=512, notnull=True
        ),  # Path/name in provider
        Field(
            "secret_type",
            "string",
            length=50,
            notnull=True,
            default="generic",
            requires=IS_IN_SET(
                ["generic", "api_key", "password", "certificate", "ssh_key"]
            ),
        ),
        Field(
            "is_kv", "boolean", default=False, notnull=True
        ),  # Key-Value store vs single value
        Field(
            "organization_id",
            "reference organizations",
            notnull=True,
            ondelete="CASCADE",
        ),
        Field(
            "parent_id", "reference secrets", ondelete="CASCADE"
        ),  # For hierarchical secrets
        Field("metadata", "json"),  # Additional metadata about the secret
        Field("last_synced_at", "datetime"),
        Field(
            "created_at",
            "datetime",
            default=lambda: datetime.datetime.now(datetime.timezone.utc),
        ),
        Field(
            "updated_at",
            "datetime",
            update=lambda: datetime.datetime.now(datetime.timezone.utc),
        ),
        migrate=False,
    )

    # Keys table (depends on: key_providers, organizations) - Phase 3: Keys Management
    db.define_table(
        "crypto_keys",
        Field("name", "string", length=255, notnull=True, requires=IS_NOT_EMPTY()),
        Field(
            "key_provider_id",
            "reference key_providers",
            notnull=True,
            ondelete="CASCADE",
        ),
        Field(
            "provider_key_id", "string", length=512, notnull=True
        ),  # Key ID in provider
        Field(
            "provider_key_arn", "string", length=512
        ),  # Key ARN in provider (AWS, GCP)
        Field(
            "key_hash", "string", length=255, notnull=True
        ),  # Hash of key for tracking
        Field(
            "key_type", "string", length=50, default="symmetric"
        ),  # symmetric, asymmetric, hmac
        Field(
            "key_state", "string", length=50, default="Enabled"
        ),  # Enabled, Disabled, PendingDeletion
        Field(
            "organization_id",
            "reference organizations",
            notnull=True,
            ondelete="CASCADE",
        ),
        Field("metadata_json", "json"),  # Additional metadata about the key
        Field("last_synced_at", "datetime"),
        Field(
            "created_at",
            "datetime",
            default=lambda: datetime.datetime.now(datetime.timezone.utc),
        ),
        Field(
            "updated_at",
            "datetime",
            update=lambda: datetime.datetime.now(datetime.timezone.utc),
        ),
        migrate=False,
    )

    # Webhook Deliveries table (depends on: webhooks) - Phase 9: Webhook & Notification System
    db.define_table(
        "webhook_deliveries",
        Field("webhook_id", "reference webhooks", notnull=True, ondelete="CASCADE"),
        Field("event_type", "string", length=100, notnull=True),
        Field("payload", "json", notnull=True),
        Field("response_status", "integer"),  # HTTP status code
        Field("response_body", "text"),
        Field(
            "delivered_at",
            "datetime",
            default=lambda: datetime.datetime.now(datetime.timezone.utc),
            notnull=True,
        ),
        Field("success", "boolean", default=False, notnull=True),
        migrate=False,
    )

    # ==========================================
    # LEVEL 5: Tables with Level 4 dependencies
    # ==========================================

    # Secret Access Log table (depends on: secrets, identities) - Phase 2: Secrets Management
    db.define_table(
        "secret_access_log",
        Field("secret_id", "reference secrets", notnull=True, ondelete="CASCADE"),
        Field("identity_id", "reference identities", notnull=True, ondelete="CASCADE"),
        Field(
            "action",
            "string",
            length=50,
            notnull=True,
            requires=IS_IN_SET(
                ["view_masked", "view_unmasked", "create", "update", "delete"]
            ),
        ),
        Field(
            "masked", "boolean", default=True, notnull=True
        ),  # Was secret masked when viewed?
        Field(
            "accessed_at",
            "datetime",
            default=lambda: datetime.datetime.now(datetime.timezone.utc),
            notnull=True,
        ),
        migrate=False,
    )

    # Key Access Log table (depends on: keys, identities) - Phase 3: Keys Management
    db.define_table(
        "key_access_log",
        Field("key_id", "reference crypto_keys", notnull=True, ondelete="CASCADE"),
        Field("identity_id", "reference identities", ondelete="CASCADE"),
        Field(
            "action",
            "string",
            length=50,
            requires=IS_IN_SET(
                ["view", "create", "update", "delete", "encrypt", "decrypt", "sign"]
            ),
        ),
        Field(
            "operation",
            "string",
            length=50,
        ),  # Operation performed (encrypt, decrypt, sign, verify, etc.)
        Field("metadata_json", "json"),  # Additional metadata about the access
        Field(
            "accessed_at",
            "datetime",
            default=lambda: datetime.datetime.now(datetime.timezone.utc),
            notnull=True,
        ),
        migrate=False,
    )

    # ==========================================
    # v2.3.0: Software, Services, and IPAM tables
    # ==========================================

    # Software table (depends on: organizations, identities) - v2.3.0: Software tracking
    db.define_table(
        "software",
        Field(
            "tenant_id",
            "reference tenants",
            default=1,
            notnull=True,
            ondelete="CASCADE",
        ),
        Field("name", "string", length=255, notnull=True, requires=IS_NOT_EMPTY()),
        Field("description", "text"),
        Field(
            "organization_id",
            "reference organizations",
            notnull=True,
            ondelete="CASCADE",
        ),
        Field(
            "purchasing_poc_id", "reference identities", ondelete="SET NULL"
        ),  # Who bought it
        Field(
            "license_url",
            "string",
            length=1024,
            default="https://www.gnu.org/licenses/agpl-3.0.html",
        ),  # License or contract link
        Field("version", "string", length=100),
        Field("business_purpose", "text"),  # Why we use this software
        Field(
            "software_type",
            "string",
            length=50,
            notnull=True,
            requires=IS_IN_SET(
                [
                    "saas",
                    "paas",
                    "iaas",
                    "productivity",
                    "software",
                    "administrative",
                    "security",
                    "development",
                    "monitoring",
                    "database",
                    "communication",
                    "other",
                ]
            ),
        ),
        Field(
            "seats", "integer", requires=IS_INT_IN_RANGE(0, None)
        ),  # Number of seats/licenses
        Field("cost_monthly", "decimal(10,2)"),  # Monthly cost
        Field("renewal_date", "date"),  # When license renews
        Field("vendor", "string", length=255),  # Software vendor
        Field("support_contact", "string", length=255),  # Vendor support contact
        Field("notes", "text"),
        Field("tags", "list:string"),
        Field("is_active", "boolean", default=True, notnull=True),
        Field(
            "created_at",
            "datetime",
            default=lambda: datetime.datetime.now(datetime.timezone.utc),
        ),
        Field(
            "updated_at",
            "datetime",
            update=lambda: datetime.datetime.now(datetime.timezone.utc),
        ),
        Field(
            "village_id", "string", length=32, unique=True, default=generate_village_id
        ),
        migrate=False,
    )

    # Services table (depends on: organizations, identities) - v2.3.0: Microservice tracking
    db.define_table(
        "services",
        Field(
            "tenant_id",
            "reference tenants",
            default=1,
            notnull=True,
            ondelete="CASCADE",
        ),
        Field("name", "string", length=255, notnull=True, requires=IS_NOT_EMPTY()),
        Field("description", "text"),
        Field(
            "organization_id",
            "reference organizations",
            notnull=True,
            ondelete="CASCADE",
        ),
        Field("domains", "list:string"),  # Domain associations (app.example.com, etc.)
        Field("paths", "list:string"),  # API paths (/api/v1/users, etc.)
        Field(
            "poc_identity_id", "reference identities", ondelete="SET NULL"
        ),  # Point of contact
        Field(
            "language",
            "string",
            length=50,
            requires=IS_EMPTY_OR(
                IS_IN_SET(
                    [
                        "rust",
                        "go",
                        "python",
                        "python2",
                        "python3",
                        "nodejs",
                        "typescript",
                        "react",
                        "vue",
                        "angular",
                        "java",
                        "kotlin",
                        "scala",
                        "c",
                        "cpp",
                        "csharp",
                        "php",
                        "ruby",
                        "swift",
                        "elixir",
                        "haskell",
                        "other",
                    ]
                )
            ),
        ),
        Field(
            "deployment_method",
            "string",
            length=50,
            requires=IS_EMPTY_OR(
                IS_IN_SET(
                    [
                        "serverless",
                        "kubernetes",
                        "docker",
                        "docker_compose",
                        "os_local",
                        "function",
                        "vm",
                        "bare_metal",
                        "other",
                    ]
                )
            ),
        ),
        Field(
            "deployment_type", "string", length=100
        ),  # e.g., "blue-green", "canary", "rolling"
        Field("is_public", "boolean", default=False, notnull=True),
        Field("port", "integer"),  # Service port
        Field("health_endpoint", "string", length=255),  # e.g., /healthz
        Field("repository_url", "string", length=1024),  # Git repo URL
        Field("documentation_url", "string", length=1024),  # Docs URL
        Field("sla_uptime", "decimal(5,2)"),  # SLA uptime percentage (e.g., 99.99)
        Field("sla_response_time_ms", "integer"),  # SLA response time in ms
        Field("notes", "text"),
        Field("tags", "list:string"),
        Field(
            "status",
            "string",
            length=50,
            default="active",
            requires=IS_IN_SET(["active", "deprecated", "maintenance", "inactive"]),
        ),
        Field(
            "created_at",
            "datetime",
            default=lambda: datetime.datetime.now(datetime.timezone.utc),
        ),
        Field(
            "updated_at",
            "datetime",
            update=lambda: datetime.datetime.now(datetime.timezone.utc),
        ),
        Field(
            "village_id", "string", length=32, unique=True, default=generate_village_id
        ),
        migrate=False,
    )

    # IPAM Prefixes table (depends on: organizations) - v2.3.0: IP Address Management
    db.define_table(
        "ipam_prefixes",
        Field(
            "tenant_id",
            "reference tenants",
            default=1,
            notnull=True,
            ondelete="CASCADE",
        ),
        Field(
            "prefix", "string", length=50, notnull=True, requires=IS_NOT_EMPTY()
        ),  # CIDR notation
        Field("description", "text"),
        Field(
            "organization_id",
            "reference organizations",
            notnull=True,
            ondelete="CASCADE",
        ),
        Field(
            "parent_id", "reference ipam_prefixes", ondelete="CASCADE"
        ),  # Hierarchical CIDR
        Field("vlan_id", "integer"),
        Field("vrf", "string", length=100),  # VRF name
        Field(
            "status",
            "string",
            length=50,
            default="active",
            requires=IS_IN_SET(["active", "reserved", "deprecated", "container"]),
        ),
        Field(
            "role", "string", length=100
        ),  # e.g., "production", "development", "management"
        Field(
            "is_pool", "boolean", default=False, notnull=True
        ),  # Is this a pool for allocation?
        Field("site", "string", length=255),  # Physical site/location
        Field("region", "string", length=100),  # Cloud region
        Field("tags", "list:string"),
        Field(
            "created_at",
            "datetime",
            default=lambda: datetime.datetime.now(datetime.timezone.utc),
        ),
        Field(
            "updated_at",
            "datetime",
            update=lambda: datetime.datetime.now(datetime.timezone.utc),
        ),
        Field(
            "village_id", "string", length=32, unique=True, default=generate_village_id
        ),
        migrate=False,
    )

    # IPAM IP Addresses table (depends on: ipam_prefixes) - v2.3.0: Individual IP tracking
    db.define_table(
        "ipam_addresses",
        Field(
            "tenant_id",
            "reference tenants",
            default=1,
            notnull=True,
            ondelete="CASCADE",
        ),
        Field(
            "address", "string", length=50, notnull=True, requires=IS_NOT_EMPTY()
        ),  # IP address
        Field("prefix_id", "reference ipam_prefixes", notnull=True, ondelete="CASCADE"),
        Field("dns_name", "string", length=255),  # FQDN
        Field("description", "text"),
        Field(
            "status",
            "string",
            length=50,
            default="active",
            requires=IS_IN_SET(["active", "reserved", "deprecated", "dhcp", "slaac"]),
        ),
        Field("assigned_object_type", "string", length=50),  # entity, service, etc.
        Field("assigned_object_id", "integer"),  # ID of assigned object
        Field(
            "nat_inside_id", "reference ipam_addresses", ondelete="SET NULL"
        ),  # NAT mapping
        Field("tags", "list:string"),
        Field(
            "created_at",
            "datetime",
            default=lambda: datetime.datetime.now(datetime.timezone.utc),
        ),
        Field(
            "updated_at",
            "datetime",
            update=lambda: datetime.datetime.now(datetime.timezone.utc),
        ),
        Field(
            "village_id", "string", length=32, unique=True, default=generate_village_id
        ),
        migrate=False,
    )

    # IPAM VLANs table (depends on: organizations) - v2.3.0: VLAN management
    db.define_table(
        "ipam_vlans",
        Field(
            "tenant_id",
            "reference tenants",
            default=1,
            notnull=True,
            ondelete="CASCADE",
        ),
        Field(
            "vid", "integer", notnull=True, requires=IS_INT_IN_RANGE(1, 4095)
        ),  # VLAN ID
        Field("name", "string", length=255, notnull=True, requires=IS_NOT_EMPTY()),
        Field("description", "text"),
        Field(
            "organization_id",
            "reference organizations",
            notnull=True,
            ondelete="CASCADE",
        ),
        Field(
            "status",
            "string",
            length=50,
            default="active",
            requires=IS_IN_SET(["active", "reserved", "deprecated"]),
        ),
        Field("role", "string", length=100),
        Field("site", "string", length=255),
        Field("tags", "list:string"),
        Field(
            "created_at",
            "datetime",
            default=lambda: datetime.datetime.now(datetime.timezone.utc),
        ),
        Field(
            "updated_at",
            "datetime",
            update=lambda: datetime.datetime.now(datetime.timezone.utc),
        ),
        Field(
            "village_id", "string", length=32, unique=True, default=generate_village_id
        ),
        migrate=False,
    )

    # Certificates table (depends on: tenants, organizations, identities, builtin_secrets) - v2.4.0
    db.define_table(
        "certificates",
        Field(
            "tenant_id",
            "reference tenants",
            default=1,
            notnull=True,
            ondelete="CASCADE",
        ),
        Field("name", "string", length=255, notnull=True, requires=IS_NOT_EMPTY()),
        Field("description", "text"),
        Field(
            "organization_id",
            "reference organizations",
            notnull=True,
            ondelete="CASCADE",
        ),
        # Certificate details
        Field(
            "creator",
            "string",
            length=100,
            notnull=True,
            requires=IS_IN_SET(
                [
                    "digicert",
                    "letsencrypt",
                    "self_signed",
                    "sectigo",
                    "globalsign",
                    "godaddy",
                    "entrust",
                    "certbot",
                    "acme",
                    "comodo",
                    "thawte",
                    "geotrust",
                    "rapidssl",
                    "internal_ca",
                    "cert_manager",
                    "other",
                ]
            ),
        ),
        Field(
            "cert_type",
            "string",
            length=50,
            notnull=True,
            requires=IS_IN_SET(
                [
                    "ca_root",
                    "ca_intermediate",
                    "server_cert",
                    "client_cert",
                    "code_signing",
                    "wildcard",
                    "san",
                    "ecc",
                    "rsa",
                    "email",
                    "other",
                ]
            ),
        ),
        # Subject information
        Field("common_name", "string", length=255),  # CN
        Field("subject_alternative_names", "list:string"),  # SAN entries
        Field("organization_unit", "string", length=255),  # OU
        Field("locality", "string", length=100),  # L
        Field("state_province", "string", length=100),  # ST
        Field("country", "string", length=2),  # C (ISO 3166-1 alpha-2)
        # Issuer information
        Field("issuer_common_name", "string", length=255),
        Field("issuer_organization", "string", length=255),
        # Key information
        Field("key_algorithm", "string", length=50),  # RSA, ECDSA, DSA, Ed25519
        Field("key_size", "integer"),  # 2048, 4096, 256, etc.
        Field("signature_algorithm", "string", length=100),
        # Dates
        Field("issue_date", "date", notnull=True),
        Field("expiration_date", "date", notnull=True),
        Field("not_before", "datetime"),
        Field("not_after", "datetime"),
        # Certificate content
        Field("certificate_pem", "text"),
        Field("certificate_fingerprint_sha1", "string", length=64),
        Field("certificate_fingerprint_sha256", "string", length=64),
        Field("serial_number", "string", length=255),
        # Key storage reference
        Field(
            "private_key_secret_id", "reference builtin_secrets", ondelete="SET NULL"
        ),
        # Usage tracking
        Field("entities_using", "json"),
        Field("services_using", "list:integer"),
        # File/location tracking
        Field("file_path", "string", length=1024),
        Field("vault_path", "string", length=512),
        # Renewal information
        Field("auto_renew", "boolean", default=False, notnull=True),
        Field("renewal_days_before", "integer", default=30),
        Field("last_renewed_at", "datetime"),
        Field(
            "renewal_method", "string", length=50
        ),  # acme_http, acme_dns, manual, api
        # ACME/Let's Encrypt specific
        Field("acme_account_url", "string", length=512),
        Field("acme_order_url", "string", length=512),
        Field(
            "acme_challenge_type", "string", length=50
        ),  # http-01, dns-01, tls-alpn-01
        # Revocation information
        Field("is_revoked", "boolean", default=False, notnull=True),
        Field("revoked_at", "datetime"),
        Field("revocation_reason", "string", length=100),
        # Validation and compliance
        Field("validation_type", "string", length=50),  # DV, OV, EV
        Field("ct_log_status", "string", length=50),  # logged, pending, not_required
        Field("ocsp_must_staple", "boolean", default=False),
        # Cost tracking
        Field("cost_annual", "decimal(10,2)"),
        Field("purchase_date", "date"),
        Field("vendor", "string", length=255),
        # Metadata
        Field("notes", "text"),
        Field("tags", "list:string"),
        Field("custom_metadata", "json"),
        # Status
        Field(
            "status",
            "string",
            length=50,
            default="active",
            notnull=True,
            requires=IS_IN_SET(
                ["active", "expiring_soon", "expired", "revoked", "pending", "archived"]
            ),
        ),
        Field("is_active", "boolean", default=True, notnull=True),
        # Audit fields
        Field(
            "created_at",
            "datetime",
            default=lambda: datetime.datetime.now(datetime.timezone.utc),
        ),
        Field(
            "updated_at",
            "datetime",
            update=lambda: datetime.datetime.now(datetime.timezone.utc),
        ),
        Field("created_by_id", "reference identities", ondelete="SET NULL"),
        Field("updated_by_id", "reference identities", ondelete="SET NULL"),
        Field(
            "village_id", "string", length=32, unique=True, default=generate_village_id
        ),
        migrate=False,
    )

    # Data Stores table (depends on: tenants, organizations, identities, crypto_keys) - v2.4.0: Data inventory management
    db.define_table(
        "data_stores",
        Field(
            "tenant_id",
            "reference tenants",
            default=1,
            notnull=True,
            ondelete="CASCADE",
        ),
        Field("organization_id", "reference organizations", ondelete="CASCADE"),
        Field(
            "village_id", "string", length=32, unique=True, default=generate_village_id
        ),
        Field("name", "string", length=255, notnull=True, requires=IS_NOT_EMPTY()),
        Field("description", "text"),
        Field(
            "storage_type",
            "string",
            length=50,
            default="other",
            requires=IS_IN_SET(
                [
                    "s3",
                    "gcs",
                    "azure_blob",
                    "disk",
                    "nas",
                    "san",
                    "database",
                    "data_lake",
                    "hdfs",
                    "other",
                ]
            ),
        ),
        Field(
            "storage_provider", "string", length=100
        ),  # AWS, GCP, Azure, On-Premise, Wasabi, Backblaze, MinIO
        Field("location_region", "string", length=50),  # us-west-1, eu-west-1, etc.
        Field("location_physical", "string", length=255),  # Dallas, TX; Frankfurt, DE
        Field(
            "data_classification",
            "string",
            length=20,
            default="internal",
            requires=IS_IN_SET(["public", "internal", "confidential", "restricted"]),
        ),
        Field("encryption_at_rest", "boolean", default=False),
        Field("encryption_in_transit", "boolean", default=False),
        Field("encryption_key_id", "reference crypto_keys", ondelete="SET NULL"),
        Field("retention_days", "integer"),  # nullable
        Field("backup_enabled", "boolean", default=False),
        Field(
            "backup_frequency",
            "string",
            length=50,
            requires=IS_EMPTY_OR(IS_IN_SET(["daily", "hourly", "weekly"])),
        ),
        Field(
            "access_control_type",
            "string",
            length=20,
            default="private",
            requires=IS_IN_SET(["iam", "acl", "rbac", "public", "private"]),
        ),
        Field("poc_identity_id", "reference identities", ondelete="SET NULL"),
        Field(
            "compliance_frameworks", "json"
        ),  # array like ["SOC2", "HIPAA", "GDPR", "PCI-DSS"]
        Field("contains_pii", "boolean", default=False),
        Field("contains_phi", "boolean", default=False),
        Field("contains_pci", "boolean", default=False),
        Field("size_bytes", "bigint"),
        Field("last_access_audit", "datetime"),
        Field("metadata", "json"),
        Field(
            "created_at",
            "datetime",
            default=lambda: datetime.datetime.now(datetime.timezone.utc),
        ),
        Field(
            "updated_at",
            "datetime",
            update=lambda: datetime.datetime.now(datetime.timezone.utc),
        ),
        Field("created_by", "reference portal_users", ondelete="SET NULL"),
        Field("is_active", "boolean", default=True),
        migrate=False,
    )

    # Data Store Labels table (depends on: data_stores, labels) - v2.4.0: Labeling for data stores
    db.define_table(
        "data_store_labels",
        Field(
            "data_store_id", "reference data_stores", notnull=True, ondelete="CASCADE"
        ),
        Field("label_id", "reference labels", notnull=True, ondelete="CASCADE"),
        Field(
            "created_at",
            "datetime",
            default=lambda: datetime.datetime.now(datetime.timezone.utc),
        ),
        migrate=False,
    )

    # SBOM Components table (depends on: tenants) - v3.0.0: Software Bill of Materials component tracking
    db.define_table(
        "sbom_components",
        Field(
            "tenant_id",
            "reference tenants",
            default=1,
            notnull=True,
            ondelete="CASCADE",
        ),
        Field(
            "parent_type",
            "string",
            length=50,
            notnull=True,
            requires=IS_IN_SET(["service", "software", "sbom_component"]),
        ),  # Polymorphic reference
        Field("parent_id", "integer", notnull=True),  # ID of parent resource
        Field("name", "string", length=255, notnull=True, requires=IS_NOT_EMPTY()),
        Field("version", "string", length=100),
        Field("purl", "string", length=512),  # Package URL (standardized identifier)
        Field(
            "package_type",
            "string",
            length=50,
            notnull=True,
            requires=IS_IN_SET(
                ["pypi", "npm", "go", "maven", "nuget", "cargo", "gem", "other"]
            ),
        ),
        Field(
            "scope",
            "string",
            length=20,
            default="runtime",
            requires=IS_IN_SET(["runtime", "dev", "optional", "test"]),
        ),
        Field("direct", "boolean", default=True, notnull=True),  # Direct vs transitive
        Field("license_id", "string", length=100),  # SPDX identifier
        Field("license_name", "string", length=255),
        Field("license_url", "string", length=1024),
        Field("source_file", "string", length=255),  # File it was parsed from
        Field("repository_url", "string", length=1024),
        Field("homepage_url", "string", length=1024),
        Field("description", "text"),
        Field("hash_sha256", "string", length=64),
        Field("hash_sha512", "string", length=128),
        Field("metadata", "json"),  # Additional package metadata
        Field("is_active", "boolean", default=True, notnull=True),
        Field(
            "created_at",
            "datetime",
            default=lambda: datetime.datetime.now(datetime.timezone.utc),
        ),
        Field(
            "updated_at",
            "datetime",
            update=lambda: datetime.datetime.now(datetime.timezone.utc),
        ),
        Field(
            "village_id", "string", length=32, unique=True, default=generate_village_id
        ),
        migrate=False,
    )

    # SBOM Scans table (depends on: tenants) - v3.0.0: SBOM scan job tracking
    db.define_table(
        "sbom_scans",
        Field(
            "tenant_id",
            "reference tenants",
            default=1,
            notnull=True,
            ondelete="CASCADE",
        ),
        Field(
            "parent_type",
            "string",
            length=50,
            notnull=True,
            requires=IS_IN_SET(["service", "software"]),
        ),
        Field("parent_id", "integer", notnull=True),
        Field(
            "scan_type",
            "string",
            length=50,
            notnull=True,
            requires=IS_IN_SET(
                ["git_clone", "git_api", "manual_upload", "sbom_import"]
            ),
        ),
        Field(
            "status",
            "string",
            length=20,
            default="pending",
            notnull=True,
            requires=IS_IN_SET(["pending", "running", "completed", "failed"]),
        ),
        Field("repository_url", "string", length=1024),
        Field("repository_branch", "string", length=255, default="main"),
        # v3.x.x: Private repository authentication
        Field(
            "credential_type",
            "string",
            length=50,
            requires=IS_EMPTY_OR(IS_IN_SET(["builtin_secret", "static", "none"])),
        ),
        Field("credential_id", "reference builtin_secrets"),
        Field("credential_mapping", "json"),  # Maps secret fields to config
        Field("commit_hash", "string", length=64),
        Field("files_scanned", "json"),  # List of dependency files found
        Field("components_found", "integer", default=0),
        Field("components_added", "integer", default=0),
        Field("components_updated", "integer", default=0),
        Field("components_removed", "integer", default=0),
        Field("error_message", "text"),
        Field("scan_duration_ms", "integer"),
        Field("started_at", "datetime"),
        Field("completed_at", "datetime"),
        Field(
            "created_at",
            "datetime",
            default=lambda: datetime.datetime.now(datetime.timezone.utc),
        ),
        Field(
            "village_id", "string", length=32, unique=True, default=generate_village_id
        ),
        migrate=False,
    )

    # Vulnerabilities table (depends on: tenants) - v3.0.0: CVE/vulnerability database
    db.define_table(
        "vulnerabilities",
        Field(
            "tenant_id",
            "reference tenants",
            default=1,
            notnull=True,
            ondelete="CASCADE",
        ),
        Field("cve_id", "string", length=50, unique=True, notnull=True),
        Field("aliases", "list:string"),  # GHSA-xxxx, etc.
        Field(
            "severity",
            "string",
            length=20,
            default="unknown",
            notnull=True,
            requires=IS_IN_SET(["critical", "high", "medium", "low", "unknown"]),
        ),
        Field("cvss_score", "decimal(3,1)"),  # 0.0-10.0
        Field("cvss_vector", "string", length=100),
        Field("title", "string", length=512),
        Field("description", "text"),
        Field("affected_packages", "json"),  # [{purl_pattern, version_range}]
        Field("fixed_versions", "json"),  # {purl: [fixed_versions]}
        Field("references", "list:string"),  # Advisory URLs
        Field("published_at", "datetime"),
        Field("modified_at", "datetime"),
        Field(
            "source",
            "string",
            length=50,
            default="manual",
            requires=IS_IN_SET(["nvd", "osv", "github_advisory", "manual"]),
        ),
        Field("is_active", "boolean", default=True, notnull=True),
        Field("nvd_last_sync", "datetime"),  # v3.x: Track when NVD data was last synced
        Field(
            "created_at",
            "datetime",
            default=lambda: datetime.datetime.now(datetime.timezone.utc),
        ),
        Field(
            "updated_at",
            "datetime",
            update=lambda: datetime.datetime.now(datetime.timezone.utc),
        ),
        Field(
            "village_id", "string", length=32, unique=True, default=generate_village_id
        ),
        migrate=False,
    )

    # Component Vulnerabilities table (depends on: sbom_components, vulnerabilities, identities) - v3.0.0
    db.define_table(
        "component_vulnerabilities",
        Field(
            "tenant_id",
            "reference tenants",
            default=1,
            notnull=True,
            ondelete="CASCADE",
        ),
        Field(
            "component_id",
            "reference sbom_components",
            notnull=True,
            ondelete="CASCADE",
        ),
        Field(
            "vulnerability_id",
            "reference vulnerabilities",
            notnull=True,
            ondelete="CASCADE",
        ),
        Field(
            "status",
            "string",
            length=20,
            default="open",
            notnull=True,
            requires=IS_IN_SET(
                ["open", "investigating", "false_positive", "remediated", "accepted"]
            ),
        ),
        Field("remediation_notes", "text"),
        Field("remediated_at", "datetime"),
        Field("remediated_by_id", "reference identities", ondelete="SET NULL"),
        Field(
            "created_at",
            "datetime",
            default=lambda: datetime.datetime.now(datetime.timezone.utc),
        ),
        Field(
            "updated_at",
            "datetime",
            update=lambda: datetime.datetime.now(datetime.timezone.utc),
        ),
        migrate=False,
    )

    # License Policies table (depends on: tenants, organizations) - v3.0.0: License compliance rules
    db.define_table(
        "license_policies",
        Field(
            "tenant_id",
            "reference tenants",
            default=1,
            notnull=True,
            ondelete="CASCADE",
        ),
        Field(
            "organization_id",
            "reference organizations",
            notnull=True,
            ondelete="CASCADE",
        ),
        Field("name", "string", length=255, notnull=True, requires=IS_NOT_EMPTY()),
        Field("description", "text"),
        Field("allowed_licenses", "list:string"),  # SPDX IDs
        Field("denied_licenses", "list:string"),  # SPDX IDs
        Field(
            "action",
            "string",
            length=10,
            default="warn",
            requires=IS_IN_SET(["warn", "block"]),
        ),
        Field("is_active", "boolean", default=True, notnull=True),
        Field(
            "created_at",
            "datetime",
            default=lambda: datetime.datetime.now(datetime.timezone.utc),
        ),
        Field(
            "updated_at",
            "datetime",
            update=lambda: datetime.datetime.now(datetime.timezone.utc),
        ),
        Field(
            "village_id", "string", length=32, unique=True, default=generate_village_id
        ),
        # v3.x.x: Private repository authentication
        Field(
            "credential_type",
            "string",
            length=50,
            requires=IS_EMPTY_OR(IS_IN_SET(["builtin_secret", "static", "none"])),
        ),
        Field("credential_id", "integer"),
        Field("credential_mapping", "json"),
        migrate=False,
    )

    # SBOM Scan Schedules table (depends on: tenants) - v3.0.0: Periodic scan configuration
    db.define_table(
        "sbom_scan_schedules",
        Field(
            "tenant_id",
            "reference tenants",
            default=1,
            notnull=True,
            ondelete="CASCADE",
        ),
        Field(
            "parent_type",
            "string",
            length=50,
            notnull=True,
            requires=IS_IN_SET(["service", "software"]),
        ),
        Field("parent_id", "integer", notnull=True),
        Field("schedule_cron", "string", length=100, notnull=True),  # Cron expression
        Field("is_active", "boolean", default=True, notnull=True),
        Field("last_run_at", "datetime"),
        Field("next_run_at", "datetime"),
        Field(
            "created_at",
            "datetime",
            default=lambda: datetime.datetime.now(datetime.timezone.utc),
        ),
        Field(
            "updated_at",
            "datetime",
            update=lambda: datetime.datetime.now(datetime.timezone.utc),
        ),
        Field(
            "village_id", "string", length=32, unique=True, default=generate_village_id
        ),
        # v3.x.x: Private repository authentication
        Field(
            "credential_type",
            "string",
            length=50,
            requires=IS_EMPTY_OR(IS_IN_SET(["builtin_secret", "static", "none"])),
        ),
        Field("credential_id", "integer"),
        Field("credential_mapping", "json"),
        migrate=False,
    )

    # ==========================================
    # ON-CALL ROTATION TABLES
    # ==========================================

    # 1. On-Call Rotations table (depends on: tenants, organizations, services) - v3.1.0: On-call rotation management
    db.define_table(
        "on_call_rotations",
        Field(
            "tenant_id",
            "reference tenants",
            default=1,
            notnull=True,
            ondelete="CASCADE",
        ),
        Field(
            "village_id", "string", length=32, unique=True, default=generate_village_id
        ),
        Field("name", "string", length=255, notnull=True, requires=IS_NOT_EMPTY()),
        Field("description", "text"),
        Field("is_active", "boolean", default=True, notnull=True),
        # Scope: organization OR service
        Field(
            "scope_type",
            "string",
            length=50,
            notnull=True,
            requires=IS_IN_SET(["organization", "service"]),
        ),
        Field("organization_id", "reference organizations", ondelete="CASCADE"),
        Field("service_id", "reference services", ondelete="CASCADE"),
        # Schedule configuration (conditional based on schedule_type)
        Field(
            "schedule_type",
            "string",
            length=50,
            notnull=True,
            requires=IS_IN_SET(["weekly", "cron", "manual", "follow_the_sun"]),
        ),
        Field("rotation_length_days", "integer"),  # Weekly: 7, 14, 21, etc.
        Field("rotation_start_date", "date"),  # Weekly: when rotation started
        Field("schedule_cron", "string", length=255),  # Cron: expression
        Field("handoff_timezone", "string", length=100),  # Follow-the-sun: timezone
        Field("shift_split", "boolean", default=False),  # Follow-the-sun: split shifts?
        Field("shift_config", "json"),  # Follow-the-sun: shift definitions
        Field(
            "created_at",
            "datetime",
            default=lambda: datetime.datetime.now(datetime.timezone.utc),
        ),
        Field(
            "updated_at",
            "datetime",
            update=lambda: datetime.datetime.now(datetime.timezone.utc),
        ),
        migrate=False,
    )

    # 2. On-Call Rotation Participants table (depends on: on_call_rotations, identities) - v3.1.0: People in rotation
    db.define_table(
        "on_call_rotation_participants",
        Field(
            "rotation_id",
            "reference on_call_rotations",
            notnull=True,
            ondelete="CASCADE",
        ),
        Field("identity_id", "reference identities", notnull=True, ondelete="CASCADE"),
        Field(
            "order_index", "integer", notnull=True
        ),  # Position in rotation (0, 1, 2, ...)
        Field("is_active", "boolean", default=True, notnull=True),
        Field("start_date", "date"),  # Optional: when this person joined rotation
        Field("end_date", "date"),  # Optional: when they leave
        # Notification preferences
        Field("notification_email", "string", length=255),
        Field("notification_phone", "string", length=50),
        Field("notification_slack", "string", length=255),
        Field(
            "created_at",
            "datetime",
            default=lambda: datetime.datetime.now(datetime.timezone.utc),
        ),
        Field(
            "updated_at",
            "datetime",
            update=lambda: datetime.datetime.now(datetime.timezone.utc),
        ),
        migrate=False,
    )

    # 3. On-Call Escalation Policies table (depends on: on_call_rotations, identities, identity_groups) - v3.1.0: Backup contacts and escalation rules
    db.define_table(
        "on_call_escalation_policies",
        Field(
            "rotation_id",
            "reference on_call_rotations",
            notnull=True,
            ondelete="CASCADE",
        ),
        Field(
            "level", "integer", notnull=True, default=1
        ),  # 1=primary, 2=first backup, 3=second backup
        # Target: identity OR group OR rotation participant
        Field(
            "escalation_type",
            "string",
            length=50,
            notnull=True,
            requires=IS_IN_SET(["identity", "group", "rotation_participant"]),
        ),
        Field("identity_id", "reference identities", ondelete="CASCADE"),
        Field("group_id", "reference identity_groups", ondelete="CASCADE"),
        Field(
            "escalation_delay_minutes", "integer", default=15
        ),  # Wait before escalating
        Field("notification_channels", "list:string"),  # ["email", "sms", "slack"]
        Field(
            "created_at",
            "datetime",
            default=lambda: datetime.datetime.now(datetime.timezone.utc),
        ),
        Field(
            "updated_at",
            "datetime",
            update=lambda: datetime.datetime.now(datetime.timezone.utc),
        ),
        migrate=False,
    )

    # 4. On-Call Overrides table (depends on: on_call_rotations, identities) - v3.1.0: Temporary substitutions
    db.define_table(
        "on_call_overrides",
        Field(
            "rotation_id",
            "reference on_call_rotations",
            notnull=True,
            ondelete="CASCADE",
        ),
        Field(
            "original_identity_id",
            "reference identities",
            notnull=True,
            ondelete="CASCADE",
        ),
        Field(
            "override_identity_id",
            "reference identities",
            notnull=True,
            ondelete="CASCADE",
        ),
        Field("start_datetime", "datetime", notnull=True),
        Field("end_datetime", "datetime", notnull=True),
        Field(
            "reason", "string", length=512
        ),  # "Vacation", "Sick leave", "Traffic delay"
        Field("created_by_id", "reference identities", ondelete="SET NULL"),
        Field(
            "created_at",
            "datetime",
            default=lambda: datetime.datetime.now(datetime.timezone.utc),
        ),
        migrate=False,
    )

    # 5. On-Call Shifts table (depends on: on_call_rotations, identities, on_call_overrides) - v3.1.0: Historical record of who was on-call
    db.define_table(
        "on_call_shifts",
        Field(
            "rotation_id",
            "reference on_call_rotations",
            notnull=True,
            ondelete="CASCADE",
        ),
        Field("identity_id", "reference identities", notnull=True, ondelete="CASCADE"),
        Field("shift_start", "datetime", notnull=True),
        Field("shift_end", "datetime", notnull=True),
        Field("is_override", "boolean", default=False, notnull=True),
        Field("override_id", "reference on_call_overrides", ondelete="SET NULL"),
        # Metrics
        Field("alerts_received", "integer", default=0),
        Field("incidents_created", "integer", default=0),
        Field(
            "created_at",
            "datetime",
            default=lambda: datetime.datetime.now(datetime.timezone.utc),
        ),
        migrate=False,
    )

    # 6. On-Call Notifications table (depends on: on_call_rotations, identities) - v3.1.0: Notification audit trail
    db.define_table(
        "on_call_notifications",
        Field(
            "rotation_id",
            "reference on_call_rotations",
            ondelete="CASCADE",
        ),
        Field("identity_id", "reference identities", ondelete="CASCADE"),
        Field(
            "notification_type",
            "string",
            length=50,
            notnull=True,
            requires=IS_IN_SET(
                ["shift_start", "shift_reminder", "alert", "escalation"]
            ),
        ),
        Field(
            "channel",
            "string",
            length=50,
            notnull=True,
            requires=IS_IN_SET(["email", "sms", "slack", "webhook"]),
        ),
        Field("subject", "string", length=512),
        Field("message", "text"),
        Field("metadata", "json"),  # Alert details, etc.
        Field(
            "status",
            "string",
            length=50,
            default="pending",
            requires=IS_IN_SET(["pending", "sent", "delivered", "failed"]),
        ),
        Field("error_message", "text"),
        Field("sent_at", "datetime"),
        Field(
            "created_at",
            "datetime",
            default=lambda: datetime.datetime.now(datetime.timezone.utc),
        ),
        migrate=False,
    )

    # ==========================================
    # COST TRACKING TABLES - v3.1.0
    # ==========================================

    # Resource costs table - tracks costs per resource across all domain tables
    db.define_table(
        "resource_costs",
        Field("resource_type", "string", length=50, notnull=True, requires=IS_IN_SET(
            ["entity", "service", "data_store", "networking_resource", "certificate"]
        )),
        Field("resource_id", "integer", notnull=True),
        Field(
            "organization_id",
            "reference organizations",
            notnull=True,
            ondelete="CASCADE",
        ),
        Field("cost_to_date", "decimal(12,2)", default=0),
        Field("cost_ytd", "decimal(12,2)", default=0),
        Field("cost_mtd", "decimal(12,2)", default=0),
        Field("estimated_monthly_cost", "decimal(12,2)"),
        Field("currency", "string", length=3, default="USD"),
        Field("cost_provider", "string", length=50, requires=IS_IN_SET(
            ["aws_cost_explorer", "gcp_billing", "azure_cost", "manual"]
        )),
        Field("recommendations", "json"),
        Field(
            "created_by_identity_id",
            "reference identities",
            ondelete="SET NULL",
        ),
        Field("resource_created_at", "datetime"),
        Field("last_synced_at", "datetime"),
        Field(
            "created_at",
            "datetime",
            default=lambda: datetime.datetime.now(datetime.timezone.utc),
        ),
        Field(
            "updated_at",
            "datetime",
            default=lambda: datetime.datetime.now(datetime.timezone.utc),
            update=lambda: datetime.datetime.now(datetime.timezone.utc),
        ),
        migrate=False,
    )

    # Cost history - daily cost snapshots for trending
    db.define_table(
        "cost_history",
        Field(
            "resource_cost_id",
            "reference resource_costs",
            notnull=True,
            ondelete="CASCADE",
        ),
        Field("snapshot_date", "date", notnull=True),
        Field("cost_amount", "decimal(12,2)", notnull=True),
        Field("usage_quantity", "decimal(12,4)"),
        Field("usage_unit", "string", length=50),
        Field(
            "created_at",
            "datetime",
            default=lambda: datetime.datetime.now(datetime.timezone.utc),
        ),
        migrate=False,
    )

    # Cost sync jobs - scheduled provider syncs
    db.define_table(
        "cost_sync_jobs",
        Field("name", "string", length=255, notnull=True, requires=IS_NOT_EMPTY()),
        Field("provider", "string", length=50, notnull=True, requires=IS_IN_SET(
            ["aws_cost_explorer", "gcp_billing", "azure_cost"]
        )),
        Field(
            "organization_id",
            "reference organizations",
            notnull=True,
            ondelete="CASCADE",
        ),
        Field("config_json", "json", notnull=True),
        Field("schedule_interval", "integer", default=86400),
        Field("enabled", "boolean", default=True),
        Field("last_run_at", "datetime"),
        Field("next_run_at", "datetime"),
        Field(
            "created_at",
            "datetime",
            default=lambda: datetime.datetime.now(datetime.timezone.utc),
        ),
        migrate=False,
    )
