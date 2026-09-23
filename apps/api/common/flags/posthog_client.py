"""PostHog feature-flag client with graceful degradation.

Provides a lightweight wrapper around PostHog for feature-flag evaluation with:
- Defensive import (never crashes if posthog not installed)
- Local evaluation where supported
- Graceful degradation to cached/default values on unavailability
- In-process flag cache
"""

from __future__ import annotations

import time
from typing import Any, Optional

import structlog

try:
    import posthog

    POSTHOG_AVAILABLE = True
except ImportError:
    POSTHOG_AVAILABLE = False

log = structlog.get_logger(__name__)

# Cache TTL for evaluated flags. Without a TTL, a single evaluation freezes
# that (flag, tenant) result forever -- killing both the kill-switch (an
# operator flips a flag off in PostHog but every already-cached process
# keeps serving "on") and per-tenant targeting (once any tenant hits a flag,
# every other tenant used to inherit that first tenant's cached result,
# since the cache used to be keyed on flag key alone).
_FLAG_CACHE_TTL_S = 300


class PostHogClient:
    """PostHog feature-flag client with graceful degradation."""

    def __init__(
        self, api_key: str | None, host: str = "https://license.penguintech.io"
    ) -> None:
        """Initialize PostHog client.

        Args:
            api_key: PostHog API key (may be None if unconfigured)
            host: PostHog host URL (default: license.penguintech.io)
        """
        self.api_key = api_key
        self.host = host
        self.enabled = False
        # Keyed by (flag_key, distinct_id) -- never by flag_key alone, so one
        # tenant's evaluation can't leak onto another's. Value is
        # (expires_at_monotonic, result).
        self._flag_cache: dict[tuple[str, str], tuple[float, bool]] = {}
        self._posthog_client: Any = None

        # Initialize PostHog only if available and configured
        if POSTHOG_AVAILABLE and api_key:
            try:
                posthog.api_key = api_key
                posthog.host = host
                self._posthog_client = posthog
                self.enabled = True
                log.info(
                    "posthog_client_initialized",
                    host=host,
                    api_key_masked=f"{api_key[:8]}...{api_key[-4:]}",
                )
            except Exception as e:
                log.warning(
                    "posthog_client_init_failed",
                    error=str(e),
                    fallback="cache_or_default",
                )
                self.enabled = False
        elif not POSTHOG_AVAILABLE:
            log.debug("posthog_library_not_installed", fallback="cache_or_default")
        else:
            log.debug("posthog_not_configured", fallback="cache_or_default")

    def flag_enabled(self, key: str, distinct_id: str, default: bool = False) -> bool:
        """Evaluate a feature flag with graceful degradation.

        Attempts to evaluate the flag via PostHog. On any error or unavailability,
        returns the last-known cached value; if not cached, returns the default.

        Args:
            key: Feature flag key (e.g., "elder.ai-semantic-search")
            distinct_id: Distinct identifier for the user/context
            default: Default value if unavailable (default: False)

        Returns:
            bool: Flag evaluation result (True/False)
        """
        cache_key = (key, distinct_id)
        now = time.monotonic()
        cached = self._flag_cache.get(cache_key)

        # Fresh cache hit -- serve without contacting PostHog.
        if cached is not None and now < cached[0]:
            log.debug(
                "flag_evaluated_from_cache",
                key=key,
                distinct_id=distinct_id,
                value=cached[1],
            )
            return cached[1]

        # Try to evaluate from PostHog (cache expired or never populated)
        if self.enabled and self._posthog_client:
            try:
                result = self._posthog_client.feature_enabled(
                    key,
                    distinct_id,
                    groups={},
                    person_properties={},
                    group_properties={},
                )
                self._flag_cache[cache_key] = (now + _FLAG_CACHE_TTL_S, result)
                log.debug(
                    "flag_evaluated_from_posthog",
                    key=key,
                    distinct_id=distinct_id,
                    value=result,
                )
                return result
            except Exception as e:
                # PostHog outage: serve the last-known value for this exact
                # (flag, tenant) pair rather than snapping to `default` --
                # never crash, never flip a targeted/kill-switched flag just
                # because the network blipped. Only fall to `default` if
                # we've genuinely never evaluated this pair before.
                if cached is not None:
                    self._flag_cache[cache_key] = (now + _FLAG_CACHE_TTL_S, cached[1])
                    log.warning(
                        "posthog_flag_evaluation_failed_using_stale_cache",
                        key=key,
                        distinct_id=distinct_id,
                        error=str(e),
                        value=cached[1],
                    )
                    return cached[1]
                log.warning(
                    "posthog_flag_evaluation_failed",
                    key=key,
                    distinct_id=distinct_id,
                    error=str(e),
                    fallback="default",
                )
                self._flag_cache[cache_key] = (now + _FLAG_CACHE_TTL_S, default)
                return default

        # PostHog unavailable/unconfigured: serve stale cache if we've ever
        # evaluated this pair, else the caller-supplied default.
        if cached is not None:
            return cached[1]
        self._flag_cache[cache_key] = (now + _FLAG_CACHE_TTL_S, default)
        log.debug(
            "flag_evaluated_from_default",
            key=key,
            distinct_id=distinct_id,
            value=default,
        )
        return default

    def close(self) -> None:
        """Close the PostHog client."""
        if self._posthog_client:
            try:
                self._posthog_client.shutdown()
            except Exception as e:
                log.warning("posthog_shutdown_failed", error=str(e))


# Module-level client instance (initialized later by init_posthog)
_client: PostHogClient | None = None


def init_posthog(app: Any) -> None:
    """Initialize PostHog client and attach to Flask app.

    Args:
        app: Flask/Quart application instance
    """
    global _client

    from apps.api.config import get_config

    cfg = get_config()
    api_key = cfg.POSTHOG_KEY
    host = cfg.POSTHOG_HOST

    _client = PostHogClient(api_key=api_key, host=host)
    app.extensions["posthog"] = _client


def get_posthog_client() -> PostHogClient:
    """Get the PostHog client instance.

    Returns a degraded instance if not yet initialized.
    """
    global _client
    if _client is None:
        _client = PostHogClient(api_key=None, host="https://license.penguintech.io")
    return _client


def flag_enabled(key: str, distinct_id: str, default: bool = False) -> bool:
    """Convenience function to evaluate a feature flag.

    Args:
        key: Feature flag key
        distinct_id: Distinct identifier
        default: Default value if unavailable

    Returns:
        bool: Flag evaluation result
    """
    client = get_posthog_client()
    return client.flag_enabled(key, distinct_id, default)
