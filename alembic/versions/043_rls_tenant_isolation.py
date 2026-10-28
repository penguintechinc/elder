"""PostgreSQL row-level security: database-enforced tenant isolation (tranche 1)

Defense in depth under the application-layer tenant scoping
(apps/api/utils/tenant_scoping.py). For every table that carries a direct
``tenant_id`` column (minus the documented deferrals below) this migration:

* ``ENABLE`` + ``FORCE ROW LEVEL SECURITY`` — FORCE so the table *owner* is
  subject to the policy too (superusers and ``BYPASSRLS`` roles always bypass;
  the application must not connect as one — see
  ``shared/database/rls.py::check_rls_posture``).
* one ``elder_tenant_isolation`` policy (``USING`` + ``WITH CHECK``): the row's
  ``tenant_id`` must equal the transaction-local GUC ``app.tenant_id`` (set by
  the data-access layer from the validated JWT / job envelope), OR the
  transaction is in explicit system scope (``app.rls_scope = 'system'``).

The predicate wraps both helper calls in scalar sub-selects
(``tenant_id = (SELECT elder_current_tenant_id()) OR (SELECT elder_rls_system_scope())``)
so PostgreSQL evaluates each once per query as an InitPlan instead of once per
row, which keeps index/bitmap scans possible (about 6x on a 1.5M-row count).

Fail closed: ``elder_current_tenant_id()`` returns NULL for an unset, empty or
non-numeric GUC, so ``tenant_id = NULL`` matches nothing and writes are
rejected. NULL-tenant (legacy/unowned) rows are invisible outside system scope,
therefore this migration PRE-FLIGHTS them: it aborts, listing counts, unless
``ELDER_RLS_NULL_TENANT_BACKFILL=default`` is set, in which case those rows are
assigned to the ``default`` tenant (idempotent; the backfill is deliberately not
undone by ``downgrade`` because un-owning rows would re-hide them).

Lock safety: ENABLE/FORCE/policy DDL takes ACCESS EXCLUSIVE, so each table runs
in its own short transaction (outside the migration transaction) under
``SET LOCAL lock_timeout`` with bounded retry; locks are never held across
tables. A timeout aborts with a clear message and a re-run resumes (idempotent).
Tunables: ``ELDER_RLS_LOCK_TIMEOUT_MS`` (5000), ``ELDER_RLS_LOCK_RETRIES`` (5),
``ELDER_RLS_LOCK_BACKOFF_S`` (2). Still, run it in a low-traffic maintenance window.

Deferred to tranche 2 (pre-auth cross-tenant lookups that must be wrapped in
``system_scope(AUTH_BOOTSTRAP)`` first): ``identities``, ``portal_users``,
``idp_configurations``, ``scim_configurations``. Org-scoped tables with no
``tenant_id`` column (``entities``, ``secrets`` ...) need a join policy and are
likewise deferred; ``identity_groups`` has no model tenant_id (documented gap).

Fails (does not warn) when an expected table is missing or lacks ``tenant_id``
unless it is in ``ABSENT_OK`` (tables only ``create_all()`` creates).

Reversible: ``downgrade`` drops every policy, ``NO FORCE`` + ``DISABLE`` RLS and
drops the helper functions. PostgreSQL only; a no-op on any other backend.

Revision ID: 043
Revises: 042
Create Date: 2026-10-28
"""

import logging
import os
import time

import sqlalchemy as sa

from alembic import op

revision = "043"
down_revision = "042"
branch_labels = None
depends_on = None

logger = logging.getLogger("alembic.runtime.migration")

POLICY_NAME = "elder_tenant_isolation"

