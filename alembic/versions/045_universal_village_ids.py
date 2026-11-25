"""Universal Village IDs: add columns, widen legacy VARCHAR(18), and backfill all trackable tables.

Revision ID: 045
Revises: 044
Create Date: 2026-11-20
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "045"
down_revision = "044"
branch_labels = None
depends_on = None

# Top-level resource tables newly gaining a village_id column in this migration.
NEW_VILLAGE_ID_TABLES: tuple[str, ...] = (
    "networking_resources",
    "geo_regions",
    "geo_locations",
    "secrets",
    "builtin_secrets",
    "secret_providers",
    "api_keys",
    "crypto_keys",
    "key_providers",
    "backups",
    "backup_jobs",
    "identity_groups",
    "portal_users",
    "labels",
    "issue_labels",
    "cloud_accounts",
    "discovery_jobs",
    "sync_configs",
    "iam_providers",
    "google_workspace_providers",
    "alert_configurations",
    "notification_rules",
    "hd_sla_policies",
)

# All trackable resource tables with a unique deterministic table_code (1..63).
# Backfill format:
#   LPAD(TO_HEX(tenant_id), 8, '0') || '-' || LPAD(TO_HEX((table_code << 40) + id), 16, '0')
# Each table_code occupies bits 40..62 of the 64-bit object suffix, making
# backfilled IDs globally collision-free across tables and disjoint from
# sequential Redis INCR counters (< 2^40).
TRACKABLE_TABLES: tuple[tuple[str, int], ...] = (
    ("tenants", 1),
    ("organizations", 2),
    ("entities", 3),
    ("identities", 4),
    ("identity_groups", 5),
    ("portal_users", 6),
    ("software", 7),
    ("services", 8),
    ("ipam_prefixes", 9),
    ("ipam_addresses", 10),
    ("ipam_vlans", 11),
    ("issues", 12),
    ("projects", 13),
    ("milestones", 14),
    ("labels", 15),
    ("doc_documents", 16),
    ("doc_collections", 17),
    ("pg_pages", 18),
    ("dg_diagrams", 19),
    ("dg_collections", 20),
    ("dg_templates", 21),
    ("stream_playbooks", 22),
    ("stream_templates", 23),
    ("stream_custom_modules", 24),
    ("iceflows", 25),
    ("hd_tickets", 26),
    ("hd_teams", 27),
    ("hd_sla_policies", 28),
    ("data_stores", 29),
    ("dependencies", 30),
    ("networking_resources", 31),
    ("geo_regions", 32),
    ("geo_locations", 33),
    ("vulnerabilities", 34),
    ("sbom_scans", 35),
    ("sbom_components", 36),
    ("sbom_scan_schedules", 37),
    ("certificates", 38),
    ("license_policies", 39),
    ("on_call_rotations", 40),
    ("ai_resources", 41),
    ("access_reviews", 42),
    ("group_access_requests", 43),
    ("resource_roles", 44),
    ("webhooks", 45),
    ("inbound_webhooks", 46),
    ("secrets", 47),
    ("builtin_secrets", 48),
    ("secret_providers", 49),
    ("api_keys", 50),
    ("crypto_keys", 51),
    ("key_providers", 52),
    ("backups", 53),
    ("backup_jobs", 54),
    ("cloud_accounts", 55),
    ("discovery_jobs", 56),
    ("sync_configs", 57),
    ("iam_providers", 58),
    ("google_workspace_providers", 59),
    ("alert_configurations", 60),
    ("notification_rules", 61),
    ("metadata_fields", 62),
    ("issue_labels", 63),
)

_INT_PG_TYPES = frozenset({"integer", "bigint", "smallint"})


def _pg_table_exists(conn: sa.Connection, table_name: str) -> bool:
    """Return True if ``table_name`` exists in the current PostgreSQL schema."""
    row = conn.execute(
        sa.text(
            "SELECT 1 FROM information_schema.tables "
            "WHERE table_schema = current_schema() AND table_name = :tbl"
        ),
        {"tbl": table_name},
    ).first()
    return row is not None


def _pg_column_info(
    conn: sa.Connection, table_name: str
) -> dict[str, tuple[str, int | None]]:
    """Return ``{column_name: (data_type, character_maximum_length)}`` for a PG table."""
    rows = conn.execute(
        sa.text(
            "SELECT column_name, data_type, character_maximum_length "
            "FROM information_schema.columns "
            "WHERE table_schema = current_schema() AND table_name = :tbl"
        ),
        {"tbl": table_name},
    ).fetchall()
    return {r[0]: (r[1], r[2]) for r in rows}


def _build_pg_tenant_expr(
    table_name: str,
    col_info: dict[str, tuple[str, int | None]],
    has_orgs: bool,
    has_tenants: bool,
    has_identities: bool,
) -> str:
    """Build a PostgreSQL BIGINT expression resolving a row's owning tenant_id."""
    if table_name == "tenants":
        return "COALESCE(t.id, 1)::bigint"

    org_subquery = (
        "(SELECT o.tenant_id FROM organizations o WHERE o.id = t.organization_id)"
    )

    if "tenant_id" in col_info:
        dtype = col_info["tenant_id"][0].lower()
        if dtype in _INT_PG_TYPES:
            if "organization_id" in col_info and has_orgs:
                return f"COALESCE(t.tenant_id, {org_subquery}, 1)::bigint"
            return "COALESCE(t.tenant_id, 1)::bigint"
        # String tenant_id (e.g., geo_regions / geo_locations)
        slug_fallback = (
            "(SELECT tn.id FROM tenants tn WHERE tn.slug = t.tenant_id LIMIT 1)"
            if has_tenants
            else "NULL"
        )
        if "organization_id" in col_info and has_orgs:
            fallback = f"COALESCE({org_subquery}, {slug_fallback}, 1)::bigint"
        else:
            fallback = f"COALESCE({slug_fallback}, 1)::bigint"
        return (
            "CASE WHEN t.tenant_id ~ '^[0-9]+$' "
            f"THEN t.tenant_id::bigint ELSE {fallback} END"
        )

    if "organization_id" in col_info and has_orgs:
        return f"COALESCE({org_subquery}, 1)::bigint"

    if table_name == "api_keys" and "identity_id" in col_info and has_identities:
        return (
            "COALESCE("
            "(SELECT i.tenant_id FROM identities i WHERE i.id = t.identity_id), 1"
            ")::bigint"
        )

    if table_name == "discovery_jobs" and "config_json" in col_info and has_orgs:
        return (
            "CASE WHEN (t.config_json->>'_organization_id') ~ '^[0-9]+$' "
            "THEN COALESCE("
            "(SELECT o.tenant_id FROM organizations o "
            "WHERE o.id = (t.config_json->>'_organization_id')::integer), 1"
            ")::bigint ELSE 1::bigint END"
        )

    return "1::bigint"


