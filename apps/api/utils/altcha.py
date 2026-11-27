"""ALTCHA proof-of-work CAPTCHA challenge generation and server-side verification.

Implements the ALTCHA SHA-256 proof-of-work specification:
- Challenge salt carries an explicit ``?expires=<unix_ts>`` expiry parameter.
- HMAC-SHA256 signature is keyed from ``CAPTCHA_SECRET`` or ``SECRET_KEY``
  (derived via PBKDF2-HMAC-SHA256; fails loudly if neither is configured).
- Solved challenges are single-use (replay-protected via Redis ``SET NX EX``
  with a bounded thread-safe in-memory fallback when Redis is unavailable).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import threading
import time
from functools import lru_cache
from typing import Any
from urllib.parse import parse_qs, urlsplit

from quart import current_app

DEFAULT_ALTCHA_MAX_NUMBER = 100_000
MIN_ALTCHA_MAX_NUMBER = 1_000
MAX_ALTCHA_MAX_NUMBER = 1_000_000
DEFAULT_ALTCHA_TTL_SECONDS = 300
ALTCHA_FAILED_ATTEMPTS_THRESHOLD = 3
_MAX_PAYLOAD_BYTES = 4096
_KDF_SALT = b"elder:altcha:hmac:v1"
_KDF_ITERATIONS = 100_000
_MAX_IN_MEMORY_USED = 10_000

_USED_LOCK = threading.Lock()
_USED_CHALLENGES: dict[str, int] = {}


@lru_cache(maxsize=8)
def _derive_hmac_key(raw_key: str) -> bytes:
    """Derive a domain-separated 32-byte HMAC key from the configured secret."""
    return hashlib.pbkdf2_hmac(
        "sha256",
        raw_key.encode("utf-8"),
        _KDF_SALT,
        _KDF_ITERATIONS,
        dklen=32,
    )


def get_altcha_hmac_key() -> bytes:
    """Resolve and derive the ALTCHA HMAC key from ``CAPTCHA_SECRET`` or ``SECRET_KEY``.

    Raises:
        RuntimeError: When neither ``CAPTCHA_SECRET`` nor ``SECRET_KEY`` is configured.
    """
    raw = (os.environ.get("CAPTCHA_SECRET") or "").strip()
    if not raw:
        try:
            cfg_secret = current_app.config.get("SECRET_KEY")
            if isinstance(cfg_secret, str):
                raw = cfg_secret.strip()
        except RuntimeError:
            raw = ""
    if not raw:
        raw = (os.environ.get("SECRET_KEY") or "").strip()
    if not raw:
        raise RuntimeError(
            "CAPTCHA_SECRET or SECRET_KEY must be configured for ALTCHA verification"
        )
    return _derive_hmac_key(raw)


def _resolve_max_number() -> int:
    raw = (os.environ.get("ALTCHA_MAX_NUMBER") or "").strip()
    if not raw:
        return DEFAULT_ALTCHA_MAX_NUMBER
    try:
        parsed = int(raw)
    except ValueError:
        return DEFAULT_ALTCHA_MAX_NUMBER
    return max(MIN_ALTCHA_MAX_NUMBER, min(MAX_ALTCHA_MAX_NUMBER, parsed))


def _resolve_ttl_seconds() -> int:
    raw = (os.environ.get("ALTCHA_TTL_SECONDS") or "").strip()
    if not raw:
        return DEFAULT_ALTCHA_TTL_SECONDS
    try:
        parsed = int(raw)
    except ValueError:
        return DEFAULT_ALTCHA_TTL_SECONDS
    return max(30, min(3600, parsed))


def create_altcha_challenge(
    *,
    max_number: int | None = None,
    ttl_seconds: int | None = None,
    now: int | None = None,
) -> dict[str, Any]:
    """Generate a signed ALTCHA SHA-256 proof-of-work challenge with expiry."""
    maxnumber = max_number if max_number is not None else _resolve_max_number()
    ttl = ttl_seconds if ttl_seconds is not None else _resolve_ttl_seconds()
    current_ts = int(time.time()) if now is None else int(now)
    expires = current_ts + ttl

    salt = f"{secrets.token_hex(12)}?expires={expires}"
    number = secrets.randbelow(maxnumber + 1)
    challenge = hashlib.sha256(f"{salt}{number}".encode("utf-8")).hexdigest()
    key = get_altcha_hmac_key()
    signature = hmac.new(key, challenge.encode("utf-8"), hashlib.sha256).hexdigest()

    return {
        "algorithm": "SHA-256",
        "challenge": challenge,
        "maxnumber": maxnumber,
        "salt": salt,
        "signature": signature,
    }


def _extract_salt_expires(salt: str) -> int | None:
    """Parse the required ``expires`` Unix timestamp query param from ``salt``."""
    query = urlsplit(salt).query
    if not query:
        return None
    values = parse_qs(query, keep_blank_values=False).get("expires")
    if not values:
        return None
    try:
        return int(values[0])
    except ValueError:
        return None


def _get_redis_client() -> Any:
    """Return an active Redis client from the Quart app if configured."""
    try:
        client = getattr(current_app, "redis_client", None) or getattr(
            current_app, "redis", None
        )
        if client is not None and hasattr(client, "set"):
            return client
    except RuntimeError:
        return None
    return None


def _consume_challenge(challenge: str, expires: int, now: int) -> bool:
    """Atomically record ``challenge`` as used. Returns False if already consumed."""
    ttl = max(1, expires - now)
    redis_client = _get_redis_client()
    if redis_client is not None:
        try:
            stored = redis_client.set(f"altcha:used:{challenge}", "1", ex=ttl, nx=True)
            return bool(stored)
        except Exception:
            # Fall through to in-memory replay store if Redis is transiently unavailable.
            pass

    with _USED_LOCK:
        expired_keys = [k for k, exp in _USED_CHALLENGES.items() if exp <= now]
        for k in expired_keys:
            _USED_CHALLENGES.pop(k, None)
        if challenge in _USED_CHALLENGES:
            return False
        if len(_USED_CHALLENGES) >= _MAX_IN_MEMORY_USED:
            # Evict oldest entry to keep memory strictly bounded.
            oldest = min(_USED_CHALLENGES.items(), key=lambda item: item[1])[0]
            _USED_CHALLENGES.pop(oldest, None)
        _USED_CHALLENGES[challenge] = expires
        return True


def reset_used_challenges() -> None:
    """Clear the in-memory replay cache (for deterministic unit tests)."""
    with _USED_LOCK:
        _USED_CHALLENGES.clear()


def verify_altcha_payload(
    token: Any,
    *,
    consume: bool = True,
    now: int | None = None,
) -> tuple[bool, str | None]:
    """Verify a base64-encoded ALTCHA proof-of-work payload server-side.

    Args:
        token: Base64-encoded JSON string produced by the ALTCHA widget.
        consume: If True, mark the challenge single-use to block replays.
        now: Optional Unix timestamp override for deterministic tests.

    Returns:
        ``(True, None)`` when valid, or ``(False, error_message)`` on rejection.
    """
    if not isinstance(token, str) or not token.strip():
        return False, "Missing CAPTCHA verification token"

    raw_token = token.strip()
    if len(raw_token) > _MAX_PAYLOAD_BYTES:
        return False, "CAPTCHA payload is too large"

    try:
        # Support both standard and URL-safe base64 with optional missing padding.
        padded = raw_token + "=" * (-len(raw_token) % 4)
        decoded = base64.b64decode(padded.encode("ascii"), validate=False)
        payload = json.loads(decoded.decode("utf-8"))
    except Exception:
        return False, "Malformed CAPTCHA payload"

    if not isinstance(payload, dict):
        return False, "Invalid CAPTCHA payload format"

    algorithm = payload.get("algorithm")
    challenge = payload.get("challenge")
    salt = payload.get("salt")
    signature = payload.get("signature")
    number = payload.get("number")

    if algorithm != "SHA-256":
        return False, "Unsupported CAPTCHA algorithm"
    if (
        not isinstance(challenge, str)
        or not challenge
        or not isinstance(salt, str)
        or not salt
        or not isinstance(signature, str)
        or not signature
    ):
        return False, "Incomplete CAPTCHA payload"
    if isinstance(number, bool) or not isinstance(number, int) or number < 0:
        return False, "Invalid CAPTCHA solution"

    current_ts = int(time.time()) if now is None else int(now)
    expires = _extract_salt_expires(salt)
    if expires is None:
        return False, "CAPTCHA challenge is missing expiry"
    if current_ts > expires:
        return False, "CAPTCHA challenge has expired"

    expected_challenge = hashlib.sha256(f"{salt}{number}".encode("utf-8")).hexdigest()
    if not hmac.compare_digest(expected_challenge, challenge):
        return False, "Invalid CAPTCHA proof-of-work"

    key = get_altcha_hmac_key()
    expected_signature = hmac.new(
        key, challenge.encode("utf-8"), hashlib.sha256
    ).hexdigest()
    if not hmac.compare_digest(expected_signature, signature):
        return False, "Invalid CAPTCHA signature"

    if consume and not _consume_challenge(challenge, expires, current_ts):
        return False, "CAPTCHA token has already been used"

    return True, None


def requires_login_captcha(
    tenant_id: int,
    email: str,
    *,
    threshold: int = ALTCHA_FAILED_ATTEMPTS_THRESHOLD,
) -> bool:
    """Return True if the account has reached the failed-attempt CAPTCHA threshold."""
    if not tenant_id or not email:
        return False
    try:
        user = (
            current_app.db(
                (current_app.db.portal_users.tenant_id == tenant_id)
                & (current_app.db.portal_users.email == email.strip().lower())
            )
            .select(current_app.db.portal_users.failed_login_attempts, limitby=(0, 1))
            .first()
        )
    except Exception:
        return False
    if not user:
        return False
    attempts = getattr(user, "failed_login_attempts", 0) or 0
    return int(attempts) >= threshold
