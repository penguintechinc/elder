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

Precedence (highest to lowest):
1. ``RATELIMIT_ENDPOINT_OVERRIDES`` config/env map (exact endpoint name or path prefix)
2. ``@rate_limit(...)`` per-endpoint decorator override
3. Platform/webhook receiver tier (``RATELIMIT_PLATFORM_DEFAULT``, prefix ``elder:platform``)
4. Global default tier (``RATELIMIT_DEFAULT``, prefix ``elder``)
"""

from __future__ import annotations

import asyncio
import functools
import inspect
import ipaddress
import json
import logging
import re
from collections.abc import Callable
from typing import Any

from penguin_limiter.algorithms.sliding_window import SlidingWindow
from penguin_limiter.config import RateLimitConfig
from penguin_limiter.ip import is_private_ip, should_rate_limit
from penguin_limiter.storage.memory import MemoryStorage
from quart import Quart, Response, current_app, g, jsonify, make_response, request

logger = logging.getLogger(__name__)

_HEADERS_G_KEY = "_rate_limit_headers"
_ENFORCED_G_KEY = "_rate_limit_enforced"
_STORAGE_EXT_KEY = "_rate_limiter_storage"
_ROUTE_LIMITERS_EXT_KEY = "route_rate_limiters"

# Values accepted to explicitly disable a tier or per-endpoint rate limit.
_DISABLED_SPEC_VALUES: frozenset[str] = frozenset(
    {"off", "disabled", "none", "unlimited", "false", "0"}
)


def _is_disabled_spec(spec: Any) -> bool:
    """Return True if *spec* explicitly disables rate limiting."""
    if spec is None or spec is False:
        return True
    if isinstance(spec, str):
        return spec.strip().lower() in _DISABLED_SPEC_VALUES
    return False


# Platform-facing / machine-to-machine receiver path prefixes (inbound webhooks,
# Alertmanager alerts, Streams & Flows public webhook triggers).
# Admin CRUD routes (e.g. /api/v1/webhooks, /api/v1/webhooks/inbound,
# /api/v1/streams/<id>/webhooks) intentionally remain on the user-facing tier.
PLATFORM_PATH_PREFIXES: tuple[str, ...] = (
    "/api/v1/webhooks/receive/",
    "/api/v1/webhooks/inbound/receive/",
    "/api/v1/on-call/webhooks/",
    "/api/v1/sync/webhooks/",
    "/api/v1/hooks/",
    "/api/v1/flows-hooks/",
)

# Regex patterns for platform-facing receivers that share a parent prefix with
# admin CRUD endpoints (SCIM provisioning vs SCIM config, scanner job/scan
# ingestion vs job/scan management).
_PLATFORM_PATH_PATTERNS: tuple[re.Pattern[str], ...] = (
    # SCIM 2.0 provisioning endpoints (/api/v1/sso/scim/<tenant_id>/...,
    # /api/v1/scim/..., /scim/...), excluding admin /scim/config* routes.
    re.compile(r"^/(?:api/v1/(?:sso/)?)?scim/(?!config(?:/|$)).+"),
    # Discovery scanner worker endpoints (pending / start / complete).
    re.compile(r"^/api/v1/discovery/jobs/(?:pending|\d+/(?:start|complete))/?$"),
    # SBOM scanner worker endpoints (pending / start / results).
    re.compile(r"^/api/v1/sbom/scans/(?:pending|\d+/(?:start|results))/?$"),
)


def is_platform_request(path: str) -> bool:
    """Return True if *path* is a platform-facing / webhook receiver route.

    Platform-facing routes (inbound webhooks, Alertmanager webhooks, Streams &
    Flows public webhook triggers, SCIM 2.0 provisioning, and scanner worker
    ingestion) use the separate ``RATELIMIT_PLATFORM_DEFAULT`` tier so bursts
    of automated traffic cannot exhaust user-facing quotas and vice versa.
    """
    if not path:
        return False
    if any(path.startswith(prefix) for prefix in PLATFORM_PATH_PREFIXES):
        return True
    return any(pattern.match(path) for pattern in _PLATFORM_PATH_PATTERNS)


def _has_rate_limit_override(view_fn: Any) -> bool:
    """Return True if *view_fn* (or any wrapped inner function) opts out of
    the global rate-limit check via ``@rate_limit(..., override_global=True)``.
    """
    seen: set[int] = set()
    current = view_fn
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if getattr(current, "_rate_limit_override", False):
            return True
        current = getattr(current, "__wrapped__", None)
    return False


def _parse_endpoint_overrides(raw: Any) -> dict[str, str | None]:
    """Parse ``RATELIMIT_ENDPOINT_OVERRIDES`` from a JSON string or dict.

    Values that disable rate limiting (``"off"``, ``"disabled"``, ``"none"``,
    ``"unlimited"``, ``null``, ``false``) are normalized to ``None``.
    """
    if not raw:
        return {}
    if isinstance(raw, str):
        raw_str = raw.strip()
        if not raw_str:
            return {}
        try:
            parsed = json.loads(raw_str)
        except json.JSONDecodeError:
            logger.warning(
                f"rate_limiter_invalid_endpoint_overrides raw={raw_str!r}; ignoring"
            )
            return {}
    elif isinstance(raw, dict):
        parsed = raw
    else:
        logger.warning(
            f"rate_limiter_invalid_endpoint_overrides type={type(raw).__name__}; ignoring"
        )
        return {}

    if not isinstance(parsed, dict):
        logger.warning("rate_limiter_endpoint_overrides_not_dict; ignoring")
        return {}

    result: dict[str, str | None] = {}
    for key, value in parsed.items():
        if not isinstance(key, str) or not key.strip():
            continue
        norm_key = key.strip()
        if _is_disabled_spec(value):
            result[norm_key] = None
        elif isinstance(value, str) and value.strip():
            result[norm_key] = value.strip()
    return result


def _match_endpoint_override(
    overrides: dict[str, RequestRateLimiter | None],
    endpoint: str | None,
    path: str,
) -> tuple[bool, RequestRateLimiter | None]:
    """Resolve the highest-priority config override limiter for a request.

    Returns ``(matched, limiter_or_none)``. When ``matched`` is ``True`` and
    ``limiter_or_none`` is ``None``, the endpoint is explicitly exempt from
    rate limiting.

    Checks exact Quart endpoint name first, then exact path, then longest
    segment-boundary path prefix.
    """
    if not overrides:
        return False, None
    if endpoint and endpoint in overrides:
        return True, overrides[endpoint]
    if path and path in overrides:
        return True, overrides[path]

    best_key = ""
    best_limiter: RequestRateLimiter | None = None
    matched_any = False
    for key, limiter in overrides.items():
        if not key.startswith("/"):
            continue
        matched = (
            path.startswith(key)
            if key.endswith("/")
            else (path == key or path.startswith(key + "/"))
        )
        if matched and len(key) > len(best_key):
            matched_any = True
            best_key = key
            best_limiter = limiter
    return matched_any, best_limiter


def _get_or_create_storage(app: Quart) -> tuple[Any, str]:
    """Return (storage, backend_name), reusing any already-initialized backend."""
    existing_limiter = app.extensions.get("rate_limiter")
    if (
        existing_limiter is not None
        and getattr(existing_limiter, "_storage", None) is not None
    ):
        backend_name = (
            "redis"
            if type(existing_limiter._storage).__name__ == "RedisStorage"
            else "memory"
        )
        return existing_limiter._storage, backend_name

    cached = app.extensions.get(_STORAGE_EXT_KEY)
    if cached is not None:
        backend_name = "redis" if type(cached).__name__ == "RedisStorage" else "memory"
        return cached, backend_name

    redis_client: Any = getattr(app, "redis_client", None)
    if redis_client is not None:
        from penguin_limiter.storage.redis_store import RedisStorage

        storage: Any = RedisStorage(redis_client, key_prefix="elder_rl")
        backend_name = "redis"
    else:
        storage = MemoryStorage()
        backend_name = "memory"

    app.extensions[_STORAGE_EXT_KEY] = storage
    return storage, backend_name


class RequestRateLimiter:
    """Per-tenant + per-IP sliding-window rate limiter for a Quart app."""

    def __init__(self, config: RateLimitConfig, storage: Any) -> None:
        self._config = config
        self._storage = storage
        self._algo = SlidingWindow(storage, config.limit, config.window)

    def _extract_client_ip(self) -> tuple[bool, str | None]:
        """Extract client IP and whether the request should be rate limited.

        Precedence:
        1. ``CF-Connecting-IP`` when present as a single valid IPv4/IPv6
           address (comma-separated lists or invalid strings fall back cleanly).
        2. ``X-Forwarded-For`` / ``X-Real-IP`` / ``request.remote_addr`` via
           ``penguin_limiter.ip.should_rate_limit``.
        """
        cf_ip_raw = request.headers.get("CF-Connecting-IP")
        if cf_ip_raw:
            candidate = cf_ip_raw.strip()
            try:
                parsed = ipaddress.ip_address(candidate)
            except ValueError:
                parsed = None
            if parsed is not None:
                normalized = str(parsed)
                return (not is_private_ip(normalized)), normalized

        xff = request.headers.get("X-Forwarded-For")
        xri = request.headers.get("X-Real-IP")
        remote_addr = request.remote_addr or ""
        return should_rate_limit(xff, xri, remote_addr)

    def _key(self) -> str | None:
        """Build the compound rate-limit key, or None to skip entirely.

        Internal/private-sourced traffic (cluster health checks, sidecars)
        is never rate limited, matching `penguin_limiter`'s own default.
        """
        do_limit, client_ip = self._extract_client_ip()
        if self._config.skip_private_ips and not do_limit:
            return None

        claims = getattr(g, "claims", {}) or {}
        tenant = claims.get("tenant") or "anon"
        ip = client_ip or (request.remote_addr or "") or "unknown"
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


def _parse_tier_config(
    spec: str, *, key_prefix: str, fallback_spec: str
) -> RateLimitConfig:
    """Parse a rate-limit spec string with a safe fallback."""
    try:
        return RateLimitConfig.from_string(spec, key_prefix=key_prefix)
    except ValueError:
        logger.warning(
            f"rate_limiter_invalid_spec spec={spec!r} prefix={key_prefix!r}; "
            f"using {fallback_spec}"
        )
        return RateLimitConfig.from_string(fallback_spec, key_prefix=key_prefix)


def _rate_limit_exceeded_response(headers: dict[str, str]) -> Response:
    """Build the standardized 429 Too Many Requests response."""
    response = jsonify({"error": "Rate Limit Exceeded", "message": "Too many requests"})
    response.status_code = 429
    for key, value in headers.items():
        response.headers[key] = value
    return response


def rate_limit(
    spec: str,
    *,
    key_prefix: str = "elder_route",
    config_key: str | None = None,
    override_global: bool = True,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Per-endpoint rate-limit decorator.

    Args:
        spec: Default rate-limit spec (e.g. ``"10/minute"`` or ``"500/minute"``).
        key_prefix: Storage key prefix for this route's bucket.
        config_key: Optional ``app.config`` key that overrides *spec* at runtime.
        override_global: When ``True`` (default), the global ``before_request``
            limiter skips this endpoint so the endpoint can raise or lower its
            limit independently without consuming the global bucket. When
            ``False``, this limit applies in addition to the global/platform
            tier.
    """

    def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
        is_async = inspect.iscoroutinefunction(fn)

        @functools.wraps(fn)
        async def _wrapper(*args: Any, **kwargs: Any) -> Any:
            if getattr(g, _ENFORCED_G_KEY, False):
                return await fn(*args, **kwargs) if is_async else fn(*args, **kwargs)

            app = current_app._get_current_object()
            if not app.config.get("RATELIMIT_ENABLED", True):
                return await fn(*args, **kwargs) if is_async else fn(*args, **kwargs)

            effective_spec: Any = (
                app.config.get(config_key, spec) if config_key else spec
            )
            if _is_disabled_spec(effective_spec):
                setattr(g, _ENFORCED_G_KEY, True)
                return await fn(*args, **kwargs) if is_async else fn(*args, **kwargs)

            effective_spec_str = str(effective_spec)
            route_limiters: dict[tuple[str, str], RequestRateLimiter] = (
                app.extensions.setdefault(_ROUTE_LIMITERS_EXT_KEY, {})
            )
            cache_key = (key_prefix, effective_spec_str)
            route_limiter = route_limiters.get(cache_key)
            if route_limiter is None:
                storage, _ = _get_or_create_storage(app)
                fallback = "100/minute" if _is_disabled_spec(spec) else spec
                cfg = _parse_tier_config(
                    effective_spec_str, key_prefix=key_prefix, fallback_spec=fallback
                )
                route_limiter = RequestRateLimiter(cfg, storage)
                route_limiters[cache_key] = route_limiter

            allowed, headers = await route_limiter.check()
            setattr(g, _HEADERS_G_KEY, headers)
            setattr(g, _ENFORCED_G_KEY, True)
            if not allowed:
                return _rate_limit_exceeded_response(headers)

            result = await fn(*args, **kwargs) if is_async else fn(*args, **kwargs)
            if headers and "rate_limiter" not in app.extensions:
                response = await make_response(result)
                for key, value in headers.items():
                    response.headers.setdefault(key, value)
                return response
            return result

        _wrapper._rate_limit_override = override_global  # type: ignore[attr-defined]
        return _wrapper

    return decorator


