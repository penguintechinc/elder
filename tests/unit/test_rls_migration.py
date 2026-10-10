"""Unit tests for PostgreSQL Row-Level Security tenant isolation.

These cover two things that must hold without a PostgreSQL server:

1. The RLS Alembic migration module imports and is well-formed (both
   ``upgrade`` and ``downgrade`` are Postgres-guarded).
2. The per-request tenant-context GUC wiring is a strict no-op on SQLite, so
   the SQLite test/dev path is never broken.
"""

import importlib.util
import os
import types
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
MIGRATION_PATH = (
    REPO_ROOT / "alembic" / "versions" / "015_enable_rls_tenant_isolation.py"
)


def _load_migration():
    """Import the migration module directly by file path."""
    spec = importlib.util.spec_from_file_location("rls_migration_015", MIGRATION_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# --------------------------------------------------------------------------- #
# Migration module
# --------------------------------------------------------------------------- #
def test_migration_module_imports():
    module = _load_migration()
    assert module.revision == "015"
    assert module.down_revision == "014"
    assert hasattr(module, "upgrade")
    assert hasattr(module, "downgrade")
    assert isinstance(module.RLS_TABLES, list) and module.RLS_TABLES


def test_migration_targets_known_tenant_tables():
    module = _load_migration()
    # A representative sample of tenant-scoped tables must be covered.
    for table in ("identities", "organizations", "on_call_rotations", "data_stores"):
        assert table in module.RLS_TABLES
    # Global CVE reference data is intentionally excluded.
    assert "vulnerabilities" not in module.RLS_TABLES


def test_migration_upgrade_downgrade_are_postgres_guarded():
    """On a non-PostgreSQL bind, upgrade/downgrade must do nothing."""
    module = _load_migration()

    executed = []

    class _FakeDialect:
        name = "sqlite"

    class _FakeBind:
        dialect = _FakeDialect()

    fake_op = types.SimpleNamespace(
        get_bind=lambda: _FakeBind(),
        execute=lambda sql: executed.append(sql),
    )

    original_op = module.op
    module.op = fake_op
    try:
        module.upgrade()
        module.downgrade()
    finally:
        module.op = original_op

    assert executed == [], "RLS DDL must not run on non-PostgreSQL backends"


def test_migration_emits_rls_ddl_on_postgres():
    """On a PostgreSQL bind, upgrade emits ENABLE RLS + CREATE POLICY per table."""
    module = _load_migration()

    executed = []

    class _FakeDialect:
        name = "postgresql"

    class _FakeBind:
        dialect = _FakeDialect()

    fake_op = types.SimpleNamespace(
        get_bind=lambda: _FakeBind(),
        execute=lambda sql: executed.append(sql),
    )

    original_op = module.op
    module.op = fake_op
    try:
        module.upgrade()
    finally:
        module.op = original_op

    joined = "\n".join(executed)
    assert "ENABLE ROW LEVEL SECURITY" in joined
    assert "CREATE POLICY tenant_isolation" in joined
    assert "current_setting('app.current_tenant_id', true)::int" in joined
    # Two statements (enable + policy) per table.
    assert len(executed) == 2 * len(module.RLS_TABLES)


# --------------------------------------------------------------------------- #
# GUC wiring (must be a no-op on SQLite)
# --------------------------------------------------------------------------- #
def test_tenant_context_helpers_roundtrip():
    from apps.api import tenant_rls

    tenant_rls.clear_current_tenant()
    assert tenant_rls.get_current_tenant() is None
    tenant_rls.set_current_tenant(42)
    assert tenant_rls.get_current_tenant() == 42
    tenant_rls.clear_current_tenant()
    assert tenant_rls.get_current_tenant() is None


def test_engine_is_postgres_detection():
    from apps.api import tenant_rls

    class _Dialect:
        name = "sqlite"

    class _Engine:
        dialect = _Dialect()

    assert tenant_rls._engine_is_postgres(_Engine()) is False

    _Engine.dialect.name = "postgresql"
    assert tenant_rls._engine_is_postgres(_Engine()) is True


def test_register_tenant_rls_is_noop_on_sqlite():
    """With a SQLite engine, registration returns False and attaches no listener."""
    from sqlalchemy import create_engine

    from apps.api import tenant_rls

    engine = create_engine("sqlite://")

    before_request_hooks = []
    teardown_hooks = []

    class _FakeApp:
        def __init__(self):
            self.db = types.SimpleNamespace(engine=engine)

        def before_request(self, fn):
            before_request_hooks.append(fn)
            return fn

        def teardown_request(self, fn):
            teardown_hooks.append(fn)
            return fn

    app = _FakeApp()
    active = tenant_rls.register_tenant_rls(app)

    assert active is False
    # before_request / teardown are still registered (but resolve to no tenant);
    # the Postgres-only checkout listener is simply not activated on SQLite.
    assert len(before_request_hooks) == 1
    assert len(teardown_hooks) == 1


def test_register_tenant_rls_active_on_postgres_engine():
    """A PostgreSQL engine (no server needed) activates the wiring."""
    from sqlalchemy import create_engine

    from apps.api import tenant_rls

    # create_engine does not connect; dialect is resolved from the URL.
    engine = create_engine("postgresql+psycopg2://user:pass@localhost/db")

    class _FakeApp:
        def __init__(self):
            self.db = types.SimpleNamespace(engine=engine)
            self._before = []
            self._teardown = []

        def before_request(self, fn):
            self._before.append(fn)
            return fn

        def teardown_request(self, fn):
            self._teardown.append(fn)
            return fn

    app = _FakeApp()
    active = tenant_rls.register_tenant_rls(app)

    assert active is True


if __name__ == "__main__":  # pragma: no cover
    os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/elder_unit_test.db")
    raise SystemExit(pytest.main([__file__, "-v"]))
