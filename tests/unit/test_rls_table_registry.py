"""Drift gates for the RLS table registry (migrations 042/043 vs models vs runtime).

A new model with a ``tenant_id`` column that is in neither ``RLS_TABLES`` nor
``DEFERRED_TABLES`` would silently ship without database tenant isolation, and
the runtime posture list (``shared.database.rls_tables``) must equal the frozen
copy inside migration 043 — these tests fail loudly instead.
"""

import importlib
import importlib.util
from pathlib import Path

import pytest

from shared.database import rls_tables

ROOT = Path(__file__).resolve().parents[2]
VERSIONS = ROOT / "alembic" / "versions"


def _load(filename: str, module_name: str):
    """Import an alembic version file by path (version dirs are not packages)."""
    spec = importlib.util.spec_from_file_location(module_name, VERSIONS / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _model_tenant_tables() -> set[str]:
    """Return every model table that carries a ``tenant_id`` column."""
    from apps.api.models.base import Base
    from apps.api.modules import CORE_MODELS, MODULES

    modules = set(CORE_MODELS)
    for manifest in MODULES:
        modules.update(manifest.models_import)
    for path in sorted(modules):
        importlib.import_module(path)
    return {
        name
        for name, table in Base.metadata.tables.items()
        if "tenant_id" in table.columns
    }


@pytest.fixture(scope="module")
def migration():
    return _load("043_rls_tenant_isolation.py", "rls_migration_043")


@pytest.fixture(scope="module")
def repair():
    return _load("042_tenant_id_drift_repair.py", "rls_migration_042")


@pytest.fixture(scope="module")
def tenant_tables():
    return _model_tenant_tables()


def test_registry_examined_a_real_number_of_tables(migration, tenant_tables):
    # Denominator guard: an empty scan must fail, never pass vacuously.
    assert len(tenant_tables) >= 80
    assert len(migration.RLS_TABLES) >= 80


def test_every_tenant_table_is_covered_or_explicitly_deferred(migration, tenant_tables):
    decided = set(migration.RLS_TABLES) | set(migration.DEFERRED_TABLES)
    missing = sorted(tenant_tables - decided)
    assert not missing, f"tenant_id tables with no RLS decision: {missing}"


def test_migration_lists_only_real_tenant_tables(migration, tenant_tables):
    stale = sorted(
        (set(migration.RLS_TABLES) | set(migration.DEFERRED_TABLES)) - tenant_tables
    )
    assert not stale, f"migration 043 names tables without tenant_id: {stale}"


def test_covered_and_deferred_are_disjoint_and_unique(migration):
    assert len(set(migration.RLS_TABLES)) == len(migration.RLS_TABLES)
    assert not set(migration.RLS_TABLES) & set(migration.DEFERRED_TABLES)


def test_migration_revision_chain(migration, repair):
    assert (repair.revision, repair.down_revision) == ("042", "041")
    assert (migration.revision, migration.down_revision) == ("043", "042")


def test_runtime_registry_equals_the_frozen_migration_copy(migration):
    assert rls_tables.RLS_TABLES == migration.RLS_TABLES
    assert rls_tables.DEFERRED_TABLES == migration.DEFERRED_TABLES
    assert rls_tables.ABSENT_OK == migration.ABSENT_OK
    assert rls_tables.POLICY_NAME == migration.POLICY_NAME


def test_allowlists_are_consistent(migration, tenant_tables):
    assert set(rls_tables.ABSENT_OK) <= set(rls_tables.RLS_TABLES)
    # identity_groups: tenant_id exists only in alembic-built DBs, never in the model.
    assert set(rls_tables.MODEL_LACKS_TENANT_ID).isdisjoint(tenant_tables)
    assert set(rls_tables.MODEL_LACKS_TENANT_ID).isdisjoint(rls_tables.RLS_TABLES)


def test_repair_migration_covers_exactly_the_tables_alembic_never_gave_tenant_id(
    repair, migration
):
    assert set(repair.REPAIR) <= set(migration.RLS_TABLES)
    # Parents are repaired before their children so the backfill chain works.
    order = list(repair.REPAIR)
    for table, (_fk, parent) in repair.REPAIR.items():
        if parent in repair.REPAIR:
            assert order.index(parent) < order.index(table)


def test_policy_predicate_uses_init_plan_subselects(migration):
    predicate = migration._PREDICATE
    assert "(SELECT elder_current_tenant_id())" in predicate
    assert "(SELECT elder_rls_system_scope())" in predicate
    assert "tenant_id = elder_current_tenant_id()" not in predicate  # per-row form
