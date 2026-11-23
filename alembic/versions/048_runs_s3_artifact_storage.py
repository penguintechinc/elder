"""Add S3-compatible artifact storage columns to Elder Runs tables.

Revision ID: 048
Revises: 047
Create Date: 2026-11-23
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "048"
down_revision = "047"
branch_labels = None
depends_on = None


def _add_column_if_missing(
    inspector: sa.Inspector, table: str, column: sa.Column
) -> None:
    existing = {col["name"] for col in inspector.get_columns(table)}
    if column.name not in existing:
        op.add_column(table, column)


def upgrade() -> None:
    """Add storage_backend, storage_key, and code_sha256 to Runs tables."""
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    existing_tables = set(inspector.get_table_names())

    for table in ("run_functions", "run_function_versions"):
        if table not in existing_tables:
            continue
        _add_column_if_missing(
            inspector,
            table,
            sa.Column(
                "storage_backend",
                sa.String(32),
                nullable=False,
                server_default="database",
                comment="database | s3",
            ),
        )
        _add_column_if_missing(
            inspector,
            table,
            sa.Column("storage_key", sa.String(512), nullable=True),
        )
        _add_column_if_missing(
            inspector,
            table,
            sa.Column("code_sha256", sa.String(64), nullable=True),
        )


def downgrade() -> None:
    """Drop S3 artifact storage columns from Runs tables."""
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    existing_tables = set(inspector.get_table_names())

    for table in ("run_function_versions", "run_functions"):
        if table not in existing_tables:
            continue
        existing = {col["name"] for col in inspector.get_columns(table)}
        for col_name in ("code_sha256", "storage_key", "storage_backend"):
            if col_name in existing:
                op.drop_column(table, col_name)
