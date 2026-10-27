"""Drift gate: every tenant_id table must have an explicit RLS decision.

A new model with a ``tenant_id`` column that is in neither ``RLS_TABLES`` nor
``DEFERRED_TABLES`` of migration 042 would silently ship without database
tenant isolation — this test fails loudly instead.
"""

import importlib
import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
MIGRATION = ROOT / "alembic" / "versions" / "042_rls_tenant_isolation.py"


def _load_migration():
    """Import migration 042 by path (alembic version dirs are not packages)."""
    spec = importlib.util.spec_from_file_location("rls_migration_042", MIGRATION)
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
    return _load_migration()


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
    assert not stale, f"migration 042 names tables without tenant_id: {stale}"


def test_covered_and_deferred_are_disjoint_and_unique(migration):
    assert len(set(migration.RLS_TABLES)) == len(migration.RLS_TABLES)
    assert not set(migration.RLS_TABLES) & set(migration.DEFERRED_TABLES)


def test_migration_revision_chain(migration):
    assert (migration.revision, migration.down_revision) == ("042", "041")
