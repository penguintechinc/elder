"""Per-request PostgreSQL tenant context for Row-Level Security (RLS).

The companion Alembic migration (``015_enable_rls_tenant_isolation``) enables
RLS policies keyed on the Postgres session GUC ``app.current_tenant_id``. This
module sets that GUC from the authenticated request's tenant, so the database
enforces tenant isolation in addition to the existing app-layer scoping.

Integration notes / penguin-dal limitation
-------------------------------------------
penguin-dal's synchronous ``DB`` opens a *fresh short-lived session* (and thus
checks out its own pooled connection, auto-committing) for every individual
operation — there is no stable per-request connection to issue a one-off
``SET app.current_tenant_id`` against. A naive ``before_request`` ``SET`` would
land on a connection that later queries never reuse.

To make the GUC reliably visible to every operation within a request we:

1. Store the resolved tenant id in a :class:`contextvars.ContextVar`
   (``before_request``).
2. Attach a SQLAlchemy pool ``checkout`` listener to ``app.db``'s engine that,
   on PostgreSQL only, applies ``set_config('app.current_tenant_id', <id>,
   false)`` from the ContextVar to each connection as it is handed out. When no
   tenant is in scope the GUC is reset to NULL (policies then fail closed).

Everything here is **PostgreSQL-only**. On SQLite (tests/dev) the checkout
listener is never registered and all helpers are no-ops, so the SQLite path is
untouched.

Trade-off: the checkout listener issues one extra ``set_config`` round-trip per
connection checkout. Pinning a single connection per request (removing that
overhead) is a penguin-dal enhancement tracked as a follow-up.
"""

# flake8: noqa: E501

import contextvars
import logging
from typing import Optional

from sqlalchemy import event

logger = logging.getLogger(__name__)

# Holds the tenant id for the current request/task. ``None`` means "no tenant
# context" → the GUC is reset and RLS policies see NULL (fail closed).
_current_tenant_id: contextvars.ContextVar[Optional[int]] = contextvars.ContextVar(
    "elder_current_tenant_id", default=None
)

GUC_NAME = "app.current_tenant_id"


def set_current_tenant(tenant_id: Optional[int]) -> None:
    """Set the tenant id for the current context (or clear it with ``None``)."""
    _current_tenant_id.set(tenant_id)


def get_current_tenant() -> Optional[int]:
    """Return the tenant id currently in scope, or ``None``."""
    return _current_tenant_id.get()


def clear_current_tenant() -> None:
    """Clear the tenant id for the current context."""
    _current_tenant_id.set(None)


def _engine_is_postgres(engine) -> bool:
    """True if the SQLAlchemy engine targets PostgreSQL."""
    try:
        return engine.dialect.name == "postgresql"
    except Exception:  # pragma: no cover - defensive
        return False


def _apply_guc(dbapi_connection, tenant_id: Optional[int]) -> None:
    """Apply (or reset) the tenant GUC on a raw DBAPI connection.

    Passing ``NULL`` for ``set_config`` resets the GUC to its default so that
    ``current_setting(..., true)`` returns NULL and policies fail closed.
    """
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute(
            "SELECT set_config(%s, %s, false)",
            (GUC_NAME, None if tenant_id is None else str(tenant_id)),
        )
    finally:
        cursor.close()


def register_tenant_rls(app) -> bool:
    """Wire per-request tenant context for RLS onto a Quart app.

    Registers a ``before_request`` hook (resolve + store the tenant), a
    teardown hook (clear it), and — on PostgreSQL only — a pool ``checkout``
    listener that stamps the GUC onto every connection.

    Returns ``True`` when the Postgres RLS wiring is active, ``False`` when it
    is a no-op (e.g. SQLite). Safe to call once during app creation.
    """
    db = getattr(app, "db", None)
    engine = getattr(db, "engine", None) if db is not None else None
    is_postgres = engine is not None and _engine_is_postgres(engine)

    # before_request: resolve the authenticated user's tenant and stash it.
    @app.before_request
    def _set_tenant_context():  # pragma: no cover - exercised via integration
        tenant_id = None
        try:
            # Local import to avoid a circular import at module load time.
            from apps.api.auth.jwt_handler import get_current_user

            user = get_current_user()
            if user is not None:
                raw = getattr(user, "tenant_id", None)
                tenant_id = int(raw) if raw is not None else None
        except Exception as exc:
            logger.debug("tenant_rls: could not resolve tenant for request: %s", exc)
            tenant_id = None
        set_current_tenant(tenant_id)

    @app.teardown_request
    def _clear_tenant_context(exc=None):  # pragma: no cover - trivial
        clear_current_tenant()

    if not is_postgres:
        logger.info(
            "tenant_rls: non-PostgreSQL backend (%s) — RLS GUC wiring is a no-op",
            getattr(getattr(engine, "dialect", None), "name", "unknown"),
        )
        return False

    @event.listens_for(engine, "checkout")
    def _set_guc_on_checkout(dbapi_connection, connection_record, connection_proxy):
        try:
            _apply_guc(dbapi_connection, get_current_tenant())
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning(
                "tenant_rls: failed to set %s on checkout: %s", GUC_NAME, exc
            )

    logger.info("tenant_rls: PostgreSQL RLS tenant-context wiring enabled")
    return True
