"""Real-PostgreSQL tests for row-level-security tenant isolation (migration 043).

Everything runs against a real PostgreSQL (``DATABASE_URL``, a superuser test
instance); see ``rls_support`` for how the throwaway database and the
non-superuser application role are built. Nothing is mocked.
"""

import asyncio
import contextlib
import secrets
import threading
import time
import uuid

import jwt
import pytest
from rls_support import (
    ISOLATED_TABLES,
    ROOT,
    T1,
    T2,
    _admin_names,
    _alembic,
    _create_database,
    _drop_database,
    _load_migration,
    _names,
    _require_postgres,
    _run,
)
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError

from shared.database import rls
from shared.database.rls import (
    RlsConfigurationError,
    check_rls_posture,
    tenant_context,
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.usefixtures("_reset_rls_state"),
]


class TestCatalog:
    """The migration produced the intended database objects."""

    def test_every_covered_table_is_enabled_forced_with_one_policy(self, rls_env):
        migration = _load_migration()
        rows, _ = _run(
            rls_env.admin,
            "SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity, "
            "(SELECT count(*) FROM pg_policy p WHERE p.polrelid = c.oid AND p.polname = :p) "
            "FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE n.nspname = 'public' AND c.relname = ANY(:names)",
            p=migration.POLICY_NAME,
            names=list(migration.RLS_TABLES),
        )
        assert (
            len(rows) == len(migration.RLS_TABLES) > 0
        )  # denominator: all 82 examined
        bad = [r for r in rows if not (r[1] and r[2] and r[3] == 1)]
        assert not bad, f"tables missing ENABLE/FORCE/policy: {bad}"

    def test_deferred_tables_are_not_yet_protected(self, rls_env):
        migration = _load_migration()
        rows, _ = _run(
            rls_env.admin,
            "SELECT relname FROM pg_class WHERE relname = ANY(:n) AND relrowsecurity",
            n=list(migration.DEFERRED_TABLES),
        )
        assert rows == []

    @pytest.mark.parametrize(
        "guc,expected",
        [
            (None, None),
            ("", None),
            ("abc", None),
            ("12", 12),
            ("-1", None),
            ("1 OR 1=1", None),
            ("9" * 19, None),
            ("0012", 12),
        ],
    )
    def test_tenant_function_is_null_safe(self, rls_env, guc, expected):
        with rls_env.admin.begin() as conn:
            if guc is not None:
                conn.execute(
                    text("SELECT set_config('app.tenant_id', :g, true)"), {"g": guc}
                )
            assert (
                conn.execute(text("SELECT elder_current_tenant_id()")).scalar()
                == expected
            )

    @pytest.mark.parametrize(
        "guc,expected",
        [(None, False), ("", False), ("tenant", False), ("system", True)],
    )
    def test_system_scope_function_only_true_for_exact_value(
        self, rls_env, guc, expected
    ):
        with rls_env.admin.begin() as conn:
            if guc is not None:
                conn.execute(
                    text("SELECT set_config('app.rls_scope', :g, true)"), {"g": guc}
                )
            assert (
                conn.execute(text("SELECT elder_rls_system_scope()")).scalar()
                is expected
            )


class TestRolePosture:
    """The application role genuinely cannot bypass RLS."""

    def test_app_role_is_not_superuser_or_bypassrls(self, rls_env):
        rows, _ = _run(
            rls_env.app,
            "SELECT rolname, rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user",
        )
        assert rows == [(rls_env.role, False, False)]

    def test_posture_reports_enforcing_for_app_role(self, rls_env):
        migration = _load_migration()
        posture = check_rls_posture(rls_env.app, strict=True)
        assert posture.role_enforces and not posture.bypassed
        assert posture.protected_tables == len(migration.RLS_TABLES)
        assert set(posture.unprotected_tenant_tables) == set(migration.DEFERRED_TABLES)

    def test_posture_flags_superuser_and_strict_refuses(self, rls_env):
        posture = check_rls_posture(rls_env.admin)
        assert posture.superuser and posture.bypassed
        with pytest.raises(RlsConfigurationError):
            check_rls_posture(rls_env.admin, strict=True)


