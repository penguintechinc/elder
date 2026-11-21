"""Global Village ID uniqueness registry, durable PG sequence, and stream_webhooks backfill.

Revision ID: 046
Revises: 045
Create Date: 2026-11-20
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "046"
down_revision = "045"
branch_labels = None
depends_on = None

# All 64 trackable resource tables with deterministic table codes (1..64).
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
    ("stream_webhooks", 64),
)

_INT_PG_TYPES = frozenset({"integer", "bigint", "smallint"})

_TRIGGER_FUNCTION_SQL = """
CREATE OR REPLACE FUNCTION sync_village_ids_registry()
RETURNS TRIGGER AS $$
DECLARE
    v_tenant_id INTEGER := 1;
    v_raw_tenant TEXT;
BEGIN
    IF TG_OP = 'DELETE' THEN
        IF OLD.village_id IS NOT NULL THEN
            DELETE FROM village_ids
            WHERE village_id = OLD.village_id
              AND table_name = TG_TABLE_NAME
              AND record_id = OLD.id::bigint;
        END IF;
        RETURN OLD;
    END IF;

    IF TG_OP = 'UPDATE' THEN
        IF OLD.village_id IS NOT DISTINCT FROM NEW.village_id THEN
            RETURN NEW;
        END IF;
        IF OLD.village_id IS NOT NULL THEN
            DELETE FROM village_ids
            WHERE village_id = OLD.village_id
              AND table_name = TG_TABLE_NAME
              AND record_id = OLD.id::bigint;
        END IF;
    END IF;

    IF NEW.village_id IS NOT NULL THEN
        BEGIN
            IF NEW.village_id ~ '^[0-9a-fA-F]{8}-' THEN
                v_tenant_id := ('x' || split_part(NEW.village_id, '-', 1))::bit(32)::int;
            ELSE
                v_raw_tenant := to_jsonb(NEW)->>'tenant_id';
                IF v_raw_tenant ~ '^[0-9]{1,9}$' THEN
                    v_tenant_id := v_raw_tenant::int;
                ELSE
                    v_tenant_id := 1;
                END IF;
            END IF;
        EXCEPTION WHEN OTHERS THEN
            v_tenant_id := 1;
        END;

        INSERT INTO village_ids (village_id, tenant_id, table_name, record_id)
        VALUES (NEW.village_id, v_tenant_id, TG_TABLE_NAME, NEW.id::bigint);
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
"""


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


def _upgrade_stream_webhooks_pg(
    conn: sa.Connection, has_orgs: bool, has_tenants: bool, has_identities: bool
) -> None:
    """Add village_id column, unique index, and collision-free backfill to stream_webhooks."""
    if not _pg_table_exists(conn, "stream_webhooks"):
        return

    conn.execute(
        sa.text(
            "ALTER TABLE stream_webhooks ADD COLUMN IF NOT EXISTS village_id VARCHAR(32)"
        )
    )
    conn.execute(
        sa.text(
            "CREATE UNIQUE INDEX IF NOT EXISTS ix_stream_webhooks_village_id "
            "ON stream_webhooks (village_id)"
        )
    )
    col_info = _pg_column_info(conn, "stream_webhooks")
    if "id" not in col_info:
        return

    tenant_expr = _build_pg_tenant_expr(
        "stream_webhooks",
        col_info,
        has_orgs=has_orgs,
        has_tenants=has_tenants,
        has_identities=has_identities,
    )
    conn.execute(
        sa.text(
            "UPDATE stream_webhooks AS t "  # nosec B608
            f"SET village_id = LPAD(TO_HEX({tenant_expr}), 8, '0') || '-' || "
            "LPAD(TO_HEX((CAST(64 AS bigint) << 40) + CAST(t.id AS bigint)), 16, '0') "
            "WHERE t.village_id IS NULL"
        )
    )


def _init_global_sequence_pg(conn: sa.Connection) -> None:
    """Create and advance village_id_global_seq above all existing < (1 << 40) suffixes."""
    conn.execute(
        sa.text(
            "CREATE SEQUENCE IF NOT EXISTS village_id_global_seq "
            "AS BIGINT START WITH 1 INCREMENT BY 1 NO MAXVALUE CACHE 1"
        )
    )

    max_seq = 0
    for table_name, _ in TRACKABLE_TABLES:
        if not _pg_table_exists(conn, table_name):
            continue
        col_info = _pg_column_info(conn, table_name)
        if "village_id" not in col_info:
            continue
        row = conn.execute(
            sa.text(
                f"SELECT COALESCE("  # nosec B608 - hardcoded allowlist
                "MAX(('x' || split_part(village_id, '-', 2))::bit(64)::bigint), 0"
                ") "
                f"FROM {table_name} "
                "WHERE village_id ~ '^[0-9a-fA-F]{8}-[0-9a-fA-F]{16}$' "
                "AND ('x' || split_part(village_id, '-', 2))::bit(64)::bigint >= 0 "
                "AND ('x' || split_part(village_id, '-', 2))::bit(64)::bigint < (1::bigint << 40)"
            )
        ).first()
        if row is not None and row[0] is not None:
            max_seq = max(max_seq, int(row[0]))

    seq_row = conn.execute(
        sa.text("SELECT last_value, is_called FROM village_id_global_seq")
    ).first()
    if seq_row is not None:
        last_val = int(seq_row[0])
        is_called = bool(seq_row[1])
        seq_floor = last_val if is_called else (last_val - 1)
        if max_seq > seq_floor:
            conn.execute(
                sa.text("SELECT setval('village_id_global_seq', :target, true)"),
                {"target": max_seq},
            )


def _create_and_backfill_village_ids_pg(
    conn: sa.Connection, has_orgs: bool, has_tenants: bool, has_identities: bool
) -> None:
    """Create central village_ids table and idempotently backfill all 64 tables."""
    conn.execute(
        sa.text(
            """
            CREATE TABLE IF NOT EXISTS village_ids (
                village_id VARCHAR(32) PRIMARY KEY,
                tenant_id INTEGER NOT NULL,
                table_name VARCHAR(64) NOT NULL,
                record_id BIGINT NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """
        )
    )
    conn.execute(
        sa.text(
            "CREATE INDEX IF NOT EXISTS ix_village_ids_tenant_table_record "
            "ON village_ids (tenant_id, table_name, record_id)"
        )
    )

    for table_name, _ in TRACKABLE_TABLES:
        if not _pg_table_exists(conn, table_name):
            continue
        col_info = _pg_column_info(conn, table_name)
        if "id" not in col_info or "village_id" not in col_info:
            continue

        tenant_expr = _build_pg_tenant_expr(
            table_name,
            col_info,
            has_orgs=has_orgs,
            has_tenants=has_tenants,
            has_identities=has_identities,
        )

        # Re-mint any pre-existing cross-table duplicate before inserting into village_ids.
        conn.execute(
            sa.text(
                f"UPDATE {table_name} AS t "  # nosec B608 - hardcoded allowlist
                "SET village_id = ("
                "CASE WHEN t.village_id ~ '^[0-9a-fA-F]{8}-' "
                "THEN LOWER(split_part(t.village_id, '-', 1)) "
                f"ELSE LPAD(TO_HEX({tenant_expr}), 8, '0') END"
                ") || '-' || LPAD(TO_HEX(nextval('village_id_global_seq')), 16, '0') "
                "WHERE t.village_id IS NOT NULL "
                "AND EXISTS ("
                "SELECT 1 FROM village_ids v "
                "WHERE v.village_id = t.village_id "
                "AND (v.table_name <> :tbl OR v.record_id <> t.id::bigint)"
                ")"
            ),
            {"tbl": table_name},
        )

        # Remove stale registry rows if rows were deleted/updated while triggers were absent.
        conn.execute(
            sa.text(
                f"DELETE FROM village_ids v "  # nosec B608 - hardcoded allowlist
                "WHERE v.table_name = :tbl "
                "AND NOT EXISTS ("
                f"SELECT 1 FROM {table_name} t "
                "WHERE t.id::bigint = v.record_id "
                "AND t.village_id = v.village_id"
                ")"
            ),
            {"tbl": table_name},
        )

        # Backfill non-NULL village_ids idempotently.
        conn.execute(
            sa.text(
                "INSERT INTO village_ids (village_id, tenant_id, table_name, record_id) "
                "SELECT t.village_id, "
                "CASE "
                "WHEN t.village_id ~ '^[0-9a-fA-F]{8}-' "
                "THEN ('x' || split_part(t.village_id, '-', 1))::bit(32)::int "
                f"ELSE ({tenant_expr})::integer "
                "END, "
                ":tbl, "
                "t.id::bigint "
                f"FROM {table_name} AS t "  # nosec B608 - hardcoded allowlist
                "WHERE t.village_id IS NOT NULL "
                "ON CONFLICT (village_id) DO NOTHING"
            ),
            {"tbl": table_name},
        )


def _install_triggers_pg(conn: sa.Connection) -> None:
    """Create sync_village_ids_registry() and attach triggers on all 64 tables."""
    conn.execute(sa.text(_TRIGGER_FUNCTION_SQL))

    for table_name, _ in TRACKABLE_TABLES:
        if not _pg_table_exists(conn, table_name):
            continue
        col_info = _pg_column_info(conn, table_name)
        if "id" not in col_info or "village_id" not in col_info:
            continue
        trg_name = f"trg_{table_name}_sync_village_ids"
        conn.execute(
            sa.text(f"DROP TRIGGER IF EXISTS {trg_name} ON {table_name}")  # nosec B608
        )
        conn.execute(
            sa.text(
                f"CREATE TRIGGER {trg_name} "  # nosec B608 - hardcoded allowlist
                f"AFTER INSERT OR UPDATE OF village_id OR DELETE ON {table_name} "
                "FOR EACH ROW EXECUTE FUNCTION sync_village_ids_registry()"
            )
        )


def _upgrade_postgres(conn: sa.Connection) -> None:
    """Apply stream_webhooks backfill, durable sequence, village_ids, and triggers on PG."""
    has_orgs = _pg_table_exists(conn, "organizations")
    has_tenants = _pg_table_exists(conn, "tenants")
    has_identities = _pg_table_exists(conn, "identities")

    _upgrade_stream_webhooks_pg(
        conn,
        has_orgs=has_orgs,
        has_tenants=has_tenants,
        has_identities=has_identities,
    )
    _init_global_sequence_pg(conn)
    _create_and_backfill_village_ids_pg(
        conn,
        has_orgs=has_orgs,
        has_tenants=has_tenants,
        has_identities=has_identities,
    )
    _install_triggers_pg(conn)


def _upgrade_generic(conn: sa.Connection) -> None:
    """Fallback upgrade path for non-PostgreSQL dialects (e.g., SQLite tests)."""
    inspector = sa.inspect(conn)
    existing_tables = set(inspector.get_table_names())

    if "stream_webhooks" in existing_tables:
        cols = {c["name"] for c in inspector.get_columns("stream_webhooks")}
        if "village_id" not in cols:
            op.add_column(
                "stream_webhooks",
                sa.Column("village_id", sa.String(32), nullable=True),
            )
        indexes = {idx["name"] for idx in inspector.get_indexes("stream_webhooks")}
        if "ix_stream_webhooks_village_id" not in indexes:
            op.create_index(
                "ix_stream_webhooks_village_id",
                "stream_webhooks",
                ["village_id"],
                unique=True,
            )
        if "id" in cols:
            rows = conn.execute(
                sa.text(
                    "SELECT id, tenant_id FROM stream_webhooks WHERE village_id IS NULL"
                )
            ).fetchall()
            for row_id, row_tid in rows:
                tid = int(row_tid or 1)
                vid = f"{tid & 0xFFFFFFFF:08x}-{(64 << 40) + int(row_id):016x}"
                conn.execute(
                    sa.text(
                        "UPDATE stream_webhooks SET village_id = :vid WHERE id = :id"
                    ),
                    {"vid": vid, "id": row_id},
                )

    if "village_ids" not in existing_tables:
        op.create_table(
            "village_ids",
            sa.Column("village_id", sa.String(32), primary_key=True),
            sa.Column("tenant_id", sa.Integer(), nullable=False),
            sa.Column("table_name", sa.String(64), nullable=False),
            sa.Column("record_id", sa.BigInteger(), nullable=False),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.text("CURRENT_TIMESTAMP"),
            ),
        )
        op.create_index(
            "ix_village_ids_tenant_table_record",
            "village_ids",
            ["tenant_id", "table_name", "record_id"],
        )

    seq_counter = 1
    for table_name, _ in TRACKABLE_TABLES:
        if table_name not in existing_tables:
            continue
        cols = {c["name"] for c in inspector.get_columns(table_name)}
        if "id" not in cols or "village_id" not in cols:
            continue
        rows = conn.execute(
            sa.text(
                f"SELECT id, village_id FROM {table_name} WHERE village_id IS NOT NULL"  # nosec B608
            )
        ).fetchall()
        for row_id, vid in rows:
            prefix = vid.split("-", 1)[0] if isinstance(vid, str) and "-" in vid else ""
            if len(prefix) == 8:
                try:
                    tid = int(prefix, 16)
                except ValueError:
                    tid = 1
            else:
                tid = 1

            existing = conn.execute(
                sa.text(
                    "SELECT table_name, record_id FROM village_ids WHERE village_id = :vid"
                ),
                {"vid": vid},
            ).first()
            if existing is not None:
                if existing[0] == table_name and int(existing[1]) == int(row_id):
                    continue
                while True:
                    candidate = f"{tid & 0xFFFFFFFF:08x}-{seq_counter:016x}"
                    seq_counter += 1
                    collision = conn.execute(
                        sa.text("SELECT 1 FROM village_ids WHERE village_id = :vid"),
                        {"vid": candidate},
                    ).first()
                    if collision is None:
                        vid = candidate
                        conn.execute(
                            sa.text(
                                f"UPDATE {table_name} SET village_id = :vid WHERE id = :id"  # nosec B608
                            ),
                            {"vid": vid, "id": row_id},
                        )
                        break

            conn.execute(
                sa.text(
                    "INSERT INTO village_ids (village_id, tenant_id, table_name, record_id) "
                    "VALUES (:vid, :tid, :tbl, :rid)"
                ),
                {"vid": vid, "tid": tid, "tbl": table_name, "rid": int(row_id)},
            )


def _downgrade_postgres(conn: sa.Connection) -> None:
    """Idempotently drop triggers, function, village_ids, sequence, and stream_webhooks.village_id."""
    for table_name, _ in reversed(TRACKABLE_TABLES):
        if not _pg_table_exists(conn, table_name):
            continue
        trg_name = f"trg_{table_name}_sync_village_ids"
        conn.execute(
            sa.text(f"DROP TRIGGER IF EXISTS {trg_name} ON {table_name}")  # nosec B608
        )

    conn.execute(sa.text("DROP FUNCTION IF EXISTS sync_village_ids_registry()"))
    conn.execute(sa.text("DROP INDEX IF EXISTS ix_village_ids_tenant_table_record"))
    conn.execute(sa.text("DROP TABLE IF EXISTS village_ids"))
    conn.execute(sa.text("DROP SEQUENCE IF EXISTS village_id_global_seq"))

    if _pg_table_exists(conn, "stream_webhooks"):
        conn.execute(sa.text("DROP INDEX IF EXISTS ix_stream_webhooks_village_id"))
        conn.execute(
            sa.text("ALTER TABLE stream_webhooks DROP COLUMN IF EXISTS village_id")
        )


def _downgrade_generic(conn: sa.Connection) -> None:
    """Idempotently drop generic artifacts for non-PostgreSQL dialects."""
    inspector = sa.inspect(conn)
    existing_tables = set(inspector.get_table_names())

    if "village_ids" in existing_tables:
        indexes = {idx["name"] for idx in inspector.get_indexes("village_ids")}
        if "ix_village_ids_tenant_table_record" in indexes:
            op.drop_index(
                "ix_village_ids_tenant_table_record", table_name="village_ids"
            )
        op.drop_table("village_ids")

    if "stream_webhooks" in existing_tables:
        indexes = {idx["name"] for idx in inspector.get_indexes("stream_webhooks")}
        if "ix_stream_webhooks_village_id" in indexes:
            op.drop_index("ix_stream_webhooks_village_id", table_name="stream_webhooks")
        cols = {c["name"] for c in inspector.get_columns("stream_webhooks")}
        if "village_id" in cols:
            op.drop_column("stream_webhooks", "village_id")


def upgrade() -> None:
    """Create platform-wide village_id uniqueness registry, sequence, and triggers."""
    conn = op.get_bind()
    if conn.dialect.name == "postgresql":
        _upgrade_postgres(conn)
    else:
        _upgrade_generic(conn)


def downgrade() -> None:
    """Drop platform-wide village_id uniqueness triggers, registry, sequence, and column."""
    conn = op.get_bind()
    if conn.dialect.name == "postgresql":
        _downgrade_postgres(conn)
    else:
        _downgrade_generic(conn)
