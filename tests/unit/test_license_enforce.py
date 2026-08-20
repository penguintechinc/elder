"""Unit tests for the @enforce_limit guard (apps/api/common/licensing/enforce.py)."""

from unittest.mock import patch

import pytest
from quart import current_app, g

from apps.api.common.licensing.enforce import check_limit, enforce_limit
from apps.api.common.licensing.limits import LimitSet

_AT_LIMIT_TENANT = LimitSet(
    max_global_admins=1,
    max_tenant_admins=0,
    max_teams=1,
    max_tenants=1,
    max_objects=1000,
    max_nodes_per_type=1,
)

_UNLIMITED = LimitSet(
    max_global_admins=None,
    max_tenant_admins=None,
    max_teams=None,
    max_tenants=None,
    max_objects=None,
    max_nodes_per_type=None,
)


def _patch_resolve(tier: str, limits: LimitSet):
    return patch(
        "apps.api.common.licensing.enforce.resolve_limits",
        return_value=(tier, limits),
    )


def _patch_count(value: int):
    return patch(
        "apps.api.common.licensing.enforce._count_for_kind", return_value=value
    )


def _patch_flag(enabled: bool):
    return patch("apps.api.common.licensing.enforce.flag_enabled", return_value=enabled)


@pytest.mark.asyncio
class TestCheckLimit:
    """check_limit(): hard-block vs observe-only per the elder.license-enforcement flag."""

    async def test_flag_on_blocks_at_limit(self, app):
        async with app.app_context():
            with (
                _patch_resolve("community", _AT_LIMIT_TENANT),
                _patch_count(1),  # count == max_tenants (1) -- at the limit
                _patch_flag(True),
            ):
                result = await check_limit("tenant", None)

            assert result is not None
            response, status = result
            assert status == 402
            body = await response.get_json()
            assert body == {
                "error": "limit_reached",
                "limit": "tenant",
                "tier": "community",
                "upgrade": True,
            }

    async def test_flag_on_blocks_over_limit(self, app):
        async with app.app_context():
            with (
                _patch_resolve("community", _AT_LIMIT_TENANT),
                _patch_count(5),  # well over max_tenants (1)
                _patch_flag(True),
            ):
                result = await check_limit("tenant", None)

            assert result is not None
            _, status = result
            assert status == 402

    async def test_under_limit_passes_without_checking_flag(self, app):
        async with app.app_context():
            with (
                _patch_resolve("community", _AT_LIMIT_TENANT),
                _patch_count(0),  # under max_tenants (1)
                _patch_flag(True) as mock_flag,
            ):
                result = await check_limit("tenant", None)

            assert result is None
            mock_flag.assert_not_called()

    async def test_flag_off_observes_only(self, app):
        async with app.app_context():
            with (
                _patch_resolve("community", _AT_LIMIT_TENANT),
                _patch_count(1),
                _patch_flag(False),
            ):
                result = await check_limit("tenant", None)

            assert result is None

    async def test_unlimited_never_blocks_and_skips_counting(self, app):
        async with app.app_context():
            with (
                _patch_resolve("enterprise", _UNLIMITED),
                _patch_count(999999) as mock_count,
                _patch_flag(True) as mock_flag,
            ):
                result = await check_limit("tenant", None)

            assert result is None
            mock_count.assert_not_called()
            mock_flag.assert_not_called()

    async def test_unknown_kind_raises(self, app):
        async with app.app_context():
            with pytest.raises(ValueError):
                await check_limit("not_a_real_kind", None)

    async def test_distinct_id_falls_back_to_global_when_tenant_none(self, app):
        async with app.app_context():
            with (
                _patch_resolve("community", _AT_LIMIT_TENANT),
                _patch_count(1),
                _patch_flag(True) as mock_flag,
            ):
                await check_limit("tenant", None)

            mock_flag.assert_called_once()
            _, kwargs = mock_flag.call_args
            called_args = mock_flag.call_args.args
            distinct_id = kwargs.get("distinct_id") or (
                called_args[1] if len(called_args) > 1 else None
            )
            assert distinct_id == "global"


@pytest.mark.asyncio
class TestEnforceLimitDecorator:
    """@enforce_limit(kind): route decorator wrapping check_limit."""

    async def test_short_circuits_with_402_when_blocked(self, app):
        @enforce_limit("tenant")
        async def handler():
            return {"ok": True}, 200

        async with app.test_request_context("/"):
            g.claims = {"tenant": "7"}
            with (
                _patch_resolve("community", _AT_LIMIT_TENANT),
                _patch_count(1),
                _patch_flag(True),
            ):
                result = await handler()

            _, status = result
            assert status == 402

    async def test_calls_through_when_under_limit(self, app):
        @enforce_limit("tenant")
        async def handler():
            return {"ok": True}, 200

        async with app.test_request_context("/"):
            g.claims = {"tenant": "7"}
            with (
                _patch_resolve("community", _AT_LIMIT_TENANT),
                _patch_count(0),
                _patch_flag(True),
            ):
                result = await handler()

            assert result == ({"ok": True}, 200)

    async def test_missing_tenant_claim_passes_none(self, app):
        @enforce_limit("node")
        async def handler():
            return {"ok": True}, 200

        async with app.test_request_context("/"):
            g.claims = {}
            with (
                _patch_resolve("community", _AT_LIMIT_TENANT),
                _patch_count(0),
                _patch_flag(True),
            ):
                result = await handler()

            assert result == ({"ok": True}, 200)


@pytest.mark.asyncio
class TestCountForKindDispatch:
    """_count_for_kind() dispatches to the right Task 3 counter per kind."""

    async def test_object_kind_uses_module_redis_extension(self, app):
        from apps.api.common.licensing.enforce import _count_for_kind

        async with app.app_context():
            db = current_app.db
            with patch(
                "apps.api.common.licensing.enforce.count_objects", return_value=3
            ) as mock_count_objects:
                result = _count_for_kind("object", 7)

            assert result == 3
            mock_count_objects.assert_called_once()
            call_args = mock_count_objects.call_args.args
            assert call_args[0] is db
            assert call_args[1] == 7