def _upgrade_postgres(conn: sa.Connection) -> None:
    """Idempotently add columns, widen legacy VARCHAR(18), index, and backfill on PostgreSQL."""
    has_orgs = _pg_table_exists(conn, "organizations")
    has_tenants = _pg_table_exists(conn, "tenants")
    has_identities = _pg_table_exists(conn, "identities")

    for table_name, table_code in TRACKABLE_TABLES:
        if not _pg_table_exists(conn, table_name):
            continue

        # 1. Add village_id VARCHAR(32) if missing (safe on both new and drifted tables)
        conn.execute(
            # nosemgrep: python.sqlalchemy.security.audit.avoid-sqlalchemy-text.avoid-sqlalchemy-text -- constant DDL/DML; only identifiers from the migration's hardcoded table allowlist are interpolated, values are bound
            sa.text(
                f"ALTER TABLE {table_name} "  # nosec B608 - hardcoded allowlist
                "ADD COLUMN IF NOT EXISTS village_id VARCHAR(32)"
            )
        )

        # 2. Widen any legacy VARCHAR(18) (or narrower) village_id columns to VARCHAR(32)
        col_info = _pg_column_info(conn, table_name)
        vid_info = col_info.get("village_id")
        if vid_info is not None and vid_info[1] is not None and vid_info[1] < 32:
            conn.execute(
                # nosemgrep: python.sqlalchemy.security.audit.avoid-sqlalchemy-text.avoid-sqlalchemy-text -- constant DDL/DML; only identifiers from the migration's hardcoded table allowlist are interpolated, values are bound
                sa.text(
                    f"ALTER TABLE {table_name} "  # nosec B608 - hardcoded allowlist
                    "ALTER COLUMN village_id TYPE VARCHAR(32)"
                )
            )

        # 3. Ensure unique index exists
        conn.execute(
            # nosemgrep: python.sqlalchemy.security.audit.avoid-sqlalchemy-text.avoid-sqlalchemy-text -- constant DDL/DML; only identifiers from the migration's hardcoded table allowlist are interpolated, values are bound
            sa.text(
                f"CREATE UNIQUE INDEX IF NOT EXISTS ix_{table_name}_village_id "  # nosec B608
                f"ON {table_name} (village_id)"
            )
        )

        # 4. Backfill NULL village_id rows using collision-free 25-char lowercase hex
        if "id" not in col_info:
            continue

        tenant_expr = _build_pg_tenant_expr(
            table_name,
            col_info,
            has_orgs=has_orgs,
            has_tenants=has_tenants,
            has_identities=has_identities,
        )
        conn.execute(
            # nosemgrep: python.sqlalchemy.security.audit.avoid-sqlalchemy-text.avoid-sqlalchemy-text -- constant DDL/DML; only identifiers from the migration's hardcoded table allowlist are interpolated, values are bound
            sa.text(
                f"UPDATE {table_name} AS t "  # nosec B608 - hardcoded allowlist
                f"SET village_id = LPAD(TO_HEX({tenant_expr}), 8, '0') || '-' || "
                "LPAD(TO_HEX((CAST(:table_code AS bigint) << 40) + CAST(t.id AS bigint)), 16, '0') "
                "WHERE t.village_id IS NULL"
            ),
            {"table_code": table_code},
        )


