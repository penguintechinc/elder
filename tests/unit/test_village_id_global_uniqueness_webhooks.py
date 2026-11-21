"""Platform-wide Village ID uniqueness, migration 046, and webhook coverage tests.

Covers:
1. Migration 046 metadata, upgrade idempotency, backfill, duplicate repair,
   sequence initialization, and downgrade/upgrade round-trips on PostgreSQL.
2. PostgreSQL trigger enforcement preventing cross-table `village_id` collisions
   across all trackable tables, plus UPDATE/DELETE cleanup and legacy IDs.
3. Redis-flush immunity when `mint_village_id()` allocates via `village_id_global_seq`.
4. O(1) `resolve_by_village_id()` via `village_ids`, tenant fail-closed behavior,
   and graceful fallback when `village_ids` is absent (SQLite unit tests).
5. Create/list/get API routes + `GET /api/v1/id/<village_id>` resolution for all
   webhook & alerting resources: `webhooks`, `inbound_webhooks`,
   `notification_rules`, `alert_configurations`, and `stream_webhooks`.
"""

from __future__ import annotations

import importlib.util
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch
from uuid import uuid4

import jwt
import pytest
from sqlalchemy import event, text
from sqlalchemy.exc import IntegrityError

from apps.api.common.refs.registry import (
    format_resolvable_url,
    get_type,
    get_type_by_table,
    resolve_by_village_id,
)
from apps.api.utils.village_id import mint_village_id
from shared.utils.village_id import is_valid_village_id, parse_village_id

REPO_ROOT = Path(__file__).resolve().parents[2]


