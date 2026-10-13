"""Async-safe rate limiting for Elder, backed by penguin-limiter.

`penguin_limiter`'s own Flask/Quart middleware (`FlaskRateLimiter`) calls its
storage backend synchronously from a plain `before_request` hook -- fine for
Flask's sync-per-request model, but a blocking Redis/memory-lock call made
directly inside a Quart coroutine would stall the whole event loop for every
request (see `general.md` "never block the event loop"). This module wraps
the same public algorithm/storage primitives behind `asyncio.to_thread()`
instead of using that middleware directly.

Key = tenant + client IP (falls back to IP-only / `"anon"` tenant before
`g.claims` is populated, e.g. the login endpoint itself) -- so a single
tenant or a single anonymous IP can't exhaust another's quota. Degrades
OPEN on any storage error: a rate limiter must never turn a backend outage
into a full service outage (see finding #5).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from penguin_limiter.algorithms.sliding_window import SlidingWindow
from penguin_limiter.config import RateLimitConfig
from penguin_limiter.ip import should_rate_limit
from penguin_limiter.storage.memory import MemoryStorage
from quart import Quart, Response, g, jsonify, request

logger = logging.getLogger(__name__)

_HEADERS_G_KEY = "_rate_limit_headers"


class RequestRateLimiter:
    """Per-tenant + per-IP sliding-window rate limiter for a Quart app."""

    def __init__(self, config: RateLimitConfig, storage: Any) -> None:
        self._config = config
        self._storage = storage
        self._algo = SlidingWindow(storage, config.limit, config.window)

    def _key(self) -> str | None:
        """Build the compound rate-limit key, or None to skip entirely.

        Internal/private-sourced traffic (cluster health checks, sidecars)
        is never rate limited, matching `penguin_limiter`'s own default.
        """
        xff = request.headers.get("X-Forwarded-For")
        xri = request.headers.get("X-Real-IP")
        remote_addr = request.remote_addr or ""
        do_limit, client_ip = should_rate_limit(xff, xri, remote_addr)
        if self._config.skip_private_ips and not do_limit:
            return None

        claims = getattr(g, "claims", {}) or {}
        tenant = claims.get("tenant") or "anon"
        ip = client_ip or remote_addr or "unknown"
        return f"{self._config.key_prefix}:{tenant}:{ip}"

    async def check(self) -> tuple[bool, dict[str, str]]:
        """Return (allowed, response_headers).

        Runs the (synchronous) storage/algorithm call in a thread so it
        never blocks the event loop. Any backend exception fails OPEN.
        """
        key = self._key()
        if key is None:
            return True, {}

        try:
            result = await asyncio.to_thread(self._algo.is_allowed, key)
        except Exception:
            logger.warning("rate_limiter_backend_error", exc_info=True)
            return True, {}

        headers = {
            "X-RateLimit-Limit": str(result.limit),
            "X-RateLimit-Remaining": str(result.remaining),
        }
        if not result.allowed:
            headers["Retry-After"] = str(max(0, int(result.reset_after)))
        return result.allowed, headers


def init_rate_limiter(app: Quart) -> RequestRateLimiter | None:
    """Instantiate the limiter and wire it into `app`'s request lifecycle.

    Reads `RATELIMIT_ENABLED` / `RATELIMIT_DEFAULT` / `RATELIMIT_STORAGE_URL`
    from `app.config` (populated from env by `apps.api.config.Config`).
    Prefers the Redis client already cached by `_init_redis_client` (shared
    counters across replicas); falls back to in-process memory storage if
    Redis is unavailable -- best-effort per-pod limiting rather than a
    startup failure.

    Must be called AFTER `_register_before_request(app)` so the tenant
    claim (`g.claims`) populated by that hook is available for the
    per-tenant key by the time this hook runs.
    """
    if not app.config.get("RATELIMIT_ENABLED", True):
        logger.info("rate_limiter_disabled")
        return None

    spec = app.config.get("RATELIMIT_DEFAULT", "100/hour")

    storage: Any = getattr(app, "redis_client", None)
    if storage is not None:
        from penguin_limiter.storage.redis_store import RedisStorage

        storage = RedisStorage(storage, key_prefix="elder_rl")
        backend_name = "redis"
    else:
        storage = MemoryStorage()
        backend_name = "memory"

    try:
        config = RateLimitConfig.from_string(spec, key_prefix="elder")
    except ValueError:
        logger.warning(f"rate_limiter_invalid_spec spec={spec!r}; using 100/hour")
        config = RateLimitConfig.from_string("100/hour", key_prefix="elder")

    limiter = RequestRateLimiter(config, storage)

    @app.before_request
    async def _enforce_rate_limit() -> Response | None:
        allowed, headers = await limiter.check()
        setattr(g, _HEADERS_G_KEY, headers)
        if not allowed:
            response = jsonify(
                {"error": "Rate Limit Exceeded", "message": "Too many requests"}
            )
            response.status_code = 429
            for key, value in headers.items():
                response.headers[key] = value
            return response
        return None

    @app.after_request
    async def _attach_rate_limit_headers(response: Response) -> Response:
        headers = getattr(g, _HEADERS_G_KEY, None)
        if headers:
            for key, value in headers.items():
                response.headers.setdefault(key, value)
        return response

    app.extensions["rate_limiter"] = limiter
    logger.info(f"rate_limiter_initialized spec={spec} backend={backend_name}")
    return limiter