def _upgrade_generic(conn: sa.Connection) -> None:
    """Fallback upgrade path for non-PostgreSQL dialects (e.g. SQLite tests)."""
    inspector = sa.inspect(conn)
    existing_tables = set(inspector.get_table_names())

    for table_name, table_code in TRACKABLE_TABLES:
        if table_name not in existing_tables:
            continue
        cols = {c["name"] for c in inspector.get_columns(table_name)}
        if "village_id" not in cols:
            op.add_column(
                table_name,
                sa.Column("village_id", sa.String(32), nullable=True),
            )
        indexes = {idx["name"] for idx in inspector.get_indexes(table_name)}
        idx_name = f"ix_{table_name}_village_id"
        if idx_name not in indexes:
            op.create_index(idx_name, table_name, ["village_id"], unique=True)

        if "id" not in cols:
            continue

        rows = conn.execute(
            # nosemgrep: python.sqlalchemy.security.audit.avoid-sqlalchemy-text.avoid-sqlalchemy-text -- constant DDL/DML; only identifiers from the migration's hardcoded table allowlist are interpolated, values are bound
            sa.text(
                f"SELECT id FROM {table_name} WHERE village_id IS NULL"  # nosec B608
            )
        ).fetchall()
        for (row_id,) in rows:
            tenant_id = row_id if table_name == "tenants" else 1
            vid = f"{int(tenant_id) & 0xFFFFFFFF:08x}-{(table_code << 40) + int(row_id):016x}"
            conn.execute(
                # nosemgrep: python.sqlalchemy.security.audit.avoid-sqlalchemy-text.avoid-sqlalchemy-text -- constant DDL/DML; only identifiers from the migration's hardcoded table allowlist are interpolated, values are bound
                sa.text(
                    f"UPDATE {table_name} SET village_id = :vid WHERE id = :id"  # nosec B608
                ),
                {"vid": vid, "id": row_id},
            )


def upgrade() -> None:
    """Add village_id columns/indexes, widen legacy columns, and backfill NULL rows."""
    conn = op.get_bind()
    if conn.dialect.name == "postgresql":
        _upgrade_postgres(conn)
    else:
        _upgrade_generic(conn)


def downgrade() -> None:
    """Drop newly added village_id columns and indexes (idempotent)."""
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    existing_tables = set(inspector.get_table_names())

    for table_name in reversed(NEW_VILLAGE_ID_TABLES):
        if table_name not in existing_tables:
            continue
        idx_name = f"ix_{table_name}_village_id"
        indexes = {idx["name"] for idx in inspector.get_indexes(table_name)}
        if idx_name in indexes:
            op.drop_index(idx_name, table_name=table_name)
        cols = {c["name"] for c in inspector.get_columns(table_name)}
        if "village_id" in cols:
            op.drop_column(table_name, "village_id")
