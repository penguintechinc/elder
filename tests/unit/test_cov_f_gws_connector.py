"""Behavioural tests for the Google Workspace connector (org units, users, groups)."""

from typing import Any
from unittest.mock import AsyncMock, patch

import httplib2
import pytest
from googleapiclient.errors import HttpError

import tests.unit.conftest_worker_stubs  # noqa: F401
from apps.worker.config.settings import settings
from apps.worker.connectors import google_workspace_connector as mod
from apps.worker.connectors.google_workspace_connector import GoogleWorkspaceConnector
from tests.unit._cov_f_helpers import (
    by_name,
    created_entities,
    logged_text,
    make_elder,
    spy_logger,
    updated_entities,
)

ROOT_ORG = 40
CUSTOMER = "C0testcust"


def _http_error(status: int) -> HttpError:
    """Build a Google API ``HttpError`` with the given HTTP status."""
    return HttpError(httplib2.Response({"status": status}), b'{"error": "x"}')


class _Request:
    """A googleapiclient request double: ``execute`` returns or raises the outcome."""

    def __init__(self, outcome: Any):
        """Store the scripted outcome."""
        self.outcome = outcome

    def execute(self) -> Any:
        """Return the page, or raise the scripted provider error."""
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


class _Resource:
    """A directory resource (users/groups/orgunits) serving pages by ``pageToken``."""

    def __init__(self, pages: dict[Any, Any], calls: list[dict[str, Any]]):
        """Store pages keyed by page token (``None`` for the first page)."""
        self.pages = pages
        self.calls = calls

    def list(self, **kwargs: Any) -> _Request:
        """Record the list call and return the matching page request."""
        self.calls.append(kwargs)
        return _Request(self.pages.get(kwargs.get("pageToken"), {}))


class _AdminService:
    """An Admin SDK directory service double."""

    def __init__(self, orgunits: Any = None, users: Any = None, groups: Any = None):
        """Store per-resource page tables."""
        self.calls: dict[str, list[dict[str, Any]]] = {
            "orgunits": [],
            "users": [],
            "groups": [],
        }
        self._pages = {
            "orgunits": {None: orgunits if orgunits is not None else {}},
            "users": self._table(users),
            "groups": self._table(groups),
        }

    @staticmethod
    def _table(pages: Any) -> dict[Any, Any]:
        """Accept either a single page or a ``{page_token: page}`` table."""
        if isinstance(pages, dict) and None in pages:
            return pages
        return {None: pages or {}}

    def orgunits(self) -> _Resource:
        """Return the org-unit resource."""
        return _Resource(self._pages["orgunits"], self.calls["orgunits"])

    def users(self) -> _Resource:
        """Return the users resource."""
        return _Resource(self._pages["users"], self.calls["users"])

    def groups(self) -> _Resource:
        """Return the groups resource."""
        return _Resource(self._pages["groups"], self.calls["groups"])


