"""Behavioural tests for the iBoss connector (users, groups, policies, apps, gateways)."""

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

import tests.unit.conftest_worker_stubs  # noqa: F401
from apps.worker.config.settings import settings
from apps.worker.connectors import iboss_connector as mod
from apps.worker.connectors.iboss_connector import IBossConnector
from tests.unit._cov_f_helpers import (
    AsyncRouteClient,
    FakeResponse,
    assert_not_leaked,
    by_name,
    created_entities,
    fake_secret,
    logged_text,
    make_elder,
    spy_logger,
    updated_entities,
)

ORG_ID = 31


def _routes() -> dict[str, Any]:
    """Return a realistic iBoss API surface."""
    return {
        "/api/v1/health": {"status": "ok"},
        "/api/v1/users": {
            "users": [
                {
                    "id": "u1",
                    "username": "jdoe",
                    "email": "jdoe@corp.invalid",
                    "firstName": "J",
                    "lastName": "Doe",
                    "department": "Eng",
                    "groups": ["g1", "g-missing"],
                    "status": "active",
                },
                {"id": "u2", "email": "nouser@corp.invalid", "status": "suspended"},
            ]
        },
        "/api/v1/groups": {
            "groups": [
                {"id": "g1", "name": "Engineers", "memberCount": 4, "policies": ["p1"]},
                {"id": "g2", "name": "Ops", "description": "Operations"},
            ]
        },
        "/api/v1/policies": {
            "policies": [
                {
                    "id": "p1",
                    "name": "Block Malware",
                    "type": "web",
                    "enabled": True,
                    "priority": 1,
                    "categories": ["malware"],
                    "actions": {"default": "block"},
                },
                {"id": "p2", "name": "Draft"},
            ]
        },
        "/api/v1/applications": {
            "applications": [
                {"id": "a1", "name": "EvilBot", "category": "Botnet", "usersCount": 2},
                {"id": "a2", "name": "Tunnel", "category": "VPN"},
                {"id": "a3", "name": "Docs", "category": "Productivity"},
                {"id": "a4", "name": "Nameless"},
            ]
        },
        "/api/v1/connectors": {
            "connectors": [
                {
                    "id": "c1",
                    "name": "DAL-GW",
                    "type": "gateway",
                    "ipAddress": "192.0.2.10",
                    "status": "online",
                },
                {"id": "c2", "status": "offline"},
            ]
        },
        "/api/v1/reports/application-usage": {
            "usage": [
                {
                    "userId": "u1",
                    "applicationId": "a1",
                    "sessionsCount": 3,
                    "bytesTransferred": 99,
                },
                {"userId": "u1", "applicationId": "a-unknown"},
                {"userId": "u-unknown", "applicationId": "a1"},
            ]
        },
    }


@pytest.fixture
def connector() -> IBossConnector:
    """Return a connector wired to a routed fake iBoss API and Elder double."""
    c = IBossConnector()
    c.http_client = AsyncRouteClient(_routes())
    c.elder_client = make_elder()
    ids = iter(range(100, 200))
    c.elder_client.create_entity.side_effect = lambda _e: {"id": next(ids)}
    spy_logger(c)
    return c


