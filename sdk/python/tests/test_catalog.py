"""Unit tests for elder_sdk.catalog and bundled api_catalog.json."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from elder_sdk import AsyncElderClient, ElderClient
from elder_sdk.catalog import ApiCatalog, ApiOperation, _prepare_operation_call
from elder_sdk.exceptions import ElderCatalogError, ElderValidationError


def test_default_catalog_integrity_and_coverage() -> None:
    catalog = ApiCatalog.load_default()
    assert len(catalog) >= 650
    services = catalog.services()
    assert len(services) >= 65

    expected_services = {
        "auth",
        "organizations",
        "entities",
        "entity_types",
        "issues",
        "projects",
        "milestones",
        "labels",
        "metadata",
        "documents",
        "collections",
        "pages",
        "diagrams",
        "diagram_collections",
        "diagram_libraries",
        "diagram_storage",
        "diagram_templates",
        "streams",
        "flows",
        "helpdesk",
        "ipam",
        "sbom",
        "vulnerabilities",
        "services",
        "software",
        "license_policies",
        "secrets",
        "builtin_secrets",
        "keys",
        "certificates",
        "discovery",
        "sync",
        "iam",
        "google_workspace",
        "on_call",
        "webhooks",
        "access_reviews",
        "group_membership",
        "resource_roles",
        "search",
        "licensing",
        "village_id",
        "lookup",
        "refs",
        "tenants",
        "identities",
        "users",
        "profile",
        "portal_auth",
        "audit",
        "audit_enterprise",
        "backup",
        "privacy",
        "privacy_admin",
        "data_stores",
        "dependencies",
        "networking",
        "geo",
        "graph",
        "costs",
        "ai",
        "sso",
        "api_keys",
        "modules",
        "tenant_modules",
        "logs",
        "status",
    }
    assert expected_services.issubset(set(services))

    # Multi-parameter routes fixed from quart-schema must be intact
    assert (
        catalog.get("sync", "receive_sync_webhook").path
        == "/api/v1/sync/webhooks/{platform}/{config_id}"
    )
    assert (
        catalog.get("dependencies", "get_resource_dependencies").path
        == "/api/v1/dependencies/resource/{resource_type}/{resource_id}"
    )
    assert (
        catalog.get("pages", "attach_collection").path
        == "/api/v1/pages/{slug}/collections/{collection_id}"
    )
    assert (
        catalog.get("costs", "get_resource_costs").path
        == "/api/v1/costs/{resource_type}/{resource_id}"
    )
    assert catalog.get("licensing", "status").path == "/api/v1/licensing/status"
    assert catalog.get("village_id", "resolve").path == "/api/v1/id/{village_id}"
    assert catalog.get("village_id", "resolve_legacy").path == "/id/{village_id}"
    assert catalog.get("search", "run_ai_research").path == "/api/v1/search/ai-research"
    assert catalog.get("status", "healthz").path == "/healthz"


def test_catalog_lookup_contains_and_errors(tmp_path: Path) -> None:
    catalog = ApiCatalog.load_default()
    assert "issues" in catalog
    assert ("issues", "list") in catalog
    assert ("issues", "nonexistent") not in catalog
    assert 123 not in catalog
    assert len(catalog.operations("status")) == 2
    assert len(catalog.operations()) == len(catalog)

    with pytest.raises(ElderCatalogError, match="Unknown Elder service"):
        catalog.operations("no_such_service")
    with pytest.raises(ElderCatalogError, match="Unknown Elder service"):
        catalog.get("no_such_service", "list")
    with pytest.raises(ElderCatalogError, match="Unknown operation"):
        catalog.get("issues", "no_such_op")

    sample = {
        "version": "1.2.3",
        "operations": [
            {
                "service": "demo",
                "operation": "ping",
                "method": "get",
                "path": "/api/v1/ping",
                "path_params": [],
                "summary": "Ping",
                "has_body": False,
            }
        ],
    }
    p = tmp_path / "cat.json"
    p.write_text(json.dumps(sample), encoding="utf-8")
    loaded = ApiCatalog.from_file(p)
    assert loaded.version == "1.2.3"
    assert loaded.get("demo", "ping").method == "GET"

    with pytest.raises(ElderCatalogError):
        ApiCatalog.from_dict({"operations": "bad"})
    with pytest.raises(ElderCatalogError):
        ApiCatalog.from_dict({"operations": ["bad"]})
    dup_op = ApiOperation("demo", "ping", "GET", "/p", (), "", False)
    with pytest.raises(ElderCatalogError, match="Duplicate"):
        ApiCatalog([dup_op, dup_op])


def test_api_operation_format_path_and_validation() -> None:
    op = ApiOperation(
        service="sync",
        operation="receive_sync_webhook",
        method="POST",
        path="/api/v1/sync/webhooks/{platform}/{config_id}",
        path_params=("platform", "config_id"),
        summary="Webhook",
        has_body=True,
    )
    assert op.format_path({"platform": "github", "config_id": 7}) == (
        "/api/v1/sync/webhooks/github/7"
    )
    # Regular path param escapes '/'
    assert op.format_path(platform="a/b", config_id=1) == (
        "/api/v1/sync/webhooks/a%2Fb/1"
    )

    secret_op = ApiOperation(
        service="builtin_secrets",
        operation="get",
        method="GET",
        path="/api/v1/builtin-secrets/{secret_path}",
        path_params=("secret_path",),
        summary="Get secret",
        has_body=False,
    )
    assert secret_op.format_path(secret_path="/prod/db/pass word") == (
        "/api/v1/builtin-secrets/prod/db/pass%20word"
    )

    with pytest.raises(ElderCatalogError, match="Missing required path parameter"):
        op.format_path(platform="github")
    with pytest.raises(ElderCatalogError, match="cannot be None"):
        op.format_path(platform="github", config_id=None)
    with pytest.raises(ElderCatalogError, match="cannot be empty"):
        op.format_path(platform="github", config_id="")
    with pytest.raises(ElderCatalogError, match="cannot be empty"):
        secret_op.format_path(secret_path="///")


def test_prepare_operation_call_variants() -> None:
    update_op = ApiOperation(
        service="issues",
        operation="update",
        method="PATCH",
        path="/api/v1/issues/{id}",
        path_params=("id",),
        summary="Update issue",
        has_body=True,
    )
    assert _prepare_operation_call(update_op, (42, {"title": "Hi"}), {}) == (
        "PATCH",
        "/api/v1/issues/42",
        None,
        {"title": "Hi"},
        None,
    )
    assert _prepare_operation_call(
        update_op,
        (),
        {"path_params": {"id": 5}, "body": {"status": "closed"}, "notify": True},
    ) == ("PATCH", "/api/v1/issues/5", {"notify": True}, {"status": "closed"}, None)

    assert _prepare_operation_call(
        update_op, (), {"id": 9, "title": "Updated", "headers": {"X-Test": "1"}}
    ) == ("PATCH", "/api/v1/issues/9", None, {"title": "Updated"}, {"X-Test": "1"})

    with pytest.raises(ElderValidationError):
        _prepare_operation_call(update_op, (1,), {"body": {}, "json": {}})
    with pytest.raises(ElderCatalogError):
        _prepare_operation_call(update_op, (1,), {"path_params": ["bad"]})
    with pytest.raises(ElderCatalogError):
        _prepare_operation_call(update_op, (1, 2, 3), {})


def test_service_proxy_sync_and_async_dispatch() -> None:
    captured: list[tuple[str, str, str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(
            (
                request.method,
                request.url.path,
                request.url.query.decode(),
                json.loads(request.content.decode()) if request.content else None,
            )
        )
        return httpx.Response(200, json={"ok": True})

    transport = httpx.MockTransport(handler)
    with ElderClient(
        base_url="https://elder.example.com",
        token="dummy-token-placeholder",
        transport=transport,
    ) as client:
        proxy = client.service("issues")
        assert proxy.service_name == "issues"
        assert "list" in proxy.operations()
        assert "issues" in repr(proxy)
        assert "list" in dir(proxy)
        assert proxy.describe("list").method == "GET"

        proxy.invoke("list", params={"status": "open"})
        proxy.create(title="Outage", organization_id=1)
        client.service("pages").attach_collection("welcome", 9)

        with pytest.raises(AttributeError):
            _ = proxy._private
        with pytest.raises(AttributeError):
            _ = proxy.nonexistent_operation

    assert captured == [
        ("GET", "/api/v1/issues", "status=open", None),
        ("POST", "/api/v1/issues", "", {"title": "Outage", "organization_id": 1}),
        ("POST", "/api/v1/pages/welcome/collections/9", "", None),
    ]


async def test_async_service_proxy_dispatch() -> None:
    captured: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append((request.method, request.url.path))
        return httpx.Response(200, json={"ok": True})

    transport = httpx.MockTransport(handler)
    async with AsyncElderClient(
        base_url="https://elder.example.com",
        token="dummy-token-placeholder",
        transport=transport,
    ) as client:
        proxy = client.service("diagram_collections")
        assert "list" in dir(proxy)
        await proxy.list()
        await client.invoke("costs", "get_resource_costs", "entity", 12)
        with pytest.raises(AttributeError):
            _ = proxy._hidden
        with pytest.raises(AttributeError):
            _ = proxy.no_such_op

    assert captured == [
        ("GET", "/api/v1/diagram-collections"),
        ("GET", "/api/v1/costs/entity/12"),
    ]
