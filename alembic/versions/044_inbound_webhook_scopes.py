"""Add scopes to inbound_webhooks and action tracking to inbound_webhook_events.

Revision ID: 044
Revises: 043
Create Date: 2026-11-20
"""

import sqlalchemy as sa

from alembic import op

revision = "044"
down_revision = "043"
branch_labels = None
depends_on = None


def upgrade():
    """Add scopes, action, and action_result columns (idempotent)."""
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    tables = set(inspector.get_table_names())

    if "inbound_webhooks" in tables:
        cols = {c["name"] for c in inspector.get_columns("inbound_webhooks")}
        if "scopes" not in cols:
            op.add_column(
                "inbound_webhooks",
                sa.Column("scopes", sa.JSON(), nullable=True),
            )

    if "inbound_webhook_events" in tables:
        cols = {c["name"] for c in inspector.get_columns("inbound_webhook_events")}
        if "action" not in cols:
            op.add_column(
                "inbound_webhook_events",
                sa.Column("action", sa.String(64), nullable=True),
            )
        if "action_result" not in cols:
            op.add_column(
                "inbound_webhook_events",
                sa.Column("action_result", sa.JSON(), nullable=True),
            )


def downgrade():
    """Drop scopes, action, and action_result columns (idempotent)."""
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    tables = set(inspector.get_table_names())

    if "inbound_webhook_events" in tables:
        cols = {c["name"] for c in inspector.get_columns("inbound_webhook_events")}
        if "action_result" in cols:
            op.drop_column("inbound_webhook_events", "action_result")
        if "action" in cols:
            op.drop_column("inbound_webhook_events", "action")

    if "inbound_webhooks" in tables:
        cols = {c["name"] for c in inspector.get_columns("inbound_webhooks")}
        if "scopes" in cols:
            op.drop_column("inbound_webhooks", "scopes")