@pytest.mark.parametrize("table", ISOLATED_TABLES)
class TestIsolation:
    """Cross-tenant SELECT/INSERT/UPDATE/DELETE denied; same-tenant allowed."""

    def _own(self, tid: int, table: str) -> set[str]:
        prefix = "org" if table == "organizations" else "hook"
        return {f"{prefix}-{tid}-a", f"{prefix}-{tid}-b"}

    def test_same_tenant_sees_only_its_rows(self, rls_env, table):
        assert _names(rls_env.app, table, tenant=T1) == self._own(T1, table)
        assert _names(rls_env.app, table, tenant=T2) == self._own(T2, table)

    def test_cross_tenant_select_returns_nothing_even_when_asked_explicitly(
        self, rls_env, table
    ):
        rows, _ = _run(
            rls_env.app, f"SELECT id FROM {table} WHERE tenant_id = :t", tenant=T1, t=T2
        )
        assert rows == []

    def test_unset_guc_returns_nothing(self, rls_env, table):
        assert _names(rls_env.app, table) == set()

    def test_unset_guc_rejects_writes(self, rls_env, table):
        with pytest.raises(DBAPIError, match="row-level security"):
            self._insert(rls_env, table, T1, tenant=None)

    def _insert(self, env, table, tid, tenant):
        extra_cols, extra_vals = (
            ("", "")
            if table == "organizations"
            else (", url, is_active", ", 'http://x', true")
        )
        return _run(
            env.app,
            f"INSERT INTO {table} (name, tenant_id{extra_cols}) VALUES (:n, :t{extra_vals})",
            tenant=tenant,
            n=f"new-{uuid.uuid4().hex[:6]}",
            t=tid,
        )

    def test_same_tenant_insert_allowed(self, rls_env, table):
        _, rowcount = self._insert(rls_env, table, T1, tenant=T1)
        assert rowcount == 1
        _run(rls_env.admin, f"DELETE FROM {table} WHERE name LIKE 'new-%'")

    def test_cross_tenant_insert_denied(self, rls_env, table):
        with pytest.raises(DBAPIError, match="row-level security"):
            self._insert(rls_env, table, T2, tenant=T1)

    def test_null_tenant_insert_denied(self, rls_env, table):
        extra_cols, extra_vals = (
            ("", "")
            if table == "organizations"
            else (", url, is_active", ", 'http://x', true")
        )
        with pytest.raises(DBAPIError, match="row-level security"):
            _run(
                rls_env.app,
                f"INSERT INTO {table} (name, tenant_id{extra_cols}) VALUES ('n', NULL{extra_vals})",
                tenant=T1,
            )

    def test_cross_tenant_update_touches_zero_rows(self, rls_env, table):
        before = _admin_names(rls_env, table)
        _, rowcount = _run(
            rls_env.app,
            f"UPDATE {table} SET name = 'hijacked' WHERE tenant_id = :t",
            tenant=T1,
            t=T2,
        )
        assert rowcount == 0
        assert _admin_names(rls_env, table) == before

    def test_update_moving_row_to_other_tenant_denied(self, rls_env, table):
        with pytest.raises(DBAPIError, match="row-level security"):
            _run(
                rls_env.app,
                f"UPDATE {table} SET tenant_id = :t2 WHERE tenant_id = :t1",
                tenant=T1,
                t1=T1,
                t2=T2,
            )

    def test_same_tenant_update_allowed(self, rls_env, table):
        name = self._own(T1, table).pop()
        _, rowcount = _run(
            rls_env.app,
            f"UPDATE {table} SET name = name WHERE name = :n",
            tenant=T1,
            n=name,
        )
        assert rowcount == 1

    def test_cross_tenant_delete_touches_zero_rows(self, rls_env, table):
        before = _admin_names(rls_env, table)
        _, rowcount = _run(
            rls_env.app, f"DELETE FROM {table} WHERE tenant_id = :t", tenant=T1, t=T2
        )
        assert rowcount == 0
        assert _admin_names(rls_env, table) == before

    def test_same_tenant_delete_allowed(self, rls_env, table):
        self._insert(rls_env, table, T1, tenant=T1)
        _, rowcount = _run(
            rls_env.app, f"DELETE FROM {table} WHERE name LIKE 'new-%'", tenant=T1
        )
        assert rowcount == 1

    def test_system_scope_sees_every_tenant(self, rls_env, table):
        names = _names(rls_env.app, table, system=True)
        assert self._own(T1, table) | self._own(T2, table) <= names

    def test_malformed_guc_returns_nothing(self, rls_env, table):
        with rls_env.app.begin() as conn:
            conn.execute(text("SELECT set_config('app.tenant_id', 'abc', true)"))
            assert conn.execute(text(f"SELECT count(*) FROM {table}")).scalar() == 0

    def test_kill_switch_on_restores_legacy_visibility_and_off_isolates_again(
        self, rls_env, table
    ):
        rls.configure_kill_switch(lambda: True, start_thread=False)
        assert self._own(T1, table) | self._own(T2, table) <= _names(rls_env.app, table)
        rls.configure_kill_switch(lambda: False, start_thread=False)
        assert _names(rls_env.app, table) == set()
        assert _names(rls_env.app, table, tenant=T1) == self._own(T1, table)


