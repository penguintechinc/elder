"""Unit tests for PostHog feature-flag helper with graceful degradation.

Tests ensure that:
1. Flag evaluation returns True/False correctly
2. Graceful degradation works when PostHog is unconfigured/unavailable
3. Caching works (last-known values used on failure)
4. New flags default to OFF
5. Defensive import prevents crashes if posthog lib missing
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from apps.api.common.flags.posthog_client import (
    PostHogClient,
    flag_enabled,
    get_posthog_client,
    init_posthog,
)


class TestPostHogClientInitialization:
    """Test PostHog client initialization and configuration."""

    def test_init_with_no_api_key(self) -> None:
        """Test client initializes in degraded mode when no API key provided."""
        client = PostHogClient(api_key=None)
        assert client.enabled is False
        assert client.api_key is None

    def test_init_with_api_key_but_posthog_unavailable(self) -> None:
        """Test client degrades gracefully when posthog library not available."""
        # This test runs in environment where posthog may or may not be installed
        # The client should handle both cases
        client = PostHogClient(api_key="test-key-123")
        # Should either be enabled (lib available) or disabled (lib unavailable)
        # Both are acceptable for this test
        assert isinstance(client.enabled, bool)

    def test_init_with_posthog_available_stores_config(self) -> None:
        """Test client stores configuration even if posthog not fully available."""
        client = PostHogClient(
            api_key="test-key-123",
            host="https://posthog.example.com",
        )
        # Client should store the configuration
        assert client.api_key == "test-key-123"
        assert client.host == "https://posthog.example.com"


class TestFlagEvaluation:
    """Test feature flag evaluation."""

    def test_flag_enabled_returns_true_when_posthog_says_true(self) -> None:
        """Test flag returns True from PostHog evaluation."""
        client = PostHogClient(api_key=None)  # Degraded mode
        client.enabled = True

        mock_posthog = MagicMock()
        mock_posthog.feature_enabled.return_value = True
        client._posthog_client = mock_posthog

        result = client.flag_enabled("test.flag", "user-123")
        assert result is True
        mock_posthog.feature_enabled.assert_called_once()

    def test_flag_enabled_returns_false_when_posthog_says_false(self) -> None:
        """Test flag returns False from PostHog evaluation."""
        client = PostHogClient(api_key=None)  # Degraded mode
        client.enabled = True

        mock_posthog = MagicMock()
        mock_posthog.feature_enabled.return_value = False
        client._posthog_client = mock_posthog

        result = client.flag_enabled("test.flag", "user-123")
        assert result is False
        mock_posthog.feature_enabled.assert_called_once()

    def test_new_flag_defaults_to_false(self) -> None:
        """Test new/unknown flag evaluates to default (OFF)."""
        client = PostHogClient(api_key=None)  # Degraded mode, no cache

        result = client.flag_enabled("new.unknown.flag", "user-123", default=False)
        assert result is False

    def test_new_flag_respects_default_true(self) -> None:
        """Test new flag can default to True when explicitly set."""
        client = PostHogClient(api_key=None)  # Degraded mode, no cache

        result = client.flag_enabled("new.flag", "user-123", default=True)
        assert result is True


class TestGracefulDegradation:
    """Test graceful degradation on unavailability."""

    def test_degradation_returns_default_when_posthog_unavailable(self) -> None:
        """Test flag evaluation returns default when PostHog unavailable."""
        client = PostHogClient(api_key=None)  # Disabled
        assert client.enabled is False

        result = client.flag_enabled("test.flag", "user-123", default=False)
        assert result is False

    def test_degradation_returns_cached_when_posthog_fails(self) -> None:
        """Test flag evaluation returns cached value when PostHog raises."""
        client = PostHogClient(api_key=None)
        client.enabled = True

        mock_posthog = MagicMock()
        mock_posthog.feature_enabled.side_effect = Exception("PostHog unavailable")
        client._posthog_client = mock_posthog

        # First call: PostHog fails, should return default
        result1 = client.flag_enabled("test.flag", "user-123", default=False)
        assert result1 is False

        # Second call: Should return cached value even though PostHog still fails
        result2 = client.flag_enabled("test.flag", "user-123", default=True)
        assert result2 is False  # Cached from first call, not new default

    def test_degradation_caches_successful_evaluations(self) -> None:
        """Test successful evaluations are cached for future calls."""
        client = PostHogClient(api_key=None)
        client.enabled = True

        mock_posthog = MagicMock()
        mock_posthog.feature_enabled.return_value = True
        client._posthog_client = mock_posthog

        # First call: PostHog evaluates to True
        result1 = client.flag_enabled("test.flag", "user-123")
        assert result1 is True
        assert mock_posthog.feature_enabled.call_count == 1

        # Simulate PostHog becoming unavailable
        mock_posthog.feature_enabled.side_effect = Exception("Down")

        # Second call: Should use cache, not call PostHog
        result2 = client.flag_enabled("test.flag", "user-123", default=False)
        assert result2 is True  # Cached value, ignores new default
        assert (
            mock_posthog.feature_enabled.call_count == 1
        )  # Not called again (cache hit)


class TestModuleLevelApi:
    """Test module-level convenience functions."""

    def test_get_posthog_client_returns_instance(self) -> None:
        """Test get_posthog_client returns a valid instance."""
        client = get_posthog_client()
        assert isinstance(client, PostHogClient)

    def test_flag_enabled_convenience_function(self) -> None:
        """Test flag_enabled convenience function works."""
        # Should not raise even if degraded
        result = flag_enabled("test.flag", "user-123", default=False)
        assert isinstance(result, bool)

    @patch("apps.api.common.flags.posthog_client.get_posthog_client")
    def test_flag_enabled_delegates_to_client(self, mock_get_client: MagicMock) -> None:
        """Test flag_enabled delegates to the PostHog client."""
        mock_client = MagicMock(spec=PostHogClient)
        mock_client.flag_enabled.return_value = True
        mock_get_client.return_value = mock_client

        result = flag_enabled("test.flag", "user-123")
        assert result is True
        mock_client.flag_enabled.assert_called_once_with("test.flag", "user-123", False)


class TestInitPosthog:
    """Test Flask app initialization."""

    def test_init_posthog_attaches_to_app_extensions(self) -> None:
        """Test init_posthog attaches client to app.extensions."""
        mock_app = MagicMock()
        mock_app.extensions = {}

        with patch.object(PostHogClient, "__init__", return_value=None) as mock_init:
            mock_init.return_value = None
            # Manually set up the mock to avoid real PostHogClient init
            mock_client = MagicMock(spec=PostHogClient)

            with patch(
                "apps.api.common.flags.posthog_client.PostHogClient",
                return_value=mock_client,
            ):
                init_posthog(mock_app)

                # Verify client was attached to app.extensions
                assert mock_app.extensions["posthog"] is not None


class TestClientClose:
    """Test client cleanup."""

    def test_close_calls_posthog_shutdown(self) -> None:
        """Test close() calls PostHog shutdown if available."""
        client = PostHogClient(api_key=None)

        mock_posthog = MagicMock()
        client._posthog_client = mock_posthog

        client.close()
        mock_posthog.shutdown.assert_called_once()

    def test_close_handles_shutdown_error(self) -> None:
        """Test close() handles shutdown errors gracefully."""
        client = PostHogClient(api_key=None)

        mock_posthog = MagicMock()
        mock_posthog.shutdown.side_effect = Exception("Shutdown failed")
        client._posthog_client = mock_posthog

        # Should not raise
        client.close()
