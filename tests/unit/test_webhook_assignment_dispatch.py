"""Integration tests for issue.assigned delivery (Plan 05). requests.post is
mocked at apps.api.services.webhooks.assignment.requests.post (dispatch's
own module) — not apps.api.services.webhooks.service, which is a different
mock target used by WebhookService's own delivery path.

webhooks.tenant_id is a real foreign key into tenants (see
test_webhook_assignment_matching.py's _make_tenant), so each test mints its
own tenant via _make_tenant rather than a hardcoded literal — a bare
`tenant_id=1` would only work by accident of insert order within a shared
test session, and fails outright when this file runs alone against a fresh
database. Delivery lookups are scoped by webhook_id (not just event_type)
for the same reason: webhook_deliveries isn't truncated between the test
functions in this file, so an unscoped query could pick up a row inserted
by an earlier test instead of the one under test.
"""

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from quart import current_app


def _make_tenant(db, name: str) -> int:
    """Create a dedicated tenant row and return its id.

    Mirrors test_webhook_assignment_matching.py's _make_tenant — webhooks.tenant_id
    is a real FK into tenants, so each test needs its own valid tenant row.
    """
    tid = db.tenants.insert(
        name=name,
        slug=f"{name.lower().replace(' ', '-')}-{uuid4().hex[:8]}",
        is_active=True,
    )
    db.commit()
    return tid


def _insert_webhook(db, tenant_id, **overrides):
    now = datetime.now(timezone.utc)
    defaults = dict(
        tenant_id=tenant_id,
        village_id=f"00000001-{overrides.pop('_vid_suffix', '0000000000000001')}",
        name="Test webhook",
        url="https://hooks.example.com/test",
        events=["issue.assigned"],
        is_active=True,
        secret="s3cr3t",
        created_at=now,
        updated_at=now,
    )
    defaults.update(overrides)
    webhook_id = db.webhooks.insert(**defaults)
    db.commit()
    return webhook_id


def _event(tenant_id, **overrides):
    from apps.api.services.webhooks.assignment import AssignmentEvent

    defaults = dict(
        issue_id=1,
        village_id="00000002-0000000000000001",
        issue_type="SUPPORT",
        status="OPEN",
        assignee_type="identity",
        assignee_id=42,
        tenant_id=tenant_id,
        actor_id=7,
    )
    defaults.update(overrides)
    return AssignmentEvent(**defaults)


@pytest.mark.asyncio
@patch("apps.api.services.webhooks.assignment.requests.post")
async def test_send_delivers_to_matching_webhook_signed(mock_post, app):
    mock_post.return_value = MagicMock(status_code=200, text="ok")

    async with app.app_context():
        from apps.api.services.webhooks.assignment import send_issue_assigned_webhooks

        db = current_app.db
        tenant_id = _make_tenant(db, "Assignment Dispatch Signed")
        webhook_id = _insert_webhook(db, tenant_id, _vid_suffix="00000000000000a1")

        results = await send_issue_assigned_webhooks(db, _event(tenant_id))

        assert len(results) == 1
        assert results[0]["success"] is True
        sent_headers = mock_post.call_args.kwargs["headers"]
        assert "X-Elder-Signature" in sent_headers

        # Recompute the signature over the ACTUAL bytes posted (mock_post's
        # `data=` kwarg), not just assert the header is present — a
        # presence-only check doesn't catch signing different bytes than
        # were sent (e.g. signing json.dumps(payload) but posting
        # json=payload, which requests re-serializes differently).
        from apps.api.services.webhooks.service import generate_signature

        sent_body = mock_post.call_args.kwargs["data"]
        assert isinstance(sent_body, bytes)
        expected_signature = generate_signature("s3cr3t", sent_body.decode("utf-8"))
        assert sent_headers["X-Elder-Signature"] == expected_signature

        delivery = (
            db(
                (db.webhook_deliveries.webhook_id == webhook_id)
                & (db.webhook_deliveries.event_type == "issue.assigned")
            )
            .select()
            .first()
        )
        assert delivery is not None
        assert delivery.status == "success"
        assert delivery.http_status == 200


@pytest.mark.asyncio
@patch("apps.api.services.webhooks.assignment.requests.post")
async def test_send_skips_non_matching_webhook(mock_post, app):
    async with app.app_context():
        from apps.api.services.webhooks.assignment import send_issue_assigned_webhooks

        db = current_app.db
        tenant_id = _make_tenant(db, "Assignment Dispatch Skip")
        _insert_webhook(
            db, tenant_id, _vid_suffix="00000000000000a2", events=["issue.created"]
        )  # not subscribed to issue.assigned

        results = await send_issue_assigned_webhooks(db, _event(tenant_id))

        assert results == []
        mock_post.assert_not_called()


@pytest.mark.asyncio
@patch("apps.api.services.webhooks.assignment.requests.post")
async def test_send_never_raises_on_delivery_failure(mock_post, app):
    mock_post.side_effect = Exception("connection refused")

    async with app.app_context():
        from apps.api.services.webhooks.assignment import send_issue_assigned_webhooks

        db = current_app.db
        tenant_id = _make_tenant(db, "Assignment Dispatch Failure")
        webhook_id = _insert_webhook(db, tenant_id, _vid_suffix="00000000000000a3")

        results = await send_issue_assigned_webhooks(
            db, _event(tenant_id)
        )  # must not raise

        assert len(results) == 1
        assert results[0]["success"] is False

        delivery = (
            db(
                (db.webhook_deliveries.webhook_id == webhook_id)
                & (db.webhook_deliveries.event_type == "issue.assigned")
            )
            .select()
            .first()
        )
        assert delivery.status == "failed"
        assert delivery.error_message is not None
