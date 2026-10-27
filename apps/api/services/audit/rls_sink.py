"""Durable audit sink for RLS system-scope (cross-tenant) access.

Registered at API startup via ``shared.database.rls.register_audit_sink`` so that
every explicit cross-tenant grant made inside a request leaves a row in
``audit_logs`` (a global, non-RLS table). ``SUPER_ADMIN`` scope is refused if
this write fails. Background sweeps run without an app context and are covered
by the structured audit log + OTel counter emitted by ``system_scope`` itself.
"""

from quart import has_app_context

from apps.api.services.audit.service import AuditService
from shared.database.rls import SystemScope, SystemScopeReason


def write_system_scope_audit(scope: SystemScope) -> None:
    """Persist one audit_logs row describing a system-scope entry.

    Needs the request's app context (``AuditService.log`` uses
    ``current_app.db``); without one (scheduler threads) it is a no-op because
    the structured log record is already the audit trail. ``actor`` is an
    opaque, non-PII reference supplied by the caller and lives in ``details``.
    """
    if not has_app_context():
        if scope.reason is SystemScopeReason.SUPER_ADMIN:
            raise RuntimeError("SUPER_ADMIN scope requires an app context to audit")
        return
    AuditService.log(
        action="rls_system_scope",
        resource_type="tenant_data",
        details={
            "reason": scope.reason.name,
            "purpose": scope.purpose,
            "actor": scope.actor,
        },
        category="security",
    )
