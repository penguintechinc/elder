"""Unit tests for shared/database/rls.py (no database required).

Real-PostgreSQL behaviour (policies, pooling, role posture, migrations) is covered
by tests/integration/test_rls_*.py; these tests pin the pure logic: context
binding, system-scope audit rules, kill-switch direction, the posture/strict gate
and the exact GUC values the per-transaction listener emits.
"""

import asyncio
import subprocess  # nosec B404 - fixed argv, interpreter under test
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, text

from shared.database import rls
from shared.database.rls import (
    RlsAuditError,
    RlsConfigurationError,
    SystemScope,
    SystemScopeReason,
    check_rls_posture,
    system_scope,
    system_scope_async,
    tenant_context,
)

ROOT = Path(__file__).resolve().parents[2]


class _FakeCounter:
    """OTel counter stand-in recording (amount, attributes) calls."""

    def __init__(self):
        self.calls = []

    def add(self, amount, attributes):
        self.calls.append((amount, attributes))


@pytest.fixture(autouse=True)
def _reset_rls_state():
    """Isolate module-level RLS state per test, then restore what was there."""
    previous = (
        rls._audit_sink,
        rls._switch,
        rls._enforcement_active,
        dict(rls._counters),
    )
    rls.clear_tenant()
    rls.register_audit_sink(None)
    rls._switch = None
    rls._enforcement_active = False
    yield
    if rls._switch is not None:
        rls._switch.stop()
    rls._audit_sink, rls._switch, rls._enforcement_active = previous[:3]
    rls._counters.clear()
    rls._counters.update(previous[3])
    rls.clear_tenant()


@pytest.fixture()
def counters():
    """Install fake counters for the metrics this module emits; returns them by name."""
    fakes = {
        name: _FakeCounter()
        for name in (
            "elder.rls.system_scope.entered",
            "elder.rls.unbound_transactions",
            "elder.rls.posture.unprotected_tables",
            "elder.rls.grpc_rejected",
        )
    }
    rls._counters.update(fakes)
    return fakes


class _RecordingConn:
    """Fake PostgreSQL Connection capturing the set_config call."""

    def __init__(self, dialect="postgresql"):
        self.dialect = SimpleNamespace(name=dialect)
        self.calls = []

    def execute(self, statement, params):
        self.calls.append((str(statement), params))


@pytest.mark.parametrize("bad", [True, False, 0, -3, "abc", "", "1.5", 1.5, None, [1]])
def test_set_tenant_rejects_invalid(bad):
    with pytest.raises(ValueError):
        rls.set_tenant(bad)
    assert rls.current_tenant_id() is None


@pytest.mark.parametrize("raw,expected", [(7, 7), ("7", 7), ("0042", 42)])
def test_set_tenant_accepts_positive_ints(raw, expected):
    rls.set_tenant(raw)
    assert rls.current_tenant_id() == expected


def test_clear_tenant_unbinds():
    rls.set_tenant(5)
    rls.clear_tenant()
    assert rls.current_tenant_id() is None


def test_tenant_context_nests_and_restores_even_on_error():
    with tenant_context(1) as outer:
        assert outer == 1
        with tenant_context("2"):
            assert rls.current_tenant_id() == 2
        assert rls.current_tenant_id() == 1
        with pytest.raises(RuntimeError):
            with tenant_context(3):
                raise RuntimeError("boom")
        assert rls.current_tenant_id() == 1
    assert rls.current_tenant_id() is None


def test_tenant_context_rejects_invalid_before_binding():
    with pytest.raises(ValueError):
        with tenant_context(0):
            pytest.fail("must not enter")
    assert rls.current_tenant_id() is None


def test_validated_tenant_message_does_not_echo_input():
    with pytest.raises(ValueError) as exc:
        rls._validated_tenant("'; DROP TABLE x;--")
    assert "DROP" not in str(exc.value)


def test_system_scope_requires_purpose():
    with pytest.raises(ValueError, match="purpose"):
        with system_scope(SystemScopeReason.BACKGROUND_SWEEP, ""):
            pytest.fail("must not enter")


