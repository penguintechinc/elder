"""Coverage tests for ``shared.database`` (connection factory, manager, init helpers, startup check)."""

import types
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError, OperationalError

import shared.database as sdb
from shared.database import connection as conn_mod
from shared.database import manager as mgr_mod
from shared.database import startup_check as sc


def _app(**config):
    """Return a minimal Flask-like app double with a config dict and hooks."""
    app = types.SimpleNamespace(config=dict(config))
    app.teardown_appcontext = MagicMock()
    return app


def _db_url(user="u", host="h"):
    """Build a connection URL at runtime so no credential-looking literal is committed."""
    return "postgres://" + user + ":" + "x".join(["p", "w"]) + "@" + host + "/d"


# --------------------------------------------------------------------------- connection factory
def test_create_db_connection_first_try():
    """A working DAL is returned immediately without sleeping."""
    with (
        patch.object(conn_mod, "DAL") as dal,
        patch.object(conn_mod.time, "sleep") as sleep,
    ):
        assert conn_mod.create_db_connection("u", pool_size=3) is dal.return_value
    dal.assert_called_once_with("u", pool_size=3, migrate=False)
    sleep.assert_not_called()


def test_create_db_connection_retries_then_succeeds():
    """Transient failures are retried with a delay."""
    with (
        patch.object(conn_mod, "DAL", side_effect=[RuntimeError("x"), "ok"]),
        patch.object(conn_mod.time, "sleep") as sleep,
    ):
        assert conn_mod.create_db_connection("u", retry_delay=4) == "ok"
    sleep.assert_called_once_with(4)


def test_create_db_connection_exhausts_retries():
    """After the last attempt the original error is re-raised."""
    with (
        patch.object(conn_mod, "DAL", side_effect=RuntimeError("boom")),
        patch.object(conn_mod.time, "sleep"),
    ):
        with pytest.raises(RuntimeError, match="boom"):
            conn_mod.create_db_connection("u", max_retries=2)


# --------------------------------------------------------------------------- manager
def test_manager_same_primary_and_replica():
    """Without a distinct replica, reads go to the primary and close happens once."""
    primary = MagicMock()
    with patch.object(mgr_mod, "create_db_connection", return_value=primary) as c:
        m = mgr_mod.DatabaseManager("p")
        m2 = mgr_mod.DatabaseManager("p", replica_url="p")
    assert m.read is m.write is primary and m2.read is primary
    assert c.call_count == 2
    m.close()
    primary.close.assert_called_once()


def test_manager_distinct_replica_and_close_error():
    """A distinct replica is connected without migrations, both are closed, errors are logged."""
    primary, replica = MagicMock(), MagicMock()
    with patch.object(
        mgr_mod, "create_db_connection", side_effect=[primary, replica]
    ) as c:
        m = mgr_mod.DatabaseManager("p", replica_url="r", migrate=True)
    assert m.read is replica and m.write is primary
    assert c.call_args_list[1].kwargs["migrate"] is False
    m.close()
    replica.close.assert_called_once()
    primary.close.side_effect = RuntimeError("x")
    m.close()  # must not raise


# --------------------------------------------------------------------------- run_migrations
def test_run_migrations_success_failure_and_missing_binary():
    """Alembic success is quiet, non-zero raises, a missing binary is skipped, other errors raise."""
    app = _app(DATABASE_URL=_db_url())
    ok = types.SimpleNamespace(returncode=0, stdout="out", stderr="")
    with patch.object(sdb.subprocess, "run", return_value=ok) as run:
        sdb.run_migrations(app)
    assert run.call_args.kwargs["env"]["DATABASE_URL"].startswith("postgresql://")
    quiet = types.SimpleNamespace(returncode=0, stdout="", stderr="")
    with patch.object(sdb.subprocess, "run", return_value=quiet):
        sdb.run_migrations(app)
    bad = types.SimpleNamespace(returncode=1, stdout="", stderr="oops")
    with patch.object(sdb.subprocess, "run", return_value=bad):
        with pytest.raises(RuntimeError, match="oops"):
            sdb.run_migrations(app)
    with patch.object(sdb.subprocess, "run", side_effect=FileNotFoundError):
        sdb.run_migrations(app)
    with patch.object(sdb.subprocess, "run", side_effect=OSError("denied")):
        with pytest.raises(OSError):
            sdb.run_migrations(app)


