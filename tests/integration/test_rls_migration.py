"""Real-PostgreSQL tests for migrations 042 (drift repair) and 043 (RLS).

Every test builds throwaway databases and drives the real alembic CLI: reversible
round trip, the NULL-tenant pre-flight and its opt-in backfill, lock-timeout
safety, schema-drift failure, alembic-built vs create_all parity, the migration
runner's system scope and the strict startup gate. Nothing is mocked.
"""

import secrets
import time
import uuid

import pytest
from rls_support import (
    _alembic,
    _create_database,
    _create_empty_database,
    _drop_database,
    _import_all_models,
    _load_migration,
    _require_postgres,
    _rls_state,
)
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from shared.database import rls
from shared.database.rls import RlsConfigurationError, check_rls_posture, init_rls

pytestmark = [
    pytest.mark.integration,
    pytest.mark.usefixtures("_reset_rls_state"),
]

BACKFILL = "ELDER_RLS_NULL_TENANT_BACKFILL"


@pytest.fixture()
def model_db():
    """Throwaway create_all() database at alembic revision 041; yields (url, admin_url)."""
    admin_url = _require_postgres()
    name = f"rls_mig_{uuid.uuid4().hex[:10]}"
    url = _create_database(admin_url, name)
    _alembic(url, "stamp", "041")
    try:
        yield url, admin_url
    finally:
        _drop_database(admin_url, name)


def _scalar(url: str, sql: str, **params):
    engine = create_engine(url)
    try:
        with engine.connect() as conn:
            return conn.execute(text(sql), params).scalar()
    finally:
        engine.dispose()


def _exec(url: str, sql: str, **params) -> None:
    engine = create_engine(url)
    try:
        with engine.begin() as conn:
            conn.execute(text(sql), params)
    finally:
        engine.dispose()


def test_upgrade_downgrade_upgrade_is_clean_and_reversible(model_db):
    url, _ = model_db
    total = len(_load_migration().RLS_TABLES)
    assert _rls_state(url) == (0, 0, 0, 0)
    _alembic(url, "upgrade", "043")
    assert _rls_state(url) == (total, total, total, 2)
    _alembic(url, "downgrade", "041")
    assert _rls_state(url) == (0, 0, 0, 0)
    _alembic(url, "upgrade", "043")
    assert _rls_state(url) == (total, total, total, 2)
    # Re-applying 043 over existing policies must be idempotent.
    _alembic(url, "stamp", "042")
    _alembic(url, "upgrade", "043")
    assert _rls_state(url) == (total, total, total, 2)


class TestNullTenantPreflight:
    """NULL-tenant rows would become invisible: abort unless backfill is opted into."""

    @staticmethod
    def _orphan(url: str) -> None:
        _exec(
            url, "INSERT INTO organizations (name, tenant_id) VALUES ('orphan', NULL)"
        )

    def test_aborts_with_counts_and_leaves_database_unchanged(self, model_db):
        url, _ = model_db
        self._orphan(url)
        result = _alembic(url, "upgrade", "043", check=False)
        assert result.returncode != 0
        assert "organizations=1" in result.stderr
        assert BACKFILL in result.stderr
        assert _rls_state(url) == (0, 0, 0, 0)

    def test_other_opt_in_values_still_abort(self, model_db):
        url, _ = model_db
        self._orphan(url)
        result = _alembic(url, "upgrade", "043", env={BACKFILL: "yes"}, check=False)
        assert result.returncode != 0 and "organizations=1" in result.stderr

    def test_opt_in_without_a_default_tenant_fails_clearly(self, model_db):
        url, _ = model_db
        self._orphan(url)
        result = _alembic(url, "upgrade", "043", env={BACKFILL: "default"}, check=False)
        assert result.returncode != 0
        assert "no tenant with slug 'default'" in result.stderr
        assert _rls_state(url) == (0, 0, 0, 0)

    def test_opt_in_backfills_to_default_tenant_and_is_idempotent(self, model_db):
        url, _ = model_db
        _exec(
            url,
            "INSERT INTO tenants (id, name, slug, is_active) VALUES (7, 'Default', 'default', true)",
        )
        self._orphan(url)
        total = len(_load_migration().RLS_TABLES)
        _alembic(url, "upgrade", "043", env={BACKFILL: "default"})
        assert (
            _scalar(url, "SELECT tenant_id FROM organizations WHERE name = 'orphan'")
            == 7
        )
        assert _rls_state(url) == (total, total, total, 2)
        # Second run (RLS already forced, rows already owned) is a clean no-op.
        _alembic(url, "stamp", "042")
        _alembic(url, "upgrade", "043", env={BACKFILL: "default"})
        # The backfill is deliberately not undone: un-owning rows would re-hide them.
        _alembic(url, "downgrade", "042")
        assert (
            _scalar(url, "SELECT tenant_id FROM organizations WHERE name = 'orphan'")
            == 7
        )
        assert _rls_state(url) == (0, 0, 0, 0)