class TestConnect:
    """Connection lifecycle."""

    @pytest.mark.asyncio
    async def test_connect_sends_bearer_and_tenant_without_logging_key(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Connect builds an authenticated, tenant-scoped client and never logs the key."""
        key = fake_secret("iboss", "apikey")
        monkeypatch.setattr(settings, "iboss_api_key", key)
        monkeypatch.setattr(settings, "iboss_tenant_id", "tenant-7")
        monkeypatch.setattr(settings, "iboss_api_url", "https://iboss.test.invalid")
        c = IBossConnector()
        logger = spy_logger(c)
        client = AsyncRouteClient(_routes())
        elder = AsyncMock()
        with (
            patch.object(mod.httpx, "AsyncClient", return_value=client) as ctor,
            patch.object(mod, "ElderAPIClient", return_value=elder),
        ):
            await c.connect()

        kwargs = ctor.call_args.kwargs
        assert kwargs["base_url"] == "https://iboss.test.invalid"
        assert kwargs["headers"]["Authorization"] == f"Bearer {key}"
        assert kwargs["headers"]["X-Tenant-ID"] == "tenant-7"
        assert kwargs["timeout"] == 30.0
        assert client.paths() == ["GET /api/v1/health"]
        elder.connect.assert_awaited_once()
        assert_not_leaked(key, logged_text(logger))

    @pytest.mark.asyncio
    async def test_connect_without_tenant_sends_empty_tenant_header(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A missing tenant id is sent as an empty header rather than 'None'."""
        monkeypatch.setattr(settings, "iboss_tenant_id", None)
        c = IBossConnector()
        with (
            patch.object(
                mod.httpx, "AsyncClient", return_value=AsyncRouteClient(_routes())
            ) as ctor,
            patch.object(mod, "ElderAPIClient", return_value=AsyncMock()),
        ):
            await c.connect()
        assert ctor.call_args.kwargs["headers"]["X-Tenant-ID"] == ""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "health",
        [
            FakeResponse({}, 401),
            FakeResponse({}, 429),
            FakeResponse({}, 503),
            TimeoutError("timed out"),
        ],
        ids=["unauthorized", "throttled", "unavailable", "timeout"],
    )
    async def test_connect_failure_raises_and_skips_elder(
        self, monkeypatch: pytest.MonkeyPatch, health: Any
    ) -> None:
        """A failing health probe aborts connect before Elder is contacted."""
        key = fake_secret("iboss", "bad")
        monkeypatch.setattr(settings, "iboss_api_key", key)
        c = IBossConnector()
        logger = spy_logger(c)
        client = AsyncRouteClient({"/api/v1/health": health})
        with (
            patch.object(mod.httpx, "AsyncClient", return_value=client),
            patch.object(mod, "ElderAPIClient") as elder_cls,
            pytest.raises(Exception, match="health check failed|timed out"),
        ):
            await c.connect()
        elder_cls.assert_not_called()
        logger.error.assert_called_once()
        assert_not_leaked(key, logged_text(logger))

    @pytest.mark.asyncio
    async def test_disconnect_closes_clients_and_clears_cache(
        self, connector: IBossConnector
    ) -> None:
        """Disconnect closes both clients and drops the organization cache."""
        http, elder = connector.http_client, connector.elder_client
        connector.organization_cache["root:x"] = 1
        await connector.disconnect()
        assert http.closed is True and connector.http_client is None
        elder.close.assert_awaited_once()
        assert connector.organization_cache == {}

    @pytest.mark.asyncio
    async def test_disconnect_when_never_connected(self) -> None:
        """Disconnecting a fresh connector does nothing and does not raise."""
        c = IBossConnector()
        await c.disconnect()
        assert c.http_client is None and c.elder_client is None


class TestUsers:
    """User mapping."""

    @pytest.mark.asyncio
    async def test_users_mapped_and_cached(self, connector: IBossConnector) -> None:
        """Users become identity entities; ids and group membership are cached."""
        assert await connector._sync_users(ORG_ID) == (2, 0)
        got = by_name(created_entities(connector.elder_client))
        jdoe = got["jdoe"]
        assert (jdoe.entity_type, jdoe.organization_id) == ("identity", ORG_ID)
        assert jdoe.is_active is True
        assert jdoe.attributes["iboss_user_id"] == "u1"
        assert jdoe.attributes["identity_type"] == "employee"
        assert jdoe.tags == ["iboss", "user", "identity"]
        fallback = got["nouser@corp.invalid"]
        assert fallback.is_active is False and fallback.attributes["groups"] == []
        assert connector.user_entity_cache == {"u1": 100, "u2": 101}
        assert connector.user_groups == {"u1": ["g1", "g-missing"], "u2": []}

    @pytest.mark.asyncio
    async def test_existing_user_is_updated(self, connector: IBossConnector) -> None:
        """A user already in Elder is updated in place and its id cached."""
        connector.elder_client.list_entities.return_value = {
            "items": [{"id": 9, "metadata": {"iboss_user_id": "u1"}}, {"id": 8}]
        }
        assert await connector._sync_users(ORG_ID) == (1, 1)
        ((entity_id, entity),) = updated_entities(connector.elder_client)
        assert (entity_id, entity.name) == (9, "jdoe")
        assert connector.user_entity_cache["u1"] == 9


def _sync_cases() -> list[tuple[str, str, str]]:
    """Return ``(method, path, log level on non-200)`` for each resource sync."""
    return [
        ("_sync_users", "/api/v1/users", "error"),
        ("_sync_groups", "/api/v1/groups", "error"),
        ("_sync_policies", "/api/v1/policies", "error"),
        ("_sync_applications", "/api/v1/applications", "warning"),
        ("_sync_cloud_connectors", "/api/v1/connectors", "error"),
    ]


