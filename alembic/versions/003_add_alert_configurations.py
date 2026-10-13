"""Add alert_configurations table for per-OU incident alerting

Revision ID: 003
Revises: 002
Create Date: 2025-10-25 00:30:00.000000

"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision = "003"
down_revision = "002"
branch_labels = None
depends_on = None


def upgrade():
    """Create alert_configurations table for per-organization alert destinations."""
    op.create_table(
        "alert_configurations",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column(
            "destination_type",
            sa.Enum(
                "EMAIL", "WEBHOOK", "PAGERDUTY", "SLACK", name="alertdestinationtype"
            ),
            nullable=False,
        ),
        sa.Column(
            "name",
            sa.String(length=255),
            nullable=False,
            comment="Friendly name for this alert configuration",
        ),
        sa.Column(
            "enabled",
            sa.Integer(),
            nullable=False,
            server_default="1",
            comment="Whether this alert configuration is active",
        ),
        sa.Column(
            "config",
            sa.JSON(),
            nullable=False,
            comment="Destination-specific configuration as JSON",
        ),
        sa.Column(
            "severity_filter",
            sa.JSON(),
            nullable=True,
            comment="Optional filter: only alert for specific priority levels",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"], ["organizations.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        comment="Per-organization alert configurations for incident notifications",
    )

    # Create indexes
    op.create_index(
        op.f("ix_alert_configurations_organization_id"),
        "alert_configurations",
        ["organization_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_alert_configurations_destination_type"),
        "alert_configurations",
        ["destination_type"],
        unique=False,
    )
    op.create_index(
        op.f("ix_alert_configurations_enabled"),
        "alert_configurations",
        ["enabled"],
        unique=False,
    )


def downgrade():
    """Drop alert_configurations table."""
    op.drop_index(
        op.f("ix_alert_configurations_enabled"), table_name="alert_configurations"
    )
    op.drop_index(
        op.f("ix_alert_configurations_destination_type"),
        table_name="alert_configurations",
    )
    op.drop_index(
        op.f("ix_alert_configurations_organization_id"),
        table_name="alert_configurations",
    )
    op.drop_table("alert_configurations")
    op.execute("DROP TYPE alertdestinationtype")
