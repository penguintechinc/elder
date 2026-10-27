"""Shared fixtures/helpers for the real-PostgreSQL RLS integration tests.

Builds a throwaway database from the models, applies migration 042 through the
real alembic CLI and connects the application as a NOSUPERUSER / NOBYPASSRLS
role — the only kind of role RLS actually constrains. Nothing is mocked.
Imported (not collected) by ``test_rls_*.py`` in this directory.
"""

import asyncio
import contextlib
import importlib
import os
import secrets
import subprocess  # nosec B404 - alembic CLI under test, fixed argv
import sys
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

import jwt
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.exc import DBAPIError

from shared.database import rls
from shared.database.rls import (
    RlsConfigurationError,
    SystemScopeReason,
    check_rls_posture,
    system_scope,
    tenant_context,
)

ROOT = Path(__file__).resolve().parents[2]
T1, T2 = 9001, 9002
ISOLATED_TABLES = ("organizations", "webhooks")


@dataclass(slots=True, frozen=True)
class RlsEnv:
    """Handles to the throwaway RLS test database."""

    admin_url: str
    app_url: str
    db_name: str
    role: str
    admin: Engine
    app: Engine


def _import_all_models() -> None:
    """Import every registry model so Base.metadata describes the full schema."""
    from apps.api.modules import CORE_MODELS, MODULES

    paths = set(CORE_MODELS)
    for manifest in MODULES:
        paths.update(manifest.models_import)
    for path in sorted(paths):
        importlib.import_module(path)


def _alembic(db_url: str, *args: str) -> subprocess.CompletedProcess:
    """Run the real alembic CLI against ``db_url`` and fail loudly on error."""
    env = {**os.environ, "DATABASE_URL": db_url}
    result = subprocess.run(  # nosec B603 - fixed argv, test-owned DB URL
        [sys.executable, "-m", "alembic", "-c", str(ROOT / "alembic.ini"), *args],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=600,
    )
    assert result.returncode == 0, f"alembic {args} failed:\n{result.stderr[-2000:]}"
    return result


def _create_database(admin_url: str, name: str) -> str:
    """CREATE DATABASE + pgvector + model schema; return its superuser URL."""
    base = make_url(admin_url)
    server = create_engine(base, isolation_level="AUTOCOMMIT")
    with server.connect() as conn:
        conn.execute(text(f'CREATE DATABASE "{name}"'))
    server.dispose()
    url = base.set(database=name).render_as_string(hide_password=False)
    engine = create_engine(url)
    with engine.begin() as conn:
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
    from apps.api.models.base import Base

    _import_all_models()
    Base.metadata.create_all(engine)
    engine.dispose()
    return url


def _drop_database(admin_url: str, name: str) -> None:
    """Terminate stragglers and DROP the throwaway database."""
    server = create_engine(make_url(admin_url), isolation_level="AUTOCOMMIT")
    with server.connect() as conn:
        conn.execute(
            text(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname = :n AND pid <> pg_backend_pid()"
            ),
            {"n": name},
        )
        conn.execute(text(f'DROP DATABASE IF EXISTS "{name}"'))
    server.dispose()


def _require_postgres() -> str:
    """Return the superuser DATABASE_URL or skip when no PostgreSQL is configured."""
    url = os.getenv("DATABASE_URL", "")
    if not url.startswith("postgresql"):
        pytest.skip("DATABASE_URL must point at a PostgreSQL test instance")
    return url


@pytest.fixture(scope="module")
def rls_env():
    """Throwaway DB: models schema + migration 042 + NOSUPERUSER app role + seed rows."""
    admin_url = _require_postgres()
    suffix = uuid.uuid4().hex[:10]
    db_name, role, password = (
        f"rls_{suffix}",
        f"rls_app_{suffix}",
        secrets.token_hex(12),
    )
    db_url = _create_database(admin_url, db_name)
    _alembic(db_url, "stamp", "041")
    _alembic(db_url, "upgrade", "042")

    admin = create_engine(db_url)
    with admin.begin() as conn:
        conn.execute(
            text(
                f'CREATE ROLE "{role}" LOGIN PASSWORD :pw NOSUPERUSER NOBYPASSRLS '
                "NOCREATEDB NOCREATEROLE"
            ),
            {"pw": password},
        )
        conn.execute(text(f'GRANT ALL ON SCHEMA public TO "{role}"'))
        conn.execute(
            text(
                f'GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO "{role}"'
            )
        )
        conn.execute(
            text(f'GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO "{role}"')
        )
        for tid in (T1, T2):
            conn.execute(
                text(
                    "INSERT INTO tenants (id, name, slug, is_active) VALUES (:i, :n, :s, true)"
                ),
                {"i": tid, "n": f"tenant-{tid}", "s": f"tenant-{tid}"},
            )
        for tid in (T1, T2):
            for tag in ("a", "b"):
                conn.execute(
                    text("INSERT INTO organizations (name, tenant_id) VALUES (:n, :t)"),
                    {"n": f"org-{tid}-{tag}", "t": tid},
                )
                conn.execute(
                    text(
                        "INSERT INTO webhooks (name, url, is_active, tenant_id) VALUES (:n, 'http://x', true, :t)"
                    ),
                    {"n": f"hook-{tid}-{tag}", "t": tid},
                )
        conn.execute(
            text(
                "INSERT INTO organizations (name, tenant_id) VALUES ('org-orphan', NULL)"
            )
        )

    app_url = (
        make_url(db_url)
        .set(username=role, password=password)
        .render_as_string(hide_password=False)
    )
    app = create_engine(app_url, pool_size=1, max_overflow=0)
    rls.install_rls_hooks()
    try:
        yield RlsEnv(db_url, app_url, db_name, role, admin, app)
    finally:
        app.dispose()
        admin.dispose()
        _drop_database(admin_url, db_name)
        server = create_engine(make_url(admin_url), isolation_level="AUTOCOMMIT")
        with server.connect() as conn:
            conn.execute(text(f'DROP ROLE IF EXISTS "{role}"'))
        server.dispose()


