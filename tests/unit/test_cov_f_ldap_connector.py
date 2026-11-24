"""Behavioural tests for the LDAP connector (OUs, users, groups, group write-back)."""

import ssl
from typing import Any
from unittest.mock import AsyncMock, patch

import ldap3
import pytest
from ldap3.core.exceptions import (
    LDAPBindError,
    LDAPInsufficientAccessRightsResult,
    LDAPSocketOpenError,
)

import tests.unit.conftest_worker_stubs  # noqa: F401
from apps.worker.config.settings import settings
from apps.worker.connectors import ldap_connector as mod
from apps.worker.connectors.ldap_connector import LDAPConnector
from tests.unit._cov_f_helpers import (
    assert_not_leaked,
    by_name,
    created_entities,
    fake_secret,
    logged_text,
    make_elder,
    spy_logger,
    updated_entities,
)

BASE_DN = "dc=corp,dc=invalid"
ROOT_ORG = 50
GROUP_DN = f"cn=admins,ou=groups,{BASE_DN}"
USER_DN = f"cn=jdoe,ou=people,{BASE_DN}"


class _Attr:
    """An ldap3 attribute stand-in: stringifies to its first value, exposes ``values``."""

    def __init__(self, *values: Any):
        """Store the attribute values."""
        self.values = list(values)

    def __str__(self) -> str:
        """Return the first value as ldap3 does for single-valued attributes."""
        return str(self.values[0]) if self.values else ""

    def __len__(self) -> int:
        """Return the number of values."""
        return len(self.values)


class _Entry:
    """An ldap3 entry stand-in exposing only the attributes the server returned."""

    def __init__(self, dn: str, **attrs: Any):
        """Store the DN and wrap each attribute value(s)."""
        self.entry_dn = dn
        for key, value in attrs.items():
            wrapped = _Attr(*value) if isinstance(value, list) else _Attr(value)
            setattr(self, key, wrapped)


class _Directory:
    """An ldap3 ``Connection`` double answering searches per filter."""

    def __init__(self, results: dict[str, Any]):
        """Remember which entries (or exception) each search filter yields."""
        self.results = results
        self.entries: list[_Entry] = []
        self.bound = True
        self.searches: list[dict[str, Any]] = []
        self.modifications: list[tuple[str, dict[str, Any]]] = []
        self.result: dict[str, Any] = {"result": 0, "description": "success"}
        self.modify_error: Exception | None = None
        self.unbound = False

    def search(self, **kwargs: Any) -> bool:
        """Serve a search and expose the entries like ldap3 does."""
        self.searches.append(kwargs)
        outcome = self.results.get(kwargs["search_filter"], [])
        if isinstance(outcome, Exception):
            raise outcome
        self.entries = list(outcome)
        return bool(self.entries)

    def modify(self, dn: str, changes: dict[str, Any]) -> bool:
        """Record a modify request, or raise the scripted failure."""
        if self.modify_error:
            raise self.modify_error
        self.modifications.append((dn, changes))
        return self.result["result"] == 0

    def unbind(self) -> None:
        """Record the unbind."""
        self.unbound = True


