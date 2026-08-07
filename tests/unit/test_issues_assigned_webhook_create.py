"""issue.assigned must fire when an issue is created with an assignee, and
must NOT fire when created without one."""

import uuid
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest
from quart import current_app


def _insert_webhook(db, tenant_id=1):
    # village_id has a unique DB constraint (migration 037) and tests in this
    # module share DB state across the session (no per-test rollback/truncate
    # in conftest.py), so each call must mint its own value rather than reuse
    # a fixed literal — a second insert with the same value would otherwise
    # collide with the first test's row.
    now = datetime.now(timezone.utc)
    webhook_id = db.webhooks.insert(
        tenant_id=tenant_id,
        village_id=f"{tenant_id:08x}-{uuid.uuid4().hex[:16]}",
        name="Assign watcher",
        url="https://hooks.example.com/assign",
        events=["issue.assigned"],
        is_active=True,
        created_at=now,
        updated_at=now,
    )
    db.commit()
    return webhook_id


@pytest.mark.asyncio
@patch("apps.api.services.webhooks.assignment.requests.post")
@patch("apps.api.auth.decorators.get_current_user")
async def test_create_issue_with_assignee_fires_webhook(
    mock_get_user, mock_post, async_client, generate_token, app
):
    mock_get_user.return_value = MagicMock(id=1, is_superuser=True)
    mock_post.return_value = MagicMock(status_code=200, text="ok")
    token = generate_token(tenant_id=1, scopes=["issues:write"])

    async with app.app_context():
        db = current_app.db
        now = datetime.now(timezone.utc)
        org_id = db.organizations.insert(
            name="Org", tenant_id=1, created_at=now, updated_at=now
        )
        db.commit()
        _insert_webhook(db)

    resp = await async_client.post(
        "/api/v1/issues",
        json={
            "title": "New ticket",
            "organization_id": org_id,
            "assignee_id": 1,
            "assignee_type": "identity",
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 201, (await resp.get_data()).decode()[:300]

    # asyncio.create_task fire-and-forget: give the event loop a tick.
    import asyncio

    await asyncio.sleep(0.1)

    async with app.app_context():
        db = current_app.db
        delivery = (
            db(db.webhook_deliveries.event_type == "issue.assigned").select().first()
        )
        assert delivery is not None


@pytest.mark.asyncio
@patch("apps.api.services.webhooks.assignment.requests.post")
@patch("apps.api.auth.decorators.get_current_user")
async def test_create_issue_without_assignee_does_not_fire(
    mock_get_user, mock_post, async_client, generate_token, app
):
    mock_get_user.return_value = MagicMock(id=1, is_superuser=True)
    token = generate_token(tenant_id=1, scopes=["issues:write"])

    async with app.app_context():
        db = current_app.db
        now = datetime.now(timezone.utc)
        org_id = db.organizations.insert(
            name="Org2", tenant_id=1, created_at=now, updated_at=now
        )
        db.commit()
        _insert_webhook(db)

    resp = await async_client.post(
        "/api/v1/issues",
        json={"title": "Unassigned ticket", "organization_id": org_id},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 201

    import asyncio

    await asyncio.sleep(0.1)
    mock_post.assert_not_called()