def _load_migration(filename: str):
    """Load an Alembic migration module directly by filename."""
    path = REPO_ROOT / "alembic" / "versions" / filename
    spec = importlib.util.spec_from_file_location(f"_test_{path.stem}", path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _run_migration(engine, fn) -> None:
    """Execute an Alembic upgrade/downgrade callable inside a PG transaction."""
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    with engine.begin() as conn:
        ctx = MigrationContext.configure(conn)
        with Operations.context(ctx):
            fn()


def _is_postgres(database_url: str) -> bool:
    return str(database_url).startswith("postgresql")


def _seed_tenant_org_identity(db) -> tuple[int, int, int]:
    """Create an isolated tenant, organization, and admin identity via PyDAL."""
    now = datetime.now(UTC)
    tenant_id = db.tenants.insert(
        name=f"VID Global Tenant {uuid4().hex[:8]}",
        slug=f"vid-global-{uuid4().hex[:10]}",
        is_active=True,
        created_at=now,
        updated_at=now,
    )
    org_id = db.organizations.insert(
        name=f"VID Global Org {uuid4().hex[:8]}",
        tenant_id=tenant_id,
        village_id=mint_village_id(tenant_id, None, db=db),
        created_at=now,
        updated_at=now,
    )
    identity_id = db.identities.insert(
        username=f"vid-global-{uuid4().hex[:8]}",
        email=f"vid-global-{uuid4().hex[:10]}@example.test",
        identity_type="human",
        auth_provider="local",
        is_active=True,
        is_superuser=True,
        mfa_enabled=False,
        must_change_password=False,
        portal_role="admin",
        tenant_id=tenant_id,
        village_id=mint_village_id(tenant_id, None, db=db),
        created_at=now,
        updated_at=now,
    )
    db.commit()
    return int(tenant_id), int(org_id), int(identity_id)


def _make_jwt(app, tenant_id: int, identity_id: int, scopes: list[str]) -> str:
    """Mint an admin JWT containing both tenant and numeric identity_id claims."""
    secret = app.config.get("JWT_SECRET_KEY") or app.config.get("SECRET_KEY")
    algorithm = app.config.get("JWT_ALGORITHM", "HS256")
    now = datetime.now(UTC)
    payload = {
        "sub": str(identity_id),
        "identity_id": identity_id,
        "tenant": str(tenant_id),
        "iat": now,
        "exp": now + timedelta(hours=1),
        "scope": scopes,
        "roles": ["admin"],
    }
    return jwt.encode(payload, secret, algorithm=algorithm)


def _seed_stream(db, tenant_id: int, identity_id: int) -> int:
    """Insert a StreamPlaybook row and return its ID."""
    now = datetime.now(UTC)
    stream_id = db.stream_playbooks.insert(
        tenant_id=tenant_id,
        village_id=mint_village_id(tenant_id, None, db=db),
        name=f"Webhook Stream {uuid4().hex[:6]}",
        description="Test stream for stream webhook village_id coverage",
        owner_identity_id=identity_id,
        created_by_identity_id=identity_id,
        trigger_type="webhook",
        is_public=False,
        is_template=False,
        is_enabled=True,
        tags=["webhook-test"],
        status="active",
        execution_count=0,
        success_count=0,
        failure_count=0,
        created_at=now,
        updated_at=now,
    )
    db.commit()
    return int(stream_id)


class TestMigration046Metadata:
    """Static metadata checks for alembic/versions/046_village_id_global_uniqueness.py."""

    def test_revision_chain_and_64_trackable_tables(self):
        m046 = _load_migration("046_village_id_global_uniqueness.py")
        assert m046.revision == "046"
        assert m046.down_revision == "045"

        tables = m046.TRACKABLE_TABLES
        assert len(tables) == 64

        names = [t for t, _ in tables]
        codes = [c for _, c in tables]
        assert len(set(names)) == 64
        assert len(set(codes)) == 64
        assert set(codes) == set(range(1, 65))
        assert ("stream_webhooks", 64) in tables
        assert ("notification_rules", 61) in tables
        assert ("alert_configurations", 60) in tables
        assert ("webhooks", 45) in tables
        assert ("inbound_webhooks", 46) in tables


class TestPostgreSQLGlobalUniqueness:
    """Real PostgreSQL tests for migration 046, triggers, sequence, and Redis resilience."""

    def test_046_upgrade_idempotent_duplicate_repair_and_downgrade_roundtrip(
        self, app, test_database_url
    ):
        """Verify 046 upgrade backfills stream_webhooks, repairs duplicates,
        seeds village_id_global_seq safely above sequential suffixes, is twice
        idempotent, downgrades cleanly, and upgrades back cleanly.
        """
        if not _is_postgres(test_database_url):
            pytest.skip("PostgreSQL required for migration 046 test")

        m046 = _load_migration("046_village_id_global_uniqueness.py")
        db = app.db
        engine = db.engine

        # Start from a clean pre-046 schema state.
        _run_migration(engine, m046.downgrade)

        t1, org1, ident1 = _seed_tenant_org_identity(db)
        t2, org2, _ = _seed_tenant_org_identity(db)
        stream_id = _seed_stream(db, t1, ident1)

        # Simulate pre-046 DB lacking stream_webhooks.village_id column.
        with engine.begin() as conn:
            conn.execute(
                text(
                    "DROP INDEX IF EXISTS ix_stream_webhooks_village_id; "
                    "ALTER TABLE stream_webhooks DROP COLUMN IF EXISTS village_id;"
                )
            )

        dup_vid = f"{t1:08x}-{0x0123ABCD:016x}"
        high_seq_vid = f"{t2:08x}-{600000:016x}"
        sw_token = f"sw-tok-{uuid4().hex}"
        now = datetime.now(UTC)

        with engine.begin() as conn:
            sw_id = conn.execute(
                text(
                    "INSERT INTO stream_webhooks "
                    "(tenant_id, playbook_id, name, token, validate_signature, "
                    "is_active, is_enabled, trigger_count, created_at, updated_at) "
                    "VALUES (:tid, :pid, 'Pre046 Hook', :tok, false, true, true, 0, :now, :now) "
                    "RETURNING id"
                ),
                {"tid": t1, "pid": stream_id, "tok": sw_token, "now": now},
            ).scalar_one()

            issue_id = conn.execute(
                text(
                    "INSERT INTO issues "
                    "(tenant_id, village_id, resource_type, resource_id, title, "
                    "status, priority, issue_type, is_incident, created_at, updated_at) "
                    "VALUES (:tid, :vid, 'organization', :oid, 'Dup Issue', "
                    "'OPEN', 'MEDIUM', 'OTHER', 0, :now, :now) RETURNING id"
                ),
                {"tid": t1, "vid": dup_vid, "oid": org1, "now": now},
            ).scalar_one()

            wh_dup_id = conn.execute(
                text(
                    "INSERT INTO webhooks "
                    "(tenant_id, village_id, organization_id, name, url, events, "
                    "is_active, created_at, updated_at) "
                    "VALUES (:tid, :vid, :oid, 'Dup Webhook', 'https://example.test/dup', "
                    "CAST(:ev AS JSON), true, :now, :now) RETURNING id"
                ),
                {
                    "tid": t1,
                    "vid": dup_vid,
                    "oid": org1,
                    "ev": '["issue.created"]',
                    "now": now,
                },
            ).scalar_one()

            wh_seq_id = conn.execute(
                text(
                    "INSERT INTO webhooks "
                    "(tenant_id, village_id, organization_id, name, url, events, "
                    "is_active, created_at, updated_at) "
                    "VALUES (:tid, :vid, :oid, 'Seq Webhook', 'https://example.test/seq', "
                    "CAST(:ev AS JSON), true, :now, :now) RETURNING id"
                ),
                {
                    "tid": t2,
                    "vid": high_seq_vid,
                    "oid": org2,
                    "ev": '["issue.created"]',
                    "now": now,
                },
            ).scalar_one()

        try:
            # First upgrade
            _run_migration(engine, m046.upgrade)

            # Double-run idempotency check
            _run_migration(engine, m046.upgrade)

            with engine.connect() as conn:
                sw_vid = conn.execute(
                    text("SELECT village_id FROM stream_webhooks WHERE id = :id"),
                    {"id": sw_id},
                ).scalar_one()
                assert is_valid_village_id(sw_vid)
                parsed_sw = parse_village_id(sw_vid)
                assert parsed_sw.tenant_id == t1
                assert parsed_sw.object_seq == ((64 << 40) | sw_id)

                iss_vid = conn.execute(
                    text("SELECT village_id FROM issues WHERE id = :id"),
                    {"id": issue_id},
                ).scalar_one()
                wh_dup_vid = conn.execute(
                    text("SELECT village_id FROM webhooks WHERE id = :id"),
                    {"id": wh_dup_id},
                ).scalar_one()
                assert iss_vid != wh_dup_vid
                assert is_valid_village_id(iss_vid)
                assert is_valid_village_id(wh_dup_vid)
                assert dup_vid in {iss_vid, wh_dup_vid}

                # Both repaired entries and stream_webhook are in village_ids
                reg_count = conn.execute(
                    text(
                        "SELECT COUNT(*) FROM village_ids "
                        "WHERE village_id IN (:v1, :v2, :v3, :v4)"
                    ),
                    {
                        "v1": sw_vid,
                        "v2": iss_vid,
                        "v3": wh_dup_vid,
                        "v4": high_seq_vid,
                    },
                ).scalar_one()
                assert reg_count == 4

                seq_val = conn.execute(
                    text("SELECT nextval('village_id_global_seq')")
                ).scalar_one()
                assert seq_val > 600000

            # Downgrade cleanly removes triggers, function, table, seq, stream_webhooks.village_id
            _run_migration(engine, m046.downgrade)

            with engine.connect() as conn:
                has_table = conn.execute(
                    text("SELECT to_regclass('public.village_ids') IS NOT NULL")
                ).scalar_one()
                has_seq = conn.execute(
                    text(
                        "SELECT to_regclass('public.village_id_global_seq') IS NOT NULL"
                    )
                ).scalar_one()
                has_fn = conn.execute(
                    text(
                        "SELECT EXISTS("
                        "SELECT 1 FROM pg_proc WHERE proname = 'sync_village_ids_registry'"
                        ")"
                    )
                ).scalar_one()
                has_sw_col = conn.execute(
                    text(
                        "SELECT EXISTS("
                        "SELECT 1 FROM information_schema.columns "
                        "WHERE table_schema = 'public' "
                        "AND table_name = 'stream_webhooks' "
                        "AND column_name = 'village_id'"
                        ")"
                    )
                ).scalar_one()
                assert not has_table
                assert not has_seq
                assert not has_fn
                assert not has_sw_col

            # Re-upgrade cleanly restores everything
            _run_migration(engine, m046.upgrade)
        finally:
            with engine.begin() as conn:
                conn.execute(
                    text("DELETE FROM stream_webhooks WHERE id = :id"), {"id": sw_id}
                )
                conn.execute(
                    text("DELETE FROM webhooks WHERE id IN (:id1, :id2)"),
                    {"id1": wh_dup_id, "id2": wh_seq_id},
                )
                conn.execute(
                    text("DELETE FROM issues WHERE id = :id"), {"id": issue_id}
                )
                conn.execute(
                    text("DELETE FROM stream_playbooks WHERE id = :id"),
                    {"id": stream_id},
                )
            _run_migration(engine, m046.upgrade)

    def test_cross_table_duplicate_rejected_by_trigger_and_cleanup_works(
        self, app, test_database_url
    ):
        """Inserting identical village_id into two tables raises IntegrityError,
        and UPDATE/DELETE syncs village_ids properly.
        """
        if not _is_postgres(test_database_url):
            pytest.skip("PostgreSQL required for trigger uniqueness test")

        m046 = _load_migration("046_village_id_global_uniqueness.py")
        db = app.db
        engine = db.engine
        _run_migration(engine, m046.upgrade)

        t1, org1, ident1 = _seed_tenant_org_identity(db)
        t2, org2, _ = _seed_tenant_org_identity(db)
        stream_id = _seed_stream(db, t1, ident1)
        now = datetime.now(UTC)

        shared_vid = mint_village_id(t1, None, db=db)
        other_vid = mint_village_id(t1, None, db=db)
        inbound_vid = mint_village_id(t2, None, db=db)

        issue_id = None
        inbound_id = None
        sw_legacy_id = None
        try:
            with engine.begin() as conn:
                issue_id = conn.execute(
                    text(
                        "INSERT INTO issues "
                        "(tenant_id, village_id, resource_type, resource_id, title, "
                        "status, priority, issue_type, is_incident, created_at, updated_at) "
                        "VALUES (:tid, :vid, 'organization', :oid, 'Trigger Issue', "
                        "'OPEN', 'MEDIUM', 'OTHER', 0, :now, :now) RETURNING id"
                    ),
                    {"tid": t1, "vid": shared_vid, "oid": org1, "now": now},
                ).scalar_one()

            # Cross-table collision: webhooks using same village_id as issues -> IntegrityError
            with pytest.raises(IntegrityError):
                with engine.begin() as conn:
                    conn.execute(
                        text(
                            "INSERT INTO webhooks "
                            "(tenant_id, village_id, organization_id, name, url, events, "
                            "is_active, created_at, updated_at) "
                            "VALUES (:tid, :vid, :oid, 'Collider', 'https://example.test/c', "
                            "CAST(:ev AS JSON), true, :now, :now)"
                        ),
                        {
                            "tid": t2,
                            "vid": shared_vid,
                            "oid": org2,
                            "ev": '["issue.created"]',
                            "now": now,
                        },
                    )

            # Insert inbound_webhook, then collide via stream_webhooks -> IntegrityError
            with engine.begin() as conn:
                inbound_id = conn.execute(
                    text(
                        "INSERT INTO inbound_webhooks "
                        "(tenant_id, village_id, organization_id, name, slug, auth_type, "
                        "is_active, receive_count, created_at, updated_at) "
                        "VALUES (:tid, :vid, :oid, 'Inbound One', :slug, 'bearer', "
                        "true, 0, :now, :now) RETURNING id"
                    ),
                    {
                        "tid": t2,
                        "vid": inbound_vid,
                        "oid": org2,
                        "slug": f"inb-{uuid4().hex[:12]}",
                        "now": now,
                    },
                ).scalar_one()

            with pytest.raises(IntegrityError):
                with engine.begin() as conn:
                    conn.execute(
                        text(
                            "INSERT INTO stream_webhooks "
                            "(tenant_id, village_id, playbook_id, name, token, validate_signature, "
                            "is_active, is_enabled, trigger_count, created_at, updated_at) "
                            "VALUES (:tid, :vid, :pid, 'Stream Collider', :tok, false, "
                            "true, true, 0, :now, :now)"
                        ),
                        {
                            "tid": t1,
                            "vid": inbound_vid,
                            "pid": stream_id,
                            "tok": f"tok-{uuid4().hex}",
                            "now": now,
                        },
                    )

            # UPDATE moves village_id cleanly inside registry
            with engine.begin() as conn:
                conn.execute(
                    text("UPDATE issues SET village_id = :new_vid WHERE id = :id"),
                    {"new_vid": other_vid, "id": issue_id},
                )
                old_present = conn.execute(
                    text("SELECT COUNT(*) FROM village_ids WHERE village_id = :v"),
                    {"v": shared_vid},
                ).scalar_one()
                new_present = conn.execute(
                    text(
                        "SELECT table_name, record_id FROM village_ids WHERE village_id = :v"
                    ),
                    {"v": other_vid},
                ).fetchone()
                assert old_present == 0
                assert new_present is not None
                assert new_present[0] == "issues"
                assert int(new_present[1]) == issue_id

            # Legacy non-hex test-* village_id inserts safely into stream_webhooks without trigger failure
            legacy_vid = f"test-{uuid4().hex[:24]}"
            with engine.begin() as conn:
                sw_legacy_id = conn.execute(
                    text(
                        "INSERT INTO stream_webhooks "
                        "(tenant_id, village_id, playbook_id, name, token, validate_signature, "
                        "is_active, is_enabled, trigger_count, created_at, updated_at) "
                        "VALUES (:tid, :vid, :pid, 'Legacy VID Hook', :tok, false, "
                        "true, true, 0, :now, :now) RETURNING id"
                    ),
                    {
                        "tid": t1,
                        "vid": legacy_vid,
                        "pid": stream_id,
                        "tok": f"tok-{uuid4().hex}",
                        "now": now,
                    },
                ).scalar_one()

            # DELETE cleans up village_ids entry
            with engine.begin() as conn:
                conn.execute(
                    text("DELETE FROM issues WHERE id = :id"), {"id": issue_id}
                )
                issue_id = None
                gone = conn.execute(
                    text("SELECT COUNT(*) FROM village_ids WHERE village_id = :v"),
                    {"v": other_vid},
                ).scalar_one()
                assert gone == 0
        finally:
            with engine.begin() as conn:
                if sw_legacy_id is not None:
                    conn.execute(
                        text("DELETE FROM stream_webhooks WHERE id = :id"),
                        {"id": sw_legacy_id},
                    )
                if inbound_id is not None:
                    conn.execute(
                        text("DELETE FROM inbound_webhooks WHERE id = :id"),
                        {"id": inbound_id},
                    )
                if issue_id is not None:
                    conn.execute(
                        text("DELETE FROM issues WHERE id = :id"), {"id": issue_id}
                    )
                conn.execute(
                    text("DELETE FROM stream_playbooks WHERE id = :id"),
                    {"id": stream_id},
                )

    def test_redis_flush_does_not_cause_mint_collisions(
        self, app, test_database_url, redis_url
    ):
        """Flushing Redis does not reset village_id sequence or collide with existing rows."""
        if not _is_postgres(test_database_url):
            pytest.skip("PostgreSQL required for durable sequence test")
        if not redis_url:
            pytest.skip("REDIS_URL required for Redis flush test")

        import redis

        r = redis.Redis.from_url(redis_url, decode_responses=True)
        try:
            r.ping()
        except Exception:
            pytest.skip("Redis server unreachable")

        m046 = _load_migration("046_village_id_global_uniqueness.py")
        db = app.db
        _run_migration(db.engine, m046.upgrade)

        t1, org1, ident1 = _seed_tenant_org_identity(db)
        stream_id = _seed_stream(db, t1, ident1)
        now = datetime.now(UTC)

        v_issue = mint_village_id(t1, redis_client=r, db=db)
        v_wh = mint_village_id(t1, redis_client=r, db=db)
        v_inb = mint_village_id(t1, redis_client=r, db=db)
        v_sw = mint_village_id(t1, redis_client=r, db=db)
        pre_flush = [v_issue, v_wh, v_inb, v_sw]
        assert len(set(pre_flush)) == 4

        created_ids = {}
        with db.engine.begin() as conn:
            created_ids["issue"] = conn.execute(
                text(
                    "INSERT INTO issues "
                    "(tenant_id, village_id, resource_type, resource_id, title, "
                    "status, priority, issue_type, is_incident, created_at, updated_at) "
                    "VALUES (:tid, :vid, 'organization', :oid, 'PreFlush Issue', "
                    "'OPEN', 'MEDIUM', 'OTHER', 0, :now, :now) RETURNING id"
                ),
                {"tid": t1, "vid": v_issue, "oid": org1, "now": now},
            ).scalar_one()
            created_ids["webhook"] = conn.execute(
                text(
                    "INSERT INTO webhooks "
                    "(tenant_id, village_id, organization_id, name, url, events, "
                    "is_active, created_at, updated_at) "
                    "VALUES (:tid, :vid, :oid, 'PreFlush Webhook', 'https://example.test/pf', "
                    "CAST(:ev AS JSON), true, :now, :now) RETURNING id"
                ),
                {
                    "tid": t1,
                    "vid": v_wh,
                    "oid": org1,
                    "ev": '["issue.created"]',
                    "now": now,
                },
            ).scalar_one()
            created_ids["inbound"] = conn.execute(
                text(
                    "INSERT INTO inbound_webhooks "
                    "(tenant_id, village_id, organization_id, name, slug, auth_type, "
                    "is_active, receive_count, created_at, updated_at) "
                    "VALUES (:tid, :vid, :oid, 'PreFlush Inbound', :slug, 'bearer', "
                    "true, 0, :now, :now) RETURNING id"
                ),
                {
                    "tid": t1,
                    "vid": v_inb,
                    "oid": org1,
                    "slug": f"pf-inb-{uuid4().hex[:12]}",
                    "now": now,
                },
            ).scalar_one()
            created_ids["sw"] = conn.execute(
                text(
                    "INSERT INTO stream_webhooks "
                    "(tenant_id, village_id, playbook_id, name, token, validate_signature, "
                    "is_active, is_enabled, trigger_count, created_at, updated_at) "
                    "VALUES (:tid, :vid, :pid, 'PreFlush SW', :tok, false, "
                    "true, true, 0, :now, :now) RETURNING id"
                ),
                {
                    "tid": t1,
                    "vid": v_sw,
                    "pid": stream_id,
                    "tok": f"pf-sw-{uuid4().hex}",
                    "now": now,
                },
            ).scalar_one()

        try:
            # Wipe Redis completely and verify PG sequence preserves monotonicity!
            r.flushdb()

            post_flush = [mint_village_id(t1, redis_client=r, db=db) for _ in range(4)]
            assert len(set(pre_flush + post_flush)) == 8
            max_pre = max(parse_village_id(v).object_seq for v in pre_flush)
            min_post = min(parse_village_id(v).object_seq for v in post_flush)
            assert min_post > max_pre

            # Inserting post-flush minted IDs succeeds with zero collision
            with db.engine.begin() as conn:
                extra_wh_id = conn.execute(
                    text(
                        "INSERT INTO webhooks "
                        "(tenant_id, village_id, organization_id, name, url, events, "
                        "is_active, created_at, updated_at) "
                        "VALUES (:tid, :vid, :oid, 'PostFlush Webhook', 'https://example.test/post', "
                        "CAST(:ev AS JSON), true, :now, :now) RETURNING id"
                    ),
                    {
                        "tid": t1,
                        "vid": post_flush[0],
                        "oid": org1,
                        "ev": '["issue.created"]',
                        "now": now,
                    },
                ).scalar_one()
            with db.engine.begin() as conn:
                conn.execute(
                    text("DELETE FROM webhooks WHERE id = :id"), {"id": extra_wh_id}
                )
        finally:
            with db.engine.begin() as conn:
                conn.execute(
                    text("DELETE FROM stream_webhooks WHERE id = :id"),
                    {"id": created_ids["sw"]},
                )
                conn.execute(
                    text("DELETE FROM inbound_webhooks WHERE id = :id"),
                    {"id": created_ids["inbound"]},
                )
                conn.execute(
                    text("DELETE FROM webhooks WHERE id = :id"),
                    {"id": created_ids["webhook"]},
                )
                conn.execute(
                    text("DELETE FROM issues WHERE id = :id"),
                    {"id": created_ids["issue"]},
                )
                conn.execute(
                    text("DELETE FROM stream_playbooks WHERE id = :id"),
                    {"id": stream_id},
                )


class TestRegistryLookupAndWebhookRoutes:
    """Verify O(1) lookup and ALL webhook/alerting HTTP endpoints + /api/v1/id/."""

    def test_o1_resolve_by_village_id_and_fail_closed(self, app, test_database_url):
        """On PG with migration 046, resolve_by_village_id hits village_ids in O(1)
        instead of scanning all 64 tables, and enforces tenant isolation.
        """
        if not _is_postgres(test_database_url):
            pytest.skip("PostgreSQL required for O(1) village_ids lookup test")

        m046 = _load_migration("046_village_id_global_uniqueness.py")
        db = app.db
        _run_migration(db.engine, m046.upgrade)

        t1, org1, ident1 = _seed_tenant_org_identity(db)
        t2, _, _ = _seed_tenant_org_identity(db)
        stream_id = _seed_stream(db, t1, ident1)
        sw_vid = mint_village_id(t1, None, db=db)
        now = datetime.now(UTC)

        with db.engine.begin() as conn:
            sw_id = conn.execute(
                text(
                    "INSERT INTO stream_webhooks "
                    "(tenant_id, village_id, playbook_id, name, token, validate_signature, "
                    "is_active, is_enabled, trigger_count, created_at, updated_at) "
                    "VALUES (:tid, :vid, :pid, 'O1 Stream Hook', :tok, false, "
                    "true, true, 0, :now, :now) RETURNING id"
                ),
                {
                    "tid": t1,
                    "vid": sw_vid,
                    "pid": stream_id,
                    "tok": f"o1-{uuid4().hex}",
                    "now": now,
                },
            ).scalar_one()

        try:
            statements: list[str] = []

            def _record_sql(conn, cursor, statement, parameters, context, executemany):
                statements.append(statement)

            event.listen(db.engine, "before_cursor_execute", _record_sql)
            try:
                resolved = resolve_by_village_id(db, sw_vid, tenant_id=t1)
                assert resolved is not None
                assert resolved["type"] == "stream_webhook"
                assert resolved["id"] == sw_id
                assert resolved["table_name"] == "stream_webhooks"
                # O(1): only village_ids lookup + stream_webhooks fetch (never 64 scans)
                assert len(statements) <= 5
                joined_sql = "\n".join(statements).lower()
                assert "village_ids" in joined_sql
                assert "stream_webhooks" in joined_sql
                assert "FROM issues" not in "\n".join(statements)

                sw_type = get_type("stream_webhook")
                assert sw_type is not None
                assert (
                    format_resolvable_url(db, sw_type, resolved["id"], tenant_id=t1)
                    == f"/streams/{stream_id}"
                )

                # Fail-closed when tenant_id is None or cross-tenant
                assert resolve_by_village_id(db, sw_vid, tenant_id=None) is None
                assert resolve_by_village_id(db, sw_vid, tenant_id=t2) is None
            finally:
                event.remove(db.engine, "before_cursor_execute", _record_sql)
        finally:
            with db.engine.begin() as conn:
                conn.execute(
                    text("DELETE FROM stream_webhooks WHERE id = :id"), {"id": sw_id}
                )
                conn.execute(
                    text("DELETE FROM stream_playbooks WHERE id = :id"),
                    {"id": stream_id},
                )

    def test_registry_covers_all_webhook_and_alerting_types(self):
        for rtype, table in (
            ("webhook", "webhooks"),
            ("inbound_webhook", "inbound_webhooks"),
            ("notification_rule", "notification_rules"),
            ("alert_configuration", "alert_configurations"),
            ("stream_webhook", "stream_webhooks"),
        ):
            by_type = get_type(rtype)
            by_table = get_type_by_table(table)
            assert by_type is not None, f"Missing resolvable type {rtype}"
            assert by_table is not None, f"Missing table mapping {table}"
            assert by_type.type == by_table.type

    @pytest.mark.asyncio
    @patch("apps.api.auth.decorators.get_current_user")
    async def test_all_webhook_and_alerting_routes_mint_and_resolve(
        self, mock_get_user, async_client, app, test_database_url
    ):
        """Every webhook/alerting resource mints a standard village_id, returns it
        in create/list/get payloads, enforces tenant isolation, and resolves via
        GET /api/v1/id/<village_id>.
        """
        db = app.db
        if _is_postgres(test_database_url):
            m046 = _load_migration("046_village_id_global_uniqueness.py")
            _run_migration(db.engine, m046.upgrade)

        t1, org1, ident1 = _seed_tenant_org_identity(db)
        t2, org2, ident2 = _seed_tenant_org_identity(db)
        stream_id = _seed_stream(db, t1, ident1)
        mock_get_user.return_value = MagicMock(
            id=ident1, is_superuser=True, tenant_id=t1, portal_role="admin"
        )

        scopes = [
            "webhooks_alerting:admin",
            "webhooks_alerting:write",
            "webhooks_alerting:read",
            "webhooks:read",
            "webhooks:write",
            "webhooks:admin",
            "streams:read",
            "streams:write",
        ]
        tok_t1 = _make_jwt(app, t1, ident1, scopes)
        tok_t2 = _make_jwt(app, t2, ident2, scopes)
        h1 = {"Authorization": f"Bearer {tok_t1}"}
        h2 = {"Authorization": f"Bearer {tok_t2}"}

        minted: list[tuple[str, str, int, str]] = []

        # 1. Outbound webhooks (/api/v1/webhooks)
        resp = await async_client.post(
            "/api/v1/webhooks",
            json={
                "name": "VID Standard Webhook",
                "url": "https://hooks.example.test/standard",
                "events": ["issue.created"],
                "organization_id": org1,
            },
            headers=h1,
        )
        assert resp.status_code == 201, await resp.get_data(as_text=True)
        wh = await resp.get_json()
        assert is_valid_village_id(wh.get("village_id"))
        assert parse_village_id(wh["village_id"]).tenant_id == t1
        minted.append(("webhook", wh["village_id"], wh["id"], "/webhooks"))

        resp_list = await async_client.get("/api/v1/webhooks", headers=h1)
        assert resp_list.status_code == 200
        wh_list = (await resp_list.get_json())["webhooks"]
        assert any(
            x["id"] == wh["id"] and x["village_id"] == wh["village_id"] for x in wh_list
        )
        resp_get = await async_client.get(f"/api/v1/webhooks/{wh['id']}", headers=h1)
        assert resp_get.status_code == 200
        assert (await resp_get.get_json())["village_id"] == wh["village_id"]

        # 2. Inbound webhooks (/api/v1/webhooks/inbound)
        resp = await async_client.post(
            "/api/v1/webhooks/inbound",
            json={
                "name": "VID Inbound Hook",
                "auth_type": "bearer",
                "scopes": ["issues:create"],
                "organization_id": org1,
            },
            headers=h1,
        )
        assert resp.status_code == 201, await resp.get_data(as_text=True)
        inb_payload = await resp.get_json()
        inb = inb_payload["webhook"]
        assert is_valid_village_id(inb.get("village_id"))
        assert parse_village_id(inb["village_id"]).tenant_id == t1
        minted.append(("inbound_webhook", inb["village_id"], inb["id"], "/webhooks"))

        resp_inb_list = await async_client.get("/api/v1/webhooks/inbound", headers=h1)
        assert resp_inb_list.status_code == 200
        assert any(
            x["id"] == inb["id"] and x["village_id"] == inb["village_id"]
            for x in (await resp_inb_list.get_json())["inbound_webhooks"]
        )

        # 3. Notification rules (/api/v1/webhooks/notification-rules)
        resp = await async_client.post(
            "/api/v1/webhooks/notification-rules",
            json={
                "name": "VID Notification Rule",
                "channel": "email",
                "events": ["issue.created"],
                "config": {"recipients": ["ops@example.test"]},
                "organization_id": org1,
            },
            headers=h1,
        )
        assert resp.status_code == 201, await resp.get_data(as_text=True)
        nr = await resp.get_json()
        assert is_valid_village_id(nr.get("village_id"))
        assert parse_village_id(nr["village_id"]).tenant_id == t1
        minted.append(("notification_rule", nr["village_id"], nr["id"], "/webhooks"))

        # Cross-tenant org rejection on POST & cross-tenant 404 on PUT/DELETE
        resp_idor = await async_client.post(
            "/api/v1/webhooks/notification-rules",
            json={
                "name": "Cross-Tenant Rule",
                "channel": "email",
                "events": ["issue.created"],
                "config": {"recipients": ["bad@example.test"]},
                "organization_id": org2,
            },
            headers=h1,
        )
        assert resp_idor.status_code == 404

        resp_nr_list = await async_client.get(
            "/api/v1/webhooks/notification-rules", headers=h1
        )
        assert resp_nr_list.status_code == 200
        assert any(
            x["id"] == nr["id"] and x["village_id"] == nr["village_id"]
            for x in (await resp_nr_list.get_json())["rules"]
        )
        resp_nr_get = await async_client.get(
            f"/api/v1/webhooks/notification-rules/{nr['id']}", headers=h1
        )
        assert resp_nr_get.status_code == 200
        assert (await resp_nr_get.get_json())["village_id"] == nr["village_id"]

        # 4. Alert configurations (/api/v1/webhooks/alert-configurations)
        resp = await async_client.post(
            "/api/v1/webhooks/alert-configurations",
            json={
                "name": "VID PagerDuty Alert",
                "destination_type": "pagerduty",
                "config": {"routing_key": "rk-test-123"},
                "severity_filter": ["critical"],
                "organization_id": org1,
            },
            headers=h1,
        )
        assert resp.status_code == 201, await resp.get_data(as_text=True)
        ac = await resp.get_json()
        assert is_valid_village_id(ac.get("village_id"))
        assert parse_village_id(ac["village_id"]).tenant_id == t1
        assert ac["destination_type"] == "pagerduty"
        minted.append(("alert_configuration", ac["village_id"], ac["id"], "/webhooks"))

        resp_ac_list = await async_client.get(
            "/api/v1/webhooks/alert-configurations", headers=h1
        )
        assert resp_ac_list.status_code == 200
        assert any(
            x["id"] == ac["id"] and x["village_id"] == ac["village_id"]
            for x in (await resp_ac_list.get_json())["alert_configurations"]
        )
        resp_ac_get = await async_client.get(
            f"/api/v1/webhooks/alert-configurations/{ac['id']}", headers=h1
        )
        assert resp_ac_get.status_code == 200
        assert (await resp_ac_get.get_json())["village_id"] == ac["village_id"]

        # Tenant isolation on alert_configurations
        mock_get_user.return_value = MagicMock(
            id=ident2, is_superuser=True, tenant_id=t2, portal_role="admin"
        )
        resp_ac_xtenant = await async_client.get(
            f"/api/v1/webhooks/alert-configurations/{ac['id']}", headers=h2
        )
        assert resp_ac_xtenant.status_code == 404
        resp_nr_xtenant_put = await async_client.put(
            f"/api/v1/webhooks/notification-rules/{nr['id']}",
            json={"name": "Hijack"},
            headers=h2,
        )
        assert resp_nr_xtenant_put.status_code == 404

        mock_get_user.return_value = MagicMock(
            id=ident1, is_superuser=True, tenant_id=t1, portal_role="admin"
        )

        # 5. Stream webhooks (/api/v1/streams/<stream_id>/webhooks)
        resp = await async_client.post(
            f"/api/v1/streams/{stream_id}/webhooks",
            json={"name": "VID Stream Hook", "allowed_methods": ["POST"]},
            headers=h1,
        )
        assert resp.status_code == 201, await resp.get_data(as_text=True)
        sw = await resp.get_json()
        assert is_valid_village_id(sw.get("village_id"))
        assert parse_village_id(sw["village_id"]).tenant_id == t1
        minted.append(
            ("stream_webhook", sw["village_id"], sw["id"], f"/streams/{stream_id}")
        )

        resp_sw_list = await async_client.get(
            f"/api/v1/streams/{stream_id}/webhooks", headers=h1
        )
        assert resp_sw_list.status_code == 200
        sw_list_payload = await resp_sw_list.get_json()
        sw_items = (
            sw_list_payload
            if isinstance(sw_list_payload, list)
            else sw_list_payload["webhooks"]
        )
        assert any(
            x["id"] == sw["id"] and x["village_id"] == sw["village_id"]
            for x in sw_items
        )

        # Verify all 5 minted village_ids are distinct and resolve via /api/v1/id/<vid>
        assert len({vid for _, vid, _, _ in minted}) == 5
        for expected_type, vid, expected_id, expected_url_prefix in minted:
            lookup = await async_client.get(f"/api/v1/id/{vid}", headers=h1)
            assert lookup.status_code == 200, (
                f"Failed to resolve {expected_type} ({vid}): "
                f"{await lookup.get_data(as_text=True)}"
            )
            payload = await lookup.get_json()
            assert payload["resource_type"] == expected_type
            assert payload["resource_id"] == expected_id
            assert payload["redirect_url"].startswith(expected_url_prefix)

            # Cross-tenant resolution returns 404
            mock_get_user.return_value = MagicMock(
                id=ident2, is_superuser=True, tenant_id=t2, portal_role="admin"
            )
            xlookup = await async_client.get(f"/api/v1/id/{vid}", headers=h2)
            assert xlookup.status_code == 404
            mock_get_user.return_value = MagicMock(
                id=ident1, is_superuser=True, tenant_id=t1, portal_role="admin"
            )

        # Also exercise alert_configurations PUT + DELETE
        resp_ac_put = await async_client.put(
            f"/api/v1/webhooks/alert-configurations/{ac['id']}",
            json={"name": "VID PagerDuty Updated", "enabled": False},
            headers=h1,
        )
        assert resp_ac_put.status_code == 200
        assert (await resp_ac_put.get_json())["name"] == "VID PagerDuty Updated"
        resp_ac_del = await async_client.delete(
            f"/api/v1/webhooks/alert-configurations/{ac['id']}", headers=h1
        )
        assert resp_ac_del.status_code == 200
