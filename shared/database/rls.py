"""Per-transaction tenant context for PostgreSQL row-level security (RLS).

Tenant isolation is enforced twice: the application filters by the JWT
``tenant`` claim (``apps/api/utils/tenant_scoping.py``) and PostgreSQL itself
refuses cross-tenant rows via policies (migration ``042_rls_tenant_isolation``).
This module is the single data-access seam that feeds those policies.

How it works
------------
* The validated tenant lives in a :class:`contextvars.ContextVar`
  (``set_tenant`` / ``tenant_context``). It is never read from request input —
  the API sets it from the verified JWT, workers set it from the job envelope.
* One global SQLAlchemy ``Engine`` ``begin`` listener stamps every PostgreSQL
  transaction with ``set_config('app.tenant_id', <tenant>, true)`` and
  ``set_config('app.rls_scope', <scope>, true)``. ``is_local=true`` makes both
  values transaction-local: PostgreSQL reverts them at COMMIT/ROLLBACK, so a
  pooled connection can never carry one request's tenant into another.
* penguin-dal opens one short-lived session (= one transaction) per operation,
  so the listener runs for every DAL call regardless of who built the DAL.
* No tenant and no system scope => the GUC is empty and the policies match no
  rows / reject writes (fail closed).

System scope
------------
Cross-tenant access (auth bootstrap, background sweeps, a ``system``-tenant
super-admin) is an explicit, audit-logged context manager — never a blanket
database role. See :func:`system_scope`.

Kill-switch
-----------
Core-platform-mechanism convention: PostHog flag ``elder.disable-rls``. Unseen
or OFF = RLS on; ON = every un-scoped transaction runs in system scope with a
``KILL_SWITCH`` reason (legacy behaviour). The flag is polled by a background
thread so a slow flag backend can never stall the DB hot path.
"""

from __future__ import annotations

import contextvars
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from enum import IntEnum
from typing import Any

import structlog
from sqlalchemy import event, text
from sqlalchemy.engine import Connection, Engine

try:  # OTel is optional in slim service images (e.g. flows-invoker).
    from opentelemetry import metrics as _otel_metrics
except ImportError:  # pragma: no cover - exercised only in slim images
    _otel_metrics = None  # type: ignore[assignment]

log = structlog.get_logger(__name__)

GUC_TENANT = "app.tenant_id"
GUC_SCOPE = "app.rls_scope"
SYSTEM_SCOPE_VALUE = "system"
KILL_SWITCH_FLAG = "elder.disable-rls"
KILL_SWITCH_DISTINCT_ID = "elder-platform"
DEFAULT_KILL_SWITCH_POLL_S = 30.0


class SystemScopeReason(IntEnum):
    """Why a block of code runs across tenants (int codes, never strings)."""

    AUTH_BOOTSTRAP = 1  # pre-auth lookup where the tenant is not yet known
    BACKGROUND_SWEEP = 2  # scheduler / maintenance iterating all tenants
    SUPER_ADMIN = 3  # `system`-tenant operator acting on another tenant
    KILL_SWITCH = 4  # elder.disable-rls ON (legacy fallback)
    PUBLIC_TOKEN = 5  # unauthenticated, token-addressed access (share links)


@dataclass(slots=True, frozen=True)
class SystemScope:
    """An active, attributable cross-tenant access grant."""

    reason: SystemScopeReason
    purpose: str
    actor: str | None = None


@dataclass(slots=True, frozen=True)
class RlsPosture:
    """What the current DB role can actually enforce (see check_rls_posture)."""

    role: str
    superuser: bool
    bypassrls: bool
    protected_tables: int
    unprotected_tenant_tables: tuple[str, ...] = ()

    @property
    def role_enforces(self) -> bool:
        """True when the role is subject to RLS (not superuser/BYPASSRLS)."""
        return not (self.superuser or self.bypassrls)

    @property
    def bypassed(self) -> bool:
        """True when policies exist but this role silently bypasses them."""
        return self.protected_tables > 0 and not self.role_enforces


class RlsConfigurationError(RuntimeError):
    """The DB role cannot enforce RLS while strict enforcement is required."""


class RlsAuditError(RuntimeError):
    """A super-admin scope was refused because it could not be audited."""


_tenant_id: contextvars.ContextVar[int | None] = contextvars.ContextVar(
    "elder_rls_tenant_id", default=None
)
_system_scope: contextvars.ContextVar[SystemScope | None] = contextvars.ContextVar(
    "elder_rls_system_scope", default=None
)

_audit_sink: Callable[[SystemScope], None] | None = None


_scope_counter: Any = None


def _meter_counter() -> Any:
    """Return the cached OTel counter for system-scope entries (None if no OTel)."""
    global _scope_counter
    if _otel_metrics is None:
        return None
    if _scope_counter is None:
        _scope_counter = _otel_metrics.get_meter("elder.rls").create_counter(
            "elder.rls.system_scope.entered",
            description="Cross-tenant (system scope) entries, by reason",
        )
    return _scope_counter


