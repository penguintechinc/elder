"""Unit tests for the API-side RLS helpers (cross-tenant scope + audit sink)."""

from unittest.mock import patch

import pytest
from quart import Quart

from apps.api.services.audit import rls_sink
from apps.api.utils.rls_scope import cross_tenant_scope
from shared.database import rls
from shared.database.rls import SystemScope, SystemScopeReason


@pytest.fixture(autouse=True)
def _reset():
    previous_sink = rls._audit_sink
    rls.register_audit_sink(None)
    yield
    rls.register_audit_sink(previous_sink)


def test_same_tenant_needs_no_elevation():
    with cross_tenant_scope(5, 5, actor="u:1", purpose="p"):
        assert rls.current_system_scope() is None


@pytest.mark.parametrize("caller", [None, 4])
def test_other_tenant_is_audited_super_admin_scope(caller):
    seen = []
    rls.register_audit_sink(seen.append)
    with cross_tenant_scope(caller, 5, actor="u:1", purpose="tenants.stats") as scope:
        assert scope.reason is SystemScopeReason.SUPER_ADMIN
        assert rls.current_system_scope() is scope
    assert [s.purpose for s in seen] == ["tenants.stats"]
    assert rls.current_system_scope() is None


def test_other_tenant_without_audit_sink_is_refused():
    with pytest.raises(rls.RlsAuditError):
        with cross_tenant_scope(4, 5, actor="u:1", purpose="p"):
            pytest.fail("must not enter")


def test_sink_noop_for_background_without_app_context():
    with patch.object(rls_sink.AuditService, "log") as log:
        rls_sink.write_system_scope_audit(
            SystemScope(SystemScopeReason.BACKGROUND_SWEEP, "sweep")
        )
    log.assert_not_called()


def test_sink_refuses_super_admin_without_app_context():
    with pytest.raises(RuntimeError, match="app context"):
        rls_sink.write_system_scope_audit(
            SystemScope(SystemScopeReason.SUPER_ADMIN, "p", actor="u:1")
        )


@pytest.mark.asyncio
async def test_sink_writes_audit_row_with_app_context():
    app = Quart(__name__)
    scope = SystemScope(SystemScopeReason.SUPER_ADMIN, "tenants.stats", actor="u:1")
    async with app.app_context():
        with patch.object(rls_sink.AuditService, "log") as log:
            rls_sink.write_system_scope_audit(scope)
    log.assert_called_once_with(
        action="rls_system_scope",
        resource_type="tenant_data",
        details={"reason": "SUPER_ADMIN", "purpose": "tenants.stats", "actor": "u:1"},
        category="security",
    )
