"""Create remote_workers table for remote worker fleet registry and liveness.

Revision ID: 051
Revises: 050
Create Date: 2026-11-23
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "051"
down_revision = "050"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create remote_workers table and supporting indexes."""
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    existing_tables = set(inspector.get_table_names())

    if "remote_workers" not in existing_tables:
        op.create_table(
            "remote_workers",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("worker_id", sa.String(64), nullable=False, unique=True),
            sa.Column(
                "tenant_id",
                sa.Integer(),
                sa.ForeignKey("tenants.id", ondelete="CASCADE"),
                nullable=True,
            ),
            sa.Column("worker_type", sa.String(32), nullable=False),
            sa.Column("name", sa.String(255), nullable=False),
            sa.Column("cluster", sa.String(128), nullable=True),
            sa.Column("token_hash", sa.String(64), nullable=True),
            sa.Column("token_prefix", sa.String(16), nullable=True),
            sa.Column("config_json", sa.JSON(), nullable=True),
            sa.Column("capabilities_json", sa.JSON(), nullable=True),
            sa.Column("hostname", sa.String(255), nullable=True),
            sa.Column("version", sa.String(64), nullable=True),
            sa.Column(
                "is_enabled",
                sa.Boolean(),
                nullable=False,
                server_default=sa.true(),
            ),
            sa.Column("last_heartbeat_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column(
                "created_by_identity_id",
                sa.Integer(),
                sa.ForeignKey("identities.id", ondelete="SET NULL"),
                nullable=True,
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
        )
        op.create_index(
            "ix_remote_workers_tenant_id",
            "remote_workers",
            ["tenant_id"],
        )
        op.create_index(
            "ix_remote_workers_worker_type",
            "remote_workers",
            ["worker_type"],
        )
        op.create_index(
            "ix_remote_workers_cluster",
            "remote_workers",
            ["cluster"],
        )
        op.create_index(
            "ix_remote_workers_token_hash",
            "remote_workers",
            ["token_hash"],
        )
        op.create_index(
            "ix_remote_workers_type_cluster",
            "remote_workers",
            ["worker_type", "cluster"],
        )


def downgrade() -> None:
    """Drop remote_workers table."""
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    existing_tables = set(inspector.get_table_names())

    if "remote_workers" in existing_tables:
        op.drop_table("remote_workers")
