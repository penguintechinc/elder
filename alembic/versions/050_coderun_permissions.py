"""Add granted_scopes_json and allowed_egress_domains_json to Elder Runs tables.

Revision ID: 050
Revises: 049
Create Date: 2026-11-23
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "050"
down_revision = "049"
branch_labels = None
depends_on = None


def _add_column_if_missing(
    inspector: sa.Inspector, table: str, column: sa.Column
) -> None:
    existing = {col["name"] for col in inspector.get_columns(table)}
    if column.name not in existing:
        op.add_column(table, column)


def upgrade() -> None:
    """Add scoped permission and egress domain columns to Runs tables."""
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    existing_tables = set(inspector.get_table_names())

    if "run_functions" in existing_tables:
        _add_column_if_missing(
            inspector,
            "run_functions",
            sa.Column("granted_scopes_json", sa.JSON(), nullable=True),
        )
        _add_column_if_missing(
            inspector,
            "run_functions",
            sa.Column("allowed_egress_domains_json", sa.JSON(), nullable=True),
        )

    if "run_function_versions" in existing_tables:
        _add_column_if_missing(
            inspector,
            "run_function_versions",
            sa.Column("granted_scopes_json", sa.JSON(), nullable=True),
        )


def downgrade() -> None:
    """Drop scoped permission and egress domain columns from Runs tables."""
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    existing_tables = set(inspector.get_table_names())

    if "run_function_versions" in existing_tables:
        existing = {
            col["name"] for col in inspector.get_columns("run_function_versions")
        }
        if "granted_scopes_json" in existing:
            op.drop_column("run_function_versions", "granted_scopes_json")

    if "run_functions" in existing_tables:
        existing = {col["name"] for col in inspector.get_columns("run_functions")}
        for col_name in ("allowed_egress_domains_json", "granted_scopes_json"):
            if col_name in existing:
                op.drop_column("run_functions", col_name)