def _validated_tenant(tenant_id: object) -> int:
    """Coerce a tenant id to a positive int or raise ValueError."""
    if isinstance(tenant_id, bool) or not isinstance(tenant_id, int | str):
        raise ValueError("tenant_id must be a positive integer")
    try:
        value = int(tenant_id)
    except ValueError as exc:
        raise ValueError("tenant_id must be a positive integer") from exc
    if value <= 0:
        raise ValueError("tenant_id must be a positive integer")
    return value


def set_tenant(tenant_id: object) -> contextvars.Token[int | None]:
    """Bind the validated tenant for the current context; returns a reset token."""
    return _tenant_id.set(_validated_tenant(tenant_id))


def clear_tenant() -> None:
    """Drop the tenant binding for the current context (fail closed)."""
    _tenant_id.set(None)


def current_tenant_id() -> int | None:
    """Return the tenant bound to the current context, or None."""
    return _tenant_id.get()


def current_system_scope() -> SystemScope | None:
    """Return the active system scope for this context, or None."""
    return _system_scope.get()


@contextmanager
def tenant_context(tenant_id: object) -> Iterator[int]:
    """Run a block with RLS pinned to ``tenant_id`` (worker/job entry points).

    The id must come from a trusted source (verified JWT or job envelope).
    Nested use restores the outer tenant on exit.
    """
    value = _validated_tenant(tenant_id)
    token = _tenant_id.set(value)
    try:
        yield value
    finally:
        _tenant_id.reset(token)


def register_audit_sink(sink: Callable[[SystemScope], None] | None) -> None:
    """Register the durable audit writer for system-scope entries (or clear it)."""
    global _audit_sink
    _audit_sink = sink


@contextmanager
def system_scope(
    reason: SystemScopeReason, purpose: str, actor: str | None = None
) -> Iterator[SystemScope]:
    """Run a block across tenants — explicit, attributable, audit-logged.

    Every entry emits a structured audit log + OTel counter. ``SUPER_ADMIN``
    additionally requires an ``actor`` and a working audit sink; if the entry
    cannot be durably audited it is refused (fail closed).
    """
    if not purpose:
        raise ValueError("system_scope requires a non-empty purpose")
    if reason is SystemScopeReason.SUPER_ADMIN and not actor:
        raise ValueError("SUPER_ADMIN system scope requires an actor")
    scope = SystemScope(reason=reason, purpose=purpose, actor=actor)
    _audit_system_scope(scope)
    token = _system_scope.set(scope)
    try:
        yield scope
    finally:
        _system_scope.reset(token)


def _audit_system_scope(scope: SystemScope) -> None:
    """Emit the audit trail for a system-scope entry (log, metric, sink)."""
    log.info(
        "rls_system_scope_entered",
        reason=scope.reason.name,
        purpose=scope.purpose,
        actor=scope.actor,
        tenant_context=current_tenant_id(),
    )
    counter = _meter_counter()
    if counter is not None:
        counter.add(1, {"reason": scope.reason.name})
    if _audit_sink is None:
        if scope.reason is SystemScopeReason.SUPER_ADMIN:
            raise RlsAuditError("SUPER_ADMIN scope refused: no audit sink registered")
        return
    try:
        _audit_sink(scope)
    except Exception as exc:
        if scope.reason is SystemScopeReason.SUPER_ADMIN:
            raise RlsAuditError(
                "SUPER_ADMIN scope refused: audit write failed"
            ) from exc
        log.warning("rls_audit_sink_failed", reason=scope.reason.name, error=str(exc))


class _KillSwitch:
    """Last-known state of the ``elder.disable-rls`` flag, refreshed off-path."""

    def __init__(self, probe: Callable[[], bool], interval_s: float) -> None:
        self._probe = probe
        self._interval_s = interval_s
        self._stop = threading.Event()
        self.state = False  # unseen/OFF = RLS on

    def refresh(self) -> bool:
        """Re-evaluate the flag; on any error keep the last-known value."""
        try:
            new_state = bool(self._probe())
        except Exception as exc:
            log.warning(
                "rls_kill_switch_probe_failed", error=str(exc), keeping=self.state
            )
            return self.state
        if new_state != self.state:
            log.warning(
                "rls_kill_switch_changed", flag=KILL_SWITCH_FLAG, disabled=new_state
            )
        self.state = new_state
        return new_state

    def start(self) -> None:
        """Poll the flag in a daemon thread so the DB hot path never blocks."""

        def _loop() -> None:
            while not self._stop.wait(self._interval_s):
                self.refresh()

        threading.Thread(target=_loop, name="elder-rls-killswitch", daemon=True).start()

    def stop(self) -> None:
        """Stop the polling thread."""
        self._stop.set()