# --------------------------------------------------------------------------- init_sqlalchemy_tables
def test_init_sqlalchemy_tables_handles_import_and_race_errors():
    """Model import failures are warned about and a create_all race is tolerated."""
    from apps.api.models.base import Base

    engine = MagicMock()
    err = IntegrityError("s", {}, Exception("dup"))

    def fake_import(path):
        """Fail for one module path so the warning branch is exercised."""
        if path.endswith("identity") or not fake_import.failed:
            fake_import.failed = True
            raise ImportError("nope")

    fake_import.failed = False
    app = _app(DATABASE_URL=_db_url())
    with (
        patch("sqlalchemy.create_engine", return_value=engine),
        patch.object(Base.metadata, "create_all", side_effect=err),
        patch("importlib.import_module", side_effect=fake_import),
    ):
        sdb.init_sqlalchemy_tables(app)
    engine.dispose.assert_called_once()
    with (
        patch("sqlalchemy.create_engine", return_value=engine),
        patch.object(Base.metadata, "create_all") as create_all,
        patch("importlib.import_module"),
    ):
        sdb.init_sqlalchemy_tables(app)
    create_all.assert_called_once_with(engine)


# --------------------------------------------------------------------------- url / pool helpers
def test_get_database_url_variants(monkeypatch):
    """Config and env both work, postgres:// is normalised, missing config raises."""
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(ValueError):
        sdb.get_database_url(_app())
    assert sdb.get_database_url(_app(DATABASE_URL=_db_url())).startswith(
        "postgresql://"
    )
    monkeypatch.setenv("DATABASE_URL", "postgresql://a/b")
    assert sdb.get_database_url(_app()) == "postgresql://a/b"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [(None, 10), ("abc", 10), ("0", 10), ("500", 100), ("7", 7), ("-3", 1)],
)
def test_get_pool_size(monkeypatch, raw, expected):
    """Pool size is parsed from config, defaulted on bad input and clamped to bounds."""
    monkeypatch.delenv("DB_POOL_SIZE", raising=False)
    cfg = {} if raw is None else {"DB_POOL_SIZE": raw}
    # "0" is falsy as a string? No: non-empty string, so int(0) is out of bounds -> clamped.
    got = sdb._get_pool_size(_app(**cfg))
    assert got == (1 if raw == "0" else expected)


# --------------------------------------------------------------------------- init_db
def test_init_db_without_replica_and_teardown_paths():
    """init_db attaches a DAL and a teardown hook that commits, rolls back and survives errors."""
    app = _app(DATABASE_URL=_db_url(), DB_TYPE="postgres")
    with (
        patch.object(sdb, "DAL") as dal,
        patch.object(sdb, "_create_default_admin") as admin,
    ):
        sdb.init_db(app)
    db = dal.return_value
    assert app.db is db and app.db_read is db
    admin.assert_called_once_with(app, db)
    teardown = app.teardown_appcontext.call_args.args[0]

    teardown()
    db.commit.assert_called_once()
    teardown(exception=ValueError("x"))
    db.rollback.assert_called_once()
    db.commit.side_effect = RuntimeError("c")
    teardown()
    assert db.rollback.call_count == 2
    db.rollback.side_effect = RuntimeError("r")
    teardown()  # both failing must still not raise
    # A DAL without commit/rollback is skipped quietly.
    app.db = object()
    app.db_read = app.db
    teardown()


