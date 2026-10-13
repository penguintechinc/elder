"""Create service_nodes table (heartbeat-based node counting for license enforcement).

Revision ID: 039
Revises: 038
Create Date: 2026-08-20

Each running pod/process of a licensed Elder service type (API, worker,
scanner, etc.) registers a row here on startup and refreshes heartbeat_ts on
a periodic cadence (see apps/api/models/service_node.py). Licensing's
count_active_nodes() counts rows with a recent heartbeat per service_type
against the tier's max_nodes_per_type limit -- there is no explicit
deregistration on shutdown, so a crashed/evicted pod's row simply goes stale
and ages out of the count.
"""

import sqlalchemy as sa

from alembic import op

revision = "039"
down_revision = "038"
branch_labels = None
depends_on = None


def upgrade():
    """Create service_nodes (idempotent)."""
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    if inspector.has_table("service_nodes"):
        return

    op.create_table(
        "service_nodes",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("service_type", sa.String(64), nullable=False),
        sa.Column("pod_id", sa.String(128), nullable=False),
        sa.Column("heartbeat_ts", sa.DateTime(timezone=True), nullable=False),
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
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("pod_id", name="uq_service_nodes_pod_id"),
    )
    op.create_index("ix_service_nodes_service_type", "service_nodes", ["service_type"])
    op.create_index("ix_service_nodes_heartbeat_ts", "service_nodes", ["heartbeat_ts"])


def downgrade():
    """Drop service_nodes (idempotent)."""
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    if not inspector.has_table("service_nodes"):
        return

    op.drop_index("ix_service_nodes_heartbeat_ts", table_name="service_nodes")
    op.drop_index("ix_service_nodes_service_type", table_name="service_nodes")
    op.drop_table("service_nodes")
