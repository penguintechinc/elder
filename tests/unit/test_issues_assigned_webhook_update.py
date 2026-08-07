"""issue.assigned must fire on PATCH only when the assignee actually
changes — not when the same assignee is resent, and not when assignee_id is
absent from the body."""

import asyncio
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
    # collide with another test's row (mirrors
    # test_issues_assigned_webhook_create.py).
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


def _insert_identity(db, tenant_id=1, username="assignee"):
    # update_issue's _resolve_assignee_type validates assignee_id against a
    # real row in db.identities scoped to the caller's tenant (cross-tenant
    # IDOR guard) -- a bare int like 5 with no backing row is rejected with
    # 404 "Assignee not found in tenant" before the diff logic under test
    # ever runs, so every assignee_id used below must be a real identity.
    now = datetime.now(timezone.utc)
    identity_id = db.identities.insert(
        identity_type="human",
        username=f"{username}_{uuid.uuid4().hex[:8]}",
        email=f"{username}_{uuid.uuid4().hex[:8]}@example.com",
        tenant_id=tenant_id,
        auth_provider="local",
        is_active=True,
        is_superuser=False,
        mfa_enabled=False,
        must_change_password=False,
        portal_role="viewer",
        created_at=now,
        updated_at=now,
    )
    db.commit()
    return identity_id


def _setup(db, tenant_id=1, initial_assignee_id=None):
    now = datetime.now(timezone.utc)
    org_id = db.organizations.insert(
        name="Org", tenant_id=tenant_id, created_at=now, updated_at=now
    )
    issue_id = db.issues.insert(
        title="Ticket",
        status="OPEN",
        priority="MEDIUM",
        issue_type="OTHER",
        reporter_id=None,
        assignee_id=initial_assignee_id,
        assignee_type="identity" if initial_assignee_id else None,
        resource_type="organization",
        resource_id=org_id,
        is_incident=0,
        tenant_id=tenant_id,
        created_at=now,
        updated_at=now,
    )
    webhook_id = _insert_webhook(db, tenant_id=tenant_id)
    db.commit()
    return issue_id, webhook_id


@pytest.mark.asyncio
@patch("apps.api.services.webhooks.assignment.requests.post")
@patch("apps.api.auth.decorators.get_current_user")
async def test_patch_new_assignee_fires_webhook(
    mock_get_user, mock_post, async_client, generate_token, app
):
    mock_get_user.return_value = MagicMock(id=1, is_superuser=True)
    mock_post.return_value = MagicMock(status_code=200, text="ok")
    token = generate_token(tenant_id=1, scopes=["issues:write"])

    async with app.app_context():
        db = current_app.db
        new_identity_id = _insert_identity(db, username="new_assignee")
        issue_id, webhook_id = _setup(db, initial_assignee_id=None)

    resp = await async_client.patch(
        f"/api/v1/issues/{issue_id}",
        json={"assignee_id": new_identity_id, "assignee_type": "identity"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, (await resp.get_data()).decode()[:300]

    await asyncio.sleep(0.1)

    async with app.app_context():
        db = current_app.db
        # webhook_deliveries is session-scoped and never truncated between
        # tests, so scope the lookup to this test's own webhook_id — an
        # unscoped query could pass vacuously on a stale row from another
        # test.
        delivery = (
            db(
                (db.webhook_deliveries.webhook_id == webhook_id)
                & (db.webhook_deliveries.event_type == "issue.assigned")
            )
            .select()
            .first()
        )
        assert delivery is not None


@pytest.mark.asyncio
@patch("apps.api.services.webhooks.assignment.requests.post")
@patch("apps.api.auth.decorators.get_current_user")
async def test_patch_same_assignee_does_not_fire(
    mock_get_user, mock_post, async_client, generate_token, app
):
    mock_get_user.return_value = MagicMock(id=1, is_superuser=True)
    token = generate_token(tenant_id=1, scopes=["issues:write"])

    async with app.app_context():
        db = current_app.db
        identity_id = _insert_identity(db, username="unchanged_assignee")
        issue_id, _ = _setup(db, initial_assignee_id=identity_id)

    resp = await async_client.patch(
        f"/api/v1/issues/{issue_id}",
        json={"assignee_id": identity_id, "assignee_type": "identity"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, (await resp.get_data()).decode()[:300]

    await asyncio.sleep(0.1)
    mock_post.assert_not_called()


@pytest.mark.asyncio
@patch("apps.api.services.webhooks.assignment.requests.post")
@patch("apps.api.auth.decorators.get_current_user")
async def test_patch_without_assignee_field_does_not_fire(
    mock_get_user, mock_post, async_client, generate_token, app
):
    mock_get_user.return_value = MagicMock(id=1, is_superuser=True)
    token = generate_token(tenant_id=1, scopes=["issues:write"])

    async with app.app_context():
        db = current_app.db
        identity_id = _insert_identity(db, username="untouched_assignee")
        issue_id, _ = _setup(db, initial_assignee_id=identity_id)

    resp = await async_client.patch(
        f"/api/v1/issues/{issue_id}",
        json={"title": "Renamed, no assignee touch"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, (await resp.get_data()).decode()[:300]

    await asyncio.sleep(0.1)
    mock_post.assert_not_called()


@pytest.mark.asyncio
@patch("apps.api.services.webhooks.assignment.requests.post")
@patch("apps.api.auth.decorators.get_current_user")
async def test_patch_cannot_clear_assignee_does_not_fire(
    mock_get_user, mock_post, async_client, generate_token, app
):
    """The update_issue schema has no way to distinguish "assignee_id absent"
    from "assignee_id explicitly cleared" (assignee_id: Optional[int],
    ge=1), and update_fields only ever sets assignee_id, never unsets it. A
    PATCH that sends assignee_id: null therefore cannot clear an existing
    assignee (the field is simply skipped, same as if it were absent), and
    must not fire issue.assigned either way. Combined with a title change so
    the request has other fields to apply -- an update body with no
    recognized fields at all hits a pre-existing, unrelated SQL-generation
    bug in update_issue (empty UPDATE ... SET) that is out of this task's
    scope and is called out separately in the task report."""
    mock_get_user.return_value = MagicMock(id=1, is_superuser=True)
    token = generate_token(tenant_id=1, scopes=["issues:write"])

    async with app.app_context():
        db = current_app.db
        identity_id = _insert_identity(db, username="uncleared_assignee")
        issue_id, _ = _setup(db, initial_assignee_id=identity_id)

    resp = await async_client.patch(
        f"/api/v1/issues/{issue_id}",
        json={"title": "Cleared attempt", "assignee_id": None},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, (await resp.get_data()).decode()[:300]

    async with app.app_context():
        db = current_app.db
        issue = db(db.issues.id == issue_id).select().first()
        # assignee_id was never cleared — it's still the original value.
        assert issue.assignee_id == identity_id

    await asyncio.sleep(0.1)
    mock_post.assert_not_called()
