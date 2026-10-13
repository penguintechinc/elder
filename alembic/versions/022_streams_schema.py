"""Create streams module schema — 17 tables for workflow automation, playbooks, executions.

Revision ID: 022
Revises: 021
Create Date: 2026-07-10

Phase 4b-a: Author Streams module schema from IceCharts v1.1.x IceStreams.
- stream_playbooks: main workflow definition (VillageIDMixin)
- stream_nodes: individual steps in workflow
- stream_edges: connections between nodes
- stream_versions: versioned snapshots for rollback
- stream_webhooks: webhook triggers
- stream_executions: individual workflow runs
- stream_node_executions: per-node execution details
- stream_schedules: cron-like scheduling
- stream_shares: sharing permissions
- stream_editor_locks: single-editor enforcement
- stream_templates: reusable templates (VillageIDMixin)
- stream_forms: dynamic forms
- stream_form_submissions: form submission history
- stream_node_metadata: node annotations
- stream_custom_modules: uploadable modules (VillageIDMixin)
- stream_approval_gates: approval gate configurations
- stream_execution_approvals: approval decisions for paused executions

Tenant ref: Integer FK to tenants.id.
User/actor refs: *_identity_id Integer FK to identities.id.
village_id: VillageIDMixin on stream_playbooks, stream_templates, stream_custom_modules.
elder_entity_id: none (no infrastructure coupling in Streams).

Table creation order respects FK dependencies:
1. stream_playbooks (root)
2. stream_nodes, stream_edges, stream_versions, stream_webhooks, stream_schedules,
   stream_templates, stream_custom_modules (FK to stream_playbooks OR parents only)
3. stream_executions (FK to stream_playbooks)
4. stream_node_executions, stream_approval_gates (FK to stream_playbooks/executions)
5. stream_shares, stream_editor_locks, stream_forms (FK to stream_playbooks)
6. stream_form_submissions (FK to stream_forms, stream_playbooks)
7. stream_node_metadata (FK to stream_playbooks)
8. stream_execution_approvals (FK to stream_execution_approvals, stream_approval_gates)
"""

import sqlalchemy as sa

from alembic import op

revision = "022"
down_revision = "021"
branch_labels = None
depends_on = None


