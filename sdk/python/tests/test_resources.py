"""Unit tests for all 50 typed resource facades (sync & async)."""

from __future__ import annotations

import json

import httpx
import pytest

from elder_sdk import AsyncElderClient, ElderClient
from elder_sdk.catalog import ApiCatalog
from elder_sdk.resources import (
    ASYNC_RESOURCE_CLASSES,
    SYNC_RESOURCE_CLASSES,
    SyncResourceBase,
    _CatalogMethod,
    _ResourceBase,
)

EXPECTED_FACADE_NAMES = {
    "auth",
    "organizations",
    "entities",
    "issues",
    "documents",
    "pages",
    "diagrams",
    "streams",
    "flows",
    "helpdesk",
    "ipam",
    "sbom",
    "vulnerabilities",
    "secrets",
    "keys",
    "certificates",
    "discovery",
    "sync",
    "iam",
    "google_workspace",
    "on_call",
    "webhooks",
    "access_reviews",
    "search",
    "licensing",
    "village_id",
    "tenants",
    "identities",
    "users",
    "audit",
    "backup",
    "privacy",
    "data_stores",
    "dependencies",
    "networking",
    "geo",
    "graph",
    "services",
    "software",
    "projects",
    "milestones",
    "labels",
    "metadata",
    "costs",
    "ai",
    "sso",
    "api_keys",
    "modules",
    "runs",
    "workers",
}

DIRECT_ONLY_FACADES = {"workers"}


def test_all_50_facades_registered_and_bound_to_valid_catalog_ops() -> None:
    assert set(SYNC_RESOURCE_CLASSES.keys()) == EXPECTED_FACADE_NAMES
    assert set(ASYNC_RESOURCE_CLASSES.keys()) == EXPECTED_FACADE_NAMES

    catalog = ApiCatalog.load_default()
    for name, cls in SYNC_RESOURCE_CLASSES.items():
        async_cls = ASYNC_RESOURCE_CLASSES[name]
        assert cls.SERVICE_NAME == name
        assert async_cls.SERVICE_NAME == name
        if name not in DIRECT_ONLY_FACADES:
            assert catalog.has_service(name)

        # Every _CatalogMethod descriptor on both sync and async classes must
        # correspond to a real operation in api_catalog.json
        for target_cls in (cls, async_cls):
            for base in target_cls.__mro__:
                for attr_name, attr_val in base.__dict__.items():
                    if isinstance(attr_val, _CatalogMethod):
                        op = catalog.get(name, attr_val.operation)
                        assert op.service == name
                        # Class-level access returns the descriptor itself
                        assert getattr(target_cls, attr_name) is attr_val


def test_resource_base_validation_and_introspection() -> None:
    transport = httpx.MockTransport(lambda req: httpx.Response(200, json={"ok": True}))
    with ElderClient(
        base_url="https://elder.example.com", transport=transport
    ) as client:
        with pytest.raises(ValueError, match="SERVICE_NAME"):
            SyncResourceBase(client)
        with pytest.raises(NotImplementedError):
            _ResourceBase._build_proxy(client, "issues")

        issues = client.issues
        assert issues.service_name == "issues"
        assert "list" in issues.operations()
        assert "list_issue_comments" in dir(issues)
        assert "IssuesResource" in repr(issues)

        with pytest.raises(AttributeError):
            _ = issues._secret_attr
        with pytest.raises(AttributeError):
            _ = issues.definitely_missing_op


