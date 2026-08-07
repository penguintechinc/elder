"""Schema tests for the reconciled `webhooks`/`webhook_deliveries` tables
(Plan 05: assignment webhooks). These exercise columns Task 1 adds/renames —
things test_schema_smoke.py's table-count floor can't catch.
"""

from datetime import datetime, timezone

import pytest
from quart import current_app
from sqlalchemy.exc import IntegrityError


@pytest.mark.asyncio
async def test_webhooks_has_new_columns(app):
    async with app.app_context():
        db = current_app.db
        now = datetime.now(timezone.utc)
        webhook_id = db.webhooks.insert(
            tenant_id=1,
            village_id="00000001-0000000000000abc",
            name="Test Webhook",
            url="https://example.com/hook",
            events=["issue.assigned"],
            is_active=True,
            filter_issue_type="SUPPORT",
            filter_assignee_type="identity",
            filter_assignee_id=42,
            metadata={"team": "support"},
            created_at=now,
            updated_at=now,
        )
        db.commit()

        row = db.webhooks[webhook_id]
        assert row.village_id == "00000001-0000000000000abc"
        assert row.tenant_id == 1
        assert row.filter_issue_type == "SUPPORT"
        assert row.filter_assignee_type == "identity"
        assert row.filter_assignee_id == 42


@pytest.mark.asyncio
async def test_webhooks_village_id_unique(app):
    async with app.app_context():
        db = current_app.db
        now = datetime.now(timezone.utc)
        db.webhooks.insert(
            tenant_id=1,
            village_id="00000001-0000000000000def",
            name="Webhook A",
            url="https://example.com/a",
            events=["issue.assigned"],
            is_active=True,
            created_at=now,
            updated_at=now,
        )
        db.commit()

        with pytest.raises(IntegrityError):
            db.webhooks.insert(
                tenant_id=1,
                village_id="00000001-0000000000000def",
                name="Webhook B",
                url="https://example.com/b",
                events=["issue.assigned"],
                is_active=True,
                created_at=now,
                updated_at=now,
            )
            db.commit()


@pytest.mark.asyncio
async def test_webhook_deliveries_has_reconciled_columns(app):
    async with app.app_context():
        db = current_app.db
        now = datetime.now(timezone.utc)
        webhook_id = db.webhooks.insert(
            tenant_id=1,
            village_id="00000001-0000000000000fff",
            name="Delivery Test Webhook",
            url="https://example.com/hook",
            events=["issue.assigned"],
            is_active=True,
            created_at=now,
            updated_at=now,
        )
        db.commit()

        delivery_id = db.webhook_deliveries.insert(
            webhook_id=webhook_id,
            event_type="issue.assigned",
            status="success",
            http_status=200,
            request_payload={"event": "issue.assigned"},
            attempt_count=1,
            delivered_at=now,
            created_at=now,
        )
        db.commit()

        delivery = db.webhook_deliveries[delivery_id]
        assert delivery.status == "success"
        assert delivery.http_status == 200
        assert delivery.attempt_count == 1
        assert delivery.request_payload is not None