def test_null_tenant_rows_invisible_to_tenants_but_visible_in_system_scope(rls_env):
    assert "org-orphan" not in _names(rls_env.app, "organizations", tenant=T1)
    assert "org-orphan" in _names(rls_env.app, "organizations", system=True)


def test_force_applies_to_table_owner(rls_env):
    """FORCE ROW LEVEL SECURITY: a non-superuser *owner* is still constrained."""
    migration = _load_migration()
    with rls_env.admin.begin() as conn:
        conn.execute(
            text(
                "CREATE TABLE rls_owner_probe (id serial PRIMARY KEY, tenant_id integer NOT NULL, name text)"
            )
        )
        conn.execute(
            text("INSERT INTO rls_owner_probe (tenant_id, name) VALUES (9001, 'x')")
        )
        conn.execute(text(f'ALTER TABLE rls_owner_probe OWNER TO "{rls_env.role}"'))
        conn.execute(text("ALTER TABLE rls_owner_probe ENABLE ROW LEVEL SECURITY"))
        conn.execute(text("ALTER TABLE rls_owner_probe FORCE ROW LEVEL SECURITY"))
        conn.execute(
            text(
                f"CREATE POLICY {migration.POLICY_NAME} ON rls_owner_probe FOR ALL "
                f"USING ({migration._PREDICATE}) WITH CHECK ({migration._PREDICATE})"
            )
        )
    assert _run(rls_env.app, "SELECT count(*) FROM rls_owner_probe")[0] == [(0,)]
    assert _run(rls_env.app, "SELECT count(*) FROM rls_owner_probe", tenant=T1)[0] == [
        (1,)
    ]
    with rls_env.admin.begin() as conn:
        conn.execute(text("ALTER TABLE rls_owner_probe NO FORCE ROW LEVEL SECURITY"))
    assert _run(rls_env.app, "SELECT count(*) FROM rls_owner_probe")[0] == [
        (1,)
    ]  # owner bypasses w/o FORCE
    with rls_env.admin.begin() as conn:
        conn.execute(text("DROP TABLE rls_owner_probe"))


