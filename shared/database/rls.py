"""Per-transaction tenant context for PostgreSQL row-level security (RLS).

Tenant isolation is meant to be enforced twice: the application filters by the
JWT ``tenant`` claim (``apps/api/utils/tenant_scoping.py``) and PostgreSQL itself
refuses cross-tenant rows via policies (migration ``043_rls_tenant_isolation``).
This module is the single data-access seam that feeds those policies.

IMPORTANT — enforcement needs two deployment facts, both checked at startup by
:func:`check_rls_posture`: (a) migration 043 is applied and (b) the application
connects as a role that is neither superuser nor ``BYPASSRLS`` (and is not the
table owner). Until both hold, this module is plumbing only and the database
enforces nothing.

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
  rows / reject writes (fail closed). Such transactions are counted
  (``elder.rls.unbound_transactions``) so missing wiring is visible.

System scope
------------
Cross-tenant access (auth bootstrap, background sweeps, a ``system``-tenant
super-admin) is an explicit context manager — never a blanket database role.
See :func:`system_scope`. Only privileged scopes (``SUPER_ADMIN``) are durably
audited; high-volume unauthenticated scopes (``PUBLIC_TOKEN``) are a structured
log + OTel counter so an anonymous caller cannot flood ``audit_logs``.

Kill-switch
-----------
Core-platform-mechanism convention: PostHog flag ``elder.disable-rls``. Unseen
or OFF = RLS on; ON = every un-scoped transaction runs in system scope with a
``KILL_SWITCH`` reason (legacy behaviour). The flag is polled by a background
thread so a slow flag backend can never stall the DB hot path. A probe error
never turns RLS off: the state starts OFF and an error keeps the last-known value.
"""

from __future__ import annotations

import asyncio
import contextvars
import os
import threading
from collections.abc import AsyncIterator, Callable, Iterator
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass
from enum import IntEnum
from typing import Any

import structlog
from sqlalchemy import event, text
from sqlalchemy.engine import Connection, Engine

from shared.database.rls_tables import POLICY_NAME, RLS_TABLES

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
STRICT_ENV_VAR = "ELDER_RLS_STRICT"


class SystemScopeReason(IntEnum):
    """Why a block of code runs across tenants (int codes, never strings).

    The reason decides how the entry is audited: only ``SUPER_ADMIN`` is durably
    audited; every reason emits a structured log and an OTel counter.
    """

    AUTH_BOOTSTRAP = 1  # pre-auth lookup where the tenant is not yet known
    BACKGROUND_SWEEP = 2  # scheduler / maintenance iterating all tenants
    SUPER_ADMIN = 3  # `system`-tenant operator acting on another tenant
    KILL_SWITCH = 4  # elder.disable-rls ON (legacy fallback)
    PUBLIC_TOKEN = 5  # unauthenticated, token-addressed access (share links)
    MAINTENANCE = 6  # operator scripts (seed/backfill) run out-of-band


# Reasons whose entries are written durably (audit_logs). Everything else is
# log + counter only: either high-volume/anonymous (PUBLIC_TOKEN, AUTH_BOOTSTRAP)
# or has no request context to write from (BACKGROUND_SWEEP, MAINTENANCE).
_DURABLE_AUDIT_REASONS = frozenset({SystemScopeReason.SUPER_ADMIN})
# Reasons that fire per anonymous request: logged at debug to keep logs usable.
_QUIET_LOG_REASONS = frozenset(
    {SystemScopeReason.PUBLIC_TOKEN, SystemScopeReason.AUTH_BOOTSTRAP}
)


@dataclass(slots=True, frozen=True)
class SystemScope:
    """An active, attributable cross-tenant access grant.

    Carries the reason code, a human-readable purpose and (for privileged
    scopes) the acting identity. Immutable so it can sit in a ContextVar.
    """

    reason: SystemScopeReason
    purpose: str
    actor: str | None = None


