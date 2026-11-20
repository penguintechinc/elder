"""Unit tests for Inbound Webhook OAuth2-like Permission Scopes and Actions."""

import importlib
import json
import tempfile
from datetime import UTC, datetime
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from penguin_dal import DAL
from quart import Quart, g, request
from sqlalchemy import create_engine

from apps.api.models.base import Base
from apps.api.modules import CORE_MODELS, MODULES
from apps.api.modules.webhooks_alerting.routes.webhooks import (
    _ensure_inbound_tables,
)
from apps.api.modules.webhooks_alerting.routes.webhooks import (
    bp as webhooks_bp,
)
from apps.api.services.webhooks.inbound_actions import INBOUND_WEBHOOK_SCOPES

_MODELS_IMPORTED = False


def _import_all_models() -> None:
    global _MODELS_IMPORTED
    if _MODELS_IMPORTED:
        return
    model_modules = set(CORE_MODELS)
    for manifest in MODULES:
        for mod in manifest.models_import:
            model_modules.add(mod)
    for mod_path in sorted(model_modules):
        importlib.import_module(mod_path)
    _MODELS_IMPORTED = True


def _seed_tenant_bundle(db: DAL, *, slug_prefix: str) -> dict[str, int]:
    now = datetime.now(UTC)
    uid = uuid4().hex[:8]
    tenant_id = db.tenants.insert(
        name=f"Tenant {slug_prefix}",
        slug=f"{slug_prefix}-{uid}",
        is_active=True,
        created_at=now,
        updated_at=now,
    )
    identity_id = db.identities.insert(
        identity_type="human",
        username=f"user-{slug_prefix}-{uid}",
        email=f"{slug_prefix}-{uid}@example.com",
        tenant_id=tenant_id,
        auth_provider="local",
        is_active=True,
        is_superuser=False,
        mfa_enabled=False,
        must_change_password=False,
        portal_role="admin",
        created_at=now,
        updated_at=now,
    )
    org_id = db.organizations.insert(
        name=f"Org {slug_prefix}",
        slug=f"org-{slug_prefix}-{uid}",
        tenant_id=tenant_id,
        is_active=True,
        created_at=now,
        updated_at=now,
    )
    playbook_id = db.stream_playbooks.insert(
        tenant_id=tenant_id,
        name=f"Playbook {slug_prefix}",
        owner_identity_id=identity_id,
        status="active",
        is_enabled=True,
        is_public=False,
        is_template=False,
        execution_count=0,
        success_count=0,
        failure_count=0,
        created_at=now,
        updated_at=now,
    )
    db.commit()
    return {
        "tenant_id": tenant_id,
        "identity_id": identity_id,
        "org_id": org_id,
        "playbook_id": playbook_id,
    }


@pytest.fixture
def scoped_app():
    """Minimal Quart app with full SQLite schema reflected in PyDAL."""
    _import_all_models()
    with tempfile.NamedTemporaryFile(suffix=".db") as db_file:
        engine = create_engine(f"sqlite:///{db_file.name}")
        Base.metadata.create_all(engine)
        engine.dispose()

        db = DAL(f"sqlite:///{db_file.name}", reflect=True, migrate=False)
        _ensure_inbound_tables(db)

        t1 = _seed_tenant_bundle(db, slug_prefix="t1")
        t2 = _seed_tenant_bundle(db, slug_prefix="t2")

        app = Quart(__name__)
        app.config["TESTING"] = True
        app.db = db
        app.test_seed = {"t1": t1, "t2": t2}

        @app.before_request
        def _inject_test_claims():
            raw_tenant = request.headers.get("X-Test-Tenant-Id")
            raw_scopes = request.headers.get("X-Test-Scopes", "")
            scopes = [s for s in raw_scopes.split(",") if s]
            g.claims = {"tenant": raw_tenant, "scope": " ".join(scopes)}

        app.register_blueprint(webhooks_bp, url_prefix="/api/v1/webhooks")
        yield app


