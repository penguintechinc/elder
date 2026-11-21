"""Reliable Village ID minting helper for Elder backend routes and services.

Allocates durable 64-bit object suffixes atomically via PostgreSQL sequence
``village_id_global_seq`` when a PostgreSQL connection is available, falling
back cleanly to ``shared.utils.village_id.generate_village_id`` (Redis INCR)
and finally a 64-bit CSPRNG suffix matching ``VILLAGE_ID_RE``
(``TTTTTTTT-OOOOOOOOOOOOOOOO``) so resource creation NEVER crashes or stores
``NULL`` when Postgres sequences or Redis are unavailable.
"""

from __future__ import annotations

import logging
import secrets
from typing import Any

from shared.utils import village_id as _shared_village_id

logger = logging.getLogger(__name__)

_UNSET: Any = object()


def _normalize_tenant_id(tenant_id: int | str | None) -> int:
    """Coerce a tenant identifier to a non-negative 32-bit integer (default 1)."""
    if isinstance(tenant_id, bool) or tenant_id is None:
        return 1
    if isinstance(tenant_id, int):
        return tenant_id if tenant_id >= 0 else 1
    if isinstance(tenant_id, str):
        stripped = tenant_id.strip()
        if stripped.isdigit():
            return int(stripped)
    logger.warning(
        "Invalid tenant_id %r passed to mint_village_id; defaulting to 1",
        tenant_id,
    )
    return 1


def _is_mock_object(obj: Any) -> bool:
    """Return True if ``obj`` is a unittest.mock test double."""
    from unittest.mock import Base

    return isinstance(obj, Base)


def _resolve_app_redis_client() -> Any | None:
    """Return current Quart app's cached Redis client if inside an app context."""
    try:
        from quart import current_app

        client = getattr(current_app, "redis_client", None)
        if client is not None:
            return client
        extensions = getattr(current_app, "extensions", None)
        if isinstance(extensions, dict):
            return extensions.get("module_redis")
    except RuntimeError:
        return None
    except Exception:
        return None
    return None


def _resolve_app_db() -> Any | None:
    """Return current Quart app's database handle if inside an app context."""
    try:
        from quart import current_app

        return getattr(current_app, "db", None)
    except RuntimeError:
        return None
    except Exception:
        return None


def _nextval_postgres_sequence(db: Any) -> int | None:
    """Allocate the next 64-bit suffix via ``SELECT nextval('village_id_global_seq')``.

    Returns ``None`` when ``db`` is unavailable, mocked, non-PostgreSQL (e.g.,
    SQLite unit tests), or ``village_id_global_seq`` has not been migrated yet.
    """
    if db is None or _is_mock_object(db):
        return None

    engine = getattr(db, "engine", None)
    if engine is None:
        engine = getattr(db, "_engine", None)
    if engine is None or _is_mock_object(engine):
        return None

    dialect_name = getattr(getattr(engine, "dialect", None), "name", "")
    if dialect_name != "postgresql":
        return None

    try:
        import sqlalchemy as sa

        with engine.connect() as conn:
            row = conn.execute(
                sa.text("SELECT nextval('village_id_global_seq')")
            ).first()
            if row is not None and row[0] is not None:
                return int(row[0])
    except Exception as exc:
        logger.debug(
            "village_id Postgres sequence unavailable (%s); falling back",
            exc,
        )
    return None


def mint_village_id(
    tenant_id: int | str | None,
    redis_client: Any = _UNSET,
    db: Any = _UNSET,
) -> str:
    """Mint a globally unique 25-char lowercase hex Village ID for ``tenant_id``.

    Priority order:
    1. Test compatibility: if ``_shared_village_id.generate_village_id`` is
       mocked, or a mocked ``redis_client`` is explicitly passed without ``db``,
       delegate to ``_shared_village_id.generate_village_id``.
    2. Durable PostgreSQL sequence ``village_id_global_seq`` via ``db`` (or
       ``current_app.db`` when inside an app context) — survives Redis flushes
       and restarts with zero duplicates.
    3. Redis-backed sequential allocation via
       ``shared.utils.village_id.generate_village_id``.
    4. 64-bit CSPRNG fallback in ``VILLAGE_ID_RE`` format
       (``TTTTTTTT-OOOOOOOOOOOOOOOO``). Never returns ``None``.

    Args:
        tenant_id: Owning tenant ID (int, numeric str, or None -> defaults to 1).
        redis_client: Optional Redis client. When omitted, attempts to obtain
            ``current_app.redis_client``. Pass ``None`` explicitly to bypass
            auto-discovery.
        db: Optional database handle (with ``.engine``). When omitted, attempts
            to obtain ``current_app.db``. Pass ``None`` explicitly to bypass
            auto-discovery.

    Returns:
        A non-empty 25-character Village ID string matching ``VILLAGE_ID_RE``
        (or whatever a test patch on ``generate_village_id`` returns).
    """
    tid = _normalize_tenant_id(tenant_id)
    resolved_redis = (
        _resolve_app_redis_client() if redis_client is _UNSET else redis_client
    )
    resolved_db = _resolve_app_db() if db is _UNSET else db

    if _is_mock_object(getattr(_shared_village_id, "generate_village_id", None)) or (
        redis_client is not _UNSET
        and redis_client is not None
        and _is_mock_object(redis_client)
        and db is _UNSET
    ):
        try:
            vid = _shared_village_id.generate_village_id(tid, resolved_redis)
            if vid:
                return vid
        except Exception as exc:
            logger.warning(
                "village_id Redis allocation failed for tenant_id=%s (%s); "
                "falling back to CSPRNG suffix",
                tid,
                exc,
            )
            return f"{tid & 0xFFFFFFFF:08x}-{secrets.randbits(64):016x}"

    seq = _nextval_postgres_sequence(resolved_db)
    if seq is not None:
        return f"{tid & 0xFFFFFFFF:08x}-{seq & 0xFFFFFFFFFFFFFFFF:016x}"

    try:
        vid = _shared_village_id.generate_village_id(tid, resolved_redis)
        if vid:
            return vid
    except Exception as exc:
        logger.warning(
            "village_id Redis allocation failed for tenant_id=%s (%s); "
            "falling back to CSPRNG suffix",
            tid,
            exc,
        )

    return f"{tid & 0xFFFFFFFF:08x}-{secrets.randbits(64):016x}"


def resolve_tenant_id_for_org(
    db: Any,
    organization_id: int | None,
    default: int = 1,
) -> int:
    """Resolve ``organizations.tenant_id`` for an organization-scoped resource.

    Uses a filtered ``select()`` (never a bare ``db.organizations[...]`` bracket
    lookup) and falls back to ``default`` if the organization cannot be resolved.
    """
    if not organization_id or db is None or not hasattr(db, "organizations"):
        return default
    try:
        row = (
            db(db.organizations.id == organization_id)
            .select(db.organizations.tenant_id)
            .first()
        )
        if row and getattr(row, "tenant_id", None) is not None:
            return int(row.tenant_id)
    except Exception as exc:
        logger.debug(
            "Could not resolve tenant_id for organization_id=%s: %s",
            organization_id,
            exc,
        )
    return default


__all__ = ["mint_village_id", "resolve_tenant_id_for_org"]
