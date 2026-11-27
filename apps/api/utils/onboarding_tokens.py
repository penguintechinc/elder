"""Single-use onboarding tokens for email verification and admin invitations.

Tokens are generated with ``secrets.token_urlsafe(32)`` and only their
SHA-256 digests are used as storage keys (``elder:onboarding:<purpose>:<sha256>``).
Storage uses Redis when available with a bounded, thread-safe in-memory
fallback (matching ``apps/api/utils/altcha.py``).

Purposes are strictly domain-separated:
- ``email_verify`` (default TTL: 24 hours)
- ``admin_invite`` (default TTL: 72 hours)

Raw tokens and email PII are never logged.
"""

from __future__ import annotations

import hashlib
import json
import secrets
import threading
import time
from typing import Any

import structlog
from quart import current_app

logger = structlog.get_logger(__name__)
try:
    from shared.observability import get_meter

    _onboarding_token_counter: Any = get_meter(__name__).create_counter(
        name="elder.onboarding.tokens",
        description="Onboarding token lifecycle operations by purpose and action",
        unit="1",
    )
except ImportError:  # pragma: no cover - lightweight unit-test fallback
    _onboarding_token_counter = None

PURPOSE_EMAIL_VERIFY = "email_verify"
PURPOSE_ADMIN_INVITE = "admin_invite"

EMAIL_VERIFY_TTL_SECONDS = 24 * 3600
ADMIN_INVITE_TTL_SECONDS = 72 * 3600

DEFAULT_TTLS: dict[str, int] = {
    PURPOSE_EMAIL_VERIFY: EMAIL_VERIFY_TTL_SECONDS,
    PURPOSE_ADMIN_INVITE: ADMIN_INVITE_TTL_SECONDS,
}
ALLOWED_PURPOSES = frozenset(DEFAULT_TTLS.keys())

_MAX_IN_MEMORY_TOKENS = 10_000
_TOKENS_LOCK = threading.Lock()
_IN_MEMORY_TOKENS: dict[str, tuple[int, str]] = {}

_ATOMIC_GETDEL_LUA = (
    "local v = redis.call('GET', KEYS[1]); "
    "if v then redis.call('DEL', KEYS[1]); end; "
    "return v"
)


def _validate_purpose(purpose: str) -> str:
    if purpose not in ALLOWED_PURPOSES:
        raise ValueError(
            f"Unsupported onboarding token purpose: {purpose!r}. "
            f"Expected one of {sorted(ALLOWED_PURPOSES)}."
        )
    return purpose


def _token_key(purpose: str, token: str) -> str:
    digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
    return f"elder:onboarding:{purpose}:{digest}"


def _get_redis_client() -> Any:
    """Return an active Redis client from the Quart app if configured."""
    try:
        client = getattr(current_app, "redis_client", None) or getattr(
            current_app, "redis", None
        )
        if client is not None and hasattr(client, "set") and hasattr(client, "get"):
            return client
    except RuntimeError:
        return None
    return None


def _decode_payload(raw: Any) -> dict[str, Any] | None:
    if raw is None:
        return None
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8")
    if not isinstance(raw, str):
        return None
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else None
    except (ValueError, TypeError):
        return None


def _record_metric(purpose: str, action: str) -> None:
    if _onboarding_token_counter is None:
        return
    try:
        _onboarding_token_counter.add(1, {"purpose": purpose, "action": action})
    except Exception:  # pragma: no cover - telemetry must never break auth
        pass