def _admin_headers(tenant_id: int) -> dict[str, str]:
    return {
        "X-Test-Tenant-Id": str(tenant_id),
        "X-Test-Scopes": "webhooks:read,webhooks:write,webhooks_alerting:admin",
    }


def _viewer_headers(tenant_id: int) -> dict[str, str]:
    return {
        "X-Test-Tenant-Id": str(tenant_id),
        "X-Test-Scopes": "webhooks:read",
    }


def _admin_user():
    return MagicMock(id=1, is_superuser=True, portal_role="admin", role="admin")


async def _create_hook(
    client,
    tenant_id: int,
    payload: dict,
) -> tuple[dict, str]:
    with patch("apps.api.auth.decorators.get_current_user", return_value=_admin_user()):
        resp = await client.post(
            "/api/v1/webhooks/inbound",
            json=payload,
            headers=_admin_headers(tenant_id),
        )
    assert resp.status_code == 201, await resp.get_data(as_text=True)
    body = json.loads(await resp.get_data())
    return body["webhook"], body["credentials"]["bearer_token"]


@pytest.mark.asyncio
async def test_list_inbound_scopes_returns_all_16_scopes(scoped_app):
    client = scoped_app.test_client()
    t1 = scoped_app.test_seed["t1"]
    with patch("apps.api.auth.decorators.get_current_user", return_value=_admin_user()):
        resp = await client.get(
            "/api/v1/webhooks/inbound/scopes",
            headers=_viewer_headers(t1["tenant_id"]),
        )
    assert resp.status_code == 200
    body = json.loads(await resp.get_data())
    scopes = body["scopes"]
    assert len(scopes) == 16
    assert len(INBOUND_WEBHOOK_SCOPES) == 16
    returned_names = {entry["scope"] for entry in scopes}
    expected_names = {entry["scope"] for entry in INBOUND_WEBHOOK_SCOPES}
    assert returned_names == expected_names


@pytest.mark.asyncio
async def test_create_defaults_and_stream_id_filter(scoped_app):
    client = scoped_app.test_client()
    t1 = scoped_app.test_seed["t1"]

    hook_plain, _ = await _create_hook(
        client, t1["tenant_id"], {"name": "Log Default Hook"}
    )
    assert hook_plain["scopes"] == ["events:log"]
    assert hook_plain["stream_id"] is None

    hook_playbook, _ = await _create_hook(
        client,
        t1["tenant_id"],
        {"name": "Playbook Default Hook", "stream_id": t1["playbook_id"]},
    )
    assert hook_playbook["scopes"] == ["events:log", "playbooks:trigger"]
    assert hook_playbook["stream_id"] == t1["playbook_id"]

    with patch("apps.api.auth.decorators.get_current_user", return_value=_admin_user()):
        filtered = await client.get(
            f"/api/v1/webhooks/inbound?stream_id={t1['playbook_id']}",
            headers=_viewer_headers(t1["tenant_id"]),
        )
    assert filtered.status_code == 200
    filtered_body = json.loads(await filtered.get_data())
    assert filtered_body["count"] == 1
    assert filtered_body["inbound_webhooks"][0]["id"] == hook_playbook["id"]

    with patch("apps.api.auth.decorators.get_current_user", return_value=_admin_user()):
        bad_filter = await client.get(
            "/api/v1/webhooks/inbound?stream_id=0",
            headers=_viewer_headers(t1["tenant_id"]),
        )
    assert bad_filter.status_code == 400