def test_super_admin_scope_requires_actor():
    with pytest.raises(ValueError, match="actor"):
        with system_scope(SystemScopeReason.SUPER_ADMIN, "p"):
            pytest.fail("must not enter")


def test_super_admin_scope_refused_without_sink():
    assert rls.current_system_scope() is None
    with pytest.raises(RlsAuditError, match="no audit sink"):
        with system_scope(SystemScopeReason.SUPER_ADMIN, "p", actor="a"):
            pytest.fail("must not enter")
    assert rls.current_system_scope() is None


def test_super_admin_scope_refused_when_sink_fails():
    def broken(_scope):
        raise OSError("db down")

    rls.register_audit_sink(broken)
    with pytest.raises(RlsAuditError, match="audit write failed"):
        with system_scope(SystemScopeReason.SUPER_ADMIN, "p", actor="a"):
            pytest.fail("must not enter")
    assert rls.current_system_scope() is None


def test_super_admin_scope_audited_via_sink_and_reset_on_exit():
    seen = []
    rls.register_audit_sink(seen.append)
    with system_scope(SystemScopeReason.SUPER_ADMIN, "tenants.stats", actor="u:1") as s:
        assert rls.current_system_scope() is s
        assert s == SystemScope(SystemScopeReason.SUPER_ADMIN, "tenants.stats", "u:1")
    assert seen == [s]
    assert rls.current_system_scope() is None


@pytest.mark.parametrize(
    "reason",
    [
        SystemScopeReason.PUBLIC_TOKEN,
        SystemScopeReason.AUTH_BOOTSTRAP,
        SystemScopeReason.BACKGROUND_SWEEP,
        SystemScopeReason.MAINTENANCE,
    ],
)
def test_non_privileged_scopes_never_reach_the_durable_sink(reason, counters):
    seen = []
    rls.register_audit_sink(seen.append)
    with system_scope(reason, "p") as scope:
        assert rls.current_system_scope() is scope
    assert seen == []
    assert counters["elder.rls.system_scope.entered"].calls == [
        (1, {"reason": reason.name})
    ]


def test_public_token_flood_writes_nothing_and_counts_every_entry(counters):
    seen = []
    rls.register_audit_sink(seen.append)
    for _ in range(25):
        with system_scope(SystemScopeReason.PUBLIC_TOKEN, "streams.webhook_lookup"):
            pass
    assert seen == []
    assert len(counters["elder.rls.system_scope.entered"].calls) == 25


def test_non_privileged_scope_works_without_any_sink():
    with system_scope(SystemScopeReason.AUTH_BOOTSTRAP, "login"):
        assert rls.current_system_scope() is not None


def test_system_scope_nesting_restores_outer():
    with system_scope(SystemScopeReason.BACKGROUND_SWEEP, "outer") as outer:
        with system_scope(SystemScopeReason.PUBLIC_TOKEN, "inner"):
            assert rls.current_system_scope().purpose == "inner"
        assert rls.current_system_scope() is outer


def test_async_super_admin_audit_runs_off_the_event_loop_thread():
    audit_threads = []

    def sink(scope):
        audit_threads.append(threading.get_ident())

    rls.register_audit_sink(sink)

    async def main():
        loop_thread = threading.get_ident()
        async with system_scope_async(
            SystemScopeReason.SUPER_ADMIN, "p", actor="u:1"
        ) as scope:
            assert rls.current_system_scope() is scope
        return loop_thread

    loop_thread = asyncio.run(main())
    assert audit_threads and audit_threads[0] != loop_thread
    assert rls.current_system_scope() is None


def test_async_scope_refused_without_sink_and_validates_arguments():
    async def refused():
        async with system_scope_async(SystemScopeReason.SUPER_ADMIN, "p", actor="u:1"):
            pytest.fail("must not enter")

    with pytest.raises(RlsAuditError):
        asyncio.run(refused())

    async def invalid():
        async with system_scope_async(SystemScopeReason.SUPER_ADMIN, "p"):
            pytest.fail("must not enter")

    with pytest.raises(ValueError):
        asyncio.run(invalid())


