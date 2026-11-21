"""Task 9: end-to-end observe-only vs hard-block across enforcement kinds.

Exercises the resolver + counters + guard together (not per-endpoint — those
have their own tests) to prove the two operating modes:
- flag OFF (default) -> observe-only: over-limit never blocks (returns None).
- flag ON            -> hard block:   over-limit returns a (payload, 402) tuple.
And that Enterprise (unlimited) never blocks regardless of the flag.
"""

from unittest.mock import MagicMock, patch

import pytest
from quart import current_app

from apps.api.common.licensing.enforce import check_limit


def _client(tier: str) -> MagicMock:
    """A stand-in penguin_licensing client fixed at `tier` with no overrides."""
    client = MagicMock()
    validation = MagicMock()
    validation.tier = tier
    validation.limits = {}
    client.validate.return_value = validation
    return client


def _flag(enabled: bool):
    return patch("apps.api.common.licensing.enforce.flag_enabled", return_value=enabled)


class TestEnforcementIntegration:
    """check_limit composes tier resolution + counting + the observe/block gate."""

    @pytest.mark.asyncio
    async def test_free_over_limit_blocks_when_flag_on(self, app):
        """Free tier: at least one tenant exists (>= max_tenants=1) -> 402 when ON."""
        async with app.app_context():
            app.extensions["license_client"] = _client("community")
            with _flag(True):
                blocked = await check_limit("tenant", None)
        assert blocked is not None, "flag ON + over-limit must hard-block"
        payload, status = blocked
        assert status == 402

    @pytest.mark.asyncio
    async def test_free_over_limit_observes_when_flag_off(self, app):
        """Same over-limit condition, flag OFF -> observe-only, never blocks."""
        async with app.app_context():
            app.extensions["license_client"] = _client("community")
            with _flag(False):
                blocked = await check_limit("tenant", None)
        assert blocked is None, "flag OFF must observe-only (allow)"

    @pytest.mark.asyncio
    async def test_enterprise_never_blocks_even_with_flag_on(self, app):
        """Enterprise limits are unlimited (None) -> never blocks even flag ON."""
        async with app.app_context():
            app.extensions["license_client"] = _client("enterprise")
            with _flag(True):
                assert await check_limit("tenant", None) is None
