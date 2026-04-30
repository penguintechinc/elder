"""
Unit tests for Authentik connector identity sync.

Tests cover:
- User sync distinguishing human vs service_account by is_service_account flag
- auth_provider_id converted to string from pk
- is_active propagated correctly
- Group sync as service_account type
- Error isolation in user sync
"""

from unittest.mock import AsyncMock

import pytest

import tests.unit.conftest_worker_stubs  # noqa: F401 — stubs heavy optional deps before any connector import
from apps.worker.connectors.authentik_connector import AuthentikConnector
from apps.worker.utils.elder_client import Identity

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_authentik_user(
    pk: int = 42,
    username: str = "jdoe",
    email: str = "jdoe@example.com",
    name: str = "John Doe",
    is_service_account: bool = False,
    is_active: bool = True,
) -> dict:
    return {
        "pk": pk,
        "username": username,
        "email": email,
        "name": name,
        "is_service_account": is_service_account,
        "is_active": is_active,
    }


def _make_authentik_group(
    pk: int = 99,
    name: str = "Engineering",
) -> dict:
    return {
        "pk": pk,
        "name": name,
    }


# ---------------------------------------------------------------------------
# TestAuthentikUserSync — covers _sync_users
# ---------------------------------------------------------------------------


class TestAuthentikUserSync:
    """Tests for Authentik user identity sync."""

    @pytest.fixture
    def connector(self):
        c = AuthentikConnector()
        c.elder_client = AsyncMock()
        c.elder_client.get_or_create_identity = AsyncMock(return_value={"id": 1})
        c.base_url = "https://test.authentik.io/api/v3"
        c._http_client = AsyncMock()
        return c

    @pytest.mark.asyncio
    async def test_regular_user_synced_as_human(self, connector):
        """is_service_account=False → identity_type='human'."""
        user = _make_authentik_user(is_service_account=False)
        connector._paginate = AsyncMock(return_value=[user])

        from apps.worker.connectors.base import SyncResult

        result = SyncResult(connector_name="authentik")
        await connector._sync_users(result)

        call_args = connector.elder_client.get_or_create_identity.call_args
        identity: Identity = call_args[0][0]
        assert identity.identity_type == "human"

    @pytest.mark.asyncio
    async def test_service_account_user_synced_as_service_account(self, connector):
        """is_service_account=True → identity_type='service_account'."""
        user = _make_authentik_user(is_service_account=True)
        connector._paginate = AsyncMock(return_value=[user])

        from apps.worker.connectors.base import SyncResult

        result = SyncResult(connector_name="authentik")
        await connector._sync_users(result)

        call_args = connector.elder_client.get_or_create_identity.call_args
        identity: Identity = call_args[0][0]
        assert identity.identity_type == "service_account"

    @pytest.mark.asyncio
    async def test_auth_provider_id_is_str_of_pk(self, connector):
        """user['pk']=42 → auth_provider_id='42' (string, not int)."""
        user = _make_authentik_user(pk=42)
        connector._paginate = AsyncMock(return_value=[user])

        from apps.worker.connectors.base import SyncResult

        result = SyncResult(connector_name="authentik")
        await connector._sync_users(result)

        call_args = connector.elder_client.get_or_create_identity.call_args
        identity: Identity = call_args[0][0]
        assert identity.auth_provider_id == "42"
        assert isinstance(identity.auth_provider_id, str)

    @pytest.mark.asyncio
    async def test_is_active_propagated(self, connector):
        """is_active=False in user → identity.is_active=False."""
        user = _make_authentik_user(is_active=False)
        connector._paginate = AsyncMock(return_value=[user])

        from apps.worker.connectors.base import SyncResult

        result = SyncResult(connector_name="authentik")
        await connector._sync_users(result)

        call_args = connector.elder_client.get_or_create_identity.call_args
        identity: Identity = call_args[0][0]
        assert identity.is_active is False

    @pytest.mark.asyncio
    async def test_user_full_name_from_name_field(self, connector):
        """user['name']='John Smith' → full_name='John Smith'."""
        user = _make_authentik_user(name="John Smith")
        connector._paginate = AsyncMock(return_value=[user])

        from apps.worker.connectors.base import SyncResult

        result = SyncResult(connector_name="authentik")
        await connector._sync_users(result)

        call_args = connector.elder_client.get_or_create_identity.call_args
        identity: Identity = call_args[0][0]
        assert identity.full_name == "John Smith"

    @pytest.mark.asyncio
    async def test_user_full_name_none_when_empty_name(self, connector):
        """user['name']='' → full_name=None."""
        user = _make_authentik_user(name="")
        connector._paginate = AsyncMock(return_value=[user])

        from apps.worker.connectors.base import SyncResult

        result = SyncResult(connector_name="authentik")
        await connector._sync_users(result)

        call_args = connector.elder_client.get_or_create_identity.call_args
        identity: Identity = call_args[0][0]
        assert identity.full_name is None

    @pytest.mark.asyncio
    async def test_multiple_users_all_get_identities(self, connector):
        """3 users → get_or_create_identity called 3 times."""
        users = [
            _make_authentik_user(pk=1, username="user1"),
            _make_authentik_user(pk=2, username="user2"),
            _make_authentik_user(pk=3, username="user3"),
        ]
        connector._paginate = AsyncMock(return_value=users)

        from apps.worker.connectors.base import SyncResult

        result = SyncResult(connector_name="authentik")
        await connector._sync_users(result)

        assert connector.elder_client.get_or_create_identity.await_count == 3
        assert result.entities_created == 3

    @pytest.mark.asyncio
    async def test_user_sync_error_is_isolated(self, connector):
        """Error on second user doesn't break first; error recorded in result.errors."""
        users = [
            _make_authentik_user(pk=1, username="user1"),
            _make_authentik_user(pk=2, username="user2"),
        ]
        connector._paginate = AsyncMock(return_value=users)

        # First call succeeds, second throws
        connector.elder_client.get_or_create_identity = AsyncMock(
            side_effect=[{"id": 1}, ValueError("API error")]
        )

        from apps.worker.connectors.base import SyncResult

        result = SyncResult(connector_name="authentik")
        await connector._sync_users(result)

        assert result.entities_created == 1
        assert len(result.errors) == 1
        assert "2" in result.errors[0]  # pk in error message


