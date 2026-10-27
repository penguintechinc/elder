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
  transaction is in explicit system scope (``app.rls_scope = 'system'``,
  audit-logged — never a blanket role).

Fail closed: ``elder_current_tenant_id()`` returns NULL for an unset, empty or
non-numeric GUC, so ``tenant_id = NULL`` matches nothing and writes are
rejected. NULL-tenant (legacy/unowned) rows are invisible outside system scope.

Deferred to tranche 2 (pre-auth cross-tenant lookups that must be wrapped in
``system_scope(AUTH_BOOTSTRAP)`` first): ``identities``, ``portal_users``,
``idp_configurations``, ``scim_configurations``. Org-scoped tables with no
``tenant_id`` column (``entities``, ``secrets`` …) need a join policy and are
likewise deferred.

Reversible: ``downgrade`` drops every policy, ``NO FORCE`` + ``DISABLE`` RLS and
drops the helper functions. No data is touched. PostgreSQL only; a no-op on any
other backend. Tables absent from a given database are skipped (idempotent).

Revision ID: 042
Revises: 041
Create Date: 2026-10-27
"""

import logging

import sqlalchemy as sa

from alembic import op

revision = "042"
down_revision = "041"
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

_PREDICATE = "tenant_id = elder_current_tenant_id() OR elder_rls_system_scope()"


def _is_postgres() -> bool:
    """Return True when the migration is running against PostgreSQL."""
    return op.get_bind().dialect.name == "postgresql"


def upgrade() -> None:
    """Create helper functions, then enable + force RLS with one policy per table."""
    if not _is_postgres():
        return

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

    inspector = sa.inspect(op.get_bind())
    existing = set(inspector.get_table_names())
    unprotected: list[str] = []
    for table in RLS_TABLES:
        if table not in existing:
            logger.warning("rls: table %s not present, skipped", table)
            continue
        columns = {c["name"] for c in inspector.get_columns(table)}
        if "tenant_id" not in columns:
            # Alembic-built databases drift from the models (which create_all()
            # uses): some tables never received tenant_id. We cannot write a
            # tenant policy without the column, so surface it loudly instead of
            # failing every upgrade or guessing a scoping column.
            unprotected.append(table)
            continue
        op.execute(f'ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY')
        op.execute(f'ALTER TABLE "{table}" FORCE ROW LEVEL SECURITY')
        op.execute(f'DROP POLICY IF EXISTS {POLICY_NAME} ON "{table}"')
        op.execute(
            f'CREATE POLICY {POLICY_NAME} ON "{table}" FOR ALL '
            f"USING ({_PREDICATE}) WITH CHECK ({_PREDICATE})"
        )
    if unprotected:
        logger.warning(
            "rls: %d table(s) have no tenant_id column in this database (schema "
            "drift) and are NOT protected by RLS: %s",
            len(unprotected),
            ", ".join(unprotected),
        )


def downgrade() -> None:
    """Drop every policy, disable RLS, then drop the helper functions."""
    if not _is_postgres():
        return

    existing = set(sa.inspect(op.get_bind()).get_table_names())
    for table in reversed(RLS_TABLES):
        if table not in existing:
            continue
        op.execute(f'DROP POLICY IF EXISTS {POLICY_NAME} ON "{table}"')
        op.execute(f'ALTER TABLE "{table}" NO FORCE ROW LEVEL SECURITY')
        op.execute(f'ALTER TABLE "{table}" DISABLE ROW LEVEL SECURITY')

    op.execute("DROP FUNCTION IF EXISTS elder_rls_system_scope()")
    op.execute("DROP FUNCTION IF EXISTS elder_current_tenant_id()")