@dataclass(slots=True, frozen=True)
class RlsPosture:
    """What the connected role and schema can actually enforce.

    ``expected_tables`` are the RLS tables present in this database;
    ``missing_tables`` are the expected ones lacking FORCE RLS or the policy.
    Produced by :func:`check_rls_posture`.
    """

    role: str
    superuser: bool
    bypassrls: bool
    protected_tables: int
    unprotected_tenant_tables: tuple[str, ...] = ()
    expected_tables: int = 0
    missing_tables: tuple[str, ...] = ()

    @property
    def role_enforces(self) -> bool:
        """True when the role is subject to RLS (not superuser/BYPASSRLS)."""
        return not (self.superuser or self.bypassrls)

    @property
    def bypassed(self) -> bool:
        """True when policies exist but this role silently bypasses them."""
        return self.protected_tables > 0 and not self.role_enforces

    @property
    def enforcing(self) -> bool:
        """True only when the role is constrained AND every expected table is protected."""
        return (
            self.role_enforces and self.expected_tables > 0 and not self.missing_tables
        )

    @property
    def problems(self) -> tuple[str, ...]:
        """Human-readable reasons RLS is not (fully) enforced; empty when enforcing."""
        found: list[str] = []
        if not self.role_enforces:
            found.append(
                f"role {self.role!r} is superuser/BYPASSRLS "
                f"(superuser={self.superuser}, bypassrls={self.bypassrls})"
            )
        if self.expected_tables == 0:
            found.append("no RLS tables present in the database")
        elif self.protected_tables == 0:
            found.append(
                f"migration 043 not applied: 0 of {self.expected_tables} tables protected"
            )
        elif self.missing_tables:
            found.append(
                f"{self.protected_tables} of {self.expected_tables} tables protected; "
                f"unprotected: {', '.join(self.missing_tables)}"
            )
        return tuple(found)


class RlsConfigurationError(RuntimeError):
    """The database cannot enforce RLS while strict enforcement is required."""


class RlsAuditError(RuntimeError):
    """A super-admin scope was refused because it could not be audited."""


_tenant_id: contextvars.ContextVar[int | None] = contextvars.ContextVar(
    "elder_rls_tenant_id", default=None
)
_system_scope: contextvars.ContextVar[SystemScope | None] = contextvars.ContextVar(
    "elder_rls_system_scope", default=None
)

_audit_sink: Callable[[SystemScope], None] | None = None
_enforcement_active = False
_counters: dict[str, Any] = {}


def count_metric(
    name: str, description: str, attributes: dict[str, str] | None = None
) -> None:
    """Increment a named OTel counter; a no-op when OTel is not installed.

    Counters are created lazily and cached so the hot path pays one dict lookup.
    A broken metrics backend must never fail a database transaction.
    """
    if _otel_metrics is None:
        return
    try:
        counter = _counters.get(name)
        if counter is None:
            counter = _otel_metrics.get_meter("elder.rls").create_counter(
                name, description=description
            )
            _counters[name] = counter
        counter.add(1, attributes or {})
    except Exception as exc:  # pragma: no cover - defensive
        log.debug("rls_counter_failed", counter=name, error=str(exc))


def _validated_tenant(tenant_id: object) -> int:
    """Coerce a tenant id to a positive int or raise ValueError.

    Rejects booleans, floats, empty/non-numeric strings and non-positive values;
    the message never echoes the input.
    """
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
    """Bind the validated tenant for the current context; returns a reset token.

    Use only with a tenant from a trusted source (verified JWT, websocket ticket
    minted from a verified JWT, job envelope). Raises ValueError otherwise.
    """
    return _tenant_id.set(_validated_tenant(tenant_id))


def clear_tenant() -> None:
    """Drop the tenant binding for the current context (fail closed).

    Called at the start and end of every request so a binding can never outlive
    the request that proved it.
    """
    _tenant_id.set(None)


def current_tenant_id() -> int | None:
    """Return the tenant bound to the current context, or None.

    None means "unbound": every RLS-protected table will return zero rows.
    """
    return _tenant_id.get()


def current_system_scope() -> SystemScope | None:
    """Return the active system scope for this context, or None.

    Reflects the innermost ``system_scope`` block entered in this context.
    """
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
    """Register the durable audit writer for privileged scopes (or clear it).

    Only reasons in ``_DURABLE_AUDIT_REASONS`` (SUPER_ADMIN) reach the sink.
    """
    global _audit_sink
    _audit_sink = sink


def _build_scope(
    reason: SystemScopeReason, purpose: str, actor: str | None
) -> SystemScope:
    """Validate scope arguments and build the immutable scope object.

    A purpose is mandatory and SUPER_ADMIN must name an actor, so every
    cross-tenant grant is attributable.
    """
    if not purpose:
        raise ValueError("system_scope requires a non-empty purpose")
    if reason is SystemScopeReason.SUPER_ADMIN and not actor:
        raise ValueError("SUPER_ADMIN system scope requires an actor")
    return SystemScope(reason=reason, purpose=purpose, actor=actor)


@contextmanager
def system_scope(
    reason: SystemScopeReason, purpose: str, actor: str | None = None
) -> Iterator[SystemScope]:
    """Run a block across tenants — explicit and attributable.

    Every entry emits a structured log + OTel counter. ``SUPER_ADMIN`` also
    requires an ``actor`` and a working durable audit sink; if the entry cannot
    be audited it is refused (fail closed). Blocking: from async code use
    :func:`system_scope_async` so the audit write stays off the event loop.
    """
    scope = _build_scope(reason, purpose, actor)
    _audit_system_scope(scope)
    token = _system_scope.set(scope)
    try:
        yield scope
    finally:
        _system_scope.reset(token)