@pytest.fixture(autouse=True)
def _ldap_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin the directory settings used by every test."""
    monkeypatch.setattr(settings, "ldap_server", "ldap.corp.invalid")
    monkeypatch.setattr(settings, "ldap_base_dn", BASE_DN)
    monkeypatch.setattr(settings, "ldap_user_filter", "(objectClass=person)")
    monkeypatch.setattr(settings, "ldap_group_filter", "(objectClass=groupOfNames)")
    monkeypatch.setattr(settings, "create_missing_organizations", True)


def _connector(results: dict[str, Any] | None = None) -> LDAPConnector:
    """Return a connector bound to a fake directory and an Elder double."""
    c = LDAPConnector()
    c.ldap_conn = _Directory(results or {})
    c.elder_client = make_elder()
    spy_logger(c)
    return c


class TestConnect:
    """Bind lifecycle."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("use_ssl", "verify", "expected"),
        [
            (True, True, ssl.CERT_REQUIRED),
            (True, False, ssl.CERT_NONE),
            (False, True, None),
        ],
    )
    async def test_connect_builds_tls_and_binds_without_logging_password(
        self,
        monkeypatch: pytest.MonkeyPatch,
        use_ssl: bool,
        verify: bool,
        expected: Any,
    ) -> None:
        """TLS validation follows settings; the bind password stays out of logs."""
        password = fake_secret("ldap", "bind", "pw")
        monkeypatch.setattr(settings, "ldap_use_ssl", use_ssl)
        monkeypatch.setattr(settings, "ldap_verify_cert", verify)
        monkeypatch.setattr(settings, "ldap_port", 636)
        monkeypatch.setattr(settings, "ldap_bind_dn", f"cn=svc,{BASE_DN}")
        monkeypatch.setattr(settings, "ldap_bind_password", password)
        c = LDAPConnector()
        logger = spy_logger(c)
        elder = AsyncMock()
        with (
            patch.object(mod.ldap3, "Tls") as tls,
            patch.object(mod, "Server") as server,
            patch.object(mod, "Connection") as conn,
            patch.object(mod, "ElderAPIClient", return_value=elder),
        ):
            await c.connect()

        if expected is None:
            tls.assert_not_called()
            assert server.call_args.kwargs["tls"] is None
        else:
            tls.assert_called_once_with(validate=expected)
            assert server.call_args.kwargs["tls"] is tls.return_value
        assert server.call_args.args == ("ldap.corp.invalid",)
        assert server.call_args.kwargs["use_ssl"] is use_ssl
        assert conn.call_args.kwargs == {
            "user": f"cn=svc,{BASE_DN}",
            "password": password,
            "auto_bind": True,
        }
        assert c.ldap_conn is conn.return_value
        elder.connect.assert_awaited_once()
        assert_not_leaked(password, logged_text(logger))

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "error",
        [LDAPBindError("invalidCredentials"), LDAPSocketOpenError("timed out")],
        ids=["bad-credentials", "unreachable"],
    )
    async def test_connect_failure_raises_before_elder(
        self, monkeypatch: pytest.MonkeyPatch, error: Exception
    ) -> None:
        """Bind and socket failures re-raise and never open the Elder client."""
        monkeypatch.setattr(settings, "ldap_use_ssl", False)
        c = LDAPConnector()
        logger = spy_logger(c)
        with (
            patch.object(mod, "Server"),
            patch.object(mod, "Connection", side_effect=error),
            patch.object(mod, "ElderAPIClient") as elder_cls,
            pytest.raises(type(error)),
        ):
            await c.connect()
        elder_cls.assert_not_called()
        logger.error.assert_called_once()

    @pytest.mark.asyncio
    async def test_disconnect_unbinds_and_clears_caches(self) -> None:
        """Disconnect unbinds, closes Elder and forgets cached org ids."""
        c = _connector()
        directory, elder = c.ldap_conn, c.elder_client
        c.organization_cache["root:x"] = 1
        c.ou_cache["ou=x"] = 2
        await c.disconnect()
        assert directory.unbound is True and c.ldap_conn is None
        elder.close.assert_awaited_once()
        assert c.organization_cache == {} and c.ou_cache == {}
        fresh = LDAPConnector()
        await fresh.disconnect()
        assert fresh.ldap_conn is None