@pytest.mark.asyncio
async def test_create_and_update_validation_and_cross_tenant_guards(scoped_app):
    client = scoped_app.test_client()
    t1 = scoped_app.test_seed["t1"]
    t2 = scoped_app.test_seed["t2"]

    with patch("apps.api.auth.decorators.get_current_user", return_value=_admin_user()):
        unknown_scope = await client.post(
            "/api/v1/webhooks/inbound",
            json={"name": "Bad Scope", "scopes": ["not:valid"]},
            headers=_admin_headers(t1["tenant_id"]),
        )
        assert unknown_scope.status_code == 400

        empty_scopes = await client.post(
            "/api/v1/webhooks/inbound",
            json={"name": "Empty Scopes", "scopes": []},
            headers=_admin_headers(t1["tenant_id"]),
        )
        assert empty_scopes.status_code == 400

        cross_org = await client.post(
            "/api/v1/webhooks/inbound",
            json={"name": "Cross Org", "organization_id": t2["org_id"]},
            headers=_admin_headers(t1["tenant_id"]),
        )
        assert cross_org.status_code == 404

        cross_stream = await client.post(
            "/api/v1/webhooks/inbound",
            json={"name": "Cross Stream", "stream_id": t2["playbook_id"]},
            headers=_admin_headers(t1["tenant_id"]),
        )
        assert cross_stream.status_code == 404

    hook, _ = await _create_hook(client, t1["tenant_id"], {"name": "Updatable Hook"})
    with patch("apps.api.auth.decorators.get_current_user", return_value=_admin_user()):
        upd_resp = await client.put(
            f"/api/v1/webhooks/inbound/{hook['id']}",
            json={
                "scopes": ["events:log", "issues:create", "playbooks:trigger"],
                "stream_id": t1["playbook_id"],
                "organization_id": t1["org_id"],
            },
            headers=_admin_headers(t1["tenant_id"]),
        )
        assert upd_resp.status_code == 200, await upd_resp.get_data(as_text=True)
        updated = json.loads(await upd_resp.get_data())["webhook"]
        assert updated["scopes"] == ["events:log", "issues:create", "playbooks:trigger"]
        assert updated["stream_id"] == t1["playbook_id"]
        assert updated["organization_id"] == t1["org_id"]

        bad_upd = await client.put(
            f"/api/v1/webhooks/inbound/{hook['id']}",
            json={"scopes": []},
            headers=_admin_headers(t1["tenant_id"]),
        )
        assert bad_upd.status_code == 400