@asynccontextmanager
async def system_scope_async(
    reason: SystemScopeReason, purpose: str, actor: str | None = None
) -> AsyncIterator[SystemScope]:
    """Async twin of :func:`system_scope` for coroutines.

    The (possibly blocking) durable audit write runs in a worker thread so it
    never stalls the event loop; the scope is then bound in the caller's context.
    """
    scope = _build_scope(reason, purpose, actor)
    if scope.reason in _DURABLE_AUDIT_REASONS:
        await asyncio.to_thread(_audit_system_scope, scope)
    else:
        _audit_system_scope(scope)
    token = _system_scope.set(scope)
    try:
        yield scope
    finally:
        _system_scope.reset(token)


def _audit_system_scope(scope: SystemScope) -> None:
    """Emit the audit trail for a scope entry: log, counter, durable sink if privileged.

    Only SUPER_ADMIN is written durably; it is refused when no sink is registered
    or the write fails. All other reasons never touch the database here.
    """
    quiet = scope.reason in _QUIET_LOG_REASONS
    (log.debug if quiet else log.info)(
        "rls_system_scope_entered",
        reason=scope.reason.name,
        purpose=scope.purpose,
        actor=scope.actor,
        tenant_context=current_tenant_id(),
    )
    count_metric(
        "elder.rls.system_scope.entered",
        "Cross-tenant (system scope) entries, by reason",
        {"reason": scope.reason.name},
    )
    if scope.reason not in _DURABLE_AUDIT_REASONS:
        return
    if _audit_sink is None:
        raise RlsAuditError("SUPER_ADMIN scope refused: no audit sink registered")
    try:
        _audit_sink(scope)
    except Exception as exc:
        raise RlsAuditError("SUPER_ADMIN scope refused: audit write failed") from exc


class _KillSwitch:
    """Last-known state of the ``elder.disable-rls`` flag, refreshed off-path.

    Starts OFF (RLS on). A failing probe keeps the last-known value, so a flag
    outage can never flip RLS from on to off (it can only fail to flip it back).
    """

    def __init__(self, probe: Callable[[], bool], interval_s: float) -> None:
        """Store the probe and poll interval; the initial state is OFF (RLS on)."""
        self._probe = probe
        self._interval_s = interval_s
        self._stop = threading.Event()
        self.state = False  # unseen/OFF = RLS on

    def refresh(self) -> bool:
        """Re-evaluate the flag; on any error keep the last-known value.

        Returns the state after the refresh. A change is logged at WARNING.
        """
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
        """Poll the flag in a daemon thread so the DB hot path never blocks.

        The thread exits when :meth:`stop` is called.
        """

        def _loop() -> None:
            while not self._stop.wait(self._interval_s):
                self.refresh()

        threading.Thread(target=_loop, name="elder-rls-killswitch", daemon=True).start()

    def stop(self) -> None:
        """Stop the polling thread (idempotent)."""
        self._stop.set()


_switch: _KillSwitch | None = None


def _default_probe() -> bool:
    """Evaluate ``elder.disable-rls`` via the API's PostHog client if present.

    Images without the API package (flows-invoker) return False, i.e. RLS stays on.
    """
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
    """(Re)configure the kill-switch probe; replaces any previous poller.

    Evaluates the probe once synchronously (so startup sees the real flag) and,
    unless ``start_thread`` is False, keeps polling in the background.
    """
    global _switch
    if _switch is not None:
        _switch.stop()
    _switch = _KillSwitch(probe or _default_probe, interval_s)
    _switch.refresh()
    if start_thread:
        _switch.start()


def kill_switch_active() -> bool:
    """Return the last-known kill-switch state (False when never configured).

    True means every un-scoped transaction currently runs in KILL_SWITCH scope.
    """
    return _switch.state if _switch is not None else False


_KILL_SWITCH_SCOPE = SystemScope(
    reason=SystemScopeReason.KILL_SWITCH, purpose="elder.disable-rls flag is ON"
)


def _on_begin(conn: Connection) -> None:
    """Stamp the transaction-local tenant/scope GUCs on every PG transaction.

    Resets both GUCs at every BEGIN, so even a stray session-level SET cannot
    survive into the next transaction. Counts transactions with no tenant and no
    scope (they will see zero rows under enforcement).
    """
    if conn.dialect.name != "postgresql":
        return
    scope = _system_scope.get()
    if scope is None and kill_switch_active():
        scope = _KILL_SWITCH_SCOPE
    tenant = _tenant_id.get()
    if tenant is None and scope is None:
        count_metric(
            "elder.rls.unbound_transactions",
            "Transactions begun with no tenant and no system scope",
        )
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
    """Register the global per-transaction listener once; True if newly added.

    Idempotent. Importing ``shared.database`` calls this, so every service that
    touches the database is covered.
    """
    if event.contains(Engine, "begin", _on_begin):
        return False
    event.listen(Engine, "begin", _on_begin)
    log.info("rls_hooks_installed")
    return True