_switch: _KillSwitch | None = None


def _default_probe() -> bool:
    """Evaluate ``elder.disable-rls`` via the API's PostHog client if present."""
    try:
        from apps.api.common.flags.posthog_client import flag_enabled
    except ImportError:  # worker/flows images ship without the API package
        return False
    return bool(flag_enabled(KILL_SWITCH_FLAG, KILL_SWITCH_DISTINCT_ID, False))


def configure_kill_switch(
    probe: Callable[[], bool] | None = None,
    interval_s: float = DEFAULT_KILL_SWITCH_POLL_S,
    start_thread: bool = True,
) -> None:
    """(Re)configure the kill-switch probe; replaces any previous poller."""
    global _switch
    if _switch is not None:
        _switch.stop()
    _switch = _KillSwitch(probe or _default_probe, interval_s)
    _switch.refresh()
    if start_thread:
        _switch.start()


def kill_switch_active() -> bool:
    """Return the last-known kill-switch state (False when never configured)."""
    return _switch.state if _switch is not None else False


_KILL_SWITCH_SCOPE = SystemScope(
    reason=SystemScopeReason.KILL_SWITCH, purpose="elder.disable-rls flag is ON"
)


def _on_begin(conn: Connection) -> None:
    """Stamp the transaction-local tenant/scope GUCs on every PG transaction."""
    if conn.dialect.name != "postgresql":
        return
    scope = _system_scope.get()
    if scope is None and kill_switch_active():
        scope = _KILL_SWITCH_SCOPE
    tenant = _tenant_id.get()
    conn.execute(
        text(
            "SELECT set_config(:g_tenant, :tenant, true), set_config(:g_scope, :scope, true)"
        ),
        {
            "g_tenant": GUC_TENANT,
            "tenant": "" if tenant is None else str(tenant),
            "g_scope": GUC_SCOPE,
            "scope": SYSTEM_SCOPE_VALUE if scope is not None else "",
        },
    )


def install_rls_hooks() -> bool:
    """Register the global per-transaction listener once; True if newly added."""
    if event.contains(Engine, "begin", _on_begin):
        return False
    event.listen(Engine, "begin", _on_begin)
    log.info("rls_hooks_installed")
    return True


def uninstall_rls_hooks() -> None:
    """Remove the global listener (tests only)."""
    if event.contains(Engine, "begin", _on_begin):
        event.remove(Engine, "begin", _on_begin)


def check_rls_posture(engine: Engine, strict: bool = False) -> RlsPosture:
    """Inspect the connected role and RLS-protected table count.

    Logs an error when policies exist but the role bypasses them; raises
    :class:`RlsConfigurationError` instead when ``strict`` is set.
    """
    with engine.connect() as conn:
        role = conn.execute(
            text(
                "SELECT rolname, rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user"
            )
        ).one()
        protected = conn.execute(
            text(
                "SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
                "WHERE n.nspname = 'public' AND c.relkind = 'r' "
                "AND c.relrowsecurity AND c.relforcerowsecurity"
            )
        ).scalar_one()
        unprotected = conn.execute(
            text(
                "SELECT c.relname FROM pg_class c "
                "JOIN pg_namespace n ON n.oid = c.relnamespace "
                "JOIN pg_attribute a ON a.attrelid = c.oid "
                "AND a.attname = 'tenant_id' AND NOT a.attisdropped "
                "WHERE n.nspname = 'public' AND c.relkind = 'r' "
                "AND NOT (c.relrowsecurity AND c.relforcerowsecurity) "
                "ORDER BY c.relname"
            )
        ).scalars()
        unprotected_names = tuple(unprotected)
    posture = RlsPosture(
        role=role.rolname,
        superuser=bool(role.rolsuper),
        bypassrls=bool(role.rolbypassrls),
        protected_tables=int(protected),
        unprotected_tenant_tables=unprotected_names,
    )
    if posture.protected_tables > 0 and posture.unprotected_tenant_tables:
        log.info(
            "rls_unprotected_tenant_tables",
            count=len(posture.unprotected_tenant_tables),
            tables=list(posture.unprotected_tenant_tables),
        )
    if posture.bypassed:
        message = (
            "RLS policies exist but the application role bypasses them "
            "(superuser or BYPASSRLS); tenant isolation is NOT enforced by the database"
        )
        log.error(
            "rls_role_bypasses_policies",
            role=posture.role,
            superuser=posture.superuser,
            bypassrls=posture.bypassrls,
            protected_tables=posture.protected_tables,
        )
        if strict:
            raise RlsConfigurationError(message)
    else:
        log.info(
            "rls_posture",
            role=posture.role,
            enforcing=posture.role_enforces and posture.protected_tables > 0,
            protected_tables=posture.protected_tables,
        )
    return posture
