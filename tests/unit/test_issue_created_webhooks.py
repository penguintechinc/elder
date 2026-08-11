"""Regression tests for send_issue_created_webhooks alert-config matching.

Covers the AlertDestinationType enum-case bug: the WEBHOOK-destination query
must compare against the SQLAlchemy-stored member NAME ("WEBHOOK"), not the
lowercase enum .value ("webhook"), or PostgreSQL raises
InvalidTextRepresentation and the fire-and-forget dispatch silently dies.
"""

from datetime import UTC, datetime, timezone
from unittest.mock import MagicMock, patch

import pytest
from quart import current_app

from shared.webhooks import send_issue_created_webhooks


class TestSendIssueCreatedWebhooks:
    """send_issue_created_webhooks must match enabled WEBHOOK alert configs."""

    @pytest.mark.asyncio
    async def test_matches_webhook_destination_config(self, app):
        """Regression: the WEBHOOK query uses the stored enum member name.

        Enum(AlertDestinationType) persists the member NAME, so the pg enum
        `alertdestinationtype` values are EMAIL/WEBHOOK/PAGERDUTY/SLACK. The
        query previously compared to lowercase "webhook", which raised
        InvalidTextRepresentation at plan time — the fire-and-forget task
        died and issue-created webhooks never fired.
        # regression: alertdestinationtype enum-case mismatch
        """
        async with app.app_context():
            db = current_app.db
            now = datetime.now(UTC)

            org_id = db.organizations.insert(
                name="Webhook Org",
                tenant_id=1,
                created_at=now,
                updated_at=now,
            )
            db.alert_configurations.insert(
                organization_id=org_id,
                destination_type="WEBHOOK",
                name="Issue Created Hook",
                enabled=1,
                config={"url": "https://example.com/hook"},
                created_at=now,
                updated_at=now,
            )
            db.commit()

            with patch("shared.webhooks.issue_webhooks.requests.post") as mock_post:
                mock_post.return_value = MagicMock(status_code=200, text="ok")
                results = await send_issue_created_webhooks(
                    db,
                    issue_id=1,
                    issue_title="Test issue",
                    issue_type="support",
                    is_incident=0,
                    organization_id=org_id,
                )

            # Query must not raise, must find the config, and POST to it.
            assert len(results) == 1
            assert results[0]["success"] is True
            mock_post.assert_called_once()

    @pytest.mark.asyncio
    async def test_no_config_returns_empty_without_error(self, app):
        """An org with no WEBHOOK config yields an empty result, not an error.

        The lowercase-"webhook" bug raised at plan time even with zero rows, so
        this exercises the query path on an empty table too.
        """
        async with app.app_context():
            db = current_app.db
            now = datetime.now(UTC)
            org_id = db.organizations.insert(
                name="No Hook Org",
                tenant_id=1,
                created_at=now,
                updated_at=now,
            )
            db.commit()

            results = await send_issue_created_webhooks(
                db,
                issue_id=2,
                issue_title="No hook",
                issue_type="support",
                is_incident=0,
                organization_id=org_id,
            )
            assert results == []
