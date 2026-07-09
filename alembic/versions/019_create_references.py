"""Create references table for cross-resource tracking.

Revision ID: 019
Revises: 018
Create Date: 2026-07-08

The Reference model (apps/api/models/references.py) was included in CORE_MODELS
but had no Alembic migration creating its table. This migration creates:
- references table with IDMixin, TimestampMixin, TenantScopedMixin
- source/target resource identification (module, type, id)
- ref_type, context, created_by columns
- Indexes for backlink and outbound queries
"""

import sqlalchemy as sa
from alembic import op

revision = "019"
down_revision = "018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create references table."""
    op.create_table(
        "references",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column("source_module", sa.String(100), nullable=False),
        sa.Column("source_type", sa.String(100), nullable=False),
        sa.Column("source_id", sa.String(255), nullable=False),
        sa.Column("target_module", sa.String(100), nullable=False),
        sa.Column("target_type", sa.String(100), nullable=False),
        sa.Column("target_id", sa.String(255), nullable=False),
        sa.Column("ref_type", sa.String(50), nullable=False),
        sa.Column("context", sa.JSON(), nullable=True),
        sa.Column("created_by", sa.String(36), nullable=True),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_references_target",
        "references",
        ["target_module", "target_type", "target_id", "tenant_id"],
    )
    op.create_index(
        "ix_references_source",
        "references",
        ["source_module", "source_type", "source_id"],
    )
    op.create_index("ix_references_tenant", "references", ["tenant_id"])


def downgrade() -> None:
    """Drop references table."""
    op.drop_index("ix_references_tenant", table_name="references")
    op.drop_index("ix_references_source", table_name="references")
    op.drop_index("ix_references_target", table_name="references")
    op.drop_table("references")
