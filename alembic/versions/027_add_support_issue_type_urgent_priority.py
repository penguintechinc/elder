"""Add 'SUPPORT' to issuetype enum and 'URGENT' to issuepriority enum.

Support tickets are a type of issue (issues foundation task 3); urgent
priority sits between high and critical. The Python side stores the
SQLAlchemy Enum's member *name* (uppercase) as the column value (no
values_callable is configured for IssueType/IssuePriority), so the values
added here match that casing, not the lowercase Python enum .value.

ALTER TYPE ... ADD VALUE cannot run inside a transaction on PostgreSQL, so
this migration runs the statements in an autocommit block (per Alembic's
documented recipe for Postgres ENUM types). IF NOT EXISTS (PG 12+) makes
it safe to re-run.

Revision ID: 027
Revises: 026
Create Date: 2026-08-06
"""

from alembic import op

revision = "027"
down_revision = "026"
branch_labels = None
depends_on = None


def upgrade():
    """Add SUPPORT/URGENT enum values outside the migration transaction."""
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE issuetype ADD VALUE IF NOT EXISTS 'SUPPORT'")
        op.execute("ALTER TYPE issuepriority ADD VALUE IF NOT EXISTS 'URGENT'")


def downgrade():
    """No-op: PostgreSQL cannot drop a single value from an enum type.

    Removing 'SUPPORT'/'URGENT' would require rebuilding the enum types
    (rename old type, create new type without the value, cast all
    dependent columns, drop old type) and is only safe if no row uses the
    value. Not implemented here — PostgreSQL has no native
    ALTER TYPE ... DROP VALUE.
    """
    pass
