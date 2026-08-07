"""Add 'customer_contact' to identitytype enum (CRM entities, Plan 03 task 1).

Ruffled CRM customer contacts are represented as `identities` rows with
this new type instead of a separate table. The Python side stores the
SQLAlchemy Enum's *value* (lowercase, via values_callable) as the column
value -- unlike IssueType/IssuePriority (see migration 027), so the value
added here is lowercase 'customer_contact', matching
`IdentityType.CUSTOMER_CONTACT.value`.

Unlike issuetype/issuepriority (027), the `identitytype` Postgres enum is
NOT guaranteed to exist on a real deployed DB: migration 014
(fix_auth_provider_enum) converts `identities.identity_type` from a native
enum column to VARCHAR(50) and `DROP TYPE IF EXISTS identitytype CASCADE`
on any DB where the `identities` table already existed at that point --
which is every real alpha/beta/prod DB with migration history. Only a
brand-new DB whose schema was bootstrapped via `create_all()` off the
*current* SQLAlchemy model (which still declares `Enum(IdentityType, ...)`)
has a live `identitytype` type at all. This migration checks for the type
before attempting to alter it, and is a no-op where it doesn't exist --
the column is a plain VARCHAR(50) there and already accepts any string
value, including 'customer_contact', with no enum constraint to update.

ALTER TYPE ... ADD VALUE cannot run inside a transaction on PostgreSQL, so
the alteration runs in an autocommit block (per Alembic's documented
recipe for Postgres ENUM types). IF NOT EXISTS (PG 12+) makes it safe to
re-run.

Revision ID: 035
Revises: 034
Create Date: 2026-08-06
"""

import sqlalchemy as sa

from alembic import op

revision = "035"
down_revision = "034"
branch_labels = None
depends_on = None


def upgrade():
    """Add the customer_contact enum value, only if identitytype still exists as a native enum."""
    conn = op.get_bind()
    type_exists = conn.execute(
        sa.text("SELECT 1 FROM pg_type WHERE typname = 'identitytype'")
    ).scalar()

    if type_exists:
        with op.get_context().autocommit_block():
            op.execute(
                "ALTER TYPE identitytype ADD VALUE IF NOT EXISTS 'customer_contact'"
            )
    # else: identity_type is VARCHAR(50) (see migration 014) -- no enum
    # constraint exists to update, nothing to do.


def downgrade():
    """No-op: PostgreSQL cannot drop a single value from an enum type.

    Removing 'customer_contact' would require rebuilding the enum type
    (rename old type, create new type without the value, cast all
    dependent columns, drop old type) and is only safe if no row uses the
    value. Not implemented here -- PostgreSQL has no native
    ALTER TYPE ... DROP VALUE.
    """
    pass