class TestSchemaDriftFailsLoudly:
    """043 refuses to run (and changes nothing) when an expected table cannot be protected."""

    def test_missing_table_and_missing_column_abort_naming_both(self, model_db):
        url, _ = model_db
        _exec(url, "ALTER TABLE webhooks DROP COLUMN tenant_id")
        _exec(url, "DROP TABLE hd_canned_responses CASCADE")
        result = _alembic(url, "upgrade", "043", check=False)
        assert result.returncode != 0
        assert "webhooks: no tenant_id column" in result.stderr
        assert "hd_canned_responses: table missing" in result.stderr
        assert _rls_state(url) == (0, 0, 0, 0)


class TestAlembicBuiltVersusCreateAll:
    """CI gate: the two schema-construction paths protect the same tables."""

    @pytest.fixture()
    def alembic_db(self):
        admin_url = _require_postgres()
        name = f"rls_chain_{uuid.uuid4().hex[:10]}"
        url = _create_empty_database(admin_url, name)
        try:
            yield url
        finally:
            _drop_database(admin_url, name)

    def test_full_chain_aborts_on_unowned_default_org_then_succeeds_with_backfill(
        self, alembic_db
    ):
        _alembic(alembic_db, "upgrade", "042")  # schema only; 043 is what we probe
        first = _alembic(alembic_db, "upgrade", "head", check=False)
        assert first.returncode != 0 and "organizations=1" in first.stderr
        assert _rls_state(alembic_db) == (0, 0, 0, 0)
        _exec(
            alembic_db,
            "INSERT INTO tenants (name, slug, is_active) VALUES ('Default', 'default', true)",
        )
        _alembic(alembic_db, "upgrade", "head", env={BACKFILL: "default"})
        migration = _load_migration()
        expected = len(migration.RLS_TABLES) - len(migration.ABSENT_OK)
        assert _rls_state(alembic_db) == (expected, expected, expected, 2)
        _alembic(alembic_db, "downgrade", "041")
        assert _rls_state(alembic_db) == (0, 0, 0, 0)
        _alembic(alembic_db, "upgrade", "head")  # rows are owned now: no opt-in needed
        assert _rls_state(alembic_db)[0] == expected

    def test_protected_sets_match_except_the_documented_allowlist(
        self, alembic_db, rls_env
    ):
        from shared.database import rls_tables

        _alembic(alembic_db, "upgrade", "042")
        _exec(
            alembic_db,
            "INSERT INTO tenants (name, slug, is_active) VALUES ('Default', 'default', true)",
        )
        _alembic(alembic_db, "upgrade", "head", env={BACKFILL: "default"})

        def protected(url: str) -> set[str]:
            engine = create_engine(url)
            try:
                with engine.connect() as conn:
                    rows = conn.execute(
                        text("SELECT relname FROM pg_class WHERE relforcerowsecurity")
                    )
                    return {r[0] for r in rows}
            finally:
                engine.dispose()

        def with_tenant_id(url: str, names) -> set[str]:
            engine = create_engine(url)
            try:
                with engine.connect() as conn:
                    rows = conn.execute(
                        text(
                            "SELECT table_name FROM information_schema.columns WHERE "
                            "table_schema = 'public' AND column_name = 'tenant_id' "
                            "AND table_name = ANY(:n)"
                        ),
                        {"n": list(names)},
                    )
                    return {r[0] for r in rows}
            finally:
                engine.dispose()

        model_side = protected(str(rls_env.admin_url))
        alembic_side = protected(alembic_db)
        assert model_side == set(rls_tables.RLS_TABLES)
        assert alembic_side == set(rls_tables.RLS_TABLES) - set(rls_tables.ABSENT_OK)
        assert model_side - alembic_side == set(rls_tables.ABSENT_OK)
        # Every RLS table has tenant_id on both paths (the allowlisted absent one aside).
        wanted = set(rls_tables.RLS_TABLES)
        assert with_tenant_id(str(rls_env.admin_url), wanted) == wanted
        assert with_tenant_id(alembic_db, wanted) == wanted - set(rls_tables.ABSENT_OK)
        # Documented model gap: alembic has identity_groups.tenant_id, the model does not.
        gap = set(rls_tables.MODEL_LACKS_TENANT_ID)
        assert with_tenant_id(alembic_db, gap) == gap
        assert with_tenant_id(str(rls_env.admin_url), gap) == set()

    def test_042_repairs_drift_backfills_from_parent_and_is_idempotent(
        self, alembic_db
    ):
        _alembic(alembic_db, "upgrade", "041")
        _exec(
            alembic_db,
            "INSERT INTO tenants (id, name, slug, is_active) VALUES (5, 'T5', 't5', true)",
        )
        _exec(
            alembic_db,
            "INSERT INTO identity_groups (id, name, tenant_id) VALUES (1, 'grp', 5)",
        )
        _exec(
            alembic_db, "INSERT INTO access_reviews (name, group_id) VALUES ('rev', 1)"
        )
        assert (
            _scalar(
                alembic_db,
                "SELECT count(*) FROM information_schema.columns WHERE table_name = "
                "'access_reviews' AND column_name = 'tenant_id'",
            )
            == 0
        )
        _alembic(alembic_db, "upgrade", "042")
        assert (
            _scalar(
                alembic_db, "SELECT tenant_id FROM access_reviews WHERE name = 'rev'"
            )
            == 5
        )
        for table in (
            "access_review_items",
            "access_review_assignments",
            "group_access_requests",
            "group_access_approvals",
        ):
            assert (
                _scalar(
                    alembic_db,
                    "SELECT count(*) FROM information_schema.columns WHERE table_name = :t "
                    "AND column_name = 'tenant_id'",
                    t=table,
                )
                == 1
            )
        assert (
            _scalar(
                alembic_db,
                "SELECT count(*) FROM pg_indexes WHERE indexname = 'ix_access_reviews_tenant_id'",
            )
            == 1
        )
        _alembic(alembic_db, "stamp", "041")
        _alembic(alembic_db, "upgrade", "042")  # idempotent re-apply
        _alembic(alembic_db, "downgrade", "041")  # no-op by design: columns are kept
        assert (
            _scalar(
                alembic_db, "SELECT tenant_id FROM access_reviews WHERE name = 'rev'"
            )
            == 5
        )