def upgrade():
    """Create 17 Streams module tables with FKs, indexes, unique constraints."""

    # 1. stream_playbooks — primary workflow definition (root, no internal FKs)
    op.create_table(
        "stream_playbooks",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("village_id", sa.String(32), nullable=True, unique=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("owner_identity_id", sa.Integer(), nullable=False),
        sa.Column("created_by_identity_id", sa.Integer(), nullable=True),
        sa.Column("updated_by_identity_id", sa.Integer(), nullable=True),
        sa.Column(
            "status",
            sa.String(20),
            nullable=False,
            server_default="draft",
            comment="draft, active, paused, archived",
        ),
        sa.Column("is_enabled", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("is_public", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("is_template", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("trigger_type", sa.String(50), nullable=True),
        sa.Column("trigger_config", sa.JSON(), nullable=True),
        sa.Column("error_handling", sa.JSON(), nullable=True),
        sa.Column("tags", sa.JSON(), nullable=True),
        sa.Column("canvas_data", sa.JSON(), nullable=True),
        sa.Column("execution_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("success_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("failure_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_execution_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("next_run_at", sa.DateTime(timezone=True), nullable=True),
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
            ["owner_identity_id"], ["identities.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["created_by_identity_id"], ["identities.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["updated_by_identity_id"], ["identities.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_stream_playbooks_tenant_id", "stream_playbooks", ["tenant_id"])

    # 2. stream_nodes — individual steps
    op.create_table(
        "stream_nodes",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("playbook_id", sa.Integer(), nullable=False),
        sa.Column("node_id", sa.String(100), nullable=False),
        sa.Column("node_type", sa.String(50), nullable=False),
        sa.Column(
            "node_category",
            sa.String(50),
            nullable=False,
            server_default="transform",
        ),
        sa.Column("label", sa.String(255), nullable=True),
        sa.Column("position_x", sa.Integer(), nullable=False),
        sa.Column("position_y", sa.Integer(), nullable=False),
        sa.Column("config", sa.JSON(), nullable=False),
        sa.Column("data_schema", sa.JSON(), nullable=True),
        sa.Column("is_enabled", sa.Boolean(), nullable=False, server_default="1"),
        sa.Column("execution_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("comments", sa.Text(), nullable=True),
        sa.Column("metadata_json", sa.JSON(), nullable=True),
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
            ["playbook_id"], ["stream_playbooks.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_stream_nodes_playbook_id", "stream_nodes", ["playbook_id"])

    # 3. stream_edges — connections between nodes
    op.create_table(
        "stream_edges",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("playbook_id", sa.Integer(), nullable=False),
        sa.Column("edge_id", sa.String(100), nullable=False),
        sa.Column("source_node_id", sa.String(100), nullable=False),
        sa.Column("target_node_id", sa.String(100), nullable=False),
        sa.Column("source_handle", sa.String(50), nullable=True),
        sa.Column("target_handle", sa.String(50), nullable=True),
        sa.Column("condition", sa.JSON(), nullable=True),
        sa.Column("label", sa.String(255), nullable=True),
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
            ["playbook_id"], ["stream_playbooks.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_stream_edges_playbook_id", "stream_edges", ["playbook_id"])

    # 4. stream_versions — versioned snapshots
    op.create_table(
        "stream_versions",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("playbook_id", sa.Integer(), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("created_by_identity_id", sa.Integer(), nullable=True),
        sa.Column("nodes_json", sa.JSON(), nullable=False),
        sa.Column("edges_json", sa.JSON(), nullable=False),
        sa.Column("canvas_json", sa.JSON(), nullable=True),
        sa.Column("change_summary", sa.Text(), nullable=True),
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
            ["playbook_id"], ["stream_playbooks.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["created_by_identity_id"], ["identities.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("playbook_id", "version_number", name="uq_stream_version"),
    )
    op.create_index(
        "ix_stream_versions_playbook_id", "stream_versions", ["playbook_id"]
    )

    # 5. stream_webhooks — webhook triggers
    op.create_table(
        "stream_webhooks",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("playbook_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(255), nullable=True),
        sa.Column("token", sa.String(255), nullable=False, unique=True),
        sa.Column("signature_secret", sa.String(255), nullable=True),
        sa.Column(
            "validate_signature", sa.Boolean(), nullable=False, server_default="0"
        ),
        sa.Column("allowed_methods", sa.JSON(), nullable=True),
        sa.Column("ip_whitelist", sa.JSON(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="1"),
        sa.Column("is_enabled", sa.Boolean(), nullable=False, server_default="1"),
        sa.Column("last_triggered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("trigger_count", sa.Integer(), nullable=False, server_default="0"),
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
            ["playbook_id"], ["stream_playbooks.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_stream_webhooks_playbook_id", "stream_webhooks", ["playbook_id"]
    )

    # 6. stream_schedules — cron scheduling
    op.create_table(
        "stream_schedules",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("playbook_id", sa.Integer(), nullable=False),
        sa.Column("cron_expression", sa.String(100), nullable=False),
        sa.Column("timezone", sa.String(100), nullable=False, server_default="UTC"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="1"),
        sa.Column("next_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("run_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("static_input", sa.JSON(), nullable=True),
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
            ["playbook_id"], ["stream_playbooks.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_stream_schedules_playbook_id", "stream_schedules", ["playbook_id"]
    )

    # 7. stream_templates — reusable templates
    op.create_table(
        "stream_templates",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("village_id", sa.String(32), nullable=True, unique=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "category",
            sa.String(100),
            nullable=False,
            server_default="custom",
        ),
        sa.Column("created_by_identity_id", sa.Integer(), nullable=True),
        sa.Column("nodes_json", sa.JSON(), nullable=False),
        sa.Column("edges_json", sa.JSON(), nullable=False),
        sa.Column("canvas_data", sa.JSON(), nullable=True),
        sa.Column("is_public", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("usage_count", sa.Integer(), nullable=False, server_default="0"),
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
            ["created_by_identity_id"], ["identities.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_stream_templates_tenant_id", "stream_templates", ["tenant_id"])

    # 8. stream_custom_modules — uploadable modules
    op.create_table(
        "stream_custom_modules",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("village_id", sa.String(32), nullable=True, unique=True),
        sa.Column("name", sa.String(255), nullable=False, unique=True),
        sa.Column("display_name", sa.String(255), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("module_type", sa.String(50), nullable=False),
        sa.Column("version", sa.String(50), nullable=False, server_default="1.0.0"),
        sa.Column("code_blob", sa.Text(), nullable=True),
        sa.Column("config_schema", sa.JSON(), nullable=True),
        sa.Column("input_schema", sa.JSON(), nullable=True),
        sa.Column("output_schema", sa.JSON(), nullable=True),
        sa.Column("is_validated", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("validation_errors", sa.JSON(), nullable=True),
        sa.Column("is_enabled", sa.Boolean(), nullable=False, server_default="1"),
        sa.Column("uploaded_by_identity_id", sa.Integer(), nullable=True),
        sa.Column("usage_count", sa.Integer(), nullable=False, server_default="0"),
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
            ["uploaded_by_identity_id"], ["identities.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_stream_custom_modules_tenant_id",
        "stream_custom_modules",
        ["tenant_id"],
    )

    # 9. stream_executions — individual runs
    op.create_table(
        "stream_executions",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("playbook_id", sa.Integer(), nullable=False),
        sa.Column("execution_id", sa.String(100), nullable=False, unique=True),
        sa.Column(
            "status",
            sa.String(50),
            nullable=False,
            server_default="pending",
        ),
        sa.Column("trigger_type", sa.String(50), nullable=True),
        sa.Column("triggered_by", sa.String(50), nullable=True),
        sa.Column("triggered_by_identity_id", sa.Integer(), nullable=True),
        sa.Column("input_json", sa.JSON(), nullable=True),
        sa.Column("output_json", sa.JSON(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("error_details", sa.JSON(), nullable=True),
        sa.Column("retry_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("parent_execution_id", sa.String(100), nullable=True),
        sa.Column("worker_id", sa.String(100), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
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
            ["playbook_id"], ["stream_playbooks.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["triggered_by_identity_id"], ["identities.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_stream_executions_playbook_id", "stream_executions", ["playbook_id"]
    )
    op.create_index(
        "ix_stream_executions_execution_id", "stream_executions", ["execution_id"]
    )

    # 10. stream_node_executions — per-node details
    op.create_table(
        "stream_node_executions",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("execution_id", sa.String(100), nullable=False),
        sa.Column("node_id", sa.String(100), nullable=False),
        sa.Column("node_type", sa.String(50), nullable=True),
        sa.Column("playbook_id", sa.Integer(), nullable=False),
        sa.Column(
            "status",
            sa.String(50),
            nullable=False,
            server_default="pending",
        ),
        sa.Column("input_json", sa.JSON(), nullable=True),
        sa.Column("output_json", sa.JSON(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("error_details", sa.JSON(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("retry_count", sa.Integer(), nullable=False, server_default="0"),
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
            ["playbook_id"], ["stream_playbooks.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_stream_node_executions_execution_id",
        "stream_node_executions",
        ["execution_id"],
    )
    op.create_index(
        "ix_stream_node_executions_playbook_id",
        "stream_node_executions",
        ["playbook_id"],
    )

    # 11. stream_approval_gates — approval gate config
    op.create_table(
        "stream_approval_gates",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("playbook_id", sa.Integer(), nullable=False),
        sa.Column("gate_id", sa.String(100), nullable=False, unique=True),
        sa.Column("node_id", sa.String(100), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("require_approval", sa.Boolean(), nullable=False, server_default="1"),
        sa.Column("min_approvers", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("approvers", sa.JSON(), nullable=True),
        sa.Column("approver_groups", sa.JSON(), nullable=True),
        sa.Column("timeout_minutes", sa.Integer(), nullable=True),
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
        sa.ForeignKeyConstraint(
            ["playbook_id"], ["stream_playbooks.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_stream_approval_gates_playbook_id",
        "stream_approval_gates",
        ["playbook_id"],
    )
    op.create_index(
        "ix_stream_approval_gates_gate_id", "stream_approval_gates", ["gate_id"]
    )

    # 12. stream_shares — sharing permissions
    op.create_table(
        "stream_shares",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("playbook_id", sa.Integer(), nullable=False),
        sa.Column("shared_with_identity_id", sa.Integer(), nullable=True),
        sa.Column("shared_with_group_id", sa.Integer(), nullable=True),
        sa.Column("shared_by_identity_id", sa.Integer(), nullable=True),
        sa.Column(
            "permission",
            sa.String(50),
            nullable=False,
            server_default="viewer",
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
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
            ["playbook_id"], ["stream_playbooks.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["shared_with_identity_id"], ["identities.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["shared_with_group_id"], ["identity_groups.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["shared_by_identity_id"], ["identities.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_stream_shares_playbook_id", "stream_shares", ["playbook_id"])

    # 13. stream_editor_locks — single-editor lock
    op.create_table(
        "stream_editor_locks",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("playbook_id", sa.Integer(), nullable=False, unique=True),
        sa.Column("locked_by_identity_id", sa.Integer(), nullable=False),
        sa.Column("locked_by_name", sa.String(255), nullable=True),
        sa.Column("locked_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("socket_id", sa.String(255), nullable=True),
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
            ["playbook_id"], ["stream_playbooks.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["locked_by_identity_id"], ["identities.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_stream_editor_locks_playbook_id",
        "stream_editor_locks",
        ["playbook_id"],
    )

    # 14. stream_forms — dynamic forms
    op.create_table(
        "stream_forms",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("playbook_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("fields_json", sa.JSON(), nullable=False),
        sa.Column("form_token", sa.String(255), nullable=True, unique=True),
        sa.Column(
            "access_type",
            sa.String(50),
            nullable=False,
            server_default="registered",
        ),
        sa.Column("allowed_users", sa.JSON(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="1"),
        sa.Column("submission_count", sa.Integer(), nullable=False, server_default="0"),
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
            ["playbook_id"], ["stream_playbooks.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_stream_forms_playbook_id", "stream_forms", ["playbook_id"])

    # 15. stream_form_submissions — submission history
    op.create_table(
        "stream_form_submissions",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("form_id", sa.Integer(), nullable=False),
        sa.Column("playbook_id", sa.Integer(), nullable=False),
        sa.Column("submitted_by_identity_id", sa.Integer(), nullable=True),
        sa.Column("submission_data", sa.JSON(), nullable=False),
        sa.Column("ip_address", sa.String(50), nullable=True),
        sa.Column("user_agent", sa.String(500), nullable=True),
        sa.Column("execution_id", sa.String(100), nullable=True),
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
        sa.ForeignKeyConstraint(["form_id"], ["stream_forms.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["playbook_id"], ["stream_playbooks.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["submitted_by_identity_id"], ["identities.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_stream_form_submissions_form_id",
        "stream_form_submissions",
        ["form_id"],
    )
    op.create_index(
        "ix_stream_form_submissions_playbook_id",
        "stream_form_submissions",
        ["playbook_id"],
    )

    # 16. stream_node_metadata — node annotations
    op.create_table(
        "stream_node_metadata",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("playbook_id", sa.Integer(), nullable=False),
        sa.Column("node_id", sa.String(100), nullable=False),
        sa.Column("comments", sa.Text(), nullable=True),
        sa.Column("metadata_json", sa.JSON(), nullable=True),
        sa.Column("updated_by_identity_id", sa.Integer(), nullable=True),
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
            ["playbook_id"], ["stream_playbooks.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["updated_by_identity_id"], ["identities.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_stream_node_metadata_playbook_id",
        "stream_node_metadata",
        ["playbook_id"],
    )

    # 17. stream_execution_approvals — approval decisions
    op.create_table(
        "stream_execution_approvals",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("execution_id", sa.String(100), nullable=False),
        sa.Column("gate_id", sa.Integer(), nullable=True),
        sa.Column("approver_identity_id", sa.Integer(), nullable=True),
        sa.Column("approval_id", sa.String(100), nullable=False, unique=True),
        sa.Column("decision", sa.String(20), nullable=False),
        sa.Column("comment", sa.Text(), nullable=True),
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
            ["gate_id"], ["stream_approval_gates.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["approver_identity_id"], ["identities.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_stream_execution_approvals_execution_id",
        "stream_execution_approvals",
        ["execution_id"],
    )
    op.create_index(
        "ix_stream_execution_approvals_gate_id",
        "stream_execution_approvals",
        ["gate_id"],
    )


def downgrade():
    """Drop all Streams tables in reverse FK dependency order."""
    # Drop in EXACT REVERSE of upgrade order (children before parents)
    op.drop_table("stream_execution_approvals")
    op.drop_table("stream_node_metadata")
    op.drop_table("stream_form_submissions")
    op.drop_table("stream_forms")
    op.drop_table("stream_editor_locks")
    op.drop_table("stream_shares")
    op.drop_table("stream_approval_gates")
    op.drop_table("stream_node_executions")
    op.drop_table("stream_executions")
    op.drop_table("stream_custom_modules")
    op.drop_table("stream_templates")
    op.drop_table("stream_schedules")
    op.drop_table("stream_webhooks")
    op.drop_table("stream_versions")
    op.drop_table("stream_edges")
    op.drop_table("stream_nodes")
    op.drop_table("stream_playbooks")
