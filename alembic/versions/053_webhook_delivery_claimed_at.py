"""Add claimed_at to webhook_deliveries for outbox recovery claims.

Revision ID: 053
Revises: 052
Create Date: 2026-11-24
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "053"
down_revision = "052"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add the nullable claimed_at column (idempotent)."""
    inspector = sa.inspect(op.get_bind())
    if "webhook_deliveries" not in inspector.get_table_names():
        return
    columns = {c["name"] for c in inspector.get_columns("webhook_deliveries")}
    if "claimed_at" not in columns:
        op.add_column(
            "webhook_deliveries",
            sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
        )


def downgrade() -> None:
    """Drop claimed_at (idempotent)."""
    inspector = sa.inspect(op.get_bind())
    if "webhook_deliveries" not in inspector.get_table_names():
        return
    columns = {c["name"] for c in inspector.get_columns("webhook_deliveries")}
    if "claimed_at" in columns:
        op.drop_column("webhook_deliveries", "claimed_at")