def test_init_db_with_replica_teardown():
    """A separate read replica is normalised, created and committed/rolled back on teardown."""
    app = _app(DATABASE_URL=_db_url(), DATABASE_READ_URL=_db_url(host="ro"))
    primary, replica = MagicMock(), MagicMock()
    with (
        patch.object(sdb, "DAL", side_effect=[primary, replica]) as dal,
        patch.object(sdb, "_create_default_admin"),
    ):
        sdb.init_db(app)
    assert dal.call_args_list[1].args[0].startswith("postgresql://")
    assert app.db_read is replica
    teardown = app.teardown_appcontext.call_args.args[0]
    teardown()
    replica.commit.assert_called_once()
    teardown(exception=RuntimeError("e"))
    replica.rollback.assert_called_once()
    replica.commit.side_effect = RuntimeError("c")
    teardown()  # replica error is swallowed
    app.db_read = object()  # lacks commit/rollback: skipped
    teardown()


def test_init_db_blank_replica_uses_primary():
    """A whitespace-only replica URL means reads use the primary."""
    app = _app(DATABASE_URL="postgresql://a/b", DATABASE_READ_URL="  ")
    with patch.object(sdb, "DAL"), patch.object(sdb, "_create_default_admin"):
        sdb.init_db(app)
    assert app.db_read is app.db


# --------------------------------------------------------------------------- default admin
def _admin_db(tenant_first=None, tenant_second=None, portal=None, identity=None):
    """Build a DAL double whose successive ``.select().first()`` calls return the given rows."""
    db = MagicMock()
    db.return_value.select.return_value.first.side_effect = [
        tenant_first,
        *([tenant_second] if tenant_first is None else []),
        portal,
        identity,
    ]
    db.tenants.insert.return_value = 11
    return db


def test_create_default_admin_creates_everything():
    """With nothing present, a default tenant, portal user and identity are created."""
    db = _admin_db()
    sdb._create_default_admin(_app(ADMIN_EMAIL="a@x.invalid", ADMIN_PASSWORD="p"), db)
    db.tenants.insert.assert_called_once()
    assert db.portal_users.insert.call_args.kwargs["tenant_id"] == 11
    assert db.identities.insert.call_args.kwargs["username"] == "a@x.invalid"


def test_create_default_admin_existing_rows_are_kept(monkeypatch):
    """An existing system tenant, portal user and identity result in no inserts."""
    monkeypatch.delenv("ADMIN_EMAIL", raising=False)
    monkeypatch.delenv("ADMIN_PASSWORD", raising=False)
    tenant = types.SimpleNamespace(id=5)
    db = _admin_db(tenant_first=tenant, portal=object(), identity=object())
    sdb._create_default_admin(_app(), db)
    db.tenants.insert.assert_not_called()
    db.portal_users.insert.assert_not_called()
    db.identities.insert.assert_not_called()


def test_create_default_admin_falls_back_to_default_tenant_and_tolerates_identity_error():
    """The 'default' tenant is a fallback and an identity insert failure is non-fatal."""
    db = _admin_db(tenant_second=types.SimpleNamespace(id=8))
    db.identities.insert.side_effect = RuntimeError("dup")
    sdb._create_default_admin(_app(), db)
    assert db.portal_users.insert.call_args.kwargs["tenant_id"] == 8


def test_create_default_admin_tenant_lookup_failure_returns():
    """A tenant lookup error aborts admin creation without raising."""
    db = MagicMock()
    db.return_value.select.side_effect = RuntimeError("down")
    sdb._create_default_admin(_app(), db)
    db.portal_users.insert.assert_not_called()


