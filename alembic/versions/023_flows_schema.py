"""Create flows module schema — 11 tables for CI/CD pipeline orchestration.

Revision ID: 023
Revises: 022
Create Date: 2026-07-10

Phase 4a: Author Flows module schema from IceFlows v1.1.x (CI/CD pipeline orchestration).
- iceflows: main pipeline/flow definitions (VillageIDMixin)
- iceflows_stages: ordered pipeline stages (dev, staging, prod, etc.)
- iceflows_stage_approvers: approval authority per stage
- iceflows_stage_tests: test execution configuration per stage
- iceflows_stage_calls: external service calls (IceStreams/IceRuns) per stage
- iceflows_stage_reviews: Darwin AI code review configuration per stage
- iceflows_credentials: Git provider credentials (GitHub/GitLab tokens)
- iceflows_promotions: promotion requests between stages
- iceflows_approvals: approval decisions for promotions
- iceflows_webhooks: GitHub/GitLab webhook triggers
- iceflows_executions: pipeline run records (reserved for future implementation)

Tenant ref: Integer FK to tenants.id.
User/actor refs: *_identity_id Integer FK to identities.id.
village_id: VillageIDMixin on iceflows (root pipeline).

Table creation order respects FK dependencies:
1. iceflows_credentials (FK to identities only)
2. iceflows (FK to iceflows_credentials, identities)
3. iceflows_stages (FK to iceflows)
4. iceflows_stage_approvers (FK to iceflows_stages, identities; group_id soft ref)
5. iceflows_stage_tests (FK to iceflows_stages)
6. iceflows_stage_calls (FK to iceflows_stages)
7. iceflows_stage_reviews (FK to iceflows_stages)
8. iceflows_webhooks (FK to iceflows)
9. iceflows_promotions (FK to iceflows, iceflows_stages, identities)
10. iceflows_approvals (FK to iceflows_promotions, identities)
11. iceflows_executions (FK to iceflows_promotions, iceflows, identities)
"""

import sqlalchemy as sa

from alembic import op

revision = "023"
down_revision = "022"
branch_labels = None
depends_on = None


