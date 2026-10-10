"""Enable PostgreSQL Row-Level Security for tenant isolation

Adds DB-enforced tenant isolation on top of the existing app-layer scoping.
This migration is **PostgreSQL-only** — it is a complete no-op on SQLite (and
any other backend), so the SQLite test/dev path is unaffected.

For every key tenant-scoped table that carries a direct ``tenant_id`` column we:

* ``ENABLE ROW LEVEL SECURITY``
* ``CREATE POLICY tenant_isolation`` restricting both reads (USING) and writes
  (WITH CHECK) to rows whose ``tenant_id`` matches the per-session GUC
  ``app.current_tenant_id``.

``current_setting('app.current_tenant_id', true)`` is used with ``missing_ok =
true`` so an unset GUC resolves to NULL and the policy fails closed (no rows
visible) rather than raising.

DB ROLE SETUP (required for RLS to take effect)
-----------------------------------------------
By default a table's owner (and any role with BYPASSRLS / superuser) is NOT
subject to its row-level policies. This is intentional and keeps the migration
connection and admin tooling from being locked out::

    -- migrations / admin run as the owner (bypasses RLS, not forced here)
    -- the application must connect as a dedicated NON-owner role:
    CREATE ROLE elder_app LOGIN PASSWORD '...';
    GRANT CONNECT ON DATABASE elder TO elder_app;
    GRANT USAGE ON SCHEMA public TO elder_app;
    GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO elder_app;
    GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO elder_app;
    -- elder_app must NOT be the table owner and must NOT have BYPASSRLS.

Because RLS is not FORCEd, enabling it is safe/inert until operators point the
application at such a non-owner role. Switch to ``FORCE ROW LEVEL SECURITY`` in
a follow-up once the dedicated role is in place everywhere.

Revision ID: 015
Revises: 014
Create Date: 2026-10-10
"""

from alembic import op

revision = "015"
down_revision = "014"
branch_labels = None
depends_on = None


# Key tenant-scoped tables that carry a direct ``tenant_id`` column and hold
# tenant-owned operational data. ``vulnerabilities`` is deliberately excluded
# (global NVD/CVE reference data with NULL tenant_id) — see the PR follow-ups.
RLS_TABLES = [
    "identities",
    "portal_users",
    "idp_configurations",
    "scim_configurations",
    "identity_groups",
    "organizations",
    "on_call_rotations",
    "dependencies",
    "software",
    "services",
    "ipam_prefixes",
    "ipam_addresses",
    "ipam_vlans",
    "certificates",
    "data_stores",
    "sbom_components",
    "sbom_scans",
    "component_vulnerabilities",
    "license_policies",
    "sbom_scan_schedules",
]

_TENANT_PREDICATE = "tenant_id = current_setting('app.current_tenant_id', true)::int"


def upgrade() -> None:
    """Enable RLS + tenant_isolation policy on PostgreSQL only."""
    if op.get_bind().dialect.name != "postgresql":
        return

    for table in RLS_TABLES:
        op.execute(f'ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY;')
        op.execute(
            f'CREATE POLICY tenant_isolation ON "{table}" '
            f"USING ({_TENANT_PREDICATE}) "
            f"WITH CHECK ({_TENANT_PREDICATE});"
        )


def downgrade() -> None:
    """Drop the tenant_isolation policy and disable RLS (PostgreSQL only)."""
    if op.get_bind().dialect.name != "postgresql":
        return

    for table in reversed(RLS_TABLES):
        op.execute(f'DROP POLICY IF EXISTS tenant_isolation ON "{table}";')
        op.execute(f'ALTER TABLE "{table}" DISABLE ROW LEVEL SECURITY;')