def uninstall_rls_hooks() -> None:
    """Remove the global listener (tests only).

    Safe to call when the listener is not installed.
    """
    if event.contains(Engine, "begin", _on_begin):
        event.remove(Engine, "begin", _on_begin)


def strict_from_env(environ: dict[str, str] | None = None) -> bool:
    """Return True when ``ELDER_RLS_STRICT`` asks for fatal enforcement checks.

    Accepts 1/true/yes (case-insensitive); anything else, including unset, is False.
    """
    env = os.environ if environ is None else environ
    return env.get(STRICT_ENV_VAR, "").strip().lower() in ("1", "true", "yes")


def enforcement_active() -> bool:
    """Return True when the last posture check found RLS fully enforced.

    False before any check, when the role bypasses RLS, when tables are missing
    policies, or while the kill-switch is ON. Guards (e.g. gRPC) use this to fail
    closed with a clear error only where the database would otherwise silently
    return zero rows.
    """
    return _enforcement_active and not kill_switch_active()


def check_rls_posture(
    engine: Engine,
    strict: bool = False,
    expected_tables: tuple[str, ...] = RLS_TABLES,
) -> RlsPosture:
    """Inspect the connected role and RLS protection of the expected tables.

    Expected = ``expected_tables`` that exist in this database. Logs at ERROR with
    counts when enforcement is incomplete (role bypass, migration not applied, or
    unprotected tables); with ``strict`` raises :class:`RlsConfigurationError`
    instead of only logging, so a misconfigured deployment fails at startup.
    """
    global _enforcement_active
    with engine.connect() as conn:
        role = conn.execute(
            text(
                "SELECT rolname, rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user"
            )
        ).one()
        rows = conn.execute(
            text(
                "SELECT c.relname, (c.relrowsecurity AND c.relforcerowsecurity), "
                "EXISTS (SELECT 1 FROM pg_policy p WHERE p.polrelid = c.oid AND p.polname = :policy) "
                "FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
                "WHERE n.nspname = 'public' AND c.relkind = 'r' AND c.relname = ANY(:names) "
                "ORDER BY c.relname"
            ),
            {"policy": POLICY_NAME, "names": list(expected_tables)},
        ).all()
        unprotected_names = tuple(
            conn.execute(
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
        )
    missing = tuple(
        name for name, forced, has_policy in rows if not (forced and has_policy)
    )
    posture = RlsPosture(
        role=role.rolname,
        superuser=bool(role.rolsuper),
        bypassrls=bool(role.rolbypassrls),
        protected_tables=len(rows) - len(missing),
        unprotected_tenant_tables=unprotected_names,
        expected_tables=len(rows),
        missing_tables=missing,
    )
    _enforcement_active = posture.enforcing
    if missing:
        count_metric(
            "elder.rls.posture.unprotected_tables",
            "RLS tables found without FORCE RLS / policy at startup",
        )
    if posture.enforcing:
        log.info(
            "rls_posture",
            role=posture.role,
            enforcing=True,
            protected_tables=posture.protected_tables,
            expected_tables=posture.expected_tables,
        )
        return posture
    log.error(
        "rls_not_enforced",
        role=posture.role,
        superuser=posture.superuser,
        bypassrls=posture.bypassrls,
        protected_tables=posture.protected_tables,
        expected_tables=posture.expected_tables,
        missing_tables=list(posture.missing_tables),
        problems=list(posture.problems),
    )
    if strict:
        raise RlsConfigurationError(
            "RLS is not enforced ("
            + STRICT_ENV_VAR
            + "=true): "
            + "; ".join(posture.problems)
        )
    return posture


def init_rls(engine: Engine, strict: bool | None = None) -> RlsPosture | None:
    """Wire RLS for one engine at service startup (API, worker, flows-invoker).

    Installs the transaction listener, evaluates the kill-switch (starting its
    poller once per process) and, on PostgreSQL, runs :func:`check_rls_posture`
    with ``strict`` defaulting to ``ELDER_RLS_STRICT``. Returns the posture, or
    None for non-PostgreSQL engines.
    """
    install_rls_hooks()
    if _switch is None:
        configure_kill_switch()
    if engine.dialect.name != "postgresql":
        return None
    return check_rls_posture(
        engine, strict=strict_from_env() if strict is None else strict
    )
