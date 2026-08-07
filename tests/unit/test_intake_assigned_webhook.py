"""issue.assigned must fire from a public intake-form submission when the
form has a default assignee configured, using the resolved customer_contact
as actor (there is no authenticated user on this path)."""

import asyncio
import json
import uuid
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest
from quart import current_app


def _setup_form_and_webhook(db, tenant_id=1, default_assignee_id=None):
    # village_id has a unique DB constraint (VillageIDMixin) and tests in
    # this module share DB state across the session (no per-test
    # rollback/truncate in conftest.py — see app fixture, scope="session"),
    # so each call must mint its own values rather than reuse a fixed
    # literal (mirrors test_issues_assigned_webhook_create.py's pattern) —
    # a second call with the same literal would otherwise collide with the
    # first call's row.
    now = datetime.now(timezone.utc)
    suffix = uuid.uuid4().hex[:16]
    org_id = db.organizations.insert(
        name="Org", tenant_id=tenant_id, created_at=now, updated_at=now
    )
    form_id = db.hd_intake_forms.insert(
        tenant_id=tenant_id,
        village_id=f"{tenant_id:08x}-form-{suffix}"[:32],
        name="Support Form",
        slug=f"support-form-assign-test-{suffix}",
        fields=json.dumps(
            [{"id": "email", "label": "Email", "type": "email", "required": True}]
        ),
        issue_type="support",
        default_assignee_type="identity" if default_assignee_id else None,
        default_assignee_id=default_assignee_id,
        organization_id=org_id,
        is_public=True,
        captcha_required=False,
        is_active=True,
        created_at=now,
        updated_at=now,
    )
    webhook_id = db.webhooks.insert(
        tenant_id=tenant_id,
        village_id=f"{tenant_id:08x}-hook-{suffix}"[:32],
        name="Assign watcher",
        url="https://hooks.example.com/assign",
        events=["issue.assigned"],
        is_active=True,
        created_at=now,
        updated_at=now,
    )
    db.commit()
    return db.hd_intake_forms[form_id], webhook_id


@pytest.mark.asyncio
@patch("apps.api.services.webhooks.assignment.requests.post")
async def test_submit_with_default_assignee_fires_webhook(mock_post, async_client, app):
    mock_post.return_value = MagicMock(status_code=200, text="ok")

    async with app.app_context():
        db = current_app.db
        form_row, webhook_id = _setup_form_and_webhook(db, default_assignee_id=5)
        slug = form_row.slug

    resp = await async_client.post(
        f"/api/v1/intake/{slug}/submit",
        json={"fields": {"email": "user@example.com"}},
    )
    assert resp.status_code == 201, (await resp.get_data()).decode()[:300]

    # asyncio.create_task fire-and-forget: give the event loop a tick.
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
async def test_submit_without_default_assignee_does_not_fire(
    mock_post, async_client, app
):
    async with app.app_context():
        db = current_app.db
        form_row, _webhook_id = _setup_form_and_webhook(db, default_assignee_id=None)
        slug = form_row.slug

    resp = await async_client.post(
        f"/api/v1/intake/{slug}/submit",
        json={"fields": {"email": "user2@example.com"}},
    )
    assert resp.status_code == 201, (await resp.get_data()).decode()[:300]

    await asyncio.sleep(0.1)
    mock_post.assert_not_called()