class TestLockSafety:
    """Per-table short transactions under lock_timeout: bounded stall, resumable."""

    def test_blocked_table_times_out_cleanly_and_a_rerun_resumes(self, model_db):
        url, _ = model_db
        migration = _load_migration()
        total = len(migration.RLS_TABLES)
        holder = create_engine(url)
        conn = holder.connect()
        txn = conn.begin()
        conn.execute(text("LOCK TABLE organizations IN ACCESS SHARE MODE"))
        started = time.monotonic()
        try:
            result = _alembic(
                url,
                "upgrade",
                "043",
                env={
                    "ELDER_RLS_LOCK_TIMEOUT_MS": "200",
                    "ELDER_RLS_LOCK_RETRIES": "2",
                    "ELDER_RLS_LOCK_BACKOFF_S": "1",
                },
                check=False,
            )
            elapsed = time.monotonic() - started
            assert result.returncode != 0
            assert "could not lock organizations after 2 attempts" in result.stderr
            assert "re-run in a maintenance window" in result.stderr
            assert elapsed < 90, "retries must be bounded"
            # Tables ahead of the blocked one were protected and committed; the rest were not.
            expected_done = migration.RLS_TABLES.index("organizations")
            assert 0 < expected_done < total
            assert _rls_state(url)[:3] == (expected_done,) * 3
            # An unrelated table is still readable while the migration is stuck/failed.
            assert _scalar(url, "SELECT count(*) FROM webhooks") == 0
        finally:
            txn.rollback()
            conn.close()
            holder.dispose()
        _alembic(url, "upgrade", "043")  # resumes where it stopped
        assert _rls_state(url) == (total, total, total, 2)


