"""Enterprise-tier route gate: bulk/admin convenience layers only.

Per critical-rules.md "Feature Flags & License Tiers": statutory rights are
Free+ (all tiers); only the *admin convenience layer* on top of a statutory
right is Enterprise-gated. Never wrap a data subject's own self-service
endpoint (e.g. DSAR export/erasure) with this -- only bulk/admin operations
built on top of one.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from functools import wraps
from typing import Any, ParamSpec, TypeVar

import structlog
from quart import current_app

from apps.api.common.licensing.limits import resolve_limits
from apps.api.utils.api_responses import ApiResponse

logger = structlog.get_logger()

# The PenguinTech License Server reports "community"; Elder markets the same
# tier as "Free" (see limits.py's module docstring) -- normalize here so
# every caller of get_tier() sees the critical-rules.md tier vocabulary
# (free/professional/enterprise) rather than the license-server's synonym.
_TIER_ALIASES: dict[str, str] = {"community": "free"}
_TIER_ORDER: dict[str, int] = {"free": 0, "professional": 1, "enterprise": 2}

P = ParamSpec("P")
T = TypeVar("T")


def get_tier() -> str:
    """Resolve the current deployment's normalized license tier.

    Reads ``current_app.extensions["license_client"]`` (missing/erroring
    client degrades to "free", never raises -- see resolve_limits).

    Returns:
        One of "free", "professional", "enterprise".
    """
    license_client = current_app.extensions.get("license_client")
    tier, _ = resolve_limits(license_client)
    return _TIER_ALIASES.get(tier, tier)


def require_tier(
    minimum: str,
) -> Callable[[Callable[P, Awaitable[T]]], Callable[P, Awaitable[T | tuple[Any, int]]]]:
    """Route decorator: 403s unless the deployment's tier is >= `minimum`.

    Args:
        minimum: One of "free", "professional", "enterprise".

    Returns:
        A decorator for async Quart route handlers.
    """

    def decorator(
        func: Callable[P, Awaitable[T]],
    ) -> Callable[P, Awaitable[T | tuple[Any, int]]]:
        @wraps(func)
        async def wrapper(*args: P.args, **kwargs: P.kwargs) -> T | tuple[Any, int]:
            tier = get_tier()
            if _TIER_ORDER.get(tier, 0) < _TIER_ORDER.get(minimum, 0):
                logger.info(
                    "tier_gate_denied",
                    required_tier=minimum,
                    tier=tier,
                    endpoint=func.__name__,
                )
                return ApiResponse.error(
                    "tier_required",
                    403,
                    required_tier=minimum,
                    tier=tier,
                    upgrade=True,
                )
            return await func(*args, **kwargs)

        return wrapper

    return decorator
