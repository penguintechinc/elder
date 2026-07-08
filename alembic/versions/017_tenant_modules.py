"""Create tenant_modules table for Phase-2 module toggles.

Revision ID: 017
Revises: 016
Create Date: 2026-07-08

Tenant-specific module enablement and settings storage.
"""

import sqlalchemy as sa
from alembic import op

revision = "017"
down_revision = "016"
branch_labels = None
depends_on = None


def upgrade():
    """Create tenant_modules table with tenant/module unique constraint."""
    op.create_table(
        "tenant_modules",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("module_name", sa.String(64), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default="1"),
        sa.Column("settings", sa.JSON(), nullable=True),
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
            onupdate=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "module_name", name="uq_tenant_module_name"),
    )

    # Create index on tenant_id for faster lookups
    op.create_index("ix_tenant_modules_tenant_id", "tenant_modules", ["tenant_id"])

    # Create index on module_name for potential cross-tenant queries
    op.create_index("ix_tenant_modules_module_name", "tenant_modules", ["module_name"])


def downgrade():
    """Drop tenant_modules table and indexes."""
    op.drop_index("ix_tenant_modules_module_name", table_name="tenant_modules")
    op.drop_index("ix_tenant_modules_tenant_id", table_name="tenant_modules")
    op.drop_table("tenant_modules")
