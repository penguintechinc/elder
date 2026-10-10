"""Rate limiting middleware for the Elder Quart API.

Implements a Redis-backed fixed-window rate limiter wired as a Quart
``before_request`` hook. The limiter is intentionally dependency-light: it
uses the existing ``redis`` package (``redis.asyncio``) and a single INCR +
TTL per request.

Design notes:
    * Keying prefers the authenticated identity/tenant, falling back to the
      client IP so unauthenticated traffic is still bounded.
    * ``/healthz`` and ``/metrics`` are always exempt.
    * If Redis is unavailable the limiter **fails open** (logs and allows the
      request) so a Redis outage never takes down the API.
    * When ``RATELIMIT_ENABLED`` is false the hook is a no-op and the default
      request behavior is preserved.
    * Per-tenant / per-tier limits are supported through
      :func:`get_rate_limit`, a simple hook that can be extended without
      touching the middleware itself.
"""

import time
from typing import Optional, Tuple

import structlog
from quart import Quart, g, jsonify, request

logger = structlog.get_logger()

# Paths that are never rate limited (health checks / scrape endpoints).
EXEMPT_PATHS = frozenset({"/healthz", "/metrics"})

# Map time-window units to seconds. Accepts singular, plural and single-letter
# abbreviations (e.g. "minute", "minutes", "min", "m").
_UNIT_SECONDS = {
    "s": 1,
    "sec": 1,
    "second": 1,
    "seconds": 1,
    "m": 60,
    "min": 60,
    "minute": 60,
    "minutes": 60,
    "h": 3600,
    "hour": 3600,
    "hours": 3600,
    "d": 86400,
    "day": 86400,
    "days": 86400,
}

# Fallback if RATELIMIT_DEFAULT is missing or unparseable.
_FALLBACK_LIMIT = (100, 3600)

# Module-level async Redis client cache, keyed by storage URL. Reused across
# requests so we do not open a new connection pool per request.
_redis_clients: dict = {}


def parse_rate_limit(spec: str) -> Tuple[int, int]:
    """Parse a rate-limit spec like ``"100/minute"`` into ``(limit, seconds)``.

    Accepts an optional multiplier on the unit, e.g. ``"50/15minutes"`` or
    ``"50/15 minutes"``. Falls back to :data:`_FALLBACK_LIMIT` on any parse
    error.

    Args:
        spec: Rate-limit string, e.g. ``"100/hour"``.

    Returns:
        Tuple of (max requests, window in seconds).
    """
    if not spec or "/" not in spec:
        return _FALLBACK_LIMIT

    try:
        count_part, window_part = spec.split("/", 1)
        count = int(count_part.strip())
        window_part = window_part.strip().lower()

        # Separate an optional leading multiplier from the unit
        # ("15minutes" -> 15, "minutes"; "minute" -> 1, "minute").
        num = ""
        idx = 0
        while idx < len(window_part) and window_part[idx].isdigit():
            num += window_part[idx]
            idx += 1
        multiplier = int(num) if num else 1
        unit = window_part[idx:].strip()

        seconds = _UNIT_SECONDS.get(unit)
        if seconds is None or count <= 0 or multiplier <= 0:
            return _FALLBACK_LIMIT

        return count, seconds * multiplier
    except (ValueError, AttributeError):
        return _FALLBACK_LIMIT


def get_rate_limit(app: Quart, identity, tenant_id) -> Tuple[int, int]:
    """Return the ``(limit, window_seconds)`` for a given identity/tenant.

    This is the extension hook for per-tenant or per-tier limits. The default
    implementation returns the limit parsed from ``RATELIMIT_DEFAULT`` for
    every caller. Extend this to look up a tenant tier (e.g. from the DB or a
    config map) and return a different limit.

    Args:
        app: The Quart application (for config access).
        identity: The authenticated identity/user, or ``None``.
        tenant_id: The resolved tenant id, or ``None``.

    Returns:
        Tuple of (max requests, window in seconds).
    """
    return parse_rate_limit(app.config.get("RATELIMIT_DEFAULT", "100/hour"))