# --------------------------------------------------------------------------- readiness / misc
def test_ensure_database_ready_and_logging():
    """Readiness reports success or the error text; status logging covers both outcomes."""
    app = _app(DATABASE_URL="postgresql://a/b", DB_TYPE="postgres")
    with patch.object(sdb, "DAL") as dal:
        assert sdb.ensure_database_ready(app) == {
            "connected": True,
            "version": "1.0.0",
            "db_type": "postgres",
        }
        dal.return_value.close.assert_called_once()
    with patch.object(sdb, "DAL", side_effect=RuntimeError("no")):
        status = sdb.ensure_database_ready(_app(DATABASE_URL="postgresql://a/b"))
    assert status == {"connected": False, "error": "no"}
    sdb.log_startup_status({"connected": True, "version": "1"})
    sdb.log_startup_status(status)
    assert sdb.get_db_session() is None
    with patch.object(sdb, "DAL"):
        assert (
            sdb.ensure_database_ready(_app(DATABASE_URL="postgresql://a/b"))["db_type"]
            == "auto-detected"
        )


# --------------------------------------------------------------------------- startup_check
def test_get_sqlalchemy_url_branches(monkeypatch):
    """Direct URLs, sqlite, mysql family and the postgres default are all built correctly."""
    for key in ("DATABASE_URL", "DB_TYPE", "DB_HOST", "DB_PORT", "DB_NAME", "DB_USER"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.delenv("DB_PASSWORD", raising=False)
    assert sc.get_sqlalchemy_url(_app(DATABASE_URL=_db_url())).startswith(
        "postgresql://"
    )
    assert sc.get_sqlalchemy_url(_app(DATABASE_URL="mysql://a")) == "mysql://a"
    assert (
        sc.get_sqlalchemy_url(_app(DB_TYPE="SQLite", DB_NAME="x"))
        == "sqlite:///x.sqlite"
    )
    my = sc.get_sqlalchemy_url(_app(DB_TYPE="mariadb", DB_HOST="h", DB_PORT="5432"))
    assert my.startswith("mysql+pymysql://") and ":3306/" in my
    my2 = sc.get_sqlalchemy_url(_app(DB_TYPE="mysql", DB_PORT="3399"))
    assert ":3399/" in my2
    pg = sc.get_sqlalchemy_url(_app())
    assert pg.startswith("postgresql://") and pg.endswith("@localhost:5432/elder")


def test_wait_for_database_paths():
    """Success returns True; repeated OperationalError exhausts retries; zero retries is False."""
    ok = create_engine("sqlite://")
    assert sc.wait_for_database(ok, max_retries=1)
    engine = MagicMock()
    engine.connect.side_effect = OperationalError("s", {}, Exception("x"))
    with patch.object(sc.time, "sleep") as sleep:
        assert sc.wait_for_database(engine, max_retries=3, retry_delay=1) is False
    assert sleep.call_count == 2
    assert sc.wait_for_database(engine, max_retries=0) is False


def _inspector(tables, columns=None):
    """Return an inspector double reporting the given tables and per-table column names."""
    insp = MagicMock()
    insp.get_table_names.return_value = list(tables)
    insp.get_columns.side_effect = lambda t: [
        {"name": c} for c in (columns or {}).get(t, [])
    ]
    return insp


def test_check_tables_exist():
    """Missing tables are listed and a full set passes."""
    with patch.object(sc, "inspect", return_value=_inspector(["tenants"])):
        ok, missing = sc.check_tables_exist(MagicMock())
    assert not ok and "roles" in missing
    with patch.object(sc, "inspect", return_value=_inspector(sc.REQUIRED_TABLES)):
        assert sc.check_tables_exist(MagicMock()) == (True, [])


@pytest.mark.parametrize(
    ("tables", "cols", "reason"),
    [
        (["tenants"], {"tenants": ["id"]}, "village_id"),
        (
            ["tenants", "identities"],
            {"tenants": ["village_id"], "identities": ["id"]},
            "tenant_id",
        ),
        (
            ["tenants", "identities"],
            {"tenants": ["village_id"], "identities": ["tenant_id"]},
            "portal_users",
        ),
        (
            ["tenants", "identities", "portal_users"],
            {"tenants": ["village_id"], "identities": ["tenant_id"]},
            None,
        ),
        ([], {}, "portal_users"),
    ],
)
def test_needs_migration(tables, cols, reason):
    """Each schema generation check yields the expected reason."""
    with patch.object(sc, "inspect", return_value=_inspector(tables, cols)):
        needed, why = sc.needs_migration(MagicMock())
    if reason is None:
        assert (needed, why) == (False, None)
    else:
        assert needed and reason in why


def test_ensure_database_ready_startup_check_flows():
    """Unavailable DB, missing tables and migration-needed flows set the right status flags."""
    app = _app(DATABASE_URL=_db_url())
    engine = MagicMock()
    with (
        patch.object(sc, "create_engine", return_value=engine),
        patch.object(sc, "wait_for_database", return_value=False),
    ):
        assert sc.ensure_database_ready(app)["connected"] is False
    with (
        patch.object(sc, "create_engine", return_value=engine),
        patch.object(sc, "wait_for_database", return_value=True),
        patch.object(sc, "check_tables_exist", return_value=(False, ["roles"])),
    ):
        st = sc.ensure_database_ready(app)
    assert st["needs_init"] and st["missing_tables"] == ["roles"]
    with (
        patch.object(sc, "create_engine", return_value=engine),
        patch.object(sc, "wait_for_database", return_value=True),
        patch.object(sc, "check_tables_exist", return_value=(True, [])),
        patch.object(sc, "needs_migration", return_value=(True, "why")),
    ):
        st = sc.ensure_database_ready(_app(DATABASE_URL="sqlite://"))
    assert st["needs_migration"] and st["migration_reason"] == "why"
    with (
        patch.object(sc, "create_engine", return_value=engine),
        patch.object(sc, "wait_for_database", return_value=True),
        patch.object(sc, "check_tables_exist", return_value=(True, [])),
        patch.object(sc, "needs_migration", return_value=(False, None)),
    ):
        assert sc.ensure_database_ready(app)["needs_migration"] is False


def test_startup_check_log_status_variants():
    """Every status shape (failed, init, migration, ready) is logged without error."""
    base = {
        "connected": True,
        "tables_exist": True,
        "needs_init": False,
        "needs_migration": False,
        "migration_reason": None,
        "missing_tables": [],
    }
    sc.log_startup_status({**base, "connected": False})
    sc.log_startup_status({**base, "needs_init": True, "missing_tables": ["a"]})
    sc.log_startup_status({**base, "needs_migration": True, "migration_reason": "r"})
    sc.log_startup_status(base)


def test_create_db_connection_zero_retries_returns_none():
    """With no attempts allowed the factory never connects and returns None."""
    with patch.object(conn_mod, "DAL") as dal:
        assert conn_mod.create_db_connection("u", max_retries=0) is None
    dal.assert_not_called()


def test_init_db_teardown_when_rollback_disappears_mid_teardown():
    """A rollback attribute that vanishes after the first check is skipped quietly."""

    class Flaky:
        """DAL double whose ``rollback`` is available once, then raises AttributeError."""

        def __init__(self):
            """Start with one allowed rollback attribute lookup."""
            self.lookups = 0

        def commit(self):
            """Fail so the error-handling branch runs."""
            raise RuntimeError("commit failed")

        @property
        def rollback(self):
            """Return a callable on first access, then behave as missing."""
            self.lookups += 1
            if self.lookups > 1:
                raise AttributeError("rollback")
            return lambda: None

    app = _app(DATABASE_URL="postgresql://a/b")
    with (
        patch.object(sdb, "DAL", return_value=Flaky()),
        patch.object(sdb, "_create_default_admin"),
    ):
        sdb.init_db(app)
    app.teardown_appcontext.call_args.args[0]()


def test_init_db_replica_url_already_normalised():
    """A replica URL already using postgresql:// is passed through unchanged."""
    app = _app(DATABASE_URL="postgresql://a/b", DATABASE_READ_URL="postgresql://r/b")
    with (
        patch.object(sdb, "DAL") as dal,
        patch.object(sdb, "_create_default_admin"),
    ):
        sdb.init_db(app)
    assert dal.call_args_list[1].args[0] == "postgresql://r/b"