@pytest.mark.asyncio
async def test_ungranted_scope_returns_403_and_records_forbidden_audit_event(
    scoped_app,
):
    client = scoped_app.test_client()
    t1 = scoped_app.test_seed["t1"]

    hook, token = await _create_hook(
        client,
        t1["tenant_id"],
        {"name": "Log Only Hook", "scopes": ["events:log"]},
    )

    resp = await client.post(
        hook["receive_path"],
        json={"action": "issues:create", "data": {"title": "Unauthorized Issue"}},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 403
    body = json.loads(await resp.get_data())
    assert body["required_scope"] == "issues:create"

    db = scoped_app.db
    events = db(db.inbound_webhook_events.inbound_webhook_id == hook["id"]).select()
    assert len(events) == 1
    assert events[0].status == "forbidden"
    assert events[0].action == "issues:create"
    assert "issues:create" in (events[0].action_result or {}).get("error", "")


@pytest.mark.asyncio
async def test_playbook_trigger_plain_explicit_and_pinning_enforcement(scoped_app):
    client = scoped_app.test_client()
    db = scoped_app.db
    t1 = scoped_app.test_seed["t1"]

    now = datetime.now(UTC)
    other_playbook_id = db.stream_playbooks.insert(
        tenant_id=t1["tenant_id"],
        name="Other Playbook",
        owner_identity_id=t1["identity_id"],
        status="active",
        is_enabled=True,
        is_public=False,
        is_template=False,
        execution_count=0,
        success_count=0,
        failure_count=0,
        created_at=now,
        updated_at=now,
    )
    db.commit()

    hook, token = await _create_hook(
        client,
        t1["tenant_id"],
        {
            "name": "Pinned Playbook Hook",
            "stream_id": t1["playbook_id"],
            "scopes": ["events:log", "playbooks:trigger"],
        },
    )

    # 1. Plain payload auto-triggers linked playbook
    plain_resp = await client.post(
        hook["receive_path"],
        json={"alert": "disk_full", "host": "web-01"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert plain_resp.status_code == 202, await plain_resp.get_data(as_text=True)
    plain_body = json.loads(await plain_resp.get_data())
    assert plain_body["status"] == "triggered"
    assert plain_body["action"] == "playbooks:trigger"
    assert plain_body["result"]["playbook_id"] == t1["playbook_id"]

    # 2. Explicit action: "playbooks:trigger" also succeeds
    explicit_resp = await client.post(
        hook["receive_path"],
        json={"action": "playbooks:trigger", "input": {"source": "ci"}},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert explicit_resp.status_code == 202
    explicit_body = json.loads(await explicit_resp.get_data())
    assert explicit_body["result"]["playbook_id"] == t1["playbook_id"]

    exec_rows = db(db.stream_executions.playbook_id == t1["playbook_id"]).select()
    assert len(exec_rows) == 2

    # 3. Attempting to trigger a different stream_id / playbook_id when pinned -> 403
    mismatch_stream_resp = await client.post(
        hook["receive_path"],
        json={"action": "playbooks:trigger", "stream_id": other_playbook_id},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert mismatch_stream_resp.status_code == 403

    mismatch_pb_resp = await client.post(
        hook["receive_path"],
        json={"action": "playbooks:trigger", "playbook_id": other_playbook_id},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert mismatch_pb_resp.status_code == 403


@pytest.mark.asyncio
async def test_issues_create_update_uppercase_enums_and_tenant_isolation(scoped_app):
    client = scoped_app.test_client()
    db = scoped_app.db
    t1 = scoped_app.test_seed["t1"]
    t2 = scoped_app.test_seed["t2"]

    hook_t1, token_t1 = await _create_hook(
        client,
        t1["tenant_id"],
        {
            "name": "Issues T1",
            "organization_id": t1["org_id"],
            "scopes": ["events:log", "issues:create", "issues:update"],
        },
    )
    hook_t2, token_t2 = await _create_hook(
        client,
        t2["tenant_id"],
        {
            "name": "Issues T2",
            "organization_id": t2["org_id"],
            "scopes": ["events:log", "issues:create", "issues:update"],
        },
    )

    create_resp = await client.post(
        hook_t1["receive_path"],
        json={
            "action": "issues:create",
            "data": {
                "title": "High CPU on DB",
                "status": "open",
                "priority": "high",
                "issue_type": "operations",
                "is_incident": True,
                "metadata": {"source": "prometheus"},
            },
        },
        headers={"Authorization": f"Bearer {token_t1}"},
    )
    assert create_resp.status_code == 201, await create_resp.get_data(as_text=True)
    issue_id = json.loads(await create_resp.get_data())["result"]["id"]

    row = db(db.issues.id == issue_id).select().first()
    assert row is not None
    assert row.tenant_id == t1["tenant_id"]
    assert row.status == "OPEN"
    assert row.priority == "HIGH"
    assert row.issue_type == "OPERATIONS"
    assert row.is_incident == 1

    update_resp = await client.post(
        hook_t1["receive_path"],
        json={
            "action": "issues:update",
            "resource_id": issue_id,
            "data": {"status": "in_progress", "priority": "critical"},
        },
        headers={"Authorization": f"Bearer {token_t1}"},
    )
    assert update_resp.status_code == 200
    updated_row = db(db.issues.id == issue_id).select().first()
    assert updated_row.status == "IN_PROGRESS"
    assert updated_row.priority == "CRITICAL"

    # Cross-tenant update returns 404
    cross_upd = await client.post(
        hook_t2["receive_path"],
        json={
            "action": "issues:update",
            "resource_id": issue_id,
            "data": {"status": "closed"},
        },
        headers={"Authorization": f"Bearer {token_t2}"},
    )
    assert cross_upd.status_code == 404


@pytest.mark.asyncio
async def test_vulnerabilities_create_update_by_id_and_cve_and_cross_tenant(scoped_app):
    client = scoped_app.test_client()
    db = scoped_app.db
    t1 = scoped_app.test_seed["t1"]
    t2 = scoped_app.test_seed["t2"]

    hook_t1, token_t1 = await _create_hook(
        client,
        t1["tenant_id"],
        {
            "name": "Vuln T1",
            "scopes": ["vulnerabilities:create", "vulnerabilities:update"],
        },
    )
    hook_t2, token_t2 = await _create_hook(
        client,
        t2["tenant_id"],
        {
            "name": "Vuln T2",
            "scopes": ["vulnerabilities:create", "vulnerabilities:update"],
        },
    )

    create_resp = await client.post(
        hook_t1["receive_path"],
        json={
            "action": "vulnerabilities:create",
            "data": {
                "cve_id": "cve-2026-9001",
                "title": "Remote Code Execution",
                "severity": "critical",
                "cvss_score": 9.8,
            },
        },
        headers={"Authorization": f"Bearer {token_t1}"},
    )
    assert create_resp.status_code == 201, await create_resp.get_data(as_text=True)
    res = json.loads(await create_resp.get_data())["result"]
    vuln_id = res["id"]
    assert res["cve_id"] == "CVE-2026-9001"

    # Update by id
    upd_by_id = await client.post(
        hook_t1["receive_path"],
        json={
            "action": "vulnerabilities:update",
            "id": vuln_id,
            "data": {"severity": "high", "title": "Updated Advisory"},
        },
        headers={"Authorization": f"Bearer {token_t1}"},
    )
    assert upd_by_id.status_code == 200
    assert db(db.vulnerabilities.id == vuln_id).select().first().severity == "HIGH"

    # Update by cve_id
    upd_by_cve = await client.post(
        hook_t1["receive_path"],
        json={
            "action": "vulnerabilities:update",
            "cve_id": "cve-2026-9001",
            "data": {"is_active": False},
        },
        headers={"Authorization": f"Bearer {token_t1}"},
    )
    assert upd_by_cve.status_code == 200
    assert db(db.vulnerabilities.id == vuln_id).select().first().is_active is False

    # Cross-tenant update returns 404
    cross_id = await client.post(
        hook_t2["receive_path"],
        json={
            "action": "vulnerabilities:update",
            "id": vuln_id,
            "data": {"severity": "low"},
        },
        headers={"Authorization": f"Bearer {token_t2}"},
    )
    assert cross_id.status_code == 404

    cross_cve = await client.post(
        hook_t2["receive_path"],
        json={
            "action": "vulnerabilities:update",
            "cve_id": "CVE-2026-9001",
            "data": {"severity": "low"},
        },
        headers={"Authorization": f"Bearer {token_t2}"},
    )
    assert cross_cve.status_code == 404


@pytest.mark.asyncio
async def test_entities_create_update_org_scoped_without_tenant_id_column(scoped_app):
    client = scoped_app.test_client()
    db = scoped_app.db
    t1 = scoped_app.test_seed["t1"]
    t2 = scoped_app.test_seed["t2"]

    hook_t1, token_t1 = await _create_hook(
        client,
        t1["tenant_id"],
        {
            "name": "Entities T1",
            "organization_id": t1["org_id"],
            "scopes": ["entities:create", "entities:update"],
        },
    )
    hook_t2, token_t2 = await _create_hook(
        client,
        t2["tenant_id"],
        {
            "name": "Entities T2",
            "organization_id": t2["org_id"],
            "scopes": ["entities:create", "entities:update"],
        },
    )

    create_resp = await client.post(
        hook_t1["receive_path"],
        json={
            "action": "entities:create",
            "data": {
                "name": "prod-api-01",
                "type": "server",
                "sub_type": "vm",
                "metadata": {"region": "us-central"},
            },
        },
        headers={"Authorization": f"Bearer {token_t1}"},
    )
    assert create_resp.status_code == 201, await create_resp.get_data(as_text=True)
    entity_id = json.loads(await create_resp.get_data())["result"]["id"]

    row = db(db.entities.id == entity_id).select().first()
    assert row.organization_id == t1["org_id"]
    assert row.name == "prod-api-01"

    upd_resp = await client.post(
        hook_t1["receive_path"],
        json={
            "action": "entities:update",
            "resource_id": entity_id,
            "data": {"status": "inactive", "name": "prod-api-01-retired"},
        },
        headers={"Authorization": f"Bearer {token_t1}"},
    )
    assert upd_resp.status_code == 200
    assert db(db.entities.id == entity_id).select().first().status == "inactive"

    cross_resp = await client.post(
        hook_t2["receive_path"],
        json={
            "action": "entities:update",
            "resource_id": entity_id,
            "data": {"status": "active"},
        },
        headers={"Authorization": f"Bearer {token_t2}"},
    )
    assert cross_resp.status_code == 404


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "resource,create_data,update_data,table_name,assert_field,assert_val",
    [
        (
            "data_stores",
            {
                "name": "Customer Warehouse",
                "data_classification": "confidential",
                "storage_type": "data_warehouse",
                "location_region": "us-east-1",
            },
            {"data_classification": "restricted"},
            "data_stores",
            "data_classification",
            "restricted",
        ),
        (
            "services",
            {"name": "billing-api", "port": 8443, "is_public": False},
            {"status": "deprecated", "port": 9443},
            "services",
            "status",
            "deprecated",
        ),
        (
            "software",
            {"name": "nginx", "software_type": "os", "version": "1.27.0"},
            {"version": "1.27.1"},
            "software",
            "version",
            "1.27.1",
        ),
        (
            "ai_resources",
            {
                "name": "Waddle Gateway",
                "provider": "anthropic",
                "model_name": "claude-sonnet",
                "resource_type": "ai_gateway",
            },
            {"status": "inactive"},
            "ai_resources",
            "status",
            "inactive",
        ),
    ],
)
async def test_other_inventory_resources_create_update_and_cross_tenant(
    scoped_app,
    resource,
    create_data,
    update_data,
    table_name,
    assert_field,
    assert_val,
):
    client = scoped_app.test_client()
    db = scoped_app.db
    t1 = scoped_app.test_seed["t1"]
    t2 = scoped_app.test_seed["t2"]

    hook_t1, token_t1 = await _create_hook(
        client,
        t1["tenant_id"],
        {
            "name": f"{resource} T1",
            "organization_id": t1["org_id"],
            "scopes": [f"{resource}:create", f"{resource}:update"],
        },
    )
    hook_t2, token_t2 = await _create_hook(
        client,
        t2["tenant_id"],
        {
            "name": f"{resource} T2",
            "organization_id": t2["org_id"],
            "scopes": [f"{resource}:create", f"{resource}:update"],
        },
    )

    create_resp = await client.post(
        hook_t1["receive_path"],
        json={"action": f"{resource}:create", "data": create_data},
        headers={"Authorization": f"Bearer {token_t1}"},
    )
    assert create_resp.status_code == 201, await create_resp.get_data(as_text=True)
    record_id = json.loads(await create_resp.get_data())["result"]["id"]

    upd_resp = await client.post(
        hook_t1["receive_path"],
        json={
            "action": f"{resource}:update",
            "resource_id": record_id,
            "data": update_data,
        },
        headers={"Authorization": f"Bearer {token_t1}"},
    )
    assert upd_resp.status_code == 200, await upd_resp.get_data(as_text=True)
    table = getattr(db, table_name)
    row = db(table.id == record_id).select().first()
    assert getattr(row, assert_field) == assert_val

    cross_resp = await client.post(
        hook_t2["receive_path"],
        json={
            "action": f"{resource}:update",
            "resource_id": record_id,
            "data": update_data,
        },
        headers={"Authorization": f"Bearer {token_t2}"},
    )
    assert cross_resp.status_code == 404