class TestOrganization:
    """Organization helper (LDAP variant carries the DN)."""

    @pytest.mark.asyncio
    async def test_created_org_carries_ldap_dn(self) -> None:
        """A newly created organization records the LDAP DN it mirrors."""
        c = _connector()
        org_id = await c._get_or_create_organization(
            "people", "d", parent_id=ROOT_ORG, ldap_dn=f"ou=people,{BASE_DN}"
        )
        payload = c.elder_client.create_organization.call_args.args[0]
        assert org_id == 501
        assert (payload.name, payload.parent_id, payload.ldap_dn) == (
            "people",
            ROOT_ORG,
            f"ou=people,{BASE_DN}",
        )
        assert await c._get_or_create_organization("people", "d", ROOT_ORG) == 501
        c.elder_client.create_organization.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_existing_default_and_error_paths(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Existing orgs are reused; otherwise default id, else a loud error."""
        c = _connector()
        c.elder_client = make_elder(orgs=[{"id": 9, "name": "people", "parent_id": 1}])
        assert await c._get_or_create_organization("people", "d", 1) == 9
        monkeypatch.setattr(settings, "create_missing_organizations", False)
        monkeypatch.setattr(settings, "default_organization_id", 42)
        assert await c._get_or_create_organization("other", "d") == 42
        monkeypatch.setattr(settings, "default_organization_id", None)
        with pytest.raises(ValueError, match="auto-creation disabled"):
            await c._get_or_create_organization("other", "d")

    def test_parse_dn_components(self) -> None:
        """DN parsing keeps key/value pairs and drops malformed parts."""
        c = _connector()
        assert c._parse_dn_components("ou= People ,garbage,dc=corp,cn=a=b") == [
            ("ou", "People"),
            ("dc", "corp"),
            ("cn", "a=b"),
        ]


class TestOrganizationalUnits:
    """OU hierarchy mapping."""

    @pytest.mark.asyncio
    async def test_ous_created_parent_first_with_fallback_names(self) -> None:
        """Parents are created before children; names fall back to the DN's ou part."""
        c = _connector(
            {
                "(objectClass=organizationalUnit)": [
                    _Entry(f"ou=eng,ou=people,{BASE_DN}", ou="eng", description="Eng"),
                    _Entry(f"ou=people,{BASE_DN}"),
                    _Entry(f"cn=weird,{BASE_DN}"),
                    _Entry(f"ou=orphan,ou=missing,{BASE_DN}", ou="orphan"),
                ]
            }
        )
        ids = iter([601, 602, 603])
        c.elder_client.create_organization.side_effect = lambda _o: {"id": next(ids)}

        assert await c._sync_organizational_units(ROOT_ORG) == (3, 0)

        created = {
            o.args[0].name: o.args[0]
            for o in c.elder_client.create_organization.call_args_list
        }
        assert created["people"].parent_id == ROOT_ORG
        assert created["people"].description == (
            f"LDAP organizational unit: ou=people,{BASE_DN}"
        )
        assert created["eng"].parent_id == created_id(c, f"ou=people,{BASE_DN}")
        assert created["eng"].description == "Eng"
        assert created["orphan"].parent_id == ROOT_ORG
        assert "weird" not in created
        search = c.ldap_conn.searches[0]
        assert search["search_base"] == BASE_DN
        assert search["search_scope"] == ldap3.SUBTREE

    @pytest.mark.asyncio
    async def test_ou_search_failure_is_contained(self) -> None:
        """A directory error is logged and reports zero OUs."""
        c = _connector(
            {
                "(objectClass=organizationalUnit)": LDAPInsufficientAccessRightsResult(
                    "denied"
                )
            }
        )
        assert await c._sync_organizational_units(ROOT_ORG) == (0, 0)
        c.logger.error.assert_called_once()


def created_id(connector: LDAPConnector, dn: str) -> int:
    """Return the Elder org id cached for an OU DN."""
    return connector.ou_cache[dn]


class TestUsers:
    """User mapping."""

    def _users(self) -> list[_Entry]:
        """Return a realistic mix of directory user entries."""
        return [
            _Entry(
                USER_DN,
                cn="jdoe",
                uid="jdoe",
                mail="jdoe@corp.invalid",
                givenName="Jane",
                sn="Doe",
                displayName="Jane Doe",
                userAccountControl="512",
            ),
            _Entry(f"cn=old,{BASE_DN}", cn="old", userAccountControl="514"),
            _Entry(f"uid=svc,{BASE_DN}", mail="not-an-email"),
            _Entry(f"cn=ghost,{BASE_DN}"),
        ]

    @pytest.mark.asyncio
    async def test_users_become_entities_and_identities(self) -> None:
        """Users map to entities plus identities; AD-disabled accounts are inactive."""
        c = _connector({"(objectClass=person)": self._users()})
        c.ou_cache[f"ou=people,{BASE_DN}"] = 71

        assert await c._sync_users(ROOT_ORG) == (3, 0)

        got = by_name(created_entities(c.elder_client))
        jane = got["Jane Doe"]
        assert (jane.entity_type, jane.organization_id, jane.is_active) == (
            "user",
            71,
            True,
        )
        assert jane.attributes["ldap_dn"] == USER_DN
        assert jane.attributes["email"] == "jdoe@corp.invalid"
        assert jane.attributes["ldap_server"] == "ldap.corp.invalid"
        assert got["old"].is_active is False
        assert got["old"].organization_id == ROOT_ORG
        assert "not-an-email" in got

        identities = {
            call.args[0].auth_provider_id: call.args[0]
            for call in c.elder_client.get_or_create_identity.call_args_list
        }
        assert len(identities) == 3
        ident = identities[USER_DN]
        assert (ident.username, ident.identity_type, ident.auth_provider) == (
            "jdoe",
            "human",
            "ldap",
        )
        assert (ident.email, ident.full_name) == ("jdoe@corp.invalid", "Jane Doe")
        assert identities[f"cn=old,{BASE_DN}"].is_active is False
        svc = identities[f"uid=svc,{BASE_DN}"]
        assert svc.email is None and svc.username == "not-an-email"

    @pytest.mark.asyncio
    async def test_existing_user_updated_by_dn(self) -> None:
        """A user already in Elder (same DN) is updated, not recreated."""
        c = _connector({"(objectClass=person)": self._users()[:1]})
        c.elder_client.list_entities.return_value = {
            "items": [{"id": 1}, {"id": 2, "metadata": {"ldap_dn": USER_DN}}]
        }
        assert await c._sync_users(ROOT_ORG) == (0, 1)
        assert updated_entities(c.elder_client)[0][0] == 2
        c.elder_client.get_or_create_identity.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_user_search_failure_is_contained(self) -> None:
        """A directory timeout is logged and writes nothing."""
        c = _connector({"(objectClass=person)": LDAPSocketOpenError("timed out")})
        assert await c._sync_users(ROOT_ORG) == (0, 0)
        c.logger.error.assert_called_once()
        c.elder_client.create_entity.assert_not_awaited()


class TestGroups:
    """Group mapping."""

    @pytest.mark.asyncio
    async def test_groups_mapped_with_member_counts(self) -> None:
        """Groups carry member counts; nameless groups are skipped."""
        c = _connector(
            {
                "(objectClass=groupOfNames)": [
                    _Entry(GROUP_DN, cn="admins", member=[USER_DN, "cn=x"]),
                    _Entry(f"cn=empty,{BASE_DN}", cn="empty", description="Empty"),
                    _Entry(f"ou=nocn,{BASE_DN}"),
                ]
            }
        )
        c.ou_cache[f"ou=groups,{BASE_DN}"] = 72
        c.elder_client.list_entities.return_value = {
            "items": [
                {"id": 4, "metadata": {"ldap_dn": f"cn=empty,{BASE_DN}"}},
                {
                    "id": 5,
                    "metadata": {"ldap_dn": f"cn=empty,{BASE_DN}", "type": "group"},
                },
            ]
        }

        assert await c._sync_groups(ROOT_ORG) == (1, 1)

        (admins,) = created_entities(c.elder_client)
        assert (admins.name, admins.organization_id) == ("Group: admins", 72)
        assert admins.attributes["member_count"] == 2
        assert admins.attributes["type"] == "group"
        assert admins.description == f"LDAP group: {GROUP_DN}"
        ((entity_id, empty),) = updated_entities(c.elder_client)
        assert entity_id == 5
        assert empty.attributes["member_count"] == 0
        assert empty.description == "Empty"

    @pytest.mark.asyncio
    async def test_group_search_failure_is_contained(self) -> None:
        """A directory error while listing groups is logged and writes nothing."""
        c = _connector({"(objectClass=groupOfNames)": LDAPSocketOpenError("down")})
        assert await c._sync_groups(ROOT_ORG) == (0, 0)
        c.logger.error.assert_called_once()


class TestSyncAndHealth:
    """End-to-end sync and health check."""

    @pytest.mark.asyncio
    async def test_full_sync_counts(self) -> None:
        """A full sync creates the root org, OUs, users and groups."""
        c = _connector(
            {
                "(objectClass=organizationalUnit)": [
                    _Entry(f"ou=people,{BASE_DN}", ou="people")
                ],
                "(objectClass=person)": [_Entry(USER_DN, cn="jdoe")],
                "(objectClass=groupOfNames)": [_Entry(GROUP_DN, cn="admins")],
            }
        )
        result = await c.sync()
        assert result.errors == []
        assert result.organizations_created == 2
        assert result.entities_created == 2
        root = c.elder_client.create_organization.call_args_list[0].args[0]
        assert (root.name, root.ldap_dn) == ("LDAP: ldap.corp.invalid", BASE_DN)

    @pytest.mark.asyncio
    async def test_sync_reports_failure(self) -> None:
        """An Elder outage is reported in the result."""
        c = _connector()
        c.elder_client.list_organizations.side_effect = RuntimeError("down")
        result = await c.sync()
        assert result.errors == ["LDAP sync failed: down"]

    @pytest.mark.asyncio
    async def test_health_check_states(self) -> None:
        """Health needs a bound connection and a working base search."""
        c = _connector()
        assert await c.health_check() is True
        assert c.ldap_conn.searches[0]["search_scope"] == ldap3.BASE
        c.ldap_conn.results["(objectClass=*)"] = LDAPSocketOpenError("down")
        assert await c.health_check() is False
        c.ldap_conn.bound = False
        assert await c.health_check() is False
        c.ldap_conn = None
        assert await c.health_check() is False


class TestGroupWriteBack:
    """Group membership write-back."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("method", "operation", "ldap_op"),
        [
            ("add_group_member", "add", ldap3.MODIFY_ADD),
            ("remove_group_member", "remove", ldap3.MODIFY_DELETE),
        ],
    )
    async def test_membership_change_sent_and_reported(
        self, method: str, operation: str, ldap_op: Any
    ) -> None:
        """A successful modify targets the group DN with the right operation."""
        c = _connector()
        result = await getattr(c, method)(GROUP_DN, USER_DN)
        assert c.ldap_conn.modifications == [
            (GROUP_DN, {"member": [(ldap_op, [USER_DN])]})
        ]
        assert result.to_dict() | {"timestamp": None} == {
            "success": True,
            "group_id": GROUP_DN,
            "user_id": USER_DN,
            "operation": operation,
            "error": None,
            "timestamp": None,
        }

    @pytest.mark.asyncio
    @pytest.mark.parametrize("method", ["add_group_member", "remove_group_member"])
    async def test_server_rejection_is_reported(self, method: str) -> None:
        """A non-zero LDAP result code surfaces as a failed result with its reason."""
        c = _connector()
        c.ldap_conn.result = {"result": 68, "description": "entryAlreadyExists"}
        result = await getattr(c, method)(GROUP_DN, USER_DN)
        assert (result.success, result.error) == (False, "entryAlreadyExists")

    @pytest.mark.asyncio
    @pytest.mark.parametrize("method", ["add_group_member", "remove_group_member"])
    async def test_ldap_exception_is_reported(self, method: str) -> None:
        """A directory exception becomes a failed result, not a raise."""
        c = _connector()
        c.ldap_conn.modify_error = LDAPInsufficientAccessRightsResult("denied")
        result = await getattr(c, method)(GROUP_DN, USER_DN)
        assert result.success is False and "denied" in result.error
        c.logger.error.assert_called_once()

    @pytest.mark.asyncio
    @pytest.mark.parametrize("method", ["add_group_member", "remove_group_member"])
    @pytest.mark.parametrize("state", ["unbound", "missing"])
    async def test_requires_bound_connection(self, method: str, state: str) -> None:
        """Without a bound connection nothing is sent and the result explains why."""
        c = _connector()
        directory = c.ldap_conn
        if state == "unbound":
            directory.bound = False
        else:
            c.ldap_conn = None
        result = await getattr(c, method)(GROUP_DN, USER_DN)
        assert result.success is False
        assert result.error == "LDAP connection not established"
        assert directory.modifications == []

    @pytest.mark.asyncio
    async def test_get_group_members_paths(self) -> None:
        """Members are listed; missing groups, no members and errors yield []."""
        c = _connector(
            {"(objectClass=*)": [_Entry(GROUP_DN, member=[USER_DN, "cn=b"])]}
        )
        assert await c.get_group_members(GROUP_DN) == [USER_DN, "cn=b"]
        assert c.ldap_conn.searches[0]["search_base"] == GROUP_DN

        c.ldap_conn.results["(objectClass=*)"] = [_Entry(GROUP_DN)]
        assert await c.get_group_members(GROUP_DN) == []
        c.ldap_conn.results["(objectClass=*)"] = []
        assert await c.get_group_members(GROUP_DN) == []
        c.ldap_conn.results["(objectClass=*)"] = LDAPSocketOpenError("down")
        assert await c.get_group_members(GROUP_DN) == []
        c.logger.error.assert_called_once()
        c.ldap_conn.bound = False
        assert await c.get_group_members(GROUP_DN) == []
        c.logger.warning.assert_called_once()

    @pytest.mark.asyncio
    async def test_replace_sync_through_the_mixin(self) -> None:
        """Replace mode removes extras and adds missing members via LDAP modifies."""
        c = _connector({"(objectClass=*)": [_Entry(GROUP_DN, member=["cn=old"])]})
        result = await c.sync_group_members(GROUP_DN, ["cn=new"], mode="replace")
        assert (result.total_operations, result.successful, result.failed) == (2, 2, 0)
        assert c.ldap_conn.modifications == [
            (GROUP_DN, {"member": [(ldap3.MODIFY_DELETE, ["cn=old"])]}),
            (GROUP_DN, {"member": [(ldap3.MODIFY_ADD, ["cn=new"])]}),
        ]
