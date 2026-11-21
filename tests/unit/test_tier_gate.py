"""Unit tests for require_tier/get_tier (apps/api/common/licensing/tier_gate.py).

This gate exists solely for the admin/bulk convenience layer on top of a
statutory right (e.g. apps/api/api/v1/privacy_admin.py) -- see
tests/unit/test_privacy_dsar.py for proof the underlying statutory rights
themselves are never wrapped in it.
"""

from unittest.mock import patch

import pytest

from apps.api.common.licensing.tier_gate import get_tier, require_tier


def _patch_resolve(tier: str):
    return patch(
        "apps.api.common.licensing.tier_gate.resolve_limits",
        return_value=(tier, None),
    )


@pytest.mark.asyncio
class TestGetTier:
    async def test_normalizes_community_to_free(self, app):
        async with app.app_context():
            with _patch_resolve("community"):
                assert get_tier() == "free"

    async def test_passes_through_professional_and_enterprise(self, app):
        async with app.app_context():
            with _patch_resolve("professional"):
                assert get_tier() == "professional"
            with _patch_resolve("enterprise"):
                assert get_tier() == "enterprise"


@pytest.mark.asyncio
class TestRequireTier:
    async def test_free_tier_blocked_from_enterprise_gate(self, app):
        @require_tier("enterprise")
        async def handler():
            return {"ok": True}, 200

        async with app.app_context():
            with _patch_resolve("community"):
                response, status = await handler()

        assert status == 403
        body = await response.get_json()
        assert body == {
            "error": "tier_required",
            "required_tier": "enterprise",
            "tier": "free",
            "upgrade": True,
        }

    async def test_professional_still_blocked_from_enterprise_gate(self, app):
        @require_tier("enterprise")
        async def handler():
            return {"ok": True}, 200

        async with app.app_context():
            with _patch_resolve("professional"):
                _, status = await handler()

        assert status == 403

    async def test_enterprise_passes_through(self, app):
        @require_tier("enterprise")
        async def handler():
            return {"ok": True}, 200

        async with app.app_context():
            with _patch_resolve("enterprise"):
                result, status = await handler()

        assert status == 200
        assert result == {"ok": True}

    async def test_professional_gate_allows_professional_and_enterprise(self, app):
        @require_tier("professional")
        async def handler():
            return {"ok": True}, 200

        async with app.app_context():
            with _patch_resolve("professional"):
                _, status = await handler()
            assert status == 200

            with _patch_resolve("enterprise"):
                _, status = await handler()
            assert status == 200

            with _patch_resolve("community"):
                _, status = await handler()
            assert status == 403