def test_async_public_token_scope_stays_inline_and_unaudited(counters):
    seen = []
    rls.register_audit_sink(seen.append)

    async def main():
        async with system_scope_async(SystemScopeReason.PUBLIC_TOKEN, "p"):
            assert rls.current_system_scope() is not None
        return threading.get_ident()

    asyncio.run(main())
    assert seen == []
    assert len(counters["elder.rls.system_scope.entered"].calls) == 1


def test_count_metric_creates_caches_and_tolerates_failures(monkeypatch):
    created = []

    class _Meter:
        def create_counter(self, name, description):
            created.append(name)
            return _FakeCounter()

    monkeypatch.setattr(
        rls, "_otel_metrics", SimpleNamespace(get_meter=lambda _name: _Meter())
    )
    rls._counters.pop("c.test", None)
    rls.count_metric("c.test", "d", {"k": "v"})
    rls.count_metric("c.test", "d")
    assert created == ["c.test"]
    assert rls._counters["c.test"].calls == [(1, {"k": "v"}), (1, {})]

    class _Broken:
        def add(self, *_a):
            raise RuntimeError("exporter down")

    rls._counters["c.broken"] = _Broken()
    rls.count_metric("c.broken", "d")  # never raises into a transaction
    monkeypatch.setattr(rls, "_otel_metrics", None)
    rls.count_metric("c.none", "d")  # no OTel installed: silent no-op


def test_on_begin_unbound_is_fail_closed_empty_values_and_counted(counters):
    conn = _RecordingConn()
    rls._on_begin(conn)
    (_sql, params) = conn.calls[0]
    assert params == {
        "g_tenant": "app.tenant_id",
        "tenant": "",
        "g_scope": "app.rls_scope",
        "scope": "",
    }
    assert len(counters["elder.rls.unbound_transactions"].calls) == 1


def test_on_begin_stamps_tenant_and_is_not_counted_as_unbound(counters):
    conn = _RecordingConn()
    with tenant_context(42):
        rls._on_begin(conn)
    params = conn.calls[0][1]
    assert (params["tenant"], params["scope"]) == ("42", "")
    assert "set_config" in conn.calls[0][0] and ", true)" in conn.calls[0][0]
    assert counters["elder.rls.unbound_transactions"].calls == []


def test_on_begin_stamps_system_scope(counters):
    conn = _RecordingConn()
    with system_scope(SystemScopeReason.BACKGROUND_SWEEP, "sweep"):
        rls._on_begin(conn)
    assert conn.calls[0][1]["scope"] == "system"
    assert conn.calls[0][1]["tenant"] == ""
    assert counters["elder.rls.unbound_transactions"].calls == []


def test_on_begin_ignores_non_postgres():
    conn = _RecordingConn(dialect="sqlite")
    with tenant_context(1):
        rls._on_begin(conn)
    assert conn.calls == []


def test_listener_is_noop_on_sqlite_and_install_is_idempotent():
    rls.uninstall_rls_hooks()
    assert rls.install_rls_hooks() is True
    assert rls.install_rls_hooks() is False
    engine = create_engine("sqlite://")
    with tenant_context(1):
        with engine.begin() as conn:
            assert conn.execute(text("SELECT 1")).scalar() == 1
    rls.uninstall_rls_hooks()
    rls.uninstall_rls_hooks()
    rls.install_rls_hooks()


def test_importing_shared_database_installs_the_hook_in_a_fresh_interpreter():
    code = (
        "from sqlalchemy import event; from sqlalchemy.engine import Engine\n"
        "import shared.database\n"
        "from shared.database import rls\n"
        "assert event.contains(Engine, 'begin', rls._on_begin)\n"
    )
    result = subprocess.run(  # nosec B603 - fixed argv
        [sys.executable, "-c", code],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )
    assert result.returncode == 0, result.stderr[-1500:]