# ---------------------------------------------------------------------------
# TestAuthentikGroupSync — covers _sync_groups
# ---------------------------------------------------------------------------


class TestAuthentikGroupSync:
    """Tests for Authentik group identity sync."""

    @pytest.fixture
    def connector(self):
        c = AuthentikConnector()
        c.elder_client = AsyncMock()
        c.elder_client.get_or_create_identity = AsyncMock(return_value={"id": 1})
        c.base_url = "https://test.authentik.io/api/v3"
        c._http_client = AsyncMock()
        return c

    @pytest.mark.asyncio
    async def test_group_synced_as_service_account(self, connector):
        """group → identity_type='service_account'."""
        group = _make_authentik_group()
        connector._paginate = AsyncMock(return_value=[group])

        from apps.worker.connectors.base import SyncResult

        result = SyncResult(connector_name="authentik")
        await connector._sync_groups(result)

        call_args = connector.elder_client.get_or_create_identity.call_args
        identity: Identity = call_args[0][0]

        assert identity.identity_type == "service_account"
        assert identity.auth_provider == "authentik"
        assert identity.username == "Engineering"
        assert identity.auth_provider_id == "99"

    @pytest.mark.asyncio
    async def test_group_full_name_equals_group_name(self, connector):
        """full_name == group['name']."""
        group = _make_authentik_group(name="Marketing")
        connector._paginate = AsyncMock(return_value=[group])

        from apps.worker.connectors.base import SyncResult

        result = SyncResult(connector_name="authentik")
        await connector._sync_groups(result)

        call_args = connector.elder_client.get_or_create_identity.call_args
        identity: Identity = call_args[0][0]
        assert identity.full_name == "Marketing"


# ---------------------------------------------------------------------------
# TestAuthentikEnumCoverage
# ---------------------------------------------------------------------------


class TestAuthentikEnumCoverage:
    """Tests for enum value correctness."""

    def test_authprovider_has_authentik_value(self):
        """AuthProvider.AUTHENTIK.value == 'authentik'."""
        from apps.api.models.identity import AuthProvider

        assert hasattr(AuthProvider, "AUTHENTIK")
        assert AuthProvider.AUTHENTIK.value == "authentik"