def rate_limit_exempt(fn: Callable[..., Any]) -> Callable[..., Any]:
    """Exempt a single route from all in-app rate limiting."""
    return rate_limit("off", override_global=True)(fn)


def init_rate_limiter(app: Quart) -> RequestRateLimiter | None:
    """Instantiate the limiter tiers and wire them into `app`'s request lifecycle.

    Reads `RATELIMIT_ENABLED` / `RATELIMIT_DEFAULT` /
    `RATELIMIT_PLATFORM_DEFAULT` / `RATELIMIT_ENDPOINT_OVERRIDES` /
    `RATELIMIT_STORAGE_URL` from `app.config` (populated from env by
    `apps.api.config.Config`).
    Prefers the Redis client already cached by `_init_redis_client` (shared
    counters across replicas); falls back to in-process memory storage if
    Redis is unavailable -- best-effort per-pod limiting rather than a
    startup failure.

    User-facing routes use `RATELIMIT_DEFAULT` with key prefix `elder`, while
    platform-facing webhook/SCIM/scanner routes use `RATELIMIT_PLATFORM_DEFAULT`
    with key prefix `elder:platform` so their counters remain completely
    isolated. Explicit `RATELIMIT_ENDPOINT_OVERRIDES` and `@rate_limit`
    overrides take precedence over both tiers.

    Must be called AFTER `_register_before_request(app)` so the tenant
    claim (`g.claims`) populated by that hook is available for the
    per-tenant key by the time this hook runs.
    """
    if not app.config.get("RATELIMIT_ENABLED", True):
        logger.info("rate_limiter_disabled")
        return None

    spec = app.config.get("RATELIMIT_DEFAULT", "100/minute")
    platform_spec = app.config.get("RATELIMIT_PLATFORM_DEFAULT", "2000/minute")

    storage, backend_name = _get_or_create_storage(app)

    user_limiter: RequestRateLimiter | None = None
    if not _is_disabled_spec(spec):
        user_config = _parse_tier_config(
            str(spec), key_prefix="elder", fallback_spec="100/minute"
        )
        user_limiter = RequestRateLimiter(user_config, storage)

    platform_limiter: RequestRateLimiter | None = None
    if not _is_disabled_spec(platform_spec):
        platform_config = _parse_tier_config(
            str(platform_spec),
            key_prefix="elder:platform",
            fallback_spec="2000/minute",
        )
        platform_limiter = RequestRateLimiter(platform_config, storage)

    raw_overrides = _parse_endpoint_overrides(
        app.config.get("RATELIMIT_ENDPOINT_OVERRIDES")
    )
    override_limiters: dict[str, RequestRateLimiter | None] = {}
    for target, override_spec in raw_overrides.items():
        if override_spec is None:
            override_limiters[target] = None
            continue
        try:
            override_cfg = RateLimitConfig.from_string(
                override_spec, key_prefix=f"elder:override:{target}"
            )
        except ValueError:
            logger.warning(
                f"rate_limiter_invalid_override_spec target={target!r} "
                f"spec={override_spec!r}; ignoring"
            )
            continue
        override_limiters[target] = RequestRateLimiter(override_cfg, storage)

    @app.before_request
    async def _enforce_rate_limit() -> Response | None:
        if not app.config.get("RATELIMIT_ENABLED", True):
            return None

        # 1. Explicit config/env override (highest precedence)
        matched_override, override_limiter = _match_endpoint_override(
            override_limiters, request.endpoint, request.path
        )
        if matched_override:
            setattr(g, _ENFORCED_G_KEY, True)
            if override_limiter is None:
                return None  # Explicitly disabled / exempt for this endpoint
            allowed, headers = await override_limiter.check()
            setattr(g, _HEADERS_G_KEY, headers)
            if not allowed:
                return _rate_limit_exceeded_response(headers)
            return None

        # 2. Per-endpoint @rate_limit(..., override_global=True) decorator
        if request.endpoint:
            view_fn = app.view_functions.get(request.endpoint)
            if view_fn is not None and _has_rate_limit_override(view_fn):
                return None

        # 3. Platform/webhook receiver tier vs 4. Global default tier
        active_limiter = (
            platform_limiter if is_platform_request(request.path) else user_limiter
        )
        if active_limiter is None:
            return None
        allowed, headers = await active_limiter.check()
        setattr(g, _HEADERS_G_KEY, headers)
        if not allowed:
            return _rate_limit_exceeded_response(headers)
        return None

    @app.after_request
    async def _attach_rate_limit_headers(response: Response) -> Response:
        headers = getattr(g, _HEADERS_G_KEY, None)
        if headers:
            for key, value in headers.items():
                response.headers.setdefault(key, value)
        return response

    app.extensions["rate_limiter"] = user_limiter
    app.extensions["platform_rate_limiter"] = platform_limiter
    app.extensions["endpoint_override_rate_limiters"] = override_limiters
    logger.info(
        f"rate_limiter_initialized spec={spec} platform_spec={platform_spec} "
        f"overrides={len(override_limiters)} backend={backend_name}"
    )
    return user_limiter