# ----- kill-switch: direction of failure ------------------------------------------


def test_kill_switch_unconfigured_is_off():
    assert rls.kill_switch_active() is False


def test_kill_switch_initial_state_is_off_before_any_evaluation():
    switch = rls._KillSwitch(lambda: True, 30.0)
    assert switch.state is False  # unseen/OFF = RLS on, until the flag says otherwise


def test_probe_error_on_first_evaluation_leaves_rls_on():
    def boom():
        raise RuntimeError("posthog unreachable")

    rls.configure_kill_switch(boom, start_thread=False)
    assert rls.kill_switch_active() is False
    conn = _RecordingConn()
    rls._on_begin(conn)
    assert conn.calls[0][1]["scope"] == ""  # no system scope: RLS enforced


def test_flag_outage_while_off_never_turns_rls_off():
    state = {"boom": False}

    def probe():
        if state["boom"]:
            raise RuntimeError("posthog down")
        return False

    rls.configure_kill_switch(probe, start_thread=False)
    state["boom"] = True
    for _ in range(5):
        assert rls._switch.refresh() is False
    assert rls.kill_switch_active() is False


def test_flag_outage_while_on_keeps_last_known_state_documented_tradeoff():
    state = {"boom": False}

    def probe():
        if state["boom"]:
            raise RuntimeError("posthog down")
        return True

    rls.configure_kill_switch(probe, start_thread=False)
    assert rls.kill_switch_active() is True
    state["boom"] = True
    assert rls._switch.refresh() is True  # last-known value, not reset to OFF


def test_kill_switch_refresh_tracks_probe_changes():
    state = {"value": True}
    rls.configure_kill_switch(lambda: state["value"], start_thread=False)
    assert rls.kill_switch_active() is True
    state["value"] = False
    assert rls._switch.refresh() is False
    assert rls.kill_switch_active() is False


def test_on_begin_kill_switch_forces_system_scope():
    rls.configure_kill_switch(lambda: True, start_thread=False)
    conn = _RecordingConn()
    rls._on_begin(conn)
    assert conn.calls[0][1]["scope"] == "system"
    rls.configure_kill_switch(lambda: False, start_thread=False)
    conn2 = _RecordingConn()
    rls._on_begin(conn2)
    assert conn2.calls[0][1]["scope"] == ""


def test_kill_switch_poller_thread_updates_state_and_stops():
    flips = {"value": False}
    rls.configure_kill_switch(
        lambda: flips["value"], interval_s=0.01, start_thread=True
    )
    assert rls.kill_switch_active() is False
    flips["value"] = True
    deadline = time.monotonic() + 2
    while not rls.kill_switch_active() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert rls.kill_switch_active() is True
    old = rls._switch
    rls.configure_kill_switch(lambda: False, start_thread=False)  # replaces + stops old
    assert old._stop.is_set()
    assert rls._switch is not old


def test_default_probe_is_false_when_flag_client_unavailable(monkeypatch):
    monkeypatch.setitem(sys.modules, "apps.api.common.flags.posthog_client", None)
    assert rls._default_probe() is False


@pytest.mark.parametrize("flag_value,expected", [(False, False), (True, True)])
def test_default_probe_evaluates_documented_flag_with_off_default(
    monkeypatch, flag_value, expected
):
    calls = []

    def fake_flag_enabled(key, distinct_id, default=False):
        calls.append((key, distinct_id, default))
        return flag_value

    monkeypatch.setitem(
        sys.modules,
        "apps.api.common.flags.posthog_client",
        SimpleNamespace(flag_enabled=fake_flag_enabled),
    )
    assert rls._default_probe() is expected
    assert calls == [("elder.disable-rls", "elder-platform", False)]


# ----- posture / strict gate -------------------------------------------------------


class _FakeResult:
    def __init__(self, one=None, rows=(), scalars=()):
        self._one, self._rows, self._scalars = one, list(rows), list(scalars)

    def one(self):
        return self._one

    def all(self):
        return self._rows

    def scalars(self):
        return iter(self._scalars)