def test_migration_runner_system_scope_lets_data_migrations_see_forced_tables():
    """alembic/env.py puts the runner in system scope; without it a forced table is invisible.

    The runner here is a NOSUPERUSER table *owner* (FORCE applies to it). Re-running
    043's NULL-tenant backfill after RLS is already enabled can only find and update
    the orphan row if the migration connection is in system scope.
    """
    admin_url = _require_postgres()
    suffix = uuid.uuid4().hex[:10]
    db_name, role, password = (
        f"rls_own_{suffix}",
        f"rls_owner_{suffix}",
        secrets.token_hex(12),
    )
    server = create_engine(make_url(admin_url), isolation_level="AUTOCOMMIT")
    with server.connect() as conn:
        conn.execute(
            text(f'CREATE ROLE "{role}" LOGIN PASSWORD :pw NOSUPERUSER NOBYPASSRLS'),
            {"pw": password},
        )
        conn.execute(text(f'CREATE DATABASE "{db_name}" OWNER "{role}"'))
    server.dispose()
    admin_db = (
        make_url(admin_url).set(database=db_name).render_as_string(hide_password=False)
    )
    owner_db = (
        make_url(admin_url)
        .set(database=db_name, username=role, password=password)
        .render_as_string(hide_password=False)
    )
    try:
        _exec(admin_db, "CREATE EXTENSION IF NOT EXISTS vector")
        from apps.api.models.base import Base

        _import_all_models()
        owner_engine = create_engine(owner_db)
        Base.metadata.create_all(owner_engine)
        owner_engine.dispose()
        _exec(
            admin_db,
            "INSERT INTO tenants (id, name, slug, is_active) VALUES (7, 'D', 'default', true)",
        )
        _alembic(owner_db, "stamp", "041")
        _alembic(owner_db, "upgrade", "043")
        total = len(_load_migration().RLS_TABLES)
        assert _rls_state(admin_db) == (total, total, total, 2)
        # Superuser plants an orphan row that FORCE-d RLS hides from the owner.
        _exec(
            admin_db,
            "INSERT INTO organizations (name, tenant_id) VALUES ('late-orphan', NULL)",
        )
        _alembic(owner_db, "stamp", "042")
        _alembic(owner_db, "upgrade", "043", env={BACKFILL: "default"})
        assert (
            _scalar(
                admin_db,
                "SELECT tenant_id FROM organizations WHERE name = 'late-orphan'",
            )
            == 7
        )
    finally:
        _drop_database(admin_url, db_name)
        server = create_engine(make_url(admin_url), isolation_level="AUTOCOMMIT")
        with server.connect() as conn:
            conn.execute(text(f'DROP ROLE IF EXISTS "{role}"'))
        server.dispose()


