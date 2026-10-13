"""Add on-call rotation management tables.

Revision ID: 008
Revises: 007
Create Date: 2025-12-19

This migration adds 6 tables for comprehensive on-call rotation management:
- on_call_rotations: Main rotation configuration (weekly, cron, manual, follow-the-sun)
- on_call_rotation_participants: People in rotation with notification preferences
- on_call_escalation_policies: Backup contacts and escalation rules
- on_call_overrides: Temporary substitutions (sick days, vacations)
- on_call_shifts: Historical record of who was on-call with metrics
- on_call_notifications: Notification audit trail

Supports organization-level and service-level rotations with Prometheus AlertManager integration.
"""

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision = "008"
down_revision = "007"
branch_labels = None
depends_on = None


def upgrade():
    """Create on-call rotation tables and indexes."""

    # 1. on_call_rotations - Main rotation configuration
    op.create_table(
        "on_call_rotations",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("village_id", sa.String(length=32), nullable=True),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="1"),
        sa.Column("scope_type", sa.String(length=50), nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=True),
        sa.Column("service_id", sa.Integer(), nullable=True),
        sa.Column("schedule_type", sa.String(length=50), nullable=False),
        sa.Column("rotation_length_days", sa.Integer(), nullable=True),
        sa.Column("rotation_start_date", sa.Date(), nullable=True),
        sa.Column("schedule_cron", sa.String(length=255), nullable=True),
        sa.Column("handoff_timezone", sa.String(length=100), nullable=True),
        sa.Column("shift_split", sa.Boolean(), nullable=True, server_default="0"),
        sa.Column("shift_config", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["organization_id"], ["organizations.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["service_id"], ["services.id"], ondelete="CASCADE"),
    )
    op.create_index(
        "idx_on_call_rotations_village_id",
        "on_call_rotations",
        ["village_id"],
        unique=True,
    )
    op.create_index(
        "idx_rotations_scope",
        "on_call_rotations",
        ["scope_type", "organization_id", "service_id"],
    )

    # 2. on_call_rotation_participants - People in rotation
    op.create_table(
        "on_call_rotation_participants",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("rotation_id", sa.Integer(), nullable=False),
        sa.Column("identity_id", sa.Integer(), nullable=False),
        sa.Column("order_index", sa.Integer(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="1"),
        sa.Column("start_date", sa.Date(), nullable=True),
        sa.Column("end_date", sa.Date(), nullable=True),
        sa.Column("notification_email", sa.String(length=255), nullable=True),
        sa.Column("notification_phone", sa.String(length=50), nullable=True),
        sa.Column("notification_slack", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["rotation_id"], ["on_call_rotations.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["identity_id"], ["identities.id"], ondelete="CASCADE"),
    )
    op.create_index(
        "idx_rotation_participants",
        "on_call_rotation_participants",
        ["rotation_id", "order_index"],
    )

    # 3. on_call_escalation_policies - Backup contacts and escalation rules
    op.create_table(
        "on_call_escalation_policies",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("rotation_id", sa.Integer(), nullable=False),
        sa.Column("level", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("escalation_type", sa.String(length=50), nullable=False),
        sa.Column("identity_id", sa.Integer(), nullable=True),
        sa.Column("group_id", sa.Integer(), nullable=True),
        sa.Column(
            "escalation_delay_minutes", sa.Integer(), nullable=True, server_default="15"
        ),
        sa.Column("notification_channels", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["rotation_id"], ["on_call_rotations.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["identity_id"], ["identities.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["group_id"], ["identity_groups.id"], ondelete="CASCADE"
        ),
    )

    # 4. on_call_overrides - Temporary substitutions
    op.create_table(
        "on_call_overrides",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("rotation_id", sa.Integer(), nullable=False),
        sa.Column("original_identity_id", sa.Integer(), nullable=False),
        sa.Column("override_identity_id", sa.Integer(), nullable=False),
        sa.Column("start_datetime", sa.DateTime(timezone=True), nullable=False),
        sa.Column("end_datetime", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reason", sa.String(length=512), nullable=True),
        sa.Column("created_by_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["rotation_id"], ["on_call_rotations.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["original_identity_id"], ["identities.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["override_identity_id"], ["identities.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["created_by_id"], ["identities.id"], ondelete="SET NULL"
        ),
    )
    op.create_index(
        "idx_on_call_overrides_timerange",
        "on_call_overrides",
        ["rotation_id", "start_datetime", "end_datetime"],
    )

    # 5. on_call_shifts - Historical record of who was on-call
    op.create_table(
        "on_call_shifts",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("rotation_id", sa.Integer(), nullable=False),
        sa.Column("identity_id", sa.Integer(), nullable=False),
        sa.Column("shift_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("shift_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("is_override", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("override_id", sa.Integer(), nullable=True),
        sa.Column("alerts_received", sa.Integer(), nullable=True, server_default="0"),
        sa.Column("incidents_created", sa.Integer(), nullable=True, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["rotation_id"], ["on_call_rotations.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["identity_id"], ["identities.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["override_id"], ["on_call_overrides.id"], ondelete="SET NULL"
        ),
    )
    op.create_index(
        "idx_on_call_shifts_active",
        "on_call_shifts",
        ["rotation_id", "shift_start", "shift_end"],
    )

    # 6. on_call_notifications - Notification audit trail
    op.create_table(
        "on_call_notifications",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("rotation_id", sa.Integer(), nullable=True),
        sa.Column("identity_id", sa.Integer(), nullable=True),
        sa.Column("notification_type", sa.String(length=50), nullable=False),
        sa.Column("channel", sa.String(length=50), nullable=False),
        sa.Column("subject", sa.String(length=512), nullable=True),
        sa.Column("message", sa.Text(), nullable=True),
        sa.Column("metadata", sa.JSON(), nullable=True),
        sa.Column(
            "status", sa.String(length=50), nullable=True, server_default="pending"
        ),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["rotation_id"], ["on_call_rotations.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["identity_id"], ["identities.id"], ondelete="CASCADE"),
    )


def downgrade():
    """Drop on-call rotation tables and indexes."""
    # Drop in reverse order due to foreign key constraints
    op.drop_table("on_call_notifications")
    op.drop_table("on_call_shifts")
    op.drop_table("on_call_overrides")
    op.drop_table("on_call_escalation_policies")
    op.drop_table("on_call_rotation_participants")
    op.drop_table("on_call_rotations")
