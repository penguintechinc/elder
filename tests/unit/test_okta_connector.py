"""
Unit tests for Okta connector identity sync.

Tests cover:
- User sync to identities with correct auth_provider and identity_type
- Group sync (OKTA_GROUP type only)
- Profile URL writeback based on settings
- Full name construction from firstName/lastName
- Error isolation (single user error doesn't break others)
"""

from unittest.mock import AsyncMock, patch

import pytest

import tests.unit.conftest_worker_stubs  # noqa: F401 — stubs heavy optional deps before any connector import
from apps.worker.connectors.okta_connector import OktaConnector
from apps.worker.utils.elder_client import Identity

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_okta_user(
    user_id: str = "user-123",
    login: str = "jane.doe@example.com",
    email: str = "jane.doe@example.com",
    first_name: str = "Jane",
    last_name: str = "Doe",
) -> dict:
    return {
        "id": user_id,
        "status": "ACTIVE",
        "profile": {
            "login": login,
            "email": email,
            "firstName": first_name,
            "lastName": last_name,
        },
    }


def _make_okta_group(
    group_id: str = "group-123",
    group_name: str = "Engineering",
    group_type: str = "OKTA_GROUP",
) -> dict:
    return {
        "id": group_id,
        "type": group_type,
        "profile": {
            "name": group_name,
            "description": f"The {group_name} group",
        },
    }


# ---------------------------------------------------------------------------
# TestOktaUserSync — covers _sync_users
# ---------------------------------------------------------------------------


class TestOktaUserSync:
    """Tests for Okta user identity sync."""

    @pytest.fixture
    def connector(self):
        c = OktaConnector()
        c.elder_client = AsyncMock()
        c.elder_client.get_or_create_identity = AsyncMock(return_value={"id": 1})
        c.base_url = "https://test.okta.com"
        c._http_client = AsyncMock()
        return c

    @pytest.mark.asyncio
    async def test_user_synced_as_human_identity(self, connector):
        """Single user produces get_or_create_identity call with identity_type='human'."""
        user = _make_okta_user()
        connector._paginate = AsyncMock(return_value=[user])

        from apps.worker.connectors.base import SyncResult

        result = SyncResult(connector_name="okta")
        await connector._sync_users(result)

        connector.elder_client.get_or_create_identity.assert_awaited_once()
        call_args = connector.elder_client.get_or_create_identity.call_args
        identity: Identity = call_args[0][0]

        assert identity.identity_type == "human"
        assert identity.auth_provider == "okta"
        assert identity.auth_provider_id == "user-123"
        assert identity.username == "jane.doe@example.com"
        assert identity.email == "jane.doe@example.com"

    @pytest.mark.asyncio
    async def test_user_full_name_from_first_last(self, connector):
        """firstName='Jane', lastName='Doe' → full_name='Jane Doe'."""
        user = _make_okta_user(first_name="Jane", last_name="Doe")
        connector._paginate = AsyncMock(return_value=[user])

        from apps.worker.connectors.base import SyncResult

        result = SyncResult(connector_name="okta")
        await connector._sync_users(result)

        call_args = connector.elder_client.get_or_create_identity.call_args
        identity: Identity = call_args[0][0]
        assert identity.full_name == "Jane Doe"

    @pytest.mark.asyncio
    async def test_user_full_name_empty_when_both_missing(self, connector):
        """Missing firstName/lastName → full_name=None."""
        user = _make_okta_user(first_name="", last_name="")
        connector._paginate = AsyncMock(return_value=[user])

        from apps.worker.connectors.base import SyncResult

        result = SyncResult(connector_name="okta")
        await connector._sync_users(result)

        call_args = connector.elder_client.get_or_create_identity.call_args
        identity: Identity = call_args[0][0]
        assert identity.full_name is None

    @pytest.mark.asyncio
    async def test_multiple_users_all_get_identities(self, connector):
        """3 users → get_or_create_identity called 3 times."""
        users = [
            _make_okta_user(user_id="u1", login="user1@example.com"),
            _make_okta_user(user_id="u2", login="user2@example.com"),
            _make_okta_user(user_id="u3", login="user3@example.com"),
        ]
        connector._paginate = AsyncMock(return_value=users)

        from apps.worker.connectors.base import SyncResult

        result = SyncResult(connector_name="okta")
        await connector._sync_users(result)

        assert connector.elder_client.get_or_create_identity.await_count == 3
        assert result.entities_created == 3

    @pytest.mark.asyncio
    async def test_user_sync_error_is_isolated(self, connector):
        """Second of two users throws; first succeeds, error in result.errors."""
        users = [
            _make_okta_user(user_id="u1", login="user1@example.com"),
            _make_okta_user(user_id="u2", login="user2@example.com"),
        ]
        connector._paginate = AsyncMock(return_value=users)

        # First call succeeds, second throws
        connector.elder_client.get_or_create_identity = AsyncMock(
            side_effect=[{"id": 1}, ValueError("API error")]
        )

        from apps.worker.connectors.base import SyncResult

        result = SyncResult(connector_name="okta")
        await connector._sync_users(result)

        # First user synced, second errored
        assert result.entities_created == 1
        assert len(result.errors) == 1
        assert "u2" in result.errors[0]

    @pytest.mark.asyncio
    async def test_profile_url_writeback_when_village_id_present(self, connector):
        """response has village_id and settings.okta_sync_profile_url=True → update_user_profile_url called."""
        user = _make_okta_user()
        connector._paginate = AsyncMock(return_value=[user])
        connector.elder_client.get_or_create_identity = AsyncMock(
            return_value={"id": 1, "village_id": "abc123"}
        )
        connector.update_user_profile_url = AsyncMock(return_value=True)

        with patch("apps.worker.connectors.okta_connector.settings") as mock_settings:
            mock_settings.okta_sync_profile_url = True

            from apps.worker.connectors.base import SyncResult

            result = SyncResult(connector_name="okta")
            await connector._sync_users(result)

        connector.update_user_profile_url.assert_awaited_once_with("user-123", "abc123")

    @pytest.mark.asyncio
    async def test_no_profile_url_writeback_when_disabled(self, connector):
        """settings.okta_sync_profile_url=False → update_user_profile_url NOT called."""
        user = _make_okta_user()
        connector._paginate = AsyncMock(return_value=[user])
        connector.elder_client.get_or_create_identity = AsyncMock(
            return_value={"id": 1, "village_id": "abc123"}
        )
        connector.update_user_profile_url = AsyncMock(return_value=False)

        with patch("apps.worker.connectors.okta_connector.settings") as mock_settings:
            mock_settings.okta_sync_profile_url = False

            from apps.worker.connectors.base import SyncResult

            result = SyncResult(connector_name="okta")
            await connector._sync_users(result)

        connector.update_user_profile_url.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_no_profile_url_writeback_when_no_village_id(self, connector):
        """response missing village_id → update_user_profile_url NOT called."""
        user = _make_okta_user()
        connector._paginate = AsyncMock(return_value=[user])
        connector.elder_client.get_or_create_identity = AsyncMock(
            return_value={"id": 1}  # no village_id
        )
        connector.update_user_profile_url = AsyncMock(return_value=False)

        with patch("apps.worker.connectors.okta_connector.settings") as mock_settings:
            mock_settings.okta_sync_profile_url = True

            from apps.worker.connectors.base import SyncResult

            result = SyncResult(connector_name="okta")
            await connector._sync_users(result)

        connector.update_user_profile_url.assert_not_awaited()


