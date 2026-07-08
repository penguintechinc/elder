"""PostHog feature-flag client with graceful degradation.

Provides a lightweight wrapper around PostHog for feature-flag evaluation with:
- Defensive import (never crashes if posthog not installed)
- Local evaluation where supported
- Graceful degradation to cached/default values on unavailability
- In-process flag cache
"""

from __future__ import annotations

from typing import Any, Optional

import structlog

try:
    import posthog
    POSTHOG_AVAILABLE = True
except ImportError:
    POSTHOG_AVAILABLE = False

log = structlog.get_logger(__name__)


class PostHogClient:
    """PostHog feature-flag client with graceful degradation."""

    def __init__(
        self, api_key: Optional[str], host: str = "https://license.penguintech.io"
    ) -> None:
        """Initialize PostHog client.

        Args:
            api_key: PostHog API key (may be None if unconfigured)
            host: PostHog host URL (default: license.penguintech.io)
        """
        self.api_key = api_key
        self.host = host
        self.enabled = False
        self._flag_cache: dict[str, bool] = {}
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

    def flag_enabled(
        self, key: str, distinct_id: str, default: bool = False
    ) -> bool:
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
        # Check cache first
        if key in self._flag_cache:
            cached = self._flag_cache[key]
            log.debug(
                "flag_evaluated_from_cache",
                key=key,
                distinct_id=distinct_id,
                value=cached,
            )
            return cached

        # Try to evaluate from PostHog
        if self.enabled and self._posthog_client:
            try:
                result = self._posthog_client.feature_enabled(
                    key, distinct_id, groups={}, person_properties={}, group_properties={}
                )
                # Cache the result
                self._flag_cache[key] = result
                log.debug(
                    "flag_evaluated_from_posthog",
                    key=key,
                    distinct_id=distinct_id,
                    value=result,
                )
                return result
            except Exception as e:
                log.warning(
                    "posthog_flag_evaluation_failed",
                    key=key,
                    distinct_id=distinct_id,
                    error=str(e),
                    fallback="default",
                )
                # Cache the default for this evaluation
                self._flag_cache[key] = default
                return default

        # PostHog unavailable: return default (or cached if available)
        self._flag_cache[key] = default
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
_client: Optional[PostHogClient] = None


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