class TestStrictStartupGate:
    """ELDER_RLS_STRICT must be able to fail: migration absent, bypass role, partial."""

    @pytest.fixture()
    def app_role_db(self, model_db):
        """create_all DB (no migration) plus a NOSUPERUSER app role; yields (url, role_url)."""
        url, _ = model_db
        role, password = f"rls_strict_{uuid.uuid4().hex[:8]}", secrets.token_hex(12)
        _exec(
            url,
            f'CREATE ROLE "{role}" LOGIN PASSWORD :pw NOSUPERUSER NOBYPASSRLS',
            pw=password,
        )
        _exec(url, f'GRANT SELECT ON ALL TABLES IN SCHEMA public TO "{role}"')
        role_url = (
            make_url(url)
            .set(username=role, password=password)
            .render_as_string(hide_password=False)
        )
        yield url, role_url
        _exec(url, f'REVOKE ALL ON ALL TABLES IN SCHEMA public FROM "{role}"')
        _exec(url, f'DROP ROLE IF EXISTS "{role}"')

    def test_strict_with_migration_not_applied_raises_even_for_a_plain_role(
        self, app_role_db
    ):
        url, role_url = app_role_db
        engine = create_engine(role_url)
        try:
            with pytest.raises(
                RlsConfigurationError, match="migration 043 not applied"
            ):
                check_rls_posture(engine, strict=True)
            with pytest.raises(RlsConfigurationError):
                init_rls(engine, strict=True)
            posture = check_rls_posture(
                engine, strict=False
            )  # non-strict: ERROR log, no raise
            assert posture.protected_tables == 0 and not posture.enforcing
            assert posture.expected_tables == len(_load_migration().RLS_TABLES)
            assert not rls.enforcement_active()
        finally:
            engine.dispose()

    def test_strict_with_superuser_raises_even_when_migration_applied(
        self, app_role_db
    ):
        url, _ = app_role_db
        _alembic(url, "upgrade", "043")
        admin = create_engine(url)
        try:
            with pytest.raises(RlsConfigurationError, match="superuser/BYPASSRLS"):
                check_rls_posture(admin, strict=True)
        finally:
            admin.dispose()

    def test_strict_with_everything_in_place_starts_and_activates_enforcement(
        self, app_role_db
    ):
        url, role_url = app_role_db
        _alembic(url, "upgrade", "043")
        engine = create_engine(role_url)
        try:
            posture = init_rls(engine, strict=True)
            assert posture is not None and posture.enforcing and posture.problems == ()
            assert rls.enforcement_active()
        finally:
            engine.dispose()

    def test_partial_protection_raises_naming_the_unprotected_table(self, app_role_db):
        url, role_url = app_role_db
        _alembic(url, "upgrade", "043")
        _exec(
            url,
            "ALTER TABLE webhooks NO FORCE ROW LEVEL SECURITY, DISABLE ROW LEVEL SECURITY",
        )
        total = len(_load_migration().RLS_TABLES)
        engine = create_engine(role_url)
        try:
            with pytest.raises(RlsConfigurationError) as exc:
                check_rls_posture(engine, strict=True)
            assert f"{total - 1} of {total} tables protected" in str(exc.value)
            assert "webhooks" in str(exc.value)
        finally:
            engine.dispose()

    def test_dropped_policy_alone_is_detected(self, app_role_db):
        url, role_url = app_role_db
        _alembic(url, "upgrade", "043")
        _exec(url, "DROP POLICY elder_tenant_isolation ON issues")
        engine = create_engine(role_url)
        try:
            posture = check_rls_posture(engine)
            assert posture.missing_tables == ("issues",)
        finally:
            engine.dispose()