def test_sync_resource_facades_execution() -> None:
    recorded: list[tuple[str, str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode()) if request.content else None
        recorded.append((request.method, request.url.path, body))
        if request.url.path == "/api/v1/auth/login":
            return httpx.Response(
                200, json={"access_token": "tok-res-1", "refresh_token": "ref-res-1"}
            )
        if request.url.path == "/api/v1/auth/refresh":
            return httpx.Response(200, json={"access_token": "tok-res-2"})
        if request.url.path == "/api/v1/organizations":
            return httpx.Response(200, json={"items": [{"id": 1}], "total": 1})
        return httpx.Response(200, json={"ok": True})

    with ElderClient(
        base_url="https://elder.example.com",
        transport=httpx.MockTransport(handler),
    ) as client:
        # Cached facade lookup
        assert client.organizations is client.organizations

        client.auth.login("admin@example.com", "pw")
        client.auth.refresh_token()
        client.auth.me()

        orgs = list(client.organizations.iter_all(per_page=10))
        assert orgs == [{"id": 1}]
        client.organizations.create(name="Acme")
        client.organizations.get(1)
        client.organizations.update(1, name="Acme 2")
        client.organizations.delete(1)
        client.organizations.tree_stats(1)

        client.entities.list(entity_type="compute")
        client.entities.update_attributes(2, attributes={"env": "prod"})
        client.issues.list_comments(5)
        client.issues.create_comment(5, content="Looks good")
        client.documents.publish(3)
        client.pages.attach_collection("runbook", 7)
        client.diagrams.export_svg(4)
        client.streams.execute(8, inputs={"x": 1})
        client.flows.enable("pipe-1")
        client.helpdesk.list_tickets(status="open")
        client.ipam.list_prefixes()
        client.sbom.list_components()
        client.vulnerabilities.dashboard()
        client.secrets.unmask(10)
        client.keys.rotate(11)
        client.certificates.list()
        client.discovery.list_jobs(provider="fleetdm")
        client.sync.trigger_config(12, direction="bidirectional")
        client.iam.list_providers()
        client.google_workspace.list_providers()
        client.on_call.list_rotations()
        client.webhooks.test(13)
        client.access_reviews.my_reviews()
        client.search.ai_research("find unpatched hosts", max_steps=2)
        client.search.entities(q="web")
        client.licensing.status()
        client.village_id.resolve("00000001-0000000000000001")
        client.tenants.stats(1)
        client.identities.list_groups()
        client.users.list()
        client.audit.list_retention_policies()
        client.backup.stats()
        client.privacy.export_my_data()
        client.data_stores.list_labels(1)
        client.dependencies.for_resource("entity", 1)
        client.networking.topology_graph()
        client.geo.list_regions()
        client.graph.analyze()
        client.services.endpoints()
        client.software.list()
        client.projects.list()
        client.milestones.issues(1)
        client.labels.list()
        client.metadata.get_entity(1)
        client.costs.get_resource_costs("entity", 1)
        client.ai.list()
        client.sso.list_idps()
        client.api_keys.list()
        client.modules.list()
        client.runs.list(runtime="python")
        client.workers.list(worker_type="runworker")

        # Also test non-CRUD iter_operation
        list(client.discovery.iter_operation("list_discovery_jobs", provider="aws"))

    paths = [p for _, p, _ in recorded]
    assert "/api/v1/admin/workers" in paths
    assert "/api/v1/auth/login" in paths
    assert "/api/v1/organizations/1/tree-stats" in paths
    assert "/api/v1/discovery/jobs" in paths
    assert "/api/v1/sync/configs/12/trigger" in paths
    assert "/api/v1/search/ai-research" in paths
    assert "/api/v1/licensing/status" in paths
    assert "/api/v1/id/00000001-0000000000000001" in paths
    assert "/api/v1/dependencies/resource/entity/1" in paths
    assert "/api/v1/pages/runbook/collections/7" in paths
    assert "/api/v1/costs/entity/1" in paths


async def test_async_resource_facades_execution() -> None:
    recorded: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        recorded.append((request.method, request.url.path))
        if request.url.path == "/api/v1/auth/login":
            return httpx.Response(
                200, json={"access_token": "atok-1", "refresh_token": "aref-1"}
            )
        if request.url.path == "/api/v1/auth/refresh":
            return httpx.Response(200, json={"access_token": "atok-2"})
        if request.url.path in ("/api/v1/issues", "/api/v1/discovery/jobs"):
            return httpx.Response(200, json={"items": [{"id": 99}], "total": 1})
        return httpx.Response(200, json={"ok": True})

    async with AsyncElderClient(
        base_url="https://elder.example.com",
        transport=httpx.MockTransport(handler),
    ) as client:
        assert client.issues is client.issues
        await client.auth.login(username="admin@example.com", password="pw")
        await client.auth.refresh_token()
        await client.auth.me()

        issues = [item async for item in client.issues.iter_all(status="open")]
        assert issues == [{"id": 99}]

        jobs = [
            item
            async for item in client.discovery.iter_operation(
                "list_discovery_jobs", provider="fleetdm"
            )
        ]
        assert jobs == [{"id": 99}]

        await client.search.ai_research("count critical vulns")
        await client.licensing.status()
        await client.village_id.resolve("00000001-00000000000000cc")
        await client.sync.status()
        await client.helpdesk.dashboard_stats()
        await client.sbom.list_scans()
        await client.modules.list()
        await client.runs.list(runtime="python")
        await client.workers.list(worker_type="runworker")

    assert ("POST", "/api/v1/auth/login") in recorded
    assert ("GET", "/api/v1/admin/workers") in recorded
    assert ("POST", "/api/v1/auth/refresh") in recorded
    assert ("GET", "/api/v1/issues") in recorded
    assert ("GET", "/api/v1/discovery/jobs") in recorded
    assert ("POST", "/api/v1/search/ai-research") in recorded
    assert ("GET", "/api/v1/licensing/status") in recorded
    assert ("GET", "/api/v1/id/00000001-00000000000000cc") in recorded