class TestPooling:
    """A pooled connection can never carry one tenant into another transaction."""

    def test_connection_is_reused_yet_tenant_never_leaks(self, rls_env):
        pids = []
        for tenant in (T1, None, T2, None):
            with contextlib.ExitStack() as stack:
                if tenant is not None:
                    stack.enter_context(tenant_context(tenant))
                with rls_env.app.begin() as conn:
                    pids.append(conn.execute(text("SELECT pg_backend_pid()")).scalar())
                    seen = conn.execute(
                        text("SELECT current_setting('app.tenant_id', true)")
                    ).scalar()
                    assert seen == ("" if tenant is None else str(tenant))
                    count = conn.execute(
                        text("SELECT count(*) FROM organizations")
                    ).scalar()
                    assert (count == 0) if tenant is None else (count == 2)
        assert len(set(pids)) == 1, "pool_size=1 must reuse the same backend"

    def test_guc_is_transaction_local_at_the_database_level(self, rls_env):
        """A pooled connection used *outside* SQLAlchemy sees no leftover tenant.

        Bypasses the begin listener entirely (raw DBAPI cursor on the same pooled
        connection), so only PostgreSQL's own ``is_local`` semantics can pass this.
        """
        with tenant_context(T1):
            with rls_env.app.begin() as conn:
                assert (
                    conn.execute(text("SELECT count(*) FROM organizations")).scalar()
                    == 2
                )
        raw = rls_env.app.raw_connection()
        try:
            cursor = raw.cursor()
            cursor.execute("SELECT current_setting('app.tenant_id', true)")
            assert cursor.fetchone()[0] in (None, "")
            cursor.execute("SELECT count(*) FROM organizations")
            assert cursor.fetchone()[0] == 0
            cursor.close()
            raw.rollback()
        finally:
            raw.close()

    def test_failed_transaction_does_not_leak_tenant(self, rls_env):
        with pytest.raises(DBAPIError):
            with tenant_context(T1):
                with rls_env.app.begin() as conn:
                    conn.execute(text("SELECT 1/0"))
        assert _names(rls_env.app, "organizations") == set()

    def test_raw_session_level_set_cannot_survive_into_next_transaction(self, rls_env):
        """Even a buggy session-level SET is overwritten by the per-transaction stamp."""
        with rls_env.app.connect() as conn:
            conn.execute(
                text("SELECT set_config('app.tenant_id', :t, false)"), {"t": str(T1)}
            )
            conn.commit()
        assert _names(rls_env.app, "organizations") == set()

    def test_concurrent_threads_each_see_only_their_tenant(self, rls_env):
        engine = create_engine(rls_env.app_url, pool_size=3, max_overflow=0)
        failures: list[str] = []

        def worker(tenant: int) -> None:
            for _ in range(40):
                with tenant_context(tenant):
                    with engine.begin() as conn:
                        names = {
                            r[0]
                            for r in conn.execute(
                                text("SELECT name FROM organizations")
                            )
                        }
                        seen = conn.execute(
                            text("SELECT current_setting('app.tenant_id', true)")
                        ).scalar()
                        time.sleep(0.001)
                if (
                    seen != str(tenant)
                    or not names
                    or any(f"-{tenant}-" not in n for n in names)
                ):
                    failures.append(f"tenant {tenant} saw {seen} / {sorted(names)}")

        threads = [
            threading.Thread(target=worker, args=(t,)) for t in (T1, T2, T1, T2, T1, T2)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=60)
        engine.dispose()
        assert not failures, failures[:3]

    def test_asyncio_to_thread_propagates_and_isolates_concurrent_tasks(self, rls_env):
        async def one(tenant: int) -> set[str]:
            with tenant_context(tenant):
                await asyncio.sleep(0)
                return await asyncio.to_thread(_names, rls_env.app, "organizations")

        async def main():
            return await asyncio.gather(one(T1), one(T2), one(T1), one(T2))

        results = asyncio.run(main())
        assert results[0] == results[2] == {"org-9001-a", "org-9001-b"}
        assert results[1] == results[3] == {"org-9002-a", "org-9002-b"}