@pytest.fixture()
def _reset_rls_state():
    """Isolate kill-switch/tenant/audit-sink state per test, then restore it.

    The audit sink registered by ``create_app`` (session fixture) must survive:
    other tests rely on it for SUPER_ADMIN scope.
    """
    previous_sink, previous_switch = rls._audit_sink, rls._switch
    rls.clear_tenant()
    rls._switch = None
    rls.register_audit_sink(None)
    yield
    rls.clear_tenant()
    rls._switch = previous_switch
    rls.register_audit_sink(previous_sink)


def _run(
    engine: Engine, sql: str, tenant: int | None = None, system: bool = False, **params
):
    """Execute one statement in its own transaction under the given RLS context."""
    with contextlib.ExitStack() as stack:
        if tenant is not None:
            stack.enter_context(tenant_context(tenant))
        if system:
            stack.enter_context(
                system_scope(SystemScopeReason.BACKGROUND_SWEEP, "test")
            )
        with engine.begin() as conn:
            result = conn.execute(text(sql), params)
            rows = [tuple(r) for r in result] if result.returns_rows else []
            return rows, result.rowcount


def _names(
    engine: Engine, table: str, tenant: int | None = None, system: bool = False
) -> set[str]:
    rows, _ = _run(engine, f"SELECT name FROM {table} ORDER BY name", tenant, system)
    return {r[0] for r in rows}


def _admin_names(env: RlsEnv, table: str) -> set[str]:
    rows, _ = _run(env.admin, f"SELECT name FROM {table}")
    return {r[0] for r in rows}


def _load_migration():
    """Import migration 042 by path (alembic version dirs are not packages)."""
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "rls_migration_042", ROOT / "alembic/versions/042_rls_tenant_isolation.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _dummy_value(column):
    """Return a harmless value for a NOT NULL column based on its SQL type."""
    import json
    from datetime import UTC, datetime

    import sqlalchemy as sa

    ctype = column.type
    if isinstance(ctype, sa.Enum):
        return ctype.enums[0]
    if isinstance(ctype, sa.JSON):
        return json.dumps({})
    if isinstance(ctype, sa.Boolean):
        return False
    if isinstance(ctype, sa.DateTime):
        return datetime.now(UTC)
    if isinstance(ctype, sa.Integer):
        return 1
    return f"x-{uuid.uuid4().hex[:12]}"


def _seed(conn, table: str, **values) -> int:
    """INSERT one row into ``table`` (admin connection) and return its id.

    Fills every NOT NULL column without a server default using a type-based
    dummy, plus created_at/updated_at when present, so tests only spell out the
    columns they care about. Pair with ``SET session_replication_role = replica``
    to skip FK parents that are irrelevant to the behaviour under test.
    """
    from datetime import UTC, datetime

    from apps.api.models.base import Base

    _import_all_models()
    columns = Base.metadata.tables[table].columns
    row = dict(values)
    for column in columns:
        if column.name in row or column.primary_key:
            continue
        if column.name in ("created_at", "updated_at"):
            row[column.name] = datetime.now(UTC)
        elif not column.nullable and column.server_default is None:
            row[column.name] = _dummy_value(column)
    names = ", ".join(f'"{name}"' for name in row)
    binds = ", ".join(f":{name}" for name in row)
    return conn.execute(
        text(f'INSERT INTO "{table}" ({names}) VALUES ({binds}) RETURNING id'), row
    ).scalar()


@pytest.fixture()
def dal(rls_env):
    """penguin-dal (the real runtime data layer) under the NOSUPERUSER app role."""
    from penguin_dal import DAL

    db = DAL(rls_env.app_url, pool_size=2, migrate=False)
    yield db
    db.close()