class TestFailureModes:
    """Shared failure handling across every resource sync."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(("method", "path", "level"), _sync_cases())
    @pytest.mark.parametrize("status", [401, 429, 500])
    async def test_non_200_returns_zero_and_logs_status(
        self, connector: IBossConnector, method: str, path: str, level: str, status: int
    ) -> None:
        """Auth failures, throttling and server errors write nothing and log the status."""
        connector.http_client.routes[path] = FakeResponse({}, status)
        assert await getattr(connector, method)(ORG_ID) == (0, 0)
        connector.elder_client.create_entity.assert_not_awaited()
        log_call = getattr(connector.logger, level).call_args
        assert log_call.kwargs == {"status_code": status}

    @pytest.mark.asyncio
    @pytest.mark.parametrize(("method", "path", "_level"), _sync_cases())
    @pytest.mark.parametrize(
        "outcome",
        [TimeoutError("timed out"), FakeResponse(ValueError("not json"))],
        ids=["timeout", "malformed-json"],
    )
    async def test_transport_or_parse_failure_is_contained(
        self,
        connector: IBossConnector,
        method: str,
        path: str,
        _level: str,
        outcome: Any,
    ) -> None:
        """Timeouts and malformed bodies are logged and reported as zero changes."""
        connector.http_client.routes[path] = outcome
        assert await getattr(connector, method)(ORG_ID) == (0, 0)
        connector.logger.error.assert_called_once()

    @pytest.mark.asyncio
    @pytest.mark.parametrize(("method", "path", "_level"), _sync_cases())
    async def test_empty_page_writes_nothing(
        self, connector: IBossConnector, method: str, path: str, _level: str
    ) -> None:
        """An empty provider response produces no Elder writes."""
        connector.http_client.routes[path] = {}
        assert await getattr(connector, method)(ORG_ID) == (0, 0)
        connector.elder_client.list_entities.assert_not_awaited()


class TestGroupsPoliciesAppsGateways:
    """Mapping of the remaining resource types."""

    @pytest.mark.asyncio
    async def test_groups_mapped(self, connector: IBossConnector) -> None:
        """Groups become identity entities with a default or provided description."""
        connector.elder_client.list_entities.return_value = {
            "items": [{"id": 70, "metadata": {"iboss_group_id": "g2"}}]
        }
        assert await connector._sync_groups(ORG_ID) == (1, 1)
        (eng,) = created_entities(connector.elder_client)
        assert eng.name == "Group: Engineers"
        assert eng.description == "iBoss group: Engineers"
        assert eng.attributes["member_count"] == 4
        assert eng.attributes["identity_type"] == "group"
        ((entity_id, ops),) = updated_entities(connector.elder_client)
        assert (entity_id, ops.description) == (70, "Operations")
        assert connector.group_entity_cache == {"g1": 100, "g2": 70}

    @pytest.mark.asyncio
    async def test_policies_mapped(self, connector: IBossConnector) -> None:
        """Policies become security entities whose activity mirrors 'enabled'."""
        connector.elder_client.list_entities.return_value = {
            "items": [{"id": 71, "metadata": {"iboss_policy_id": "p2"}}]
        }
        assert await connector._sync_policies(ORG_ID) == (1, 1)
        (block,) = created_entities(connector.elder_client)
        assert (block.name, block.entity_type) == ("Policy: Block Malware", "security")
        assert block.is_active is True
        assert block.attributes["actions"] == {"default": "block"}
        ((entity_id, draft),) = updated_entities(connector.elder_client)
        assert entity_id == 71
        assert draft.is_active is False and draft.attributes["categories"] == []
        connector.elder_client.list_entities.assert_awaited_with(
            organization_id=ORG_ID, entity_type="security"
        )

    @pytest.mark.asyncio
    async def test_applications_risk_tagged(self, connector: IBossConnector) -> None:
        """Application categories drive risk tags; usage drives activity."""
        connector.elder_client.list_entities.return_value = {
            "items": [{"id": 72, "metadata": {"iboss_app_id": "a3"}}]
        }
        assert await connector._sync_applications(ORG_ID) == (3, 1)
        got = by_name(created_entities(connector.elder_client))
        assert got["App: EvilBot"].tags[-1] == "high-risk"
        assert got["App: EvilBot"].is_active is True
        assert got["App: Tunnel"].tags[-1] == "medium-risk"
        assert got["App: Tunnel"].is_active is False
        assert got["App: Nameless"].tags == [
            "iboss",
            "application",
            "software",
            "unknown",
        ]
        ((entity_id, docs),) = updated_entities(connector.elder_client)
        assert entity_id == 72 and "high-risk" not in docs.tags
        assert connector.app_entity_cache["a3"] == 72
        assert set(connector.app_entity_cache) == {"a1", "a2", "a3", "a4"}

    @pytest.mark.asyncio
    async def test_cloud_connectors_mapped(self, connector: IBossConnector) -> None:
        """Gateways become network entities; unnamed ones get an id-based name."""
        connector.elder_client.list_entities.return_value = {
            "items": [{"id": 73, "metadata": {"iboss_connector_id": "c2"}}]
        }
        assert await connector._sync_cloud_connectors(ORG_ID) == (1, 1)
        (gw,) = created_entities(connector.elder_client)
        assert (gw.name, gw.entity_type, gw.is_active) == ("DAL-GW", "network", True)
        assert gw.attributes["ip_address"] == "192.0.2.10"
        ((entity_id, anon),) = updated_entities(connector.elder_client)
        assert (entity_id, anon.name, anon.is_active) == (73, "Connector-c2", False)


class TestRelationships:
    """Dependency edges between synced entities."""

    @pytest.fixture
    def primed(self, connector: IBossConnector) -> IBossConnector:
        """Prime the entity caches as a prior sync pass would."""
        connector.user_entity_cache = {"u1": 100, "u2": 101}
        connector.group_entity_cache = {"g1": 200}
        connector.app_entity_cache = {"a1": 300}
        connector.user_groups = {"u1": ["g1", "g-missing"]}
        return connector

    @pytest.mark.asyncio
    async def test_membership_and_usage_edges(self, primed: IBossConnector) -> None:
        """Only edges whose both ends were synced are created, with usage attributes."""
        assert await primed._sync_relationships() == 2
        calls = [
            c.kwargs
            for c in primed.elder_client.get_or_create_dependency.call_args_list
        ]
        assert calls[0] == {
            "source_entity_id": 100,
            "target_entity_id": 200,
            "dependency_type": "member_of",
            "description": "User is member of group",
        }
        assert calls[1]["dependency_type"] == "uses"
        assert (calls[1]["source_entity_id"], calls[1]["target_entity_id"]) == (
            100,
            300,
        )
        assert calls[1]["attributes"] == {"sessions_count": 3, "bytes_transferred": 99}

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "usage",
        [FakeResponse({}, 500), TimeoutError("slow")],
        ids=["server-error", "timeout"],
    )
    async def test_usage_report_failure_keeps_membership_edges(
        self, primed: IBossConnector, usage: Any
    ) -> None:
        """A failing usage report still leaves the membership edges counted."""
        primed.http_client.routes["/api/v1/reports/application-usage"] = usage
        assert await primed._sync_relationships() == 1

    @pytest.mark.asyncio
    async def test_dependency_write_failure_is_contained(
        self, primed: IBossConnector
    ) -> None:
        """An Elder failure while writing edges is logged and returns the partial count."""
        primed.elder_client.get_or_create_dependency.side_effect = RuntimeError("x")
        assert await primed._sync_relationships() == 0
        primed.logger.error.assert_called_once()


class TestSyncAndHealth:
    """End-to-end sync and health check."""

    @pytest.mark.asyncio
    async def test_full_sync_is_tenant_scoped(
        self, connector: IBossConnector, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A full sync writes everything under the tenant-named organization."""
        monkeypatch.setattr(settings, "iboss_tenant_id", "tenant-7")
        monkeypatch.setattr(settings, "create_missing_organizations", True)
        result = await connector.sync()

        assert result.errors == []
        assert result.entities_created == 12 and result.entities_updated == 0
        org = connector.elder_client.create_organization.call_args.args[0]
        assert org.name == "iBoss: tenant-7"
        assert {
            e.organization_id for e in created_entities(connector.elder_client)
        } == {501}
        assert connector.elder_client.get_or_create_dependency.await_count == 2

    @pytest.mark.asyncio
    async def test_sync_without_tenant_uses_default_label(
        self, connector: IBossConnector, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Without a tenant id the organization is labelled 'Default'."""
        monkeypatch.setattr(settings, "iboss_tenant_id", None)
        monkeypatch.setattr(settings, "create_missing_organizations", True)
        await connector.sync()
        org = connector.elder_client.create_organization.call_args.args[0]
        assert org.name == "iBoss: Default"

    @pytest.mark.asyncio
    async def test_sync_captures_org_failure(self, connector: IBossConnector) -> None:
        """An organization lookup failure is reported in the result, not raised."""
        connector.elder_client.list_organizations.side_effect = RuntimeError("down")
        result = await connector.sync()
        assert result.errors == ["iBoss sync failed: down"]

    @pytest.mark.asyncio
    async def test_health_check_states(self, connector: IBossConnector) -> None:
        """Health reflects the probe status and tolerates transport errors."""
        assert await connector.health_check() is True
        connector.http_client.routes["/api/v1/health"] = FakeResponse({}, 503)
        assert await connector.health_check() is False
        connector.http_client.routes["/api/v1/health"] = TimeoutError("t")
        assert await connector.health_check() is False
        connector.http_client = None
        assert await connector.health_check() is False
