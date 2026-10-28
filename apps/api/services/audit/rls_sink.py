"""Durable audit sink for privileged RLS system-scope (SUPER_ADMIN) access.

Registered at API startup via ``shared.database.rls.register_audit_sink``. Only
``SUPER_ADMIN`` scope entries reach it (a row in ``audit_logs``, a global
non-RLS table); the entry is refused if the write fails. High-volume anonymous
scopes (``PUBLIC_TOKEN``) deliberately never write here — they are a structured
log + OTel counter — so an unauthenticated caller cannot flood ``audit_logs``.
"""

from quart import has_app_context

from apps.api.services.audit.service import AuditService
from shared.database.rls import SystemScope, SystemScopeReason


def write_system_scope_audit(scope: SystemScope) -> None:
    """Persist one audit_logs row describing a privileged system-scope entry.

    Needs the request's app context (``AuditService.log`` uses ``current_app.db``);
    without one it is a no-op unless the scope is SUPER_ADMIN, which is refused.
    ``actor`` is an opaque reference supplied by the caller and lives in ``details``.
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
