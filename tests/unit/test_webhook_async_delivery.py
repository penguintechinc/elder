"""End-to-end tests for off-request-path webhook delivery through the API.

Proves the org Event Webhooks contract at the route/service level: the
originating request succeeds and returns before delivery finishes, failed
deliveries are retried then recorded (never raised), the signature verifies
over the exact bytes delivered, and private-network targets are rejected by
every dispatcher.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import time
from datetime import UTC, datetime
from unittest.mock import MagicMock, patch
from uuid import uuid4

import httpx
import pytest
from quart import current_app
from webhook_http import ENGINE_HTTP, WebhookPostRecorder

from shared.webhooks import delivery, send_issue_created_webhooks

ALL_SCOPES = [
    "webhooks_alerting:read",
    "webhooks_alerting:write",
    "webhooks_alerting:admin",
]


@pytest.fixture(autouse=True)
def _fast_retries(monkeypatch):
    """Keep the real retry loop but shrink its sleeps to milliseconds."""
    monkeypatch.setenv("WEBHOOK_MAX_ATTEMPTS", "3")
    monkeypatch.setenv("WEBHOOK_BACKOFF_BASE_SECONDS", "0.01")
    monkeypatch.setenv("WEBHOOK_BACKOFF_CAP_SECONDS", "0.01")


async def _make_org(app) -> int:
    """Insert an organization in tenant 1 and return its id."""
    async with app.app_context():
        db = current_app.db
        now = datetime.now(UTC)
        org_id = db.organizations.insert(
            name=f"Async Delivery Org {uuid4().hex[:6]}",
            tenant_id=1,
            created_at=now,
            updated_at=now,
        )
        db.commit()
        return org_id


async def _create_webhook(async_client, token, org_id, **overrides) -> int:
    """Create a webhook through the API and return its id."""
    body = {
        "name": f"hook-{uuid4().hex[:6]}",
        "url": "https://hooks.example.com/async",
        "events": ["entity.created"],
        "organization_id": org_id,
    }
    body.update(overrides)
    resp = await async_client.post(
        "/api/v1/webhooks", json=body, headers={"Authorization": f"Bearer {token}"}
    )
    assert resp.status_code == 201, (await resp.get_data()).decode()[:300]
    return (await resp.get_json())["id"]


async def _deliveries(app, webhook_id: int) -> list:
    """Return the webhook_deliveries rows for one webhook."""
    async with app.app_context():
        db = current_app.db
        return list(db(db.webhook_deliveries.webhook_id == webhook_id).select())


async def _broadcast(async_client, token, org_id, payload=None):
    """POST a broadcast for entity.created and return the response."""
    return await async_client.post(
        "/api/v1/webhooks/broadcast",
        json={
            "event_type": "entity.created",
            "payload": payload or {"entity": "e-1"},
            "organization_id": org_id,
        },
        headers={"Authorization": f"Bearer {token}"},
    )


@pytest.mark.asyncio
@patch("apps.api.auth.decorators.get_current_user")
async def test_originating_request_succeeds_when_delivery_fails(
    mock_get_user, async_client, generate_token, app, webhook_transport
):
    """A dead receiver costs retries in the background, never the request."""
    mock_get_user.return_value = MagicMock(id=1, is_superuser=True)
    webhook_transport.respond_with(500)
    token = generate_token(tenant_id=1, scopes=ALL_SCOPES)
    org_id = await _make_org(app)
    webhook_id = await _create_webhook(async_client, token, org_id)

    resp = await _broadcast(async_client, token, org_id)

    assert resp.status_code == 200, (await resp.get_data()).decode()[:300]
    body = await resp.get_json()
    assert body["webhooks_triggered"] == 1
    assert body["webhooks_queued"] == 1
    assert body["webhook_results"][0]["queued"] is True

    await delivery.drain_background(timeout=10)

    # Retried up to the cap, then recorded as failed - not raised anywhere.
    assert len(webhook_transport.requests) == 3
    rows = await _deliveries(app, webhook_id)
    assert len(rows) == 1
    assert rows[0].id == body["webhook_results"][0]["delivery_id"]
    assert rows[0].status == "failed"
    assert rows[0].attempt_count == 3
    assert rows[0].http_status == 500
    assert "500" in rows[0].error_message


@pytest.mark.asyncio
@patch("apps.api.auth.decorators.get_current_user")
async def test_originating_request_succeeds_when_receiver_raises(
    mock_get_user, async_client, generate_token, app, webhook_transport
):
    """A connection failure is retried and recorded, and the request is 200."""
    mock_get_user.return_value = MagicMock(id=1, is_superuser=True)

    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    webhook_transport.handler = refuse
    token = generate_token(tenant_id=1, scopes=ALL_SCOPES)
    org_id = await _make_org(app)
    webhook_id = await _create_webhook(async_client, token, org_id)

    resp = await _broadcast(async_client, token, org_id)
    assert resp.status_code == 200

    await delivery.drain_background(timeout=10)
    rows = await _deliveries(app, webhook_id)
    assert rows[0].status == "failed"
    assert rows[0].attempt_count == 3
    assert rows[0].http_status is None
    assert "ConnectError" in rows[0].error_message


@pytest.mark.asyncio
@patch("apps.api.auth.decorators.get_current_user")
async def test_broadcast_returns_before_slow_delivery_completes(
    mock_get_user, async_client, generate_token, app, webhook_transport
):
    """Delivery is off the request path: the response does not wait for it."""
    mock_get_user.return_value = MagicMock(id=1, is_superuser=True)
    release = asyncio.Event()

    async def slow(request: httpx.Request) -> httpx.Response:
        await release.wait()
        return httpx.Response(200, text="ok")

    webhook_transport.handler = slow
    token = generate_token(tenant_id=1, scopes=ALL_SCOPES)
    org_id = await _make_org(app)
    webhook_id = await _create_webhook(async_client, token, org_id)

    started = time.monotonic()
    resp = await _broadcast(async_client, token, org_id)
    elapsed = time.monotonic() - started

    # The receiver is still blocked, yet the request already completed.
    assert resp.status_code == 200
    assert not release.is_set()
    assert elapsed < 5.0
    rows = await _deliveries(app, webhook_id)
    assert len(rows) == 1
    assert rows[0].status in (None, "pending")
    assert rows[0].attempt_count == 0

    release.set()
    await delivery.drain_background(timeout=10)
    rows = await _deliveries(app, webhook_id)
    assert rows[0].status == "success"
    assert rows[0].attempt_count == 1
    assert rows[0].http_status == 200


@pytest.mark.asyncio
@patch("apps.api.auth.decorators.get_current_user")
async def test_signature_header_verifies_over_delivered_bytes(
    mock_get_user, async_client, generate_token, app, webhook_transport
):
    """A receiver recomputing HMAC-SHA256 over the raw body gets a match."""
    mock_get_user.return_value = MagicMock(id=1, is_superuser=True)
    token = generate_token(tenant_id=1, scopes=ALL_SCOPES)
    org_id = await _make_org(app)
    secret = "receiver-shared-secret"
    await _create_webhook(async_client, token, org_id, secret=secret)

    resp = await _broadcast(async_client, token, org_id, payload={"z": 1, "a": [1, 2]})
    assert resp.status_code == 200
    await delivery.drain_background(timeout=10)

    assert len(webhook_transport.requests) == 1
    sent = webhook_transport.requests[0]
    expected = hmac.new(secret.encode(), sent.content, hashlib.sha256).hexdigest()
    assert sent.headers["X-Elder-Signature"]
    assert hmac.compare_digest(sent.headers["X-Elder-Signature"], expected)
    # A tampered body must NOT verify with the same header.
    tampered = hmac.new(
        secret.encode(), sent.content + b" ", hashlib.sha256
    ).hexdigest()
    assert sent.headers["X-Elder-Signature"] != tampered
    assert json.loads(sent.content)["data"] == {"z": 1, "a": [1, 2]}
    assert sent.headers["Idempotency-Key"]


@pytest.mark.asyncio
@patch("apps.api.auth.decorators.get_current_user")
async def test_test_route_rejects_private_ip_target(
    mock_get_user, async_client, generate_token, app, webhook_transport
):
    """A webhook pointed at an internal address is refused, nothing is sent."""
    mock_get_user.return_value = MagicMock(id=1, is_superuser=True)
    token = generate_token(tenant_id=1, scopes=ALL_SCOPES)
    org_id = await _make_org(app)
    webhook_id = await _create_webhook(
        async_client, token, org_id, url="http://169.254.169.254/latest/meta-data/"
    )

    resp = await async_client.post(
        f"/api/v1/webhooks/{webhook_id}/test",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert resp.status_code == 400
    body = await resp.get_json()
    assert body["success"] is False
    assert "blocked" in body["error"]
    assert webhook_transport.requests == []
    rows = await _deliveries(app, webhook_id)
    assert rows[0].status == "failed"
    assert "blocked" in rows[0].error_message


@pytest.mark.asyncio
@patch("apps.api.auth.decorators.get_current_user")
async def test_test_route_is_single_attempt(
    mock_get_user, async_client, generate_token, app, webhook_transport
):
    """Interactive test delivery never holds the request open for retries."""
    mock_get_user.return_value = MagicMock(id=1, is_superuser=True)
    webhook_transport.respond_with(503)
    token = generate_token(tenant_id=1, scopes=ALL_SCOPES)
    org_id = await _make_org(app)
    webhook_id = await _create_webhook(async_client, token, org_id)

    resp = await async_client.post(
        f"/api/v1/webhooks/{webhook_id}/test",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert resp.status_code == 400
    assert (await resp.get_json())["status_code"] == 503
    assert len(webhook_transport.requests) == 1


@pytest.mark.asyncio
async def test_issue_created_webhook_retries_and_rejects_private(
    app, webhook_transport
):
    """issue.created: 5xx is retried to success; a private target is refused."""
    webhook_transport.respond_with(502, 200)
    async with app.app_context():
        db = current_app.db
        now = datetime.now(UTC)
        org_id = db.organizations.insert(
            name="Issue Hook Retry Org", tenant_id=1, created_at=now, updated_at=now
        )
        for name, url in (
            ("public", "https://hooks.example.com/issue?token=TOPSECRET"),
            ("private", "http://10.20.30.40/issue"),
        ):
            db.alert_configurations.insert(
                organization_id=org_id,
                destination_type="WEBHOOK",
                name=name,
                enabled=1,
                config={"url": url},
                created_at=now,
                updated_at=now,
            )
        db.commit()

        results = await send_issue_created_webhooks(
            db,
            issue_id=7,
            issue_title="Retry me",
            issue_type="support",
            is_incident=0,
            organization_id=org_id,
        )

    assert len(results) == 2
    by_host = {r["target_host"]: r for r in results}
    assert by_host["hooks.example.com"]["success"] is True
    assert by_host["hooks.example.com"]["attempts"] == 2
    assert by_host["10.20.30.40"]["success"] is False
    assert "blocked" in by_host["10.20.30.40"]["error"]
    # Only the public target was ever contacted, and no result echoes the URL.
    assert {r.url.host for r in webhook_transport.requests} == {"93.184.216.34"}
    assert "TOPSECRET" not in repr(results)


def _make_tenant(db, name: str) -> int:
    """Create a dedicated tenant row (webhooks.tenant_id is a real FK)."""
    tid = db.tenants.insert(name=name, slug=f"async-{uuid4().hex[:10]}", is_active=True)
    db.commit()
    return tid


@pytest.mark.asyncio
async def test_assignment_dispatcher_rejects_private_ip_target(app):
    """The issue.assigned dispatcher refuses internal targets too."""
    from apps.api.services.webhooks.assignment import (
        AssignmentEvent,
        send_issue_assigned_webhooks,
    )

    async with app.app_context():
        db = current_app.db
        tenant_id = _make_tenant(db, "Assign SSRF")
        now = datetime.now(UTC)
        webhook_id = db.webhooks.insert(
            tenant_id=tenant_id,
            village_id=f"00000001-{uuid4().hex[:16]}",
            name="internal target",
            url="http://192.168.7.7/assign?token=TOPSECRET",
            events=["issue.assigned"],
            is_active=True,
            secret="s3cr3t",
            created_at=now,
            updated_at=now,
        )
        db.commit()
        event = AssignmentEvent(
            issue_id=1,
            village_id="00000002-0000000000000001",
            issue_type="SUPPORT",
            status="OPEN",
            assignee_type="identity",
            assignee_id=42,
            tenant_id=tenant_id,
            actor_id=7,
        )

        with patch(ENGINE_HTTP, new_callable=WebhookPostRecorder) as mock_post:
            results = await send_issue_assigned_webhooks(db, event)

        mock_post.assert_not_called()
        assert len(results) == 1
        assert results[0]["success"] is False
        assert "blocked" in results[0]["error"]
        assert "TOPSECRET" not in repr(results)
        row = db(db.webhook_deliveries.webhook_id == webhook_id).select().first()
        assert row.status == "failed"
        assert "blocked" in row.error_message


@pytest.mark.asyncio
async def test_user_created_dispatcher_rejects_private_ip_target(app):
    """The user.created dispatcher refuses internal targets too."""
    from apps.api.services.webhooks import user_events

    async with app.app_context():
        db = current_app.db
        tenant_id = _make_tenant(db, "User SSRF")
        now = datetime.now(UTC)
        webhook_id = db.webhooks.insert(
            tenant_id=tenant_id,
            village_id=f"00000001-{uuid4().hex[:16]}",
            name="internal user target",
            url="http://127.0.0.1:9/users",
            events=["user.created"],
            is_active=True,
            created_at=now,
            updated_at=now,
        )
        db.commit()
        webhook = db.webhooks[webhook_id]

        with patch(ENGINE_HTTP, new_callable=WebhookPostRecorder) as mock_post:
            result = await user_events._dispatch_user_webhook(
                db,
                webhook,
                "user.created",
                {"tenant_id": tenant_id, "event": "user.created"},
            )

        mock_post.assert_not_called()
        assert result["success"] is False
        assert "blocked" in result["error"]
