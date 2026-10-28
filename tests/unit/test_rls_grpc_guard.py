"""Unit tests for the gRPC fail-closed guard (apps/api/grpc/rls_guard.py)."""

import grpc
import pytest

from apps.api.grpc import rls_guard
from apps.api.grpc.servicers.elder_servicer import ElderServicer
from shared.database import rls
from shared.database.rls import SystemScopeReason, system_scope, tenant_context


class _Aborted(Exception):
    """Raised by the fake context the way grpc's ServicerContext.abort does."""


class _Ctx:
    def __init__(self):
        self.aborts = []

    def abort(self, code, details):
        self.aborts.append((code, details))
        raise _Aborted()


class _Counter:
    def __init__(self):
        self.calls = []

    def add(self, amount, attributes):
        self.calls.append((amount, attributes))


@pytest.fixture(autouse=True)
def _state():
    previous = (rls._enforcement_active, rls._switch, dict(rls._counters))
    rls.clear_tenant()
    rls._switch = None
    rls._enforcement_active = False
    yield
    rls._enforcement_active, rls._switch = previous[0], previous[1]
    rls._counters.clear()
    rls._counters.update(previous[2])
    rls.clear_tenant()


class _Svc:
    def Rpc(self, request, context):
        return ("ran", request)


def test_rejects_with_unauthenticated_when_enforced_and_unbound():
    counter = _Counter()
    rls._counters["elder.rls.grpc_rejected"] = counter
    rls._enforcement_active = True
    ctx = _Ctx()
    guarded = rls_guard.requires_tenant_context(_Svc.Rpc)
    with pytest.raises(_Aborted):
        guarded(_Svc(), "req", ctx)
    assert ctx.aborts[0][0] is grpc.StatusCode.UNAUTHENTICATED
    assert "tenant context required" in ctx.aborts[0][1]
    assert counter.calls == [(1, {"rpc": "Rpc"})]


def test_passes_through_when_rls_is_not_enforced():
    guarded = rls_guard.requires_tenant_context(_Svc.Rpc)
    assert guarded(_Svc(), "req", _Ctx()) == ("ran", "req")


def test_passes_through_when_a_tenant_or_scope_is_bound():
    rls._enforcement_active = True
    guarded = rls_guard.requires_tenant_context(_Svc.Rpc)
    with tenant_context(3):
        assert guarded(_Svc(), "req", _Ctx()) == ("ran", "req")
    with system_scope(SystemScopeReason.MAINTENANCE, "test"):
        assert guarded(_Svc(), "req", _Ctx()) == ("ran", "req")


def test_kill_switch_on_means_not_enforced():
    rls._enforcement_active = True
    rls.configure_kill_switch(lambda: True, start_thread=False)
    guarded = rls_guard.requires_tenant_context(_Svc.Rpc)
    assert guarded(_Svc(), "req", _Ctx()) == ("ran", "req")


def test_every_listed_rpc_exists_and_is_wrapped_on_the_real_servicer():
    assert len(rls_guard.RLS_GUARDED_RPCS) == len(set(rls_guard.RLS_GUARDED_RPCS)) > 20
    for name in rls_guard.RLS_GUARDED_RPCS:
        method = getattr(ElderServicer, name)
        assert hasattr(method, "__wrapped__"), f"{name} is not guarded"


def test_auth_and_health_rpcs_are_deliberately_not_guarded():
    for name in (
        "Login",
        "RefreshToken",
        "ValidateToken",
        "HealthCheck",
        "RegisterIdentity",
    ):
        assert not hasattr(getattr(ElderServicer, name), "__wrapped__"), name


def test_guard_servicer_fails_loudly_when_an_rpc_is_renamed(monkeypatch):
    monkeypatch.setattr(rls_guard, "RLS_GUARDED_RPCS", ("Rpc", "Renamed"))
    with pytest.raises(AttributeError):
        rls_guard.guard_servicer(type("Svc", (), {"Rpc": _Svc.Rpc}))