class _FakeEngine:
    """Engine stand-in answering the three posture queries from canned data."""

    def __init__(self, superuser=False, bypassrls=False, tables=(), unprotected=()):
        """``tables`` is a list of (name, forced, has_policy) rows."""
        self._results = [
            _FakeResult(
                one=SimpleNamespace(
                    rolname="app", rolsuper=superuser, rolbypassrls=bypassrls
                )
            ),
            _FakeResult(rows=tables),
            _FakeResult(scalars=unprotected),
        ]

    def connect(self):
        results = self._results

        class _Conn:
            def __enter__(self_inner):
                return self_inner

            def __exit__(self_inner, *exc):
                return False

            def execute(self_inner, _stmt, _params=None):
                return results.pop(0)

        return _Conn()


def _all_protected(n=4):
    return [(f"t{i}", True, True) for i in range(n)]


def test_posture_enforcing_role_and_all_tables_protected():
    posture = check_rls_posture(
        _FakeEngine(tables=_all_protected(), unprotected=("identities",)), strict=True
    )
    assert posture.enforcing and posture.role_enforces and not posture.bypassed
    assert (posture.protected_tables, posture.expected_tables) == (4, 4)
    assert posture.unprotected_tenant_tables == ("identities",)
    assert posture.problems == () and rls.enforcement_active()


@pytest.mark.parametrize("superuser,bypass", [(True, False), (False, True)])
def test_strict_raises_for_a_bypassing_role_even_with_every_table_protected(
    superuser, bypass
):
    engine = _FakeEngine(superuser, bypass, _all_protected())
    posture = check_rls_posture(engine)
    assert posture.bypassed and not posture.enforcing and not rls.enforcement_active()
    with pytest.raises(RlsConfigurationError, match="superuser/BYPASSRLS"):
        check_rls_posture(_FakeEngine(superuser, bypass, _all_protected()), strict=True)


def test_strict_raises_when_the_migration_is_not_applied():
    tables = [(f"t{i}", False, False) for i in range(3)]
    posture = check_rls_posture(_FakeEngine(tables=tables))  # non-strict: logs, returns
    assert posture.protected_tables == 0 and not posture.enforcing
    assert not rls.enforcement_active()
    with pytest.raises(
        RlsConfigurationError, match="migration 043 not applied: 0 of 3 tables"
    ):
        check_rls_posture(_FakeEngine(tables=tables), strict=True)


def test_strict_raises_on_partial_protection_naming_the_tables():
    tables = [("a", True, True), ("b", True, False), ("c", False, True)]
    with pytest.raises(RlsConfigurationError) as exc:
        check_rls_posture(_FakeEngine(tables=tables), strict=True)
    assert "1 of 3 tables protected" in str(exc.value)
    assert "b, c" in str(exc.value)


def test_strict_raises_when_no_rls_tables_exist_at_all():
    with pytest.raises(RlsConfigurationError, match="no RLS tables present"):
        check_rls_posture(_FakeEngine(tables=[]), strict=True)


def test_enforcement_flag_follows_the_latest_check_and_the_kill_switch():
    check_rls_posture(_FakeEngine(tables=_all_protected()))
    assert rls.enforcement_active() is True
    rls.configure_kill_switch(lambda: True, start_thread=False)
    assert rls.enforcement_active() is False  # kill-switch ON => not enforced
    rls.configure_kill_switch(lambda: False, start_thread=False)
    check_rls_posture(_FakeEngine(superuser=True, tables=_all_protected()))
    assert rls.enforcement_active() is False


def test_unprotected_tables_are_counted(counters):
    check_rls_posture(_FakeEngine(tables=[("a", False, False)]))
    assert len(counters["elder.rls.posture.unprotected_tables"].calls) == 1


