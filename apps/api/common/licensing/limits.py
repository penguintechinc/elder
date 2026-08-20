"""Tier -> LimitSet resolver for Elder's license-enforcement framework.

Resolves per-deployment scale limits (users/admins/teams/tenants/objects/
nodes) from the license client's tier, with any license-server `.limits`
override layered on top of the tier defaults. `community` is the tier name
the PenguinTech License Server reports; Elder markets the same tier as
"Free" (see docs/licensing/README.md) -- the two are the same thing.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, fields, replace
from typing import Any
from weakref import WeakKeyDictionary

import structlog

logger = structlog.get_logger()


@dataclass(slots=True)
class LimitSet:
    """Resolved scale limits for a deployment tier.

    Each field is a non-negative int cap, or `None` for "unlimited" (∞).
    """

    max_global_admins: int | None
    max_tenant_admins: int | None
    max_teams: int | None
    max_tenants: int | None
    max_objects: int | None
    max_nodes_per_type: int | None


# Defaults per the plan's Global Constraints table. `community` == Elder's
# Free tier under a different name -- see module docstring.
TIER_DEFAULTS: dict[str, LimitSet] = {
    "community": LimitSet(
        max_global_admins=1,
        max_tenant_admins=0,
        max_teams=1,
        max_tenants=1,
        max_objects=1000,
        max_nodes_per_type=1,
    ),
    "professional": LimitSet(
        max_global_admins=1,
        max_tenant_admins=10,
        max_teams=None,
        max_tenants=1,
        max_objects=None,
        max_nodes_per_type=1,
    ),
    "enterprise": LimitSet(
        max_global_admins=None,
        max_tenant_admins=None,
        max_teams=None,
        max_tenants=None,
        max_objects=None,
        max_nodes_per_type=None,
    ),
}

_LIMIT_SET_FIELDS = frozenset(f.name for f in fields(LimitSet))
_CACHE_TTL_S = 60

# Cache keyed by the client object's identity (via a WeakKeyDictionary), not
# a single global slot and not id(license_client). A single-slot cache would
# let one call's resolved tier silently "stick" for 60s regardless of which
# client asked -- fine in production (one long-lived client per app), but
# wrong the moment more than one client is in play (tests, or a future
# multi-license-client deployment). Plain id()-keying has its own hazard:
# CPython reuses ids after garbage collection, so two short-lived,
# *different* client objects (e.g. one per test) can collide on the same id
# and silently return each other's cached tier. WeakKeyDictionary keys on
# real object identity while the object is alive and evicts its entry the
# moment the object is collected, so a reused id can never hit a stale entry.
_cache: "WeakKeyDictionary[Any, tuple[float, str, LimitSet]]" = WeakKeyDictionary()


def resolve_limits(license_client: Any | None) -> tuple[str, LimitSet]:
    """Resolve the effective (tier, LimitSet) for a license client.

    `license_client` is expected to expose `.validate()` returning an object
    with `.tier: str` and `.limits: dict[str, Any]` (matches
    `penguin_licensing.LicenseInfo`, but only duck-types on those two
    attributes so a lightweight test double works too). `None` (no client
    configured) and any exception from `.validate()` both degrade to the
    community tier with no overrides -- this must never raise.

    Args:
        license_client: A penguin_licensing client instance, or None.

    Returns:
        (tier, LimitSet) -- tier is one of "community", "professional",
        "enterprise" (or whatever the server returns, defaulting unknown
        values to the community LimitSet).
    """
    now = time.monotonic()
    if license_client is not None:
        cached = _cache.get(license_client)
        if cached is not None and now < cached[0]:
            return cached[1], cached[2]

    tier = "community"
    overrides: dict[str, Any] = {}

    if license_client is not None:
        try:
            validation = license_client.validate()
            tier = getattr(validation, "tier", None) or "community"
            raw_limits = getattr(validation, "limits", None) or {}
            overrides = {k: v for k, v in raw_limits.items() if k in _LIMIT_SET_FIELDS}
        except Exception as exc:
            logger.warning(
                "license_limits_resolve_failed", error=str(exc), fallback="community"
            )
            tier = "community"
            overrides = {}

    base = TIER_DEFAULTS.get(tier, TIER_DEFAULTS["community"])
    limits = replace(base, **overrides) if overrides else base

    if license_client is not None:
        try:
            _cache[license_client] = (now + _CACHE_TTL_S, tier, limits)
        except TypeError:
            # license_client doesn't support weak references (e.g. a slotted
            # class with no __weakref__ slot) -- skip caching, still correct.
            pass

    return tier, limits