@pytest.fixture(autouse=True)
def _gws_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin the customer id and auto-create behaviour."""
    monkeypatch.setattr(settings, "google_workspace_customer_id", CUSTOMER)
    monkeypatch.setattr(settings, "create_missing_organizations", True)


def _connector(service: _AdminService | None = None) -> GoogleWorkspaceConnector:
    """Return a connector attached to a fake Admin SDK and an Elder double."""
    c = GoogleWorkspaceConnector()
    c.admin_service = service or _AdminService()
    c.elder_client = make_elder()
    spy_logger(c)
    return c


def _user(uid: str, email: str, **extra: Any) -> dict[str, Any]:
    """Build a directory user resource."""
    return {"id": uid, "primaryEmail": email, **extra}


class TestConnect:
    """Service-account delegation."""

    @pytest.mark.asyncio
    async def test_connect_delegates_to_admin_with_readonly_scopes(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The key file is loaded with read-only scopes and delegated to the admin."""
        monkeypatch.setattr(
            settings, "google_workspace_credentials_path", "/run/secrets/gws.json"
        )
        monkeypatch.setattr(
            settings, "google_workspace_admin_email", "admin@corp.invalid"
        )
        c = GoogleWorkspaceConnector()
        elder = AsyncMock()
        with (
            patch.object(
                mod.service_account.Credentials, "from_service_account_file"
            ) as loader,
            patch.object(mod, "build") as build,
            patch.object(mod, "ElderAPIClient", return_value=elder),
        ):
            await c.connect()

        loader.assert_called_once_with("/run/secrets/gws.json", scopes=c.SCOPES)
        assert all(scope.endswith(".readonly") for scope in c.SCOPES)
        loader.return_value.with_subject.assert_called_once_with("admin@corp.invalid")
        build.assert_called_once_with(
            "admin",
            "directory_v1",
            credentials=loader.return_value.with_subject.return_value,
        )
        assert c.admin_service is build.return_value
        elder.connect.assert_awaited_once()

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("path", "admin", "message"),
        [
            (None, "admin@corp.invalid", "credentials path not configured"),
            ("/k.json", None, "admin email not configured"),
        ],
    )
    async def test_connect_requires_configuration(
        self, monkeypatch: pytest.MonkeyPatch, path: Any, admin: Any, message: str
    ) -> None:
        """Missing key path or admin email fails fast with a specific message."""
        monkeypatch.setattr(settings, "google_workspace_credentials_path", path)
        monkeypatch.setattr(settings, "google_workspace_admin_email", admin)
        with pytest.raises(ValueError, match=message):
            await GoogleWorkspaceConnector().connect()

    @pytest.mark.asyncio
    async def test_connect_bad_key_file_raises_before_elder(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An unreadable key file aborts connect before Elder is opened."""
        monkeypatch.setattr(settings, "google_workspace_credentials_path", "/k.json")
        monkeypatch.setattr(settings, "google_workspace_admin_email", "a@corp.invalid")
        c = GoogleWorkspaceConnector()
        logger = spy_logger(c)
        with (
            patch.object(
                mod.service_account.Credentials,
                "from_service_account_file",
                side_effect=FileNotFoundError("/k.json"),
            ),
            patch.object(mod, "ElderAPIClient") as elder_cls,
            pytest.raises(FileNotFoundError),
        ):
            await c.connect()
        elder_cls.assert_not_called()
        logger.error.assert_called_once()

    @pytest.mark.asyncio
    async def test_disconnect_clears_state(self) -> None:
        """Disconnect closes Elder, drops the service and clears caches."""
        c = _connector()
        elder = c.elder_client
        c.organization_cache["root:x"] = 1
        c.orgunit_cache["/Eng"] = 2
        await c.disconnect()
        elder.close.assert_awaited_once()
        assert c.admin_service is None
        assert c.organization_cache == {} and c.orgunit_cache == {}
        c.elder_client = None
        await c.disconnect()


class TestOrgUnits:
    """Org-unit hierarchy mapping."""

    @pytest.mark.asyncio
    async def test_orgunits_nested_parent_first(self) -> None:
        """Parents are created before children regardless of listing order."""
        c = _connector(
            _AdminService(
                orgunits={
                    "organizationUnits": [
                        {"orgUnitPath": "/Eng/Platform", "name": "Platform"},
                        {
                            "orgUnitPath": "/Eng",
                            "name": "Eng",
                            "description": "Engineering",
                        },
                        {"orgUnitPath": "/Ghost/Child", "name": "Child"},
                    ]
                }
            )
        )
        ids = iter([801, 802, 803])
        c.elder_client.create_organization.side_effect = lambda _o: {"id": next(ids)}

        created, updated = await c._sync_organizational_units(ROOT_ORG)

        assert created + updated == 3
        orgs = {
            o.args[0].name: o.args[0]
            for o in c.elder_client.create_organization.call_args_list
        }
        assert orgs["Eng"].parent_id == ROOT_ORG
        assert orgs["Eng"].description == "Engineering"
        assert orgs["Platform"].parent_id == c.orgunit_cache["/Eng"]
        assert orgs["Platform"].description == (
            "Google Workspace organizational unit: /Eng/Platform"
        )
        assert orgs["Child"].parent_id == ROOT_ORG
        assert c.admin_service.calls["orgunits"] == [{"customerId": CUSTOMER}]

    @pytest.mark.asyncio
    async def test_existing_orgunit_counts_as_update(self) -> None:
        """An org unit already present in Elder is counted as updated."""
        c = _connector(
            _AdminService(
                orgunits={"organizationUnits": [{"orgUnitPath": "/Eng", "name": "Eng"}]}
            )
        )
        c.elder_client = make_elder(
            orgs=[{"id": 9, "name": "Eng", "parent_id": ROOT_ORG}]
        )
        assert await c._sync_organizational_units(ROOT_ORG) == (0, 1)
        assert c.orgunit_cache == {"/Eng": 9}

    @pytest.mark.asyncio
    @pytest.mark.parametrize("status", [403, 429, 500])
    async def test_orgunit_api_error_contained(self, status: int) -> None:
        """A provider error is logged and reports zero org units."""
        c = _connector(_AdminService(orgunits=_http_error(status)))
        assert await c._sync_organizational_units(ROOT_ORG) == (0, 0)
        c.logger.error.assert_called_once()


class TestUsers:
    """User mapping and pagination."""

    @pytest.mark.asyncio
    async def test_users_paginated_into_entities_and_identities(self) -> None:
        """Every page is fetched; users map to entities plus google identities."""
        service = _AdminService(
            users={
                None: {
                    "users": [
                        _user(
                            "1",
                            "jane@corp.invalid",
                            name={
                                "fullName": "Jane Doe",
                                "givenName": "Jane",
                                "familyName": "Doe",
                            },
                            orgUnitPath="/Eng",
                            isAdmin=True,
                        )
                    ],
                    "nextPageToken": "p2",
                },
                "p2": {"users": [_user("2", "gone@corp.invalid", suspended=True)]},
            }
        )
        c = _connector(service)
        c.orgunit_cache["/Eng"] = 61

        assert await c._sync_users(ROOT_ORG) == (2, 0)

        assert [call["pageToken"] for call in service.calls["users"]] == [None, "p2"]
        assert service.calls["users"][0]["customer"] == CUSTOMER
        got = by_name(created_entities(c.elder_client))
        jane = got["Jane Doe"]
        assert (jane.organization_id, jane.is_active) == (61, True)
        assert jane.attributes["is_admin"] is True
        assert jane.attributes["org_unit_path"] == "/Eng"
        gone = got["gone@corp.invalid"]
        assert (gone.organization_id, gone.is_active) == (ROOT_ORG, False)
        identities = {
            call.args[0].auth_provider_id: call.args[0]
            for call in c.elder_client.get_or_create_identity.call_args_list
        }
        assert identities["1"].full_name == "Jane Doe"
        assert identities["1"].auth_provider == "google"
        assert identities["2"].full_name is None
        assert identities["2"].is_active is False

    @pytest.mark.asyncio
    async def test_existing_user_updated_by_email(self) -> None:
        """A user already in Elder (same email) is updated rather than duplicated."""
        c = _connector(_AdminService(users={"users": [_user("1", "a@corp.invalid")]}))
        c.elder_client.list_entities.return_value = {
            "items": [{"id": 1}, {"id": 2, "metadata": {"email": "a@corp.invalid"}}]
        }
        assert await c._sync_users(ROOT_ORG) == (0, 1)
        assert updated_entities(c.elder_client)[0][0] == 2

    @pytest.mark.asyncio
    async def test_error_on_second_page_keeps_first_page(self) -> None:
        """A failure mid-pagination keeps what was already synced and logs the error."""
        c = _connector(
            _AdminService(
                users={
                    None: {
                        "users": [_user("1", "a@corp.invalid")],
                        "nextPageToken": "p2",
                    },
                    "p2": _http_error(503),
                }
            )
        )
        assert await c._sync_users(ROOT_ORG) == (1, 0)
        c.logger.error.assert_called_once()


class TestGroups:
    """Group mapping and pagination."""

    @pytest.mark.asyncio
    async def test_groups_paginated_and_matched_by_type(self) -> None:
        """Groups are created/updated; a user sharing the email is not mistaken for it."""
        service = _AdminService(
            groups={
                None: {
                    "groups": [
                        {
                            "id": "g1",
                            "email": "eng@corp.invalid",
                            "name": "Eng",
                            "directMembersCount": "12",
                        }
                    ],
                    "nextPageToken": "p2",
                },
                "p2": {"groups": [{"id": "g2", "email": "ops@corp.invalid"}]},
            }
        )
        c = _connector(service)
        c.elder_client.list_entities.return_value = {
            "items": [
                {"id": 1, "metadata": {"email": "ops@corp.invalid"}},
                {"id": 2, "metadata": {"email": "ops@corp.invalid", "type": "group"}},
            ]
        }
        assert await c._sync_groups(ROOT_ORG) == (1, 1)
        assert [call["pageToken"] for call in service.calls["groups"]] == [None, "p2"]
        (eng,) = created_entities(c.elder_client)
        assert (eng.name, eng.organization_id) == ("Group: Eng", ROOT_ORG)
        assert eng.attributes["direct_members_count"] == "12"
        assert eng.description == "Google Workspace group: eng@corp.invalid"
        ((entity_id, ops),) = updated_entities(c.elder_client)
        assert (entity_id, ops.name) == (2, "Group: ops@corp.invalid")

    @pytest.mark.asyncio
    async def test_group_api_error_contained(self) -> None:
        """A provider error while listing groups is logged and writes nothing."""
        c = _connector(_AdminService(groups={None: _http_error(403)}))
        assert await c._sync_groups(ROOT_ORG) == (0, 0)
        c.logger.error.assert_called_once()
        c.elder_client.create_entity.assert_not_awaited()


class TestSyncAndHealth:
    """End-to-end sync and health check."""

    @pytest.mark.asyncio
    async def test_full_sync(self) -> None:
        """A full sync creates the root org and all users and groups under it."""
        c = _connector(
            _AdminService(
                users={"users": [_user("1", "a@corp.invalid")]},
                groups={"groups": [{"id": "g", "email": "g@corp.invalid"}]},
            )
        )
        result = await c.sync()
        assert result.errors == []
        assert result.organizations_created == 1
        assert result.entities_created == 2
        root = c.elder_client.create_organization.call_args.args[0]
        assert root.name == "Google Workspace"
        assert CUSTOMER in root.description
        assert "a@corp.invalid" not in logged_text(c.logger)

    @pytest.mark.asyncio
    async def test_sync_reports_failure(self) -> None:
        """An Elder outage is reported in the result's errors."""
        c = _connector()
        c.elder_client.list_organizations.side_effect = RuntimeError("down")
        result = await c.sync()
        assert result.errors == ["Google Workspace sync failed: down"]

    @pytest.mark.asyncio
    async def test_health_check_states(self) -> None:
        """Health lists one user; provider errors or no service report unhealthy."""
        service = _AdminService()
        c = _connector(service)
        assert await c.health_check() is True
        assert service.calls["users"] == [{"customer": CUSTOMER, "maxResults": 1}]
        c.admin_service = _AdminService(users={None: _http_error(401)})
        assert await c.health_check() is False
        c.admin_service = None
        assert await c.health_check() is False
