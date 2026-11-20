"""Reliable Village ID minting helper for Elder backend routes and services.

Wraps ``shared.utils.village_id.generate_village_id`` (Redis sequential counter)
with a CSPRNG fallback matching ``VILLAGE_ID_RE`` (``TTTTTTTT-OOOOOOOOOOOOOOOO``)
so resource creation NEVER crashes or stores ``NULL`` when Redis is unavailable.
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


def mint_village_id(
    tenant_id: int | str | None,
    redis_client: Any = _UNSET,
) -> str:
    """Mint a unique 25-char lowercase hex Village ID for ``tenant_id``.

    Attempts Redis-backed sequential allocation via
    ``shared.utils.village_id.generate_village_id`` first. If Redis is not
    configured, disconnected, or raises any exception, logs a warning and falls
    back cleanly to a 64-bit CSPRNG suffix in the exact same ``VILLAGE_ID_RE``
    format (``TTTTTTTT-OOOOOOOOOOOOOOOO``). Never returns ``None``.

    Args:
        tenant_id: Owning tenant ID (int, numeric str, or None -> defaults to 1).
        redis_client: Optional Redis client. When omitted, attempts to obtain
            ``current_app.redis_client``. Pass ``None`` explicitly to bypass
            auto-discovery and exercise fallback (or patched generator).

    Returns:
        A non-empty 25-character Village ID string matching ``VILLAGE_ID_RE``
        (or whatever a test patch on ``generate_village_id`` returns).
    """
    tid = _normalize_tenant_id(tenant_id)
    resolved_redis = (
        _resolve_app_redis_client() if redis_client is _UNSET else redis_client
    )

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
