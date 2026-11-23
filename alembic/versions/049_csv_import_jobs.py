"""Create csv_import_jobs table for S3-backed async batch CSV imports.

Revision ID: 049
Revises: 048
Create Date: 2026-11-23
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "049"
down_revision = "048"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create csv_import_jobs table and supporting indexes."""
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    existing_tables = set(inspector.get_table_names())

    if "csv_import_jobs" not in existing_tables:
        op.create_table(
            "csv_import_jobs",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("job_id", sa.String(64), nullable=False, unique=True),
            sa.Column(
                "tenant_id",
                sa.Integer(),
                sa.ForeignKey("tenants.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("resource", sa.String(64), nullable=False),
            sa.Column("filename", sa.String(255), nullable=False, server_default=""),
            sa.Column(
                "storage_backend",
                sa.String(32),
                nullable=False,
                server_default="s3",
            ),
            sa.Column("storage_key", sa.String(512), nullable=True),
            sa.Column("file_sha256", sa.String(64), nullable=True),
            sa.Column("file_size_bytes", sa.Integer(), nullable=True),
            sa.Column(
                "status",
                sa.String(32),
                nullable=False,
                server_default="pending",
            ),
            sa.Column(
                "total_rows",
                sa.Integer(),
                nullable=False,
                server_default="0",
            ),
            sa.Column(
                "imported_count",
                sa.Integer(),
                nullable=False,
                server_default="0",
            ),
            sa.Column(
                "skipped_count",
                sa.Integer(),
                nullable=False,
                server_default="0",
            ),
            sa.Column("errors_json", sa.JSON(), nullable=True),
            sa.Column("error_message", sa.Text(), nullable=True),
            sa.Column(
                "created_by_identity_id",
                sa.Integer(),
                sa.ForeignKey("identities.id", ondelete="SET NULL"),
                nullable=True,
            ),
            sa.Column("worker_id", sa.String(128), nullable=True),
            sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=True,
                server_default=sa.func.now(),
            ),
        )
        op.create_index(
            "ix_csv_import_jobs_tenant_id",
            "csv_import_jobs",
            ["tenant_id"],
        )
        op.create_index(
            "ix_csv_import_jobs_resource",
            "csv_import_jobs",
            ["resource"],
        )
        op.create_index(
            "ix_csv_import_jobs_status",
            "csv_import_jobs",
            ["status"],
        )
        op.create_index(
            "ix_csv_import_jobs_tenant_status",
            "csv_import_jobs",
            ["tenant_id", "status"],
        )
        op.create_index(
            "ix_csv_import_jobs_status_created",
            "csv_import_jobs",
            ["status", "created_at"],
        )


def downgrade() -> None:
    """Drop csv_import_jobs table."""
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    existing_tables = set(inspector.get_table_names())

    if "csv_import_jobs" in existing_tables:
        op.drop_table("csv_import_jobs")