RLS_TABLES: tuple[str, ...] = (
    "access_review_assignments",
    "access_review_items",
    "access_reviews",
    "certificates",
    "component_vulnerabilities",
    "data_stores",
    "dependencies",
    "dg_collaboration_sessions",
    "dg_collection_items",
    "dg_collection_shares",
    "dg_collections",
    "dg_comment_replies",
    "dg_comments",
    "dg_connectors",
    "dg_diagram_versions",
    "dg_diagrams",
    "dg_library_shapes",
    "dg_node_metadata",
    "dg_shape_libraries",
    "dg_shape_metadata",
    "dg_shapes",
    "dg_share_analytics",
    "dg_shares",
    "dg_storage_providers",
    "dg_templates",
    "doc_collections",
    "doc_documents",
    "dsar_requests",
    "group_access_approvals",
    "group_access_requests",
    "hd_canned_responses",
    "hd_sla_policies",
    "hd_teams",
    "hd_tickets",
    "iceflows",
    "iceflows_approvals",
    "iceflows_credentials",
    "iceflows_executions",
    "iceflows_promotions",
    "iceflows_stage_approvers",
    "iceflows_stage_calls",
    "iceflows_stage_reviews",
    "iceflows_stage_tests",
    "iceflows_stages",
    "iceflows_webhooks",
    "ipam_addresses",
    "ipam_prefixes",
    "ipam_vlans",
    "issue_comments",
    "issues",
    "license_policies",
    "milestones",
    "on_call_rotations",
    "organizations",
    "pg_pages",
    "projects",
    "references",
    "sbom_components",
    "sbom_scan_schedules",
    "sbom_scans",
    "services",
    "software",
    "stream_approval_gates",
    "stream_custom_modules",
    "stream_edges",
    "stream_editor_locks",
    "stream_execution_approvals",
    "stream_executions",
    "stream_form_submissions",
    "stream_forms",
    "stream_node_executions",
    "stream_node_metadata",
    "stream_nodes",
    "stream_playbooks",
    "stream_schedules",
    "stream_shares",
    "stream_templates",
    "stream_versions",
    "stream_webhooks",
    "tenant_modules",
    "vulnerabilities",
    "webhooks",
)

# tenant_id tables intentionally NOT covered yet (tranche 2): their pre-auth
# lookups (login, SSO, SCIM) need ``system_scope(AUTH_BOOTSTRAP)`` wrappers first.
# tests/unit/test_rls_table_registry.py fails if a tenant_id table is in neither list.
DEFERRED_TABLES: tuple[str, ...] = (
    "identities",
    "idp_configurations",
    "portal_users",
    "scim_configurations",
)

# Absent on alembic-built databases by design: only the models' create_all()
# creates them. The runtime posture check flags them if they appear unprotected.
ABSENT_OK: tuple[str, ...] = ("references",)

# One-sub-select-per-helper form: evaluated once per query (InitPlan), not per row.
_PREDICATE = "tenant_id = (SELECT elder_current_tenant_id()) OR (SELECT elder_rls_system_scope())"

_BACKFILL_ENV = "ELDER_RLS_NULL_TENANT_BACKFILL"
_LOCK_STATE_LOCK_NOT_AVAILABLE = "55P03"
_LOCK_STATE_DEADLOCK = "40P01"


def _is_postgres() -> bool:
    """Return True when the migration is running against PostgreSQL."""
    return op.get_bind().dialect.name == "postgresql"


def _int_env(name: str, default: int) -> int:
    """Read a positive integer tunable from the environment, else ``default``."""
    raw = os.environ.get(name, "").strip()
    return int(raw) if raw.isdigit() and int(raw) > 0 else default


def _run_table_ddl(table: str, statements: list[str]) -> None:
    """Run ``statements`` for one table in its own transaction with lock_timeout + retry.

    Must be called inside ``autocommit_block`` so locks are held only for this
    table. Retries lock timeouts/deadlocks with linear backoff, then raises a
    RuntimeError naming the table so the operator can re-run in a quieter window.
    """
    timeout_ms = _int_env("ELDER_RLS_LOCK_TIMEOUT_MS", 5000)
    retries = _int_env("ELDER_RLS_LOCK_RETRIES", 5)
    backoff_s = _int_env("ELDER_RLS_LOCK_BACKOFF_S", 2)
    script = "; ".join(
        ["BEGIN", f"SET LOCAL lock_timeout = '{timeout_ms}ms'", *statements, "COMMIT"]
    )
    conn = op.get_bind()
    for attempt in range(1, retries + 1):
        try:
            conn.exec_driver_sql(script)
            return
        except sa.exc.DBAPIError as exc:
            conn.exec_driver_sql("ROLLBACK")
            code = getattr(exc.orig, "pgcode", None)
            if code not in (_LOCK_STATE_LOCK_NOT_AVAILABLE, _LOCK_STATE_DEADLOCK):
                raise
            logger.warning(
                "rls: lock timeout on %s (attempt %d/%d, lock_timeout=%dms)",
                table,
                attempt,
                retries,
                timeout_ms,
            )
            if attempt == retries:
                raise RuntimeError(
                    f"rls: could not lock {table} after {retries} attempts "
                    f"(lock_timeout={timeout_ms}ms); re-run in a maintenance window "
                    "(the migration is idempotent and resumes where it stopped)"
                ) from exc
            time.sleep(backoff_s * attempt)


