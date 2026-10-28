"""Behavioural RLS check for EVERY protected table, the policy text and the plan shape.

A tautological (``USING (true)``) or missing policy on any single table must fail
here — the two-table isolation tests alone cannot catch that. Also pins the
performance property of the policy: the helper calls are evaluated once per
query (InitPlan), leaving index scans possible.
"""

import pytest
from rls_support import T1, T2, _load_migration, _run, _seed
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from shared.database.rls import tenant_context

pytestmark = [
    pytest.mark.integration,
    pytest.mark.usefixtures("_reset_rls_state"),
]

ALL_TABLES = _load_migration().RLS_TABLES


@pytest.fixture(scope="module")
def seeded(rls_env):
    """One row per tenant in every protected table (FK parents skipped); table -> (id1, id2)."""
    ids: dict[str, tuple[int, int]] = {}
    with rls_env.admin.begin() as conn:
        conn.execute(text("SET session_replication_role = replica"))
        for table in ALL_TABLES:
            ids[table] = (
                _seed(conn, table, tenant_id=T1, int_value=11),
                _seed(conn, table, tenant_id=T2, int_value=22),
            )
    return ids


def _visible(rls_env, table, ids, tenant=None, system=False):
    rows, _ = _run(
        rls_env.app,
        f'SELECT id FROM "{table}" WHERE id = ANY(:ids)',
        tenant,
        system,
        ids=list(ids),
    )
    return {r[0] for r in rows}


def test_every_protected_table_was_seeded(seeded):
    assert len(seeded) == len(ALL_TABLES) == 82  # denominator: all 82, none skipped


@pytest.mark.parametrize("table", ALL_TABLES)
def test_tenant_isolation_holds_on_this_table(rls_env, seeded, table):
    id1, id2 = seeded[table]
    ids = (id1, id2)
    assert _visible(rls_env, table, ids, tenant=T1) == {id1}
    assert _visible(rls_env, table, ids, tenant=T2) == {id2}
    assert _visible(rls_env, table, ids) == set()  # unbound: fail closed
    assert _visible(rls_env, table, ids, system=True) == {id1, id2}
    # Writes: another tenant's rows are untouchable, own rows stay writable.
    other = _run(
        rls_env.app,
        f'UPDATE "{table}" SET tenant_id = tenant_id WHERE id = :i',
        T1,
        i=id2,
    )
    assert other[1] == 0
    assert _run(rls_env.app, f'DELETE FROM "{table}" WHERE id = :i', T1, i=id2)[1] == 0
    own = _run(
        rls_env.app,
        f'UPDATE "{table}" SET tenant_id = tenant_id WHERE id = :i',
        T1,
        i=id1,
    )
    assert own[1] == 1
    with pytest.raises(DBAPIError, match="row-level security"):
        _run(
            rls_env.app,
            f'UPDATE "{table}" SET tenant_id = :t2 WHERE id = :i',
            T1,
            i=id1,
            t2=T2,
        )
    with pytest.raises(DBAPIError, match="row-level security"):
        with tenant_context(T1), rls_env.app.begin() as conn:
            _seed(conn, table, tenant_id=T2, int_value=33)  # cross-tenant INSERT


@pytest.mark.parametrize("table", ALL_TABLES)
def test_policy_text_is_the_expected_predicate_not_a_tautology(rls_env, table):
    rows, _ = _run(
        rls_env.admin,
        "SELECT pg_get_expr(p.polqual, p.polrelid), pg_get_expr(p.polwithcheck, p.polrelid), "
        "p.polcmd, p.polpermissive, p.polroles::text "
        "FROM pg_policy p WHERE p.polrelid = CAST(:t AS regclass) AND p.polname = 'elder_tenant_isolation'",
        t=f'"{table}"',
    )
    assert (
        len(rows) == 1
    ), f"{table}: expected exactly one elder_tenant_isolation policy"
    using, check, cmd, permissive, roles = rows[0]
    for expr in (using, check):
        assert "elder_current_tenant_id()" in expr
        assert "elder_rls_system_scope()" in expr
        assert "tenant_id" in expr
        assert expr.strip().lower() not in ("true", "(true)")
    assert (cmd, permissive, roles) == ("*", True, "{0}")


class TestPlanShape:
    """Per-query (InitPlan) evaluation keeps index scans possible; per-row form does not."""

    @staticmethod
    def _plan(engine, sql, tenant):
        rows, _ = _run(engine, f"EXPLAIN (COSTS OFF) {sql}", tenant)
        return "\n".join(r[0] for r in rows)

    def test_policy_helpers_are_init_plans_and_index_scans_remain_possible(
        self, rls_env
    ):
        with rls_env.admin.begin() as conn:
            conn.execute(text("SET session_replication_role = replica"))
            conn.execute(
                text(
                    "INSERT INTO organizations (name, tenant_id) "
                    "SELECT 'perf-' || g, 100 + (g % 300) FROM generate_series(1, 30000) g"
                )
            )
            conn.execute(
                text("CREATE INDEX ix_perf_orgs_tenant ON organizations (tenant_id)")
            )
            conn.execute(text("ANALYZE organizations"))
        try:
            plan = self._plan(
                rls_env.app, "SELECT * FROM organizations WHERE tenant_id = 150", 150
            )
        finally:
            with rls_env.admin.begin() as conn:
                conn.execute(text("DROP INDEX IF EXISTS ix_perf_orgs_tenant"))
                conn.execute(text("DELETE FROM organizations WHERE name LIKE 'perf-%'"))
        assert "InitPlan" in plan, plan
        assert "Index Scan" in plan or "Bitmap Index Scan" in plan, plan
        assert "current_setting" not in plan, plan  # no per-row GUC call

    def test_negative_control_the_old_per_row_form_is_detected(self, rls_env):
        """The assertion above can fail: the pre-fix predicate shows current_setting per row."""
        migration = _load_migration()
        old = "tenant_id = elder_current_tenant_id() OR elder_rls_system_scope()"
        assert old != migration._PREDICATE
        with rls_env.admin.begin() as conn:
            conn.execute(text("DROP TABLE IF EXISTS perf_old_form"))
            conn.execute(
                text(
                    "CREATE TABLE perf_old_form (id serial PRIMARY KEY, tenant_id integer NOT NULL)"
                )
            )
            conn.execute(
                text(
                    "INSERT INTO perf_old_form (tenant_id) SELECT g % 50 FROM generate_series(1, 5000) g"
                )
            )
            conn.execute(text(f'GRANT SELECT ON perf_old_form TO "{rls_env.role}"'))
            conn.execute(
                text(
                    "ALTER TABLE perf_old_form ENABLE ROW LEVEL SECURITY, FORCE ROW LEVEL SECURITY"
                )
            )
            conn.execute(
                text(
                    f"CREATE POLICY p ON perf_old_form FOR ALL USING ({old}) WITH CHECK ({old})"
                )
            )
        try:
            plan = self._plan(rls_env.app, "SELECT count(*) FROM perf_old_form", 5)
        finally:
            with rls_env.admin.begin() as conn:
                conn.execute(text("DROP TABLE IF EXISTS perf_old_form"))
        assert "InitPlan" not in plan and "current_setting" in plan, plan
