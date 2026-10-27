"""Unit tests for shared/database/rls.py (no database required).

Real-PostgreSQL behaviour (policies, pooling, role posture) is covered by
tests/integration/test_rls_tenant_isolation.py; these tests pin the pure logic:
context binding, system-scope audit rules, kill-switch polling and the exact
GUC values the per-transaction listener emits.
"""

import sys
import time
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
    tenant_context,
)


@pytest.fixture(autouse=True)
def _reset_rls_state():
    """Isolate module-level RLS state per test, then restore what was there."""
    previous_sink, previous_switch = rls._audit_sink, rls._switch
    rls.clear_tenant()
    rls.register_audit_sink(None)
    rls._switch = None
    yield
    if rls._switch is not None:
        rls._switch.stop()
    rls._switch = previous_switch
    rls.register_audit_sink(previous_sink)
    rls.clear_tenant()


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


def test_background_scope_survives_failing_sink():
    def broken(_scope):
        raise OSError("db down")

    rls.register_audit_sink(broken)
    with system_scope(SystemScopeReason.BACKGROUND_SWEEP, "sweep") as s:
        assert rls.current_system_scope() is s


def test_background_scope_without_sink_is_allowed():
    with system_scope(SystemScopeReason.AUTH_BOOTSTRAP, "login"):
        assert rls.current_system_scope() is not None


def test_system_scope_nesting_restores_outer():
    with system_scope(SystemScopeReason.BACKGROUND_SWEEP, "outer") as outer:
        with system_scope(SystemScopeReason.PUBLIC_TOKEN, "inner"):
            assert rls.current_system_scope().purpose == "inner"
        assert rls.current_system_scope() is outer


def test_system_scope_increments_otel_counter(monkeypatch):
    counter = SimpleNamespace(
        calls=[], add=lambda n, attrs: counter.calls.append((n, attrs))
    )
    monkeypatch.setattr(rls, "_scope_counter", counter)
    with system_scope(SystemScopeReason.PUBLIC_TOKEN, "p"):
        pass
    assert counter.calls == [(1, {"reason": "PUBLIC_TOKEN"})]


def test_meter_counter_is_cached_and_optional(monkeypatch):
    monkeypatch.setattr(rls, "_scope_counter", None)
    first = rls._meter_counter()
    assert first is not None and rls._meter_counter() is first
    monkeypatch.setattr(rls, "_scope_counter", None)
    monkeypatch.setattr(rls, "_otel_metrics", None)
    assert rls._meter_counter() is None
    with system_scope(SystemScopeReason.PUBLIC_TOKEN, "no-otel"):
        pass


def test_on_begin_unbound_is_fail_closed_empty_values():
    conn = _RecordingConn()
    rls._on_begin(conn)
    (_sql, params) = conn.calls[0]
    assert params == {
        "g_tenant": "app.tenant_id",
        "tenant": "",
        "g_scope": "app.rls_scope",
        "scope": "",
    }


def test_on_begin_stamps_tenant():
    conn = _RecordingConn()
    with tenant_context(42):
        rls._on_begin(conn)
    params = conn.calls[0][1]
    assert (params["tenant"], params["scope"]) == ("42", "")
    assert "set_config" in conn.calls[0][0] and ", true)" in conn.calls[0][0]


def test_on_begin_stamps_system_scope():
    conn = _RecordingConn()
    with system_scope(SystemScopeReason.BACKGROUND_SWEEP, "sweep"):
        rls._on_begin(conn)
    assert conn.calls[0][1]["scope"] == "system"
    assert conn.calls[0][1]["tenant"] == ""


def test_on_begin_kill_switch_forces_system_scope():
    rls.configure_kill_switch(lambda: True, start_thread=False)
    conn = _RecordingConn()
    rls._on_begin(conn)
    assert conn.calls[0][1]["scope"] == "system"
    rls.configure_kill_switch(lambda: False, start_thread=False)
    conn2 = _RecordingConn()
    rls._on_begin(conn2)
    assert conn2.calls[0][1]["scope"] == ""


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


def test_kill_switch_unconfigured_is_off():
    assert rls.kill_switch_active() is False


def test_kill_switch_refresh_tracks_probe_and_keeps_last_on_error():
    state = {"value": True, "boom": False}

    def probe():
        if state["boom"]:
            raise RuntimeError("posthog down")
        return state["value"]

    rls.configure_kill_switch(probe, start_thread=False)
    assert rls.kill_switch_active() is True
    state["boom"] = True
    assert rls._switch.refresh() is True  # last-known value kept on error
    state.update(boom=False, value=False)
    assert rls._switch.refresh() is False
    assert rls.kill_switch_active() is False


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


class _FakeResult:
    def __init__(self, row=None, scalar=None, scalars=()):
        self._row, self._scalar, self._scalars = row, scalar, scalars

    def one(self):
        return self._row

    def scalar_one(self):
        return self._scalar

    def scalars(self):
        return iter(self._scalars)


class _FakeEngine:
    """Engine stand-in returning canned pg_roles/pg_class answers."""

    def __init__(self, superuser, bypassrls, protected, unprotected=()):
        self._results = [
            _FakeResult(
                row=SimpleNamespace(
                    rolname="app", rolsuper=superuser, rolbypassrls=bypassrls
                )
            ),
            _FakeResult(scalar=protected),
            _FakeResult(scalars=unprotected),
        ]

    def connect(self):
        results = self._results

        class _Conn:
            def __enter__(self_inner):
                return self_inner

            def __exit__(self_inner, *exc):
                return False

            def execute(self_inner, _stmt):
                return results.pop(0)

        return _Conn()


def test_posture_enforcing_role():
    posture = check_rls_posture(_FakeEngine(False, False, 76, ("identities",)))
    assert posture.role_enforces and not posture.bypassed
    assert posture.protected_tables == 76
    assert posture.unprotected_tenant_tables == ("identities",)


@pytest.mark.parametrize("superuser,bypass", [(True, False), (False, True)])
def test_posture_bypassing_role_logs_and_strict_raises(superuser, bypass):
    posture = check_rls_posture(_FakeEngine(superuser, bypass, 10))
    assert posture.bypassed and not posture.role_enforces
    with pytest.raises(RlsConfigurationError, match="NOT enforced"):
        check_rls_posture(_FakeEngine(superuser, bypass, 10), strict=True)


def test_posture_without_policies_is_not_bypassed_even_for_superuser():
    posture = check_rls_posture(_FakeEngine(True, False, 0), strict=True)
    assert not posture.bypassed
    assert posture.protected_tables == 0


def test_validated_tenant_message_does_not_echo_input():
    with pytest.raises(ValueError) as exc:
        rls._validated_tenant("'; DROP TABLE x;--")
    assert "DROP" not in str(exc.value)