def _preflight(inspector: sa.engine.reflection.Inspector, present: list[str]) -> None:
    """Refuse to enable RLS while NULL-tenant rows exist, unless backfill is opted in.

    NULL-tenant rows become invisible to every tenant once RLS is enforced. With
    ``ELDER_RLS_NULL_TENANT_BACKFILL=default`` they are assigned to the ``default``
    tenant instead; any other value (or unset) aborts with per-table counts.
    """
    bind = op.get_bind()
    nulls: dict[str, int] = {}
    for table in present:
        column = next(
            c for c in inspector.get_columns(table) if c["name"] == "tenant_id"
        )
        if not column.get("nullable", True):
            continue
        count = bind.execute(
            # identifiers come from the frozen RLS_TABLES constant, never from input
            sa.text(f'SELECT count(*) FROM "{table}" WHERE tenant_id IS NULL')  # nosec B608
        ).scalar_one()
        if count:
            nulls[table] = count
    if not nulls:
        return
    detail = ", ".join(f"{t}={n}" for t, n in sorted(nulls.items()))
    if os.environ.get(_BACKFILL_ENV, "").strip().lower() != "default":
        raise RuntimeError(
            f"rls: refusing to enable RLS: NULL tenant_id rows would become invisible "
            f"({detail}). Assign them an owner, or re-run with {_BACKFILL_ENV}=default to "
            "assign them to the 'default' tenant."
        )
    default_id = bind.execute(
        sa.text("SELECT id FROM tenants WHERE slug = 'default'")
    ).scalar()
    if default_id is None:
        raise RuntimeError(
            f"rls: {_BACKFILL_ENV}=default requested but no tenant with slug 'default' exists"
        )
    for table in nulls:
        bind.execute(
            # identifiers come from the frozen RLS_TABLES constant, never from input
            sa.text(f'UPDATE "{table}" SET tenant_id = :tid WHERE tenant_id IS NULL'),  # nosec B608
            {"tid": default_id},
        )
    logger.warning("rls: backfilled NULL tenant_id to the default tenant: %s", detail)


def upgrade() -> None:
    """Validate the schema, pre-flight NULL owners, then protect each table in turn."""
    if not _is_postgres():
        return

    inspector = sa.inspect(op.get_bind())
    existing = set(inspector.get_table_names())
    problems: list[str] = []
    present: list[str] = []
    for table in RLS_TABLES:
        if table not in existing:
            if table in ABSENT_OK:
                logger.warning(
                    "rls: %s not present (created only by create_all), skipped", table
                )
            else:
                problems.append(f"{table}: table missing")
            continue
        if "tenant_id" not in {c["name"] for c in inspector.get_columns(table)}:
            problems.append(f"{table}: no tenant_id column")
            continue
        present.append(table)
    if problems:
        raise RuntimeError(
            "rls: schema does not match the expected tenant tables (run migration 042 / "
            "fix drift first): " + "; ".join(problems)
        )

    op.execute(
        """
        CREATE OR REPLACE FUNCTION elder_current_tenant_id() RETURNS bigint
        LANGUAGE sql STABLE PARALLEL SAFE AS $$
            SELECT CASE
                WHEN current_setting('app.tenant_id', true) ~ '^[0-9]{1,18}$'
                THEN current_setting('app.tenant_id', true)::bigint
            END
        $$
        """
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION elder_rls_system_scope() RETURNS boolean
        LANGUAGE sql STABLE PARALLEL SAFE AS $$
            SELECT COALESCE(current_setting('app.rls_scope', true) = 'system', false)
        $$
        """
    )
    _preflight(inspector, present)

    with op.get_context().autocommit_block():
        for table in present:
            _run_table_ddl(
                table,
                [
                    f'DROP POLICY IF EXISTS {POLICY_NAME} ON "{table}"',
                    f'CREATE POLICY {POLICY_NAME} ON "{table}" FOR ALL '
                    f"USING ({_PREDICATE}) WITH CHECK ({_PREDICATE})",
                    f'ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY, FORCE ROW LEVEL SECURITY',
                ],
            )


def downgrade() -> None:
    """Disable RLS and drop every policy table by table, then drop the helper functions."""
    if not _is_postgres():
        return

    existing = set(sa.inspect(op.get_bind()).get_table_names())
    with op.get_context().autocommit_block():
        for table in reversed(RLS_TABLES):
            if table not in existing:
                continue
            _run_table_ddl(
                table,
                [
                    f'ALTER TABLE "{table}" NO FORCE ROW LEVEL SECURITY, DISABLE ROW LEVEL SECURITY',
                    f'DROP POLICY IF EXISTS {POLICY_NAME} ON "{table}"',
                ],
            )

    op.execute("DROP FUNCTION IF EXISTS elder_rls_system_scope()")
    op.execute("DROP FUNCTION IF EXISTS elder_current_tenant_id()")
