"""HTTP-level tests for tenant-scoped webhook CRUD (Plan 05)."""

import json
from datetime import datetime, timezone
from typing import Any
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from quart import current_app


def _create_identity(db: Any, tenant_id: int) -> int:
    """Insert a minimal identity row for tenant_id and return its id."""
    now = datetime.now(timezone.utc)
    return db.identities.insert(
        identity_type="human",
        username=f"user-{uuid4().hex[:8]}",
        email=f"user-{uuid4().hex[:8]}@example.com",
        tenant_id=tenant_id,
        auth_provider="local",
        is_active=True,
        is_superuser=False,
        mfa_enabled=False,
        must_change_password=False,
        portal_role="observer",
        created_at=now,
        updated_at=now,
    )


@pytest.mark.asyncio
@patch("apps.api.auth.decorators.get_current_user")
async def test_create_and_get_webhook(mock_get_user, async_client, generate_token):
    mock_get_user.return_value = MagicMock(id=1, is_superuser=True)
    token = generate_token(
        tenant_id=1, scopes=["webhooks_alerting:admin", "webhooks_alerting:read"]
    )

    resp = await async_client.post(
        "/api/v1/webhooks",
        json={
            "name": "Support assignments",
            "url": "https://hooks.example.com/support",
            "events": ["issue.assigned"],
            "filter_issue_type": "support",
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 201, (await resp.get_data()).decode()[:300]
    created = json.loads(await resp.get_data())
    assert created["village_id"] is not None
    assert created["filter_issue_type"] == "SUPPORT"

    get_resp = await async_client.get(
        f"/api/v1/webhooks/{created['id']}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert get_resp.status_code == 200


@pytest.mark.asyncio
@patch("apps.api.auth.decorators.get_current_user")
async def test_webhook_not_visible_cross_tenant(
    mock_get_user, async_client, generate_token
):
    mock_get_user.return_value = MagicMock(id=1, is_superuser=True)
    token_t1 = generate_token(tenant_id=1, scopes=["webhooks_alerting:admin"])
    token_t2 = generate_token(tenant_id=2, scopes=["webhooks_alerting:read"])

    resp = await async_client.post(
        "/api/v1/webhooks",
        json={
            "name": "Tenant 1 only",
            "url": "https://hooks.example.com/t1",
            "events": ["issue.assigned"],
        },
        headers={"Authorization": f"Bearer {token_t1}"},
    )
    webhook_id = json.loads(await resp.get_data())["id"]

    get_resp = await async_client.get(
        f"/api/v1/webhooks/{webhook_id}",
        headers={"Authorization": f"Bearer {token_t2}"},
    )
    assert get_resp.status_code == 404


@pytest.mark.asyncio
@patch("apps.api.auth.decorators.get_current_user")
async def test_create_webhook_missing_required_field(
    mock_get_user, async_client, generate_token
):
    mock_get_user.return_value = MagicMock(id=1, is_superuser=True)
    token = generate_token(tenant_id=1, scopes=["webhooks_alerting:admin"])

    resp = await async_client.post(
        "/api/v1/webhooks",
        json={"name": "Missing url and events"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
@patch("apps.api.auth.decorators.get_current_user")
async def test_webhook_tenant_isolation_list_update_delete(
    mock_get_user, async_client, generate_token
):
    """Regression: a webhook owned by tenant 1 is invisible to tenant 2 in
    list/update/delete, not just get — every route filters by tenant_id."""
    mock_get_user.return_value = MagicMock(id=1, is_superuser=True)
    token_t1 = generate_token(tenant_id=1, scopes=["webhooks_alerting:admin"])
    token_t2 = generate_token(
        tenant_id=2, scopes=["webhooks_alerting:admin", "webhooks_alerting:read"]
    )

    resp = await async_client.post(
        "/api/v1/webhooks",
        json={
            "name": "Tenant 1 isolation check",
            "url": "https://hooks.example.com/t1-isolation",
            "events": ["issue.assigned"],
        },
        headers={"Authorization": f"Bearer {token_t1}"},
    )
    webhook_id = json.loads(await resp.get_data())["id"]

    list_resp = await async_client.get(
        "/api/v1/webhooks", headers={"Authorization": f"Bearer {token_t2}"}
    )
    assert list_resp.status_code == 200
    listed_ids = [w["id"] for w in json.loads(await list_resp.get_data())["webhooks"]]
    assert webhook_id not in listed_ids

    update_resp = await async_client.put(
        f"/api/v1/webhooks/{webhook_id}",
        json={"name": "Hijacked"},
        headers={"Authorization": f"Bearer {token_t2}"},
    )
    assert update_resp.status_code == 404

    delete_resp = await async_client.delete(
        f"/api/v1/webhooks/{webhook_id}",
        headers={"Authorization": f"Bearer {token_t2}"},
    )
    assert delete_resp.status_code == 404


@pytest.mark.asyncio
@patch("apps.api.auth.decorators.get_current_user")
async def test_create_webhook_rejects_cross_tenant_filter_assignee_id(
    mock_get_user, async_client, generate_token, app
):
    """Regression (security review): `filter_assignee_id` (type `identity`)
    must belong to the caller's own tenant — an identity in a DIFFERENT
    tenant is rejected with 400, mirroring
    test_intake_forms.py::test_create_form_rejects_cross_tenant_assignee_identity."""
    mock_get_user.return_value = MagicMock(id=1, is_superuser=True)

    async with app.app_context():
        db = current_app.db
        tenant2_id = db.tenants.insert(
            name="Cross Tenant Webhook Assignee",
            slug=f"idor-webhook-assignee-{uuid4().hex[:8]}",
            is_active=True,
        )
        db.commit()
        foreign_identity_id = _create_identity(db, tenant2_id)
        db.commit()

    token = generate_token(tenant_id=1, scopes=["webhooks_alerting:admin"])
    resp = await async_client.post(
        "/api/v1/webhooks",
        json={
            "name": "Cross tenant assignee filter",
            "url": "https://hooks.example.com/idor",
            "events": ["issue.assigned"],
            "filter_assignee_type": "identity",
            "filter_assignee_id": foreign_identity_id,
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 400, (await resp.get_data()).decode()[:300]


@pytest.mark.asyncio
@patch("apps.api.auth.decorators.get_current_user")
async def test_update_webhook_rejects_cross_tenant_filter_assignee_id_without_type(
    mock_get_user, async_client, generate_token, app
):
    """Regression (security review, HIGH): PUT-ing only `filter_assignee_id`
    (no `filter_assignee_type`) against a webhook whose stored type is None
    must NOT silently persist a foreign-tenant id. Previously
    `_validate_filter_assignee_ref` only checked recognized types
    ("identity"/"org_unit") and let a (None, <id>) pair through unchecked —
    WebhookService.update_webhook has no pairing check of its own, so the id
    persisted with no type to scope the in-tenant check against."""
    mock_get_user.return_value = MagicMock(id=1, is_superuser=True)

    async with app.app_context():
        db = current_app.db
        tenant2_id = db.tenants.insert(
            name="Cross Tenant Webhook Update Assignee",
            slug=f"idor-webhook-update-{uuid4().hex[:8]}",
            is_active=True,
        )
        db.commit()
        foreign_identity_id = _create_identity(db, tenant2_id)
        db.commit()

    token = generate_token(
        tenant_id=1, scopes=["webhooks_alerting:admin", "webhooks_alerting:read"]
    )

    create_resp = await async_client.post(
        "/api/v1/webhooks",
        json={
            "name": "No filters yet",
            "url": "https://hooks.example.com/put-idor",
            "events": ["issue.assigned"],
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    webhook_id = json.loads(await create_resp.get_data())["id"]

    update_resp = await async_client.put(
        f"/api/v1/webhooks/{webhook_id}",
        json={"filter_assignee_id": foreign_identity_id},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert update_resp.status_code == 400, (await update_resp.get_data()).decode()[:300]

    get_resp = await async_client.get(
        f"/api/v1/webhooks/{webhook_id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    unchanged = json.loads(await get_resp.get_data())
    assert unchanged["filter_assignee_id"] is None
    assert unchanged["filter_assignee_type"] is None


@pytest.mark.asyncio
@patch("apps.api.auth.decorators.get_current_user")
async def test_update_webhook_requires_filter_assignee_pair(
    mock_get_user, async_client, generate_token, app
):
    """filter_assignee_type and filter_assignee_id must be set together on
    PUT, same as create — setting only one half is rejected with 400."""
    mock_get_user.return_value = MagicMock(id=1, is_superuser=True)

    async with app.app_context():
        db = current_app.db
        in_tenant_identity_id = _create_identity(db, 1)
        db.commit()

    token = generate_token(tenant_id=1, scopes=["webhooks_alerting:admin"])

    create_resp = await async_client.post(
        "/api/v1/webhooks",
        json={
            "name": "Pairing check",
            "url": "https://hooks.example.com/pairing",
            "events": ["issue.assigned"],
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    webhook_id = json.loads(await create_resp.get_data())["id"]

    type_only_resp = await async_client.put(
        f"/api/v1/webhooks/{webhook_id}",
        json={"filter_assignee_type": "identity"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert type_only_resp.status_code == 400

    id_only_resp = await async_client.put(
        f"/api/v1/webhooks/{webhook_id}",
        json={"filter_assignee_id": in_tenant_identity_id},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert id_only_resp.status_code == 400


@pytest.mark.asyncio
@patch("apps.api.auth.decorators.get_current_user")
async def test_update_webhook_accepts_valid_in_tenant_filter_assignee_pair(
    mock_get_user, async_client, generate_token, app
):
    """A valid, in-tenant (filter_assignee_type, filter_assignee_id) pair on
    PUT is accepted and persists."""
    mock_get_user.return_value = MagicMock(id=1, is_superuser=True)

    async with app.app_context():
        db = current_app.db
        in_tenant_identity_id = _create_identity(db, 1)
        db.commit()

    token = generate_token(
        tenant_id=1, scopes=["webhooks_alerting:admin", "webhooks_alerting:read"]
    )

    create_resp = await async_client.post(
        "/api/v1/webhooks",
        json={
            "name": "Valid pair",
            "url": "https://hooks.example.com/valid-pair",
            "events": ["issue.assigned"],
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    webhook_id = json.loads(await create_resp.get_data())["id"]

    update_resp = await async_client.put(
        f"/api/v1/webhooks/{webhook_id}",
        json={
            "filter_assignee_type": "identity",
            "filter_assignee_id": in_tenant_identity_id,
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert update_resp.status_code == 200, (await update_resp.get_data()).decode()[:300]
    updated = json.loads(await update_resp.get_data())
    assert updated["filter_assignee_type"] == "identity"
    assert updated["filter_assignee_id"] == in_tenant_identity_id

    get_resp = await async_client.get(
        f"/api/v1/webhooks/{webhook_id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    persisted = json.loads(await get_resp.get_data())
    assert persisted["filter_assignee_type"] == "identity"
    assert persisted["filter_assignee_id"] == in_tenant_identity_id