@pytest.mark.parametrize(
    "value,expected",
    [
        ("true", True),
        ("TRUE", True),
        (" 1 ", True),
        ("yes", True),
        ("false", False),
        ("0", False),
        ("", False),
        ("on", False),
        ("2", False),
    ],
)
def test_strict_from_env_parsing(value, expected):
    assert rls.strict_from_env({"ELDER_RLS_STRICT": value}) is expected


def test_strict_from_env_unset_is_false_and_reads_process_env(monkeypatch):
    assert rls.strict_from_env({}) is False
    monkeypatch.setenv("ELDER_RLS_STRICT", "true")
    assert rls.strict_from_env() is True


def test_init_rls_defaults_strict_from_env_and_starts_kill_switch_once(monkeypatch):
    seen = []
    monkeypatch.setattr(
        rls,
        "check_rls_posture",
        lambda engine, strict=False: seen.append(strict) or "p",
    )
    configured = []
    monkeypatch.setattr(
        rls, "configure_kill_switch", lambda *a, **k: configured.append(1)
    )
    engine = SimpleNamespace(dialect=SimpleNamespace(name="postgresql"))
    monkeypatch.delenv("ELDER_RLS_STRICT", raising=False)
    assert rls.init_rls(engine) == "p"
    monkeypatch.setenv("ELDER_RLS_STRICT", "true")
    assert rls.init_rls(engine) == "p"
    assert rls.init_rls(engine, strict=False) == "p"
    assert seen == [False, True, False]
    assert configured == [
        1,
        1,
        1,
    ]  # _switch stays None in this stub, so each call starts it
    rls._switch = rls._KillSwitch(lambda: False, 30.0)
    rls.init_rls(engine)
    assert configured == [1, 1, 1]  # already configured: not restarted


def test_init_rls_skips_posture_for_non_postgres_engines():
    rls.configure_kill_switch(lambda: False, start_thread=False)
    assert rls.init_rls(create_engine("sqlite://")) is None


def test_shared_database_init_rls_delegates_to_rls(monkeypatch):
    from shared.database import _init_rls

    calls = []
    monkeypatch.setattr(
        rls, "init_rls", lambda engine, strict=None: calls.append(engine)
    )
    db = SimpleNamespace(engine="ENGINE")
    _init_rls(db)
    assert calls == ["ENGINE"]


def test_create_db_connection_runs_posture_once_and_never_retries_a_strict_failure(
    monkeypatch,
):
    from shared.database import connection

    built = []
    monkeypatch.setattr(
        connection,
        "DAL",
        lambda *a, **k: built.append(1) or SimpleNamespace(engine="E"),
    )

    def strict_fail(engine, strict=None):
        raise RlsConfigurationError("not enforced")

    monkeypatch.setattr(connection.rls, "init_rls", strict_fail)
    monkeypatch.setattr(
        connection.time, "sleep", lambda _s: pytest.fail("must not retry")
    )
    with pytest.raises(RlsConfigurationError):
        connection.create_db_connection("postgresql://x", max_retries=5)
    assert built == [1]

    seen = []
    monkeypatch.setattr(
        connection.rls, "init_rls", lambda engine, strict=None: seen.append(engine)
    )
    db = connection.create_db_connection("postgresql://x", max_retries=5)
    assert db.engine == "E" and seen == ["E"]


def test_worker_startup_lets_a_strict_rls_failure_escape_but_degrades_on_other_errors(
    monkeypatch,
):
    from apps.worker import main as worker_main

    service = worker_main.WorkerService.__new__(worker_main.WorkerService)
    monkeypatch.setattr(worker_main.settings, "database_url", "postgresql://x")

    class _Strict:
        def __init__(self, *a, **k):
            raise RlsConfigurationError("not enforced")

    monkeypatch.setattr("shared.database.manager.DatabaseManager", _Strict)
    with pytest.raises(RlsConfigurationError):
        service._init_database()

    class _Other:
        def __init__(self, *a, **k):
            raise RuntimeError("db down")

    monkeypatch.setattr("shared.database.manager.DatabaseManager", _Other)
    service._init_database()  # pre-existing behaviour: log and continue without DB
    assert getattr(service, "db_manager", None) is None