def _resolve_identity() -> Tuple[Optional[object], Optional[object]]:
    """Resolve the current identity and tenant id from the request context.

    Returns:
        Tuple of (identity, tenant_id); either element may be ``None``.
    """
    identity = getattr(g, "current_user", None)
    tenant_id = getattr(identity, "tenant_id", None) if identity is not None else None
    return identity, tenant_id


def _build_key(identity, tenant_id) -> str:
    """Build the Redis key for the current caller.

    Prefers a stable authenticated identity (tenant + identity id); falls back
    to the client IP for unauthenticated traffic.
    """
    if identity is not None and getattr(identity, "id", None) is not None:
        tenant_part = tenant_id if tenant_id is not None else "none"
        return f"ratelimit:tenant:{tenant_part}:identity:{identity.id}"

    client_ip = request.remote_addr or "unknown"
    return f"ratelimit:ip:{client_ip}"


def get_redis_client(url: str):
    """Return (and cache) an async Redis client for ``url``.

    Separated into its own function so tests can patch it without a live
    Redis server.
    """
    client = _redis_clients.get(url)
    if client is None:
        import redis.asyncio as redis_asyncio

        client = redis_asyncio.from_url(url, encoding="utf-8", decode_responses=True)
        _redis_clients[url] = client
    return client


async def _incr_fixed_window(client, key: str, window: int) -> Tuple[int, int]:
    """Increment the fixed-window counter and return ``(count, ttl)``.

    Sets the TTL on first increment so the window expires automatically.

    Args:
        client: Async Redis client.
        key: Counter key.
        window: Window length in seconds.

    Returns:
        Tuple of (current count in window, seconds until reset).
    """
    pipe = client.pipeline()
    pipe.incr(key)
    pipe.ttl(key)
    count, ttl = await pipe.execute()
    count = int(count)

    # ttl == -1 means the key exists with no expiry (first hit in this window,
    # or a key that somehow lost its TTL). Set/refresh the window expiry.
    if ttl is None or int(ttl) < 0:
        await client.expire(key, window)
        ttl = window

    return count, int(ttl)


def init_rate_limiting(app: Quart) -> None:
    """Wire the rate-limiting middleware onto the Quart app.

    Registers a ``before_request`` hook that enforces the limit and an
    ``after_request`` hook that attaches ``X-RateLimit-*`` headers to allowed
    responses. Does nothing meaningful when ``RATELIMIT_ENABLED`` is false.

    Args:
        app: The Quart application.
    """

    @app.before_request
    async def _rate_limit_before_request():
        if not app.config.get("RATELIMIT_ENABLED"):
            return None

        path = request.path
        if path in EXEMPT_PATHS:
            return None

        identity, tenant_id = _resolve_identity()
        limit, window = get_rate_limit(app, identity, tenant_id)
        key = _build_key(identity, tenant_id)

        try:
            client = get_redis_client(app.config["RATELIMIT_STORAGE_URL"])
            count, ttl = await _incr_fixed_window(client, key, window)
        except Exception as exc:
            # Fail OPEN: never let a Redis problem break the request.
            logger.warning("rate_limit_backend_unavailable", error=str(exc), key=key)
            return None

        remaining = max(0, limit - count)
        reset_at = int(time.time()) + ttl

        # Stash for the after_request hook so allowed responses carry headers.
        g.rate_limit_headers = {
            "X-RateLimit-Limit": str(limit),
            "X-RateLimit-Remaining": str(remaining),
            "X-RateLimit-Reset": str(reset_at),
        }

        if count > limit:
            logger.info(
                "rate_limit_exceeded",
                key=key,
                limit=limit,
                window=window,
                count=count,
            )
            response = jsonify(
                {
                    "error": "Rate Limit Exceeded",
                    "message": "Too many requests",
                }
            )
            response.status_code = 429
            response.headers["Retry-After"] = str(ttl)
            response.headers["X-RateLimit-Limit"] = str(limit)
            response.headers["X-RateLimit-Remaining"] = "0"
            response.headers["X-RateLimit-Reset"] = str(reset_at)
            return response

        return None

    @app.after_request
    async def _rate_limit_after_request(response):
        headers = getattr(g, "rate_limit_headers", None)
        if headers:
            for name, value in headers.items():
                response.headers.setdefault(name, value)
        return response

    logger.info(
        "rate_limiting_initialized", enabled=app.config.get("RATELIMIT_ENABLED")
    )