def upgrade():
    """Create 11 Flows module tables with FKs, indexes, unique constraints."""

    # 1. iceflows_credentials — Git provider credential (FK to identities only)
    op.create_table(
        "iceflows_credentials",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("credential_id", sa.String(36), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("provider", sa.String(50), nullable=False),
        sa.Column("access_token", sa.Text(), nullable=False),
        sa.Column(
            "token_type",
            sa.String(50),
            nullable=False,
            server_default="personal",
            comment="personal, oauth, app",
        ),
        sa.Column("scopes", sa.JSON(), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="1"),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by_identity_id", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=True,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=True,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["created_by_identity_id"], ["identities.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_iceflows_credentials_tenant_id", "iceflows_credentials", ["tenant_id"]
    )

    # 2. iceflows — main pipeline/flow definition (root, VillageIDMixin)
    op.create_table(
        "iceflows",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("village_id", sa.String(32), nullable=True, unique=True),
        sa.Column("flow_id", sa.String(36), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("repository_url", sa.String(500), nullable=False),
        sa.Column("repository_provider", sa.String(50), nullable=False),
        sa.Column("repository_name", sa.String(255), nullable=True),
        sa.Column(
            "default_branch", sa.String(255), nullable=True, server_default="main"
        ),
        sa.Column("credential_id", sa.Integer(), nullable=True),
        sa.Column("gitops_enabled", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("gitops_repo_url", sa.String(500), nullable=True),
        sa.Column(
            "gitops_branch", sa.String(255), nullable=True, server_default="main"
        ),
        sa.Column("gitops_path", sa.String(500), nullable=True),
        sa.Column("webhook_secret", sa.String(64), nullable=True),
        sa.Column(
            "status",
            sa.String(50),
            nullable=False,
            server_default="draft",
            comment="draft, active, paused, archived",
        ),
        sa.Column("is_enabled", sa.Boolean(), nullable=False, server_default="1"),
        sa.Column("created_by_identity_id", sa.Integer(), nullable=False),
        sa.Column("tags", sa.JSON(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=True,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=True,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["credential_id"], ["iceflows_credentials.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["created_by_identity_id"], ["identities.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_iceflows_tenant_id", "iceflows", ["tenant_id"])

    # 3. iceflows_stages — pipeline stages
    op.create_table(
        "iceflows_stages",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("stage_id", sa.String(36), nullable=False),
        sa.Column("flow_id", sa.Integer(), nullable=False),
        sa.Column("stage_order", sa.Integer(), nullable=False),
        sa.Column("branch_name", sa.String(255), nullable=False),
        sa.Column("display_name", sa.String(255), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("is_production", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("auto_promote", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("require_approval", sa.Boolean(), nullable=False, server_default="1"),
        sa.Column("min_approvers", sa.Integer(), nullable=False, server_default="1"),
        sa.Column(
            "override_min_approvers", sa.Integer(), nullable=False, server_default="2"
        ),
        sa.Column("day_restrictions", sa.JSON(), nullable=True),
        sa.Column("time_restrictions", sa.JSON(), nullable=True),
        sa.Column("notification_config", sa.JSON(), nullable=True),
        sa.Column("is_enabled", sa.Boolean(), nullable=False, server_default="1"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=True,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=True,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["flow_id"], ["iceflows.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_iceflows_stages_flow_id", "iceflows_stages", ["flow_id"])

    # 4. iceflows_stage_approvers — approval authority per stage
    op.create_table(
        "iceflows_stage_approvers",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("approver_id", sa.String(36), nullable=False),
        sa.Column("stage_id", sa.Integer(), nullable=False),
        sa.Column("identity_id", sa.Integer(), nullable=True),
        sa.Column("group_id", sa.Integer(), nullable=True),
        sa.Column(
            "role",
            sa.String(50),
            nullable=False,
            server_default="approver",
            comment="approver, admin, reviewer",
        ),
        sa.Column("can_override", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=True,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=True,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["stage_id"], ["iceflows_stages.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["identity_id"], ["identities.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_iceflows_stage_approvers_stage_id", "iceflows_stage_approvers", ["stage_id"]
    )

    # 5. iceflows_stage_tests — test execution configuration per stage
    op.create_table(
        "iceflows_stage_tests",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("test_id", sa.String(36), nullable=False),
        sa.Column("stage_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("test_type", sa.String(50), nullable=False),
        sa.Column(
            "path_mode", sa.String(50), nullable=False, server_default="repo_relative"
        ),
        sa.Column("centralized_path", sa.String(500), nullable=True),
        sa.Column("repo_relative_path", sa.String(500), nullable=True),
        sa.Column("command", sa.Text(), nullable=True),
        sa.Column(
            "timeout_seconds", sa.Integer(), nullable=False, server_default="600"
        ),
        sa.Column("is_blocking", sa.Boolean(), nullable=False, server_default="1"),
        sa.Column("is_required", sa.Boolean(), nullable=False, server_default="1"),
        sa.Column("execution_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("env_vars", sa.JSON(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=True,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=True,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["stage_id"], ["iceflows_stages.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_iceflows_stage_tests_stage_id", "iceflows_stage_tests", ["stage_id"]
    )

    # 6. iceflows_stage_calls — external service calls per stage
    op.create_table(
        "iceflows_stage_calls",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("call_id", sa.String(36), nullable=False),
        sa.Column("stage_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("call_type", sa.String(50), nullable=False),
        sa.Column("target_id", sa.String(50), nullable=False),
        sa.Column(
            "trigger_on",
            sa.String(50),
            nullable=False,
            server_default="on_promotion",
            comment="pre_merge, post_merge, on_approval, on_promotion",
        ),
        sa.Column("input_template", sa.JSON(), nullable=True),
        sa.Column(
            "timeout_seconds", sa.Integer(), nullable=False, server_default="300"
        ),
        sa.Column("is_blocking", sa.Boolean(), nullable=False, server_default="1"),
        sa.Column("retry_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("execution_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=True,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=True,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["stage_id"], ["iceflows_stages.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_iceflows_stage_calls_stage_id", "iceflows_stage_calls", ["stage_id"]
    )

    # 7. iceflows_stage_reviews — Darwin AI review configuration per stage
    op.create_table(
        "iceflows_stage_reviews",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("review_id", sa.String(36), nullable=False),
        sa.Column("stage_id", sa.Integer(), nullable=False),
        sa.Column("is_required", sa.Boolean(), nullable=False, server_default="1"),
        sa.Column(
            "review_type",
            sa.String(50),
            nullable=False,
            server_default="inherit",
            comment="inherit, standard, security, performance, full",
        ),
        sa.Column("min_score", sa.Integer(), nullable=False, server_default="70"),
        sa.Column(
            "block_on_critical", sa.Boolean(), nullable=False, server_default="1"
        ),
        sa.Column("allowed_issue_types", sa.JSON(), nullable=True),
        sa.Column(
            "reviewers_notified", sa.Boolean(), nullable=False, server_default="1"
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=True,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=True,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["stage_id"], ["iceflows_stages.id"], ondelete="CASCADE"
        ),
        sa.UniqueConstraint("stage_id", name="uq_iceflows_stage_reviews_stage_id"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_iceflows_stage_reviews_stage_id", "iceflows_stage_reviews", ["stage_id"]
    )

    # 8. iceflows_webhooks — GitHub/GitLab webhook triggers
    op.create_table(
        "iceflows_webhooks",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("webhook_id", sa.String(36), nullable=False),
        sa.Column("flow_id", sa.Integer(), nullable=False),
        sa.Column("provider", sa.String(50), nullable=False),
        sa.Column("webhook_secret", sa.String(64), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="1"),
        sa.Column("last_triggered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=True,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=True,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["flow_id"], ["iceflows.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_iceflows_webhooks_flow_id", "iceflows_webhooks", ["flow_id"])

    # 9. iceflows_promotions — promotion requests between stages
    op.create_table(
        "iceflows_promotions",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("promotion_id", sa.String(36), nullable=False),
        sa.Column("flow_id", sa.Integer(), nullable=False),
        sa.Column("source_stage_id", sa.Integer(), nullable=False),
        sa.Column("target_stage_id", sa.Integer(), nullable=False),
        sa.Column("source_commit", sa.String(100), nullable=True),
        sa.Column(
            "status",
            sa.String(50),
            nullable=False,
            server_default="pending",
            comment="pending, approved, rejected, merged, cancelled",
        ),
        sa.Column("requested_by_identity_id", sa.Integer(), nullable=True),
        sa.Column("merged_by_identity_id", sa.Integer(), nullable=True),
        sa.Column("merged_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=True,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=True,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["flow_id"], ["iceflows.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["source_stage_id"], ["iceflows_stages.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["target_stage_id"], ["iceflows_stages.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["requested_by_identity_id"], ["identities.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["merged_by_identity_id"], ["identities.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_iceflows_promotions_flow_id", "iceflows_promotions", ["flow_id"]
    )
    op.create_index("ix_iceflows_promotions_status", "iceflows_promotions", ["status"])

    # 10. iceflows_approvals — approval decisions for promotions
    op.create_table(
        "iceflows_approvals",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("approval_id", sa.String(36), nullable=False),
        sa.Column("promotion_id", sa.Integer(), nullable=False),
        sa.Column("approver_identity_id", sa.Integer(), nullable=False),
        sa.Column("decision", sa.String(50), nullable=False),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column("can_override", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=True,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=True,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["promotion_id"], ["iceflows_promotions.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["approver_identity_id"], ["identities.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_iceflows_approvals_promotion_id", "iceflows_approvals", ["promotion_id"]
    )

    # 11. iceflows_executions — pipeline execution records
    op.create_table(
        "iceflows_executions",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("execution_id", sa.String(36), nullable=False),
        sa.Column("promotion_id", sa.Integer(), nullable=True),
        sa.Column("flow_id", sa.Integer(), nullable=False),
        sa.Column(
            "status",
            sa.String(50),
            nullable=False,
            server_default="pending",
            comment="pending, in_progress, success, failed, cancelled",
        ),
        sa.Column("started_by_identity_id", sa.Integer(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("duration_seconds", sa.Integer(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("execution_log", sa.JSON(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=True,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=True,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["promotion_id"], ["iceflows_promotions.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(["flow_id"], ["iceflows.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["started_by_identity_id"], ["identities.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_iceflows_executions_flow_id", "iceflows_executions", ["flow_id"]
    )
    op.create_index(
        "ix_iceflows_executions_promotion_id", "iceflows_executions", ["promotion_id"]
    )


def downgrade():
    """Drop 11 Flows module tables in reverse creation order."""
    op.drop_table("iceflows_executions")
    op.drop_table("iceflows_approvals")
    op.drop_table("iceflows_promotions")
    op.drop_table("iceflows_webhooks")
    op.drop_table("iceflows_stage_reviews")
    op.drop_table("iceflows_stage_calls")
    op.drop_table("iceflows_stage_tests")
    op.drop_table("iceflows_stage_approvers")
    op.drop_table("iceflows_stages")
    op.drop_table("iceflows")
    op.drop_table("iceflows_credentials")