class TestPenguinDalRealPath:
    """The real runtime data layer (penguin-dal) under the app role."""

    @pytest.fixture()
    def db(self, rls_env):
        from penguin_dal import DAL

        dal = DAL(rls_env.app_url, pool_size=2, migrate=False)
        yield dal
        dal.close()

    def test_dal_reads_are_tenant_scoped(self, db):
        with tenant_context(T1):
            names = {r.name for r in db(db.organizations.id > 0).select()}
        assert names == {"org-9001-a", "org-9001-b"}
        assert {r.name for r in db(db.organizations.id > 0).select()} == set()

    def test_dal_query_missing_tenant_filter_cannot_cross_tenants(self, db):
        """The bug RLS exists for: an app query that forgot WHERE tenant_id=..."""
        with tenant_context(T1):
            assert db(db.organizations.tenant_id == T2).select().first() is None
            other = db(db.organizations.name == "org-9002-a").select().first()
        assert other is None

    def test_dal_insert_update_delete_roundtrip_and_cross_tenant_denial(self, db):
        with tenant_context(T1):
            new_id = db.organizations.insert(name="dal-new", tenant_id=T1)
            assert db(db.organizations.id == new_id).count() == 1
            assert db(db.organizations.id == new_id).update(name="dal-renamed") == 1
            with pytest.raises(DBAPIError, match="row-level security"):
                db.organizations.insert(name="dal-evil", tenant_id=T2)
            assert db(db.organizations.id == new_id).delete() == 1
        with tenant_context(T2):
            foreign = db(db.organizations.name == "org-9001-a").select().first()
            assert foreign is None
            assert db(db.organizations.tenant_id == T1).delete() == 0

    def test_dal_without_context_fails_closed(self, db):
        with pytest.raises(DBAPIError, match="row-level security"):
            db.organizations.insert(name="no-ctx", tenant_id=T1)
        assert db(db.organizations.id > 0).count() == 0


class TestRequestWiring:
    """populate_claims -> ContextVar -> RLS, through a real Quart request."""

    @pytest.fixture()
    def rls_app(self, rls_env):
        from penguin_dal import DAL
        from quart import Quart, current_app, jsonify

        from apps.api.main import _register_before_request
        from apps.api.utils.async_utils import run_in_threadpool

        app = Quart(__name__)
        app.config.update(
            JWT_SECRET_KEY=secrets.token_hex(32),
            SECRET_KEY="x",
            JWT_ALGORITHM="HS256",
            TESTING=True,
        )
        app.db = DAL(rls_env.app_url, pool_size=2, migrate=False)
        _register_before_request(app)

        @app.route("/orgs")
        async def orgs():
            db = current_app.db
            rows = await run_in_threadpool(lambda: db(db.organizations.id > 0).select())
            return jsonify(sorted(r.name for r in rows))

        yield app
        app.db.close()

    def _token(self, app, tenant):
        claims = {
            "sub": "1",
            "type": "access",
            "scope": [],
            "roles": [],
            "exp": int(time.time()) + 600,
        }
        if tenant is not None:
            claims["tenant"] = str(tenant)
        return jwt.encode(claims, app.config["JWT_SECRET_KEY"], algorithm="HS256")

    async def _get(self, app, tenant, header=True):
        client = app.test_client()
        headers = (
            {"Authorization": f"Bearer {self._token(app, tenant)}"} if header else {}
        )
        response = await client.get("/orgs", headers=headers)
        return await response.get_json()

    @pytest.mark.asyncio
    async def test_each_tenant_token_sees_only_its_tenant(self, rls_app):
        assert await self._get(rls_app, T1) == ["org-9001-a", "org-9001-b"]
        assert await self._get(rls_app, T2) == ["org-9002-a", "org-9002-b"]

    @pytest.mark.asyncio
    async def test_no_token_and_missing_or_bad_tenant_claim_fail_closed(self, rls_app):
        assert await self._get(rls_app, T1, header=False) == []
        assert await self._get(rls_app, None) == []
        assert await self._get(rls_app, "not-a-number") == []

    @pytest.mark.asyncio
    async def test_tenant_does_not_leak_between_sequential_requests(self, rls_app):
        assert await self._get(rls_app, T1) == ["org-9001-a", "org-9001-b"]
        assert await self._get(rls_app, T1, header=False) == []
        assert rls.current_tenant_id() is None
