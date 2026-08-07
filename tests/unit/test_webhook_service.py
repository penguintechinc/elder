"""Unit/integration tests for the reconciled WebhookService (Plan 05).

Covers: tenant-scoped CRUD against the real schema, village_id minting on
create, HMAC signature generation, and delivery recording. requests.post is
mocked — no real network calls.
"""

from unittest.mock import MagicMock, patch

import pytest
from quart import current_app


@pytest.mark.asyncio
async def test_create_webhook_mints_village_id_and_scopes_tenant(app):
    async with app.app_context():
        from apps.api.services.webhooks.service import WebhookService

        db = current_app.db
        service = WebhookService(db)

        webhook = service.create_webhook(
            tenant_id=1,
            name="Support assignments",
            url="https://hooks.example.com/support",
            events=["issue.assigned"],
            redis_client=None,
            filter_issue_type="support",
            filter_assignee_type="identity",
            filter_assignee_id=99,
        )

        assert webhook["tenant_id"] == 1
        assert webhook["village_id"] is not None
        assert webhook["filter_issue_type"] == "SUPPORT"
        assert webhook["filter_assignee_type"] == "identity"
        assert webhook["filter_assignee_id"] == 99
        assert webhook["secret"] is None


@pytest.mark.asyncio
async def test_create_webhook_requires_assignee_filter_pair(app):
    async with app.app_context():
        from apps.api.services.webhooks.service import WebhookService

        db = current_app.db
        service = WebhookService(db)

        with pytest.raises(Exception, match="must be set together"):
            service.create_webhook(
                tenant_id=1,
                name="Bad filter",
                url="https://hooks.example.com/bad",
                events=["issue.assigned"],
                filter_assignee_id=5,  # filter_assignee_type missing
            )


@pytest.mark.asyncio
async def test_get_webhook_cross_tenant_not_found(app):
    async with app.app_context():
        from apps.api.services.webhooks.service import WebhookService

        db = current_app.db
        service = WebhookService(db)
        webhook = service.create_webhook(
            tenant_id=1,
            name="Tenant 1 webhook",
            url="https://hooks.example.com/t1",
            events=["issue.assigned"],
        )

        with pytest.raises(Exception, match="not found"):
            service.get_webhook(webhook["id"], tenant_id=2)


@pytest.mark.asyncio
@patch("apps.api.services.webhooks.service.requests.post")
async def test_deliver_webhook_signs_and_records_delivery(mock_post, app):
    mock_post.return_value = MagicMock(status_code=200, text="ok")

    async with app.app_context():
        from apps.api.services.webhooks.service import WebhookService

        db = current_app.db
        service = WebhookService(db)
        webhook = service.create_webhook(
            tenant_id=1,
            name="Signed webhook",
            url="https://hooks.example.com/signed",
            events=["issue.assigned"],
            secret="s3cr3t",
        )

        result = service.deliver_webhook(
            webhook["id"], tenant_id=1, event_type="issue.assigned", payload={"x": 1}
        )

        assert result["success"] is True
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

        deliveries = service.get_webhook_deliveries(webhook["id"], tenant_id=1)
        assert len(deliveries) == 1
        assert deliveries[0]["status"] == "success"
        assert deliveries[0]["http_status"] == 200


@pytest.mark.asyncio
async def test_create_webhook_inactive(app):
    """Regression: creating a webhook with is_active=False persists as inactive."""
    async with app.app_context():
        from apps.api.services.webhooks.service import WebhookService

        db = current_app.db
        service = WebhookService(db)

        webhook = service.create_webhook(
            tenant_id=1,
            name="Inactive webhook",
            url="https://hooks.example.com/inactive",
            events=["issue.assigned"],
            is_active=False,
        )

        assert webhook["is_active"] is False

        # Verify persistence: fetch and re-check
        persisted = service.get_webhook(webhook["id"], tenant_id=1)
        assert persisted["is_active"] is False


@pytest.mark.asyncio
async def test_create_webhook_defaults_active(app):
    """Regression: creating a webhook without is_active defaults to True."""
    async with app.app_context():
        from apps.api.services.webhooks.service import WebhookService

        db = current_app.db
        service = WebhookService(db)

        webhook = service.create_webhook(
            tenant_id=1,
            name="Active by default webhook",
            url="https://hooks.example.com/default-active",
            events=["issue.assigned"],
        )

        assert webhook["is_active"] is True

        # Verify persistence: fetch and re-check
        persisted = service.get_webhook(webhook["id"], tenant_id=1)
        assert persisted["is_active"] is True
