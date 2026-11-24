"""Per-caller rate and concurrency limiting for expensive read endpoints.

Wraps the shared sliding-window rate limiter and the in-process in-flight
limiter behind one decorator, keyed by ``tenant:subject`` taken from the
verified token (never the client IP or any request-supplied value).
"""

import inspect
from collections.abc import Callable
from functools import wraps
from typing import Any

from quart import current_app, g, jsonify

from apps.api.utils.inflight_limiter import InflightLimiter
from apps.api.utils.pagination import env_bounded_int
from apps.api.utils.rate_limiter import check_subject_rate_limit
from apps.api.utils.tenant_scoping import get_current_tenant_id


def resolve_limit_subject() -> str | None:
    """Build the ``tenant:subject`` limiter key from the verified token.

    Returns ``None`` when the token carries no tenant: such requests are
    rejected by the handlers' own tenant guard, so they are not metered.
    """
    tenant_id = get_current_tenant_id()
    if not tenant_id:
        return None
    claims = getattr(g, "claims", {}) or {}
    user = getattr(g, "current_user", None)
    subject = str(claims.get("sub") or getattr(user, "id", "") or "").strip()
    return f"{tenant_id}:{subject or '-'}"


def _refusal(error: str, message: str, retry_after: str) -> Any:
    """Build a 429 response carrying a ``Retry-After`` hint."""
    resp = jsonify({"error": error, "message": message})
    resp.status_code = 429
    resp.headers["Retry-After"] = retry_after
    return resp


def limit_per_subject(
    key_prefix: str,
    *,
    rate_env: str,
    rate_default: int,
    inflight_env: str,
    inflight_default: int,
) -> Callable:
    """Limit an endpoint per caller by requests/minute and in-flight count.

    Limits are read from the environment on each request through a parser
    that cannot raise. Routes sharing a decorator instance (or a
    ``key_prefix``) share one budget. Place below the auth decorators so the
    key always comes from an authenticated token.
    """
    inflight = InflightLimiter()

    def decorator(f: Callable) -> Callable:
        @wraps(f)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            subject = resolve_limit_subject()
            if subject is None:
                result = f(*args, **kwargs)
                return await result if inspect.isawaitable(result) else result

            allowed, headers = await check_subject_rate_limit(
                current_app,
                subject=subject,
                limit_per_minute=env_bounded_int(rate_env, rate_default, 100_000),
                key_prefix=key_prefix,
            )
            if not allowed:
                return _refusal(
                    "RATE_LIMITED",
                    "Too many requests",
                    headers.get("Retry-After", "60"),
                )
            max_inflight = env_bounded_int(inflight_env, inflight_default, 1000)
            if not inflight.try_acquire(subject, max_inflight):
                return _refusal(
                    "TOO_MANY_CONCURRENT", "Too many concurrent requests", "5"
                )
            try:
                result = f(*args, **kwargs)
                return await result if inspect.isawaitable(result) else result
            finally:
                inflight.release(subject)

        return wrapper

    # Exposed so operators/tests can inspect the shared in-flight table.
    decorator.inflight = inflight
    return decorator