def issue_onboarding_token(
    purpose: str,
    payload: dict[str, Any],
    ttl_seconds: int | None = None,
    *,
    now: int | None = None,
) -> str:
    """Issue a cryptographically random onboarding token and persist its payload.

    Args:
        purpose: Strictly ``"email_verify"`` or ``"admin_invite"``.
        payload: JSON-serializable metadata dict associated with the token.
        ttl_seconds: Optional TTL override in seconds (defaults per purpose).
        now: Optional Unix timestamp override for deterministic tests.

    Returns:
        The raw URL-safe token string (returned once to the caller; never stored
        in plaintext or logged).
    """
    _validate_purpose(purpose)
    if not isinstance(payload, dict):
        raise ValueError("payload must be a dict")

    ttl = DEFAULT_TTLS[purpose] if ttl_seconds is None else int(ttl_seconds)
    if ttl <= 0:
        raise ValueError("ttl_seconds must be positive")

    token = secrets.token_urlsafe(32)
    key = _token_key(purpose, token)
    serialized = json.dumps(payload, separators=(",", ":"), sort_keys=True)

    redis_client = _get_redis_client()
    stored_in_redis = False
    if redis_client is not None:
        try:
            redis_client.set(key, serialized, ex=ttl)
            stored_in_redis = True
        except Exception as exc:
            logger.warning(
                "onboarding_token_redis_store_failed",
                purpose=purpose,
                error_type=type(exc).__name__,
            )

    if not stored_in_redis:
        current_ts = int(time.time()) if now is None else int(now)
        expires_at = current_ts + ttl
        with _TOKENS_LOCK:
            expired_keys = [
                k for k, (exp, _val) in _IN_MEMORY_TOKENS.items() if exp <= current_ts
            ]
            for k in expired_keys:
                _IN_MEMORY_TOKENS.pop(k, None)
            if len(_IN_MEMORY_TOKENS) >= _MAX_IN_MEMORY_TOKENS:
                oldest = min(_IN_MEMORY_TOKENS.items(), key=lambda item: item[1][0])[0]
                _IN_MEMORY_TOKENS.pop(oldest, None)
            _IN_MEMORY_TOKENS[key] = (expires_at, serialized)

    _record_metric(purpose, "issue")
    return token


def peek_onboarding_token(
    purpose: str,
    token: str,
    *,
    now: int | None = None,
) -> dict[str, Any] | None:
    """Read an onboarding token's payload without consuming it.

    Returns ``None`` if the token is missing, malformed, expired, or issued for
    a different purpose.
    """
    _validate_purpose(purpose)
    if not isinstance(token, str) or not token.strip():
        return None

    key = _token_key(purpose, token.strip())
    redis_client = _get_redis_client()
    if redis_client is not None:
        try:
            raw = redis_client.get(key)
            if raw is not None:
                _record_metric(purpose, "peek_hit")
                return _decode_payload(raw)
        except Exception as exc:
            logger.warning(
                "onboarding_token_redis_peek_failed",
                purpose=purpose,
                error_type=type(exc).__name__,
            )

    current_ts = int(time.time()) if now is None else int(now)
    with _TOKENS_LOCK:
        entry = _IN_MEMORY_TOKENS.get(key)
        if entry is None:
            return None
        expires_at, serialized = entry
        if expires_at <= current_ts:
            _IN_MEMORY_TOKENS.pop(key, None)
            return None
        _record_metric(purpose, "peek_hit")
        return _decode_payload(serialized)


def consume_onboarding_token(
    purpose: str,
    token: str,
    *,
    now: int | None = None,
) -> dict[str, Any] | None:
    """Atomically consume a single-use onboarding token and return its payload.

    Returns ``None`` if the token does not exist, has expired, was issued for a
    different purpose, or has already been consumed.
    """
    _validate_purpose(purpose)
    if not isinstance(token, str) or not token.strip():
        return None

    key = _token_key(purpose, token.strip())
    redis_client = _get_redis_client()
    if redis_client is not None:
        try:
            if hasattr(redis_client, "getdel"):
                raw = redis_client.getdel(key)
            elif hasattr(redis_client, "eval"):
                raw = redis_client.eval(_ATOMIC_GETDEL_LUA, 1, key)
            else:
                raw = redis_client.get(key)
                if raw is not None and hasattr(redis_client, "delete"):
                    redis_client.delete(key)
            if raw is not None:
                _record_metric(purpose, "consume_hit")
                return _decode_payload(raw)
        except Exception as exc:
            logger.warning(
                "onboarding_token_redis_consume_failed",
                purpose=purpose,
                error_type=type(exc).__name__,
            )

    current_ts = int(time.time()) if now is None else int(now)
    with _TOKENS_LOCK:
        entry = _IN_MEMORY_TOKENS.pop(key, None)
        if entry is None:
            return None
        expires_at, serialized = entry
        if expires_at <= current_ts:
            return None
        _record_metric(purpose, "consume_hit")
        return _decode_payload(serialized)


def reset_onboarding_tokens() -> None:
    """Clear the in-memory onboarding token store (for deterministic unit tests)."""
    with _TOKENS_LOCK:
        _IN_MEMORY_TOKENS.clear()