# ---------------------------------------------------------------------------
# TestOktaGroupSync — covers _sync_groups
# ---------------------------------------------------------------------------


class TestOktaGroupSync:
    """Tests for Okta group identity sync."""

    @pytest.fixture
    def connector(self):
        c = OktaConnector()
        c.elder_client = AsyncMock()
        c.elder_client.get_or_create_identity = AsyncMock(return_value={"id": 1})
        c.base_url = "https://test.okta.com"
        c._http_client = AsyncMock()
        return c

    @pytest.mark.asyncio
    async def test_okta_group_synced_as_service_account(self, connector):
        """OKTA_GROUP type → identity_type='service_account'."""
        group = _make_okta_group(group_type="OKTA_GROUP")
        connector._paginate = AsyncMock(return_value=[group])

        from apps.worker.connectors.base import SyncResult

        result = SyncResult(connector_name="okta")
        await connector._sync_groups(result)

        connector.elder_client.get_or_create_identity.assert_awaited_once()
        call_args = connector.elder_client.get_or_create_identity.call_args
        identity: Identity = call_args[0][0]

        assert identity.identity_type == "service_account"
        assert identity.auth_provider == "okta"
        assert identity.auth_provider_id == "group-123"
        assert identity.username == "Engineering"

    @pytest.mark.asyncio
    async def test_app_group_skipped(self, connector):
        """group with type='APP_GROUP' → get_or_create_identity NOT called."""
        group = _make_okta_group(group_type="APP_GROUP")
        connector._paginate = AsyncMock(return_value=[group])

        from apps.worker.connectors.base import SyncResult

        result = SyncResult(connector_name="okta")
        await connector._sync_groups(result)

        connector.elder_client.get_or_create_identity.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_multiple_groups_only_okta_type_synced(self, connector):
        """Mix of OKTA_GROUP and APP_GROUP → only OKTA_GROUPs produce identity calls."""
        groups = [
            _make_okta_group(group_id="g1", group_name="eng", group_type="OKTA_GROUP"),
            _make_okta_group(group_id="g2", group_name="slack", group_type="APP_GROUP"),
            _make_okta_group(group_id="g3", group_name="ops", group_type="OKTA_GROUP"),
        ]
        connector._paginate = AsyncMock(return_value=groups)

        from apps.worker.connectors.base import SyncResult

        result = SyncResult(connector_name="okta")
        await connector._sync_groups(result)

        # Only 2 OKTA_GROUP types
        assert connector.elder_client.get_or_create_identity.await_count == 2
        assert result.entities_created == 2


# ---------------------------------------------------------------------------
# TestOktaEnumCoverage
# ---------------------------------------------------------------------------


class TestOktaEnumCoverage:
    """Tests for enum value correctness."""

    def test_authprovider_has_okta_value(self):
        """AuthProvider.OKTA.value == 'okta'."""
        from apps.api.models.identity import AuthProvider

        assert hasattr(AuthProvider, "OKTA")
        assert AuthProvider.OKTA.value == "okta"
