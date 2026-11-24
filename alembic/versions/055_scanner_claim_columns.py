"""Add dedicated scanner-claim columns to discovery_jobs and sbom_scans.

Scanner claims (which replica owns a running job, and until when) get their
own columns -- ``claim_token_hash`` and ``claim_expires_at`` -- instead of
overloading ``discovery_jobs.next_run_at`` as a lease deadline. With the
lease in ``next_run_at`` a one-time job that had already completed could
look like an expired claim and be re-queued and re-executed.

NOTE (merge-time): revisions 053 and 054 are in flight on other branches,
both also with ``down_revision = "052"``. This revision is parked on 052 so
it applies standalone; re-point ``down_revision`` at the real head when
those land, otherwise alembic will see multiple heads.

Revision ID: 055
Revises: 052
Create Date: 2026-11-24
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "055"
down_revision = "052"
branch_labels = None
depends_on = None

_TABLES = ("discovery_jobs", "sbom_scans")
_SCANNER_PROVIDERS = "('network', 'http_screenshot', 'banner')"

# One-time scanner jobs still running under the old lease-in-next_run_at
# scheme: carry the lease over to the new column so a dead claimant's job is
# still recovered. The placeholder is not a SHA-256 hex digest, so no token
# can ever match it -- such a job can only be re-queued, never completed by
# a caller presenting a token.
_CARRY_OVER_LIVE_LEASES = f"""
    UPDATE discovery_jobs
    SET claim_expires_at = next_run_at,
        claim_token_hash = 'migrated-from-next-run-at'
    WHERE schedule_interval = 0
      AND provider IN {_SCANNER_PROVIDERS}
      AND last_run_at IS NOT NULL
      AND next_run_at IS NOT NULL
      AND EXISTS (
          SELECT 1 FROM discovery_history h
          WHERE h.job_id = discovery_jobs.id AND h.status = 'running'
      )
"""  # nosec B608 - constant SQL, no user input

# next_run_at has no scheduling meaning for a one-time scanner job (it is
# pending only while last_run_at IS NULL), so any value left on one that has
# already run is a stale lease/queue marker: clear it so nothing can ever
# read it as an expired claim again.
_CLEAR_STALE_LEASES = f"""
    UPDATE discovery_jobs
    SET next_run_at = NULL
    WHERE schedule_interval = 0
      AND provider IN {_SCANNER_PROVIDERS}
      AND last_run_at IS NOT NULL
      AND next_run_at IS NOT NULL
"""  # nosec B608 - constant SQL, no user input


def _columns(table: str) -> set[str]:
    """Return the existing column names of ``table`` (empty if absent)."""
    inspector = sa.inspect(op.get_bind())
    if table not in set(inspector.get_table_names()):
        return set()
    return {col["name"] for col in inspector.get_columns(table)}


def upgrade() -> None:
    """Add claim columns, then migrate/clear leases stored in next_run_at."""
    for table in _TABLES:
        existing = _columns(table)
        if not existing:
            continue
        if "claim_token_hash" not in existing:
            op.add_column(
                table, sa.Column("claim_token_hash", sa.String(64), nullable=True)
            )
        if "claim_expires_at" not in existing:
            op.add_column(
                table,
                sa.Column(
                    "claim_expires_at", sa.DateTime(timezone=True), nullable=True
                ),
            )

    if _columns("discovery_jobs") and _columns("discovery_history"):
        op.execute(sa.text(_CARRY_OVER_LIVE_LEASES))
        op.execute(sa.text(_CLEAR_STALE_LEASES))


def downgrade() -> None:
    """Drop the claim columns (in-flight claims are forgotten)."""
    for table in _TABLES:
        existing = _columns(table)
        for column in ("claim_expires_at", "claim_token_hash"):
            if column in existing:
                op.drop_column(table, column)
