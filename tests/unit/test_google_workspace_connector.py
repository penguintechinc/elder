"""
Unit tests for Google Workspace connector identity sync.

Tests cover:
- User identity sync with Google ID as auth_provider_id (not email)
- Username is email
- full_name set only when different from email
- suspended flag → is_active=False
- Pagination across multiple pages
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

import tests.unit.conftest_worker_stubs  # noqa: F401 — stubs heavy optional deps before any connector import
from apps.worker.connectors.google_workspace_connector import GoogleWorkspaceConnector
from apps.worker.utils.elder_client import Identity

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_google_user(
    user_id: str = "id123abc",
    email: str = "jane@example.com",
    full_name: str = "Jane Smith",
    suspended: bool = False,
) -> dict:
    return {
        "id": user_id,
        "primaryEmail": email,
        "name": {"fullName": full_name},
        "suspended": suspended,
    }


# ---------------------------------------------------------------------------
# TestGoogleWorkspaceUserIdentitySync — covers _sync_users identity section
# ---------------------------------------------------------------------------


class TestGoogleWorkspaceUserIdentitySync:
    """Tests for Google Workspace user identity sync."""

    @pytest.fixture
    def connector(self):
        c = GoogleWorkspaceConnector()
        c.elder_client = AsyncMock()
        c.elder_client.get_or_create_identity = AsyncMock(return_value={"id": 1})
        c.elder_client.list_entities = AsyncMock(return_value={"items": []})
        c.elder_client.create_entity = AsyncMock(return_value={"id": 1})
        c.admin_service = MagicMock()
        c.organization_cache = {"root:Google Workspace": 1}
        c.orgunit_cache = {"/": 1}
        return c

    @pytest.mark.asyncio
    async def test_user_identity_synced_with_google_id(self, connector):
        """auth_provider='google', auth_provider_id=user['id'] (the stable Google ID)."""
        user = _make_google_user(user_id="id123abc")
        users_result = {"users": [user], "nextPageToken": None}

        mock_users = MagicMock()
        mock_users.list.return_value.execute.return_value = users_result
        connector.admin_service.users.return_value = mock_users

        from apps.worker.connectors.base import SyncResult

        result = SyncResult(connector_name="google_workspace")
        await connector._sync_users(1)

        connector.elder_client.get_or_create_identity.assert_awaited_once()
        call_args = connector.elder_client.get_or_create_identity.call_args
        identity: Identity = call_args[0][0]

        assert identity.auth_provider == "google"
        assert identity.auth_provider_id == "id123abc"

    @pytest.mark.asyncio
    async def test_username_is_email(self, connector):
        """username == user['primaryEmail']."""
        user = _make_google_user(email="jane@example.com")
        users_result = {"users": [user], "nextPageToken": None}

        mock_users = MagicMock()
        mock_users.list.return_value.execute.return_value = users_result
        connector.admin_service.users.return_value = mock_users

        from apps.worker.connectors.base import SyncResult

        result = SyncResult(connector_name="google_workspace")
        await connector._sync_users(1)

        call_args = connector.elder_client.get_or_create_identity.call_args
        identity: Identity = call_args[0][0]
        assert identity.username == "jane@example.com"

    @pytest.mark.asyncio
    async def test_full_name_set_when_different_from_email(self, connector):
        """fullName='Jane Smith', email='jane@example.com' → full_name='Jane Smith'."""
        user = _make_google_user(email="jane@example.com", full_name="Jane Smith")
        users_result = {"users": [user], "nextPageToken": None}

        mock_users = MagicMock()
        mock_users.list.return_value.execute.return_value = users_result
        connector.admin_service.users.return_value = mock_users

        from apps.worker.connectors.base import SyncResult

        result = SyncResult(connector_name="google_workspace")
        await connector._sync_users(1)

        call_args = connector.elder_client.get_or_create_identity.call_args
        identity: Identity = call_args[0][0]
        assert identity.full_name == "Jane Smith"

    @pytest.mark.asyncio
    async def test_full_name_none_when_equals_email(self, connector):
        """fullName == primaryEmail → full_name=None."""
        email = "jane@example.com"
        user = _make_google_user(email=email, full_name=email)
        users_result = {"users": [user], "nextPageToken": None}

        mock_users = MagicMock()
        mock_users.list.return_value.execute.return_value = users_result
        connector.admin_service.users.return_value = mock_users

        from apps.worker.connectors.base import SyncResult

        result = SyncResult(connector_name="google_workspace")
        await connector._sync_users(1)

        call_args = connector.elder_client.get_or_create_identity.call_args
        identity: Identity = call_args[0][0]
        assert identity.full_name is None

    @pytest.mark.asyncio
    async def test_suspended_user_is_inactive(self, connector):
        """suspended=True → is_active=False."""
        user = _make_google_user(suspended=True)
        users_result = {"users": [user], "nextPageToken": None}

        mock_users = MagicMock()
        mock_users.list.return_value.execute.return_value = users_result
        connector.admin_service.users.return_value = mock_users

        from apps.worker.connectors.base import SyncResult

        result = SyncResult(connector_name="google_workspace")
        await connector._sync_users(1)

        call_args = connector.elder_client.get_or_create_identity.call_args
        identity: Identity = call_args[0][0]
        assert identity.is_active is False

    @pytest.mark.asyncio
    async def test_active_user_is_active(self, connector):
        """suspended=False → is_active=True."""
        user = _make_google_user(suspended=False)
        users_result = {"users": [user], "nextPageToken": None}

        mock_users = MagicMock()
        mock_users.list.return_value.execute.return_value = users_result
        connector.admin_service.users.return_value = mock_users

        from apps.worker.connectors.base import SyncResult

        result = SyncResult(connector_name="google_workspace")
        await connector._sync_users(1)

        call_args = connector.elder_client.get_or_create_identity.call_args
        identity: Identity = call_args[0][0]
        assert identity.is_active is True

    @pytest.mark.asyncio
    async def test_multiple_users_all_get_identities(self, connector):
        """3 users → called 3 times."""
        users = [
            _make_google_user(user_id="id1", email="user1@example.com"),
            _make_google_user(user_id="id2", email="user2@example.com"),
            _make_google_user(user_id="id3", email="user3@example.com"),
        ]
        users_result = {"users": users, "nextPageToken": None}

        mock_users = MagicMock()
        mock_users.list.return_value.execute.return_value = users_result
        connector.admin_service.users.return_value = mock_users

        from apps.worker.connectors.base import SyncResult

        result = SyncResult(connector_name="google_workspace")
        await connector._sync_users(1)

        assert connector.elder_client.get_or_create_identity.await_count == 3

    @pytest.mark.asyncio
    async def test_pagination_syncs_all_users(self, connector):
        """first API call returns page1 + nextPageToken; second returns page2 + no nextPageToken → all users synced."""
        user1 = _make_google_user(user_id="id1", email="user1@example.com")
        user2 = _make_google_user(user_id="id2", email="user2@example.com")
        user3 = _make_google_user(user_id="id3", email="user3@example.com")

        page1_result = {"users": [user1, user2], "nextPageToken": "page2_token"}
        page2_result = {"users": [user3], "nextPageToken": None}

        mock_users = MagicMock()
        # First call returns page1, second call returns page2
        mock_users.list.return_value.execute.side_effect = [
            page1_result,
            page2_result,
        ]
        connector.admin_service.users.return_value = mock_users

        from apps.worker.connectors.base import SyncResult

        result = SyncResult(connector_name="google_workspace")
        await connector._sync_users(1)

        # All 3 users across both pages should be synced
        assert connector.elder_client.get_or_create_identity.await_count == 3

        # Verify all user IDs were processed
        call_ids = [
            call[0][0].auth_provider_id
            for call in connector.elder_client.get_or_create_identity.call_args_list
        ]
        assert set(call_ids) == {"id1", "id2", "id3"}


# ---------------------------------------------------------------------------
# TestGoogleWorkspaceEnumCoverage
# ---------------------------------------------------------------------------


class TestGoogleWorkspaceEnumCoverage:
    """Tests for enum value correctness."""

    def test_authprovider_has_google_value(self):
        """AuthProvider.GOOGLE.value == 'google'."""
        from apps.api.models.identity import AuthProvider

        assert hasattr(AuthProvider, "GOOGLE")
        assert AuthProvider.GOOGLE.value == "google"

    def test_authprovider_has_gcp_value(self):
        """AuthProvider.GCP.value == 'gcp'."""
        from apps.api.models.identity import AuthProvider

        assert hasattr(AuthProvider, "GCP")
        assert AuthProvider.GCP.value == "gcp"
