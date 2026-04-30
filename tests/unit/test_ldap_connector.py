"""
Unit tests for LDAP connector identity sync.

Tests cover:
- LDAP user identity sync with DN as auth_provider_id
- Username precedence: uid > cn > mail
- Email only set when contains '@'
- is_active from userAccountControl bit 2
- Exception handling without propagation
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

import tests.unit.conftest_worker_stubs  # noqa: F401 — stubs heavy optional deps before any connector import
from apps.worker.connectors.ldap_connector import LDAPConnector
from apps.worker.utils.elder_client import Identity

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_ldap_entry(
    dn: str = "cn=jdoe,ou=users,dc=example,dc=com",
    uid: str = None,
    cn: str = "jdoe",
    mail: str = "jdoe@example.com",
    display_name: str = "John Doe",
    given_name: str = "John",
    surname: str = "Doe",
    uac: int = None,
) -> MagicMock:
    """Create a mock LDAP entry with attributes."""
    entry = MagicMock()
    entry.entry_dn = dn

    # Configure attributes; use del to make hasattr return False when attr is None
    if uid is not None:
        entry.uid = MagicMock(__str__=lambda s: uid)
    else:
        del entry.uid

    if cn is not None:
        entry.cn = MagicMock(__str__=lambda s: cn)
    else:
        del entry.cn

    if mail is not None:
        entry.mail = MagicMock(__str__=lambda s: mail)
    else:
        del entry.mail

    if display_name is not None:
        entry.displayName = MagicMock(__str__=lambda s: display_name)
    else:
        del entry.displayName

    if given_name is not None:
        entry.givenName = MagicMock(__str__=lambda s: given_name)
    else:
        del entry.givenName

    if surname is not None:
        entry.sn = MagicMock(__str__=lambda s: surname)
    else:
        del entry.sn

    if uac is not None:
        entry.userAccountControl = MagicMock(__str__=lambda s: str(uac))
    else:
        del entry.userAccountControl

    return entry


# ---------------------------------------------------------------------------
# TestLDAPUserIdentitySync — covers _sync_users identity section
# ---------------------------------------------------------------------------


class TestLDAPUserIdentitySync:
    """Tests for LDAP user identity sync."""

    @pytest.fixture
    def connector(self):
        c = LDAPConnector()
        c.elder_client = AsyncMock()
        c.elder_client.get_or_create_identity = AsyncMock(return_value={"id": 1})
        c.elder_client.list_entities = AsyncMock(return_value={"items": []})
        c.elder_client.create_entity = AsyncMock(return_value={"id": 1})
        c.ldap_conn = MagicMock()
        c.ldap_conn.entries = []
        c.ou_cache = {}
        return c

    @pytest.mark.asyncio
    async def test_ldap_user_identity_created_with_dn_as_auth_provider_id(
        self, connector
    ):
        """auth_provider='ldap', auth_provider_id=entry.entry_dn."""
        dn = "cn=jdoe,ou=users,dc=example,dc=com"
        entry = _make_ldap_entry(dn=dn)
        connector.ldap_conn.entries = [entry]
        connector.ldap_conn.search = MagicMock()

        from apps.worker.connectors.base import SyncResult

        result = SyncResult(connector_name="ldap")
        created, updated = await connector._sync_users(1)

        connector.elder_client.get_or_create_identity.assert_awaited_once()
        call_args = connector.elder_client.get_or_create_identity.call_args
        identity: Identity = call_args[0][0]

        assert identity.auth_provider == "ldap"
        assert identity.auth_provider_id == dn

    @pytest.mark.asyncio
    async def test_username_prefers_uid_over_cn_over_mail(self, connector):
        """uid present → username=uid; uid missing → fallback to cn; uid+cn missing → fallback to mail."""
        # Test 1: uid present
        entry1 = _make_ldap_entry(uid="jdoe123", cn="jdoe", mail="jdoe@example.com")
        connector.ldap_conn.entries = [entry1]
        connector.ldap_conn.search = MagicMock()

        await connector._sync_users(1)
        call_args = connector.elder_client.get_or_create_identity.call_args
        identity: Identity = call_args[0][0]
        assert identity.username == "jdoe123"

        # Test 2: uid missing, cn present
        connector.elder_client.get_or_create_identity.reset_mock()
        entry2 = _make_ldap_entry(uid=None, cn="jdoe", mail="jdoe@example.com")
        connector.ldap_conn.entries = [entry2]

        await connector._sync_users(1)
        call_args = connector.elder_client.get_or_create_identity.call_args
        identity: Identity = call_args[0][0]
        assert identity.username == "jdoe"

        # Test 3: uid+cn missing, mail present
        connector.elder_client.get_or_create_identity.reset_mock()
        entry3 = _make_ldap_entry(uid=None, cn=None, mail="jdoe@example.com")
        connector.ldap_conn.entries = [entry3]

        await connector._sync_users(1)
        call_args = connector.elder_client.get_or_create_identity.call_args
        identity: Identity = call_args[0][0]
        assert identity.username == "jdoe@example.com"

    @pytest.mark.asyncio
    async def test_email_set_only_when_at_sign_present(self, connector):
        """mail='user@example.com' → email set; mail='NOMATCH' (no @) → email=None."""
        # Test 1: valid email
        entry1 = _make_ldap_entry(mail="jdoe@example.com")
        connector.ldap_conn.entries = [entry1]
        connector.ldap_conn.search = MagicMock()

        await connector._sync_users(1)
        call_args = connector.elder_client.get_or_create_identity.call_args
        identity: Identity = call_args[0][0]
        assert identity.email == "jdoe@example.com"

        # Test 2: mail without @
        connector.elder_client.get_or_create_identity.reset_mock()
        entry2 = _make_ldap_entry(mail="NOMATCH")
        connector.ldap_conn.entries = [entry2]

        await connector._sync_users(1)
        call_args = connector.elder_client.get_or_create_identity.call_args
        identity: Identity = call_args[0][0]
        assert identity.email is None

    @pytest.mark.asyncio
    async def test_is_active_from_useraccountcontrol_disabled(self, connector):
        """userAccountControl with bit 2 set → is_active=False."""
        # Bit 2 = 0x2 = disabled; 0x200 (bit 9) = normal account
        # Disabled: uac & 2 == 2, so is_active = not(uac & 2)
        # uac = 514 = 0x202 (disabled + normal) → is_active = False
        # uac = 512 = 0x200 (normal, not disabled) → is_active = True

        entry1 = _make_ldap_entry(uac=514)  # disabled
        connector.ldap_conn.entries = [entry1]
        connector.ldap_conn.search = MagicMock()

        await connector._sync_users(1)
        call_args = connector.elder_client.get_or_create_identity.call_args
        identity: Identity = call_args[0][0]
        assert identity.is_active is False

        # Test enabled
        connector.elder_client.get_or_create_identity.reset_mock()
        entry2 = _make_ldap_entry(uac=512)  # enabled
        connector.ldap_conn.entries = [entry2]

        await connector._sync_users(1)
        call_args = connector.elder_client.get_or_create_identity.call_args
        identity: Identity = call_args[0][0]
        assert identity.is_active is True

    @pytest.mark.asyncio
    async def test_is_active_true_when_no_useraccountcontrol(self, connector):
        """no userAccountControl attr → is_active=True."""
        entry = _make_ldap_entry(uac=None)
        connector.ldap_conn.entries = [entry]
        connector.ldap_conn.search = MagicMock()

        await connector._sync_users(1)
        call_args = connector.elder_client.get_or_create_identity.call_args
        identity: Identity = call_args[0][0]
        assert identity.is_active is True

    @pytest.mark.asyncio
    async def test_identity_called_even_when_entity_exists(self, connector):
        """entity already exists (update path) → get_or_create_identity still called."""
        entry = _make_ldap_entry()
        connector.ldap_conn.entries = [entry]
        connector.ldap_conn.search = MagicMock()

        # Simulate entity already exists
        connector.elder_client.list_entities = AsyncMock(
            return_value={"items": [{"id": 42}]}
        )

        await connector._sync_users(1)

        # Identity should still be created even though entity was updated
        connector.elder_client.get_or_create_identity.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_ldap_exception_does_not_propagate(self, connector):
        """ldap_conn.search raises LDAPException → _sync_users returns (0, 0), no exception raised."""
        from ldap3.core.exceptions import LDAPException

        connector.ldap_conn.search.side_effect = LDAPException("Connection lost")

        created, updated = await connector._sync_users(1)

        # Function should return (0, 0) without propagating exception
        assert created == 0
        assert updated == 0


# ---------------------------------------------------------------------------
# TestLDAPEnumCoverage
# ---------------------------------------------------------------------------


class TestLDAPEnumCoverage:
    """Tests for enum value correctness."""

    def test_authprovider_has_ldap_value(self):
        """AuthProvider.LDAP.value == 'ldap'."""
        from apps.api.models.identity import AuthProvider

        assert hasattr(AuthProvider, "LDAP")
        assert AuthProvider.LDAP.value == "ldap"
