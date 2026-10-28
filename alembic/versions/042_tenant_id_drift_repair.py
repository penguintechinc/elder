"""Repair tenant_id drift between alembic-built and model-built schemas

The models (``create_all()``) declare ``tenant_id`` on the access-review and
group-access tables, but the alembic history created them without it, so an
alembic-built database cannot be protected by tenant row-level security
(migration 043). This migration adds the missing column where it is absent,
backfills it from the parent group/review where derivable, and indexes it.

Additive and idempotent: a database that already has the column (every
``create_all()`` schema) is untouched. ``downgrade`` is intentionally a no-op —
dropping a derived ownership column would destroy data and re-open the drift.
Rows whose owner cannot be derived keep ``tenant_id`` NULL; migration 043's
pre-flight then refuses to enable RLS until the operator chooses a remedy.

Revision ID: 042
Revises: 041
Create Date: 2026-10-28
"""

import logging

import sqlalchemy as sa

from alembic import op

revision = "042"
down_revision = "041"
branch_labels = None
depends_on = None

logger = logging.getLogger("alembic.runtime.migration")

# table -> (foreign key column to the parent, parent table); parents first.
REPAIR: dict[str, tuple[str, str]] = {
    "access_reviews": ("group_id", "identity_groups"),
    "access_review_items": ("review_id", "access_reviews"),
    "access_review_assignments": ("review_id", "access_reviews"),
    "group_access_requests": ("group_id", "identity_groups"),
    "group_access_approvals": ("request_id", "group_access_requests"),
}


def upgrade() -> None:
    """Add tenant_id (nullable, FK, indexed) where missing and backfill from the parent."""
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute("SET LOCAL lock_timeout = '5s'")
    inspector = sa.inspect(op.get_bind())
    tables = set(inspector.get_table_names())
    for table, (fk_column, parent) in REPAIR.items():
        if table not in tables:
            continue
        columns = {c["name"] for c in inspector.get_columns(table)}
        if "tenant_id" in columns:
            continue
        op.execute(
            f'ALTER TABLE "{table}" ADD COLUMN tenant_id INTEGER REFERENCES tenants(id)'
        )
        op.execute(
            f'CREATE INDEX IF NOT EXISTS ix_{table}_tenant_id ON "{table}" (tenant_id)'
        )
        parent_columns = (
            {c["name"] for c in inspector.get_columns(parent)}
            if parent in tables
            else set()
        )
        # A parent repaired earlier in this loop gained the column after the
        # inspector snapshot, so refresh before deciding whether to backfill.
        if parent in REPAIR:
            parent_columns.add("tenant_id")
        if "tenant_id" in parent_columns and fk_column in columns:
            op.execute(
                f'UPDATE "{table}" t SET tenant_id = p.tenant_id FROM "{parent}" p '  # nosec B608
                f"WHERE t.{fk_column} = p.id AND t.tenant_id IS NULL"
            )  # identifiers come from the frozen REPAIR constant, never from input
        logger.warning(
            "rls: added tenant_id to %s (alembic/model schema drift repaired)", table
        )


def downgrade() -> None:
    """No-op by design: the added ownership columns are never dropped (non-destructive)."""
    return
