"""Tests for the webhook delivery hardening follow-ups.

Covers: allowlist prefix floor and embedded-IPv4 unwrapping, the response
excerpt policy, the timestamped v2 signature, pending caps with per-tenant
fairness and load shedding, the delivery outbox with restart recovery, the
sync bridge and the engine's dedicated thread pool.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import threading
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import MagicMock, patch
from uuid import uuid4

import httpx
import pytest
from quart import current_app

from apps.api.services.webhooks import user_events
from apps.api.services.webhooks.service import (
    WebhookService,
    delivery_idempotency_key,
)
from shared.webhooks import delivery
from shared.webhooks.delivery import DeliveryPolicy, DeliveryRequest, deliver

FAST = DeliveryPolicy(
    max_attempts=4,
    connect_timeout=1.0,
    read_timeout=1.0,
    attempt_timeout=2.0,
    backoff_base=0.001,
    backoff_cap=0.001,
    max_response_bytes=65536,
)

ALL_SCOPES = [
    "webhooks_alerting:read",
    "webhooks_alerting:write",
    "webhooks_alerting:admin",
]


@pytest.fixture(autouse=True)
def _fast_retries(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the real retry loop but shrink its sleeps to milliseconds."""
    monkeypatch.setenv("WEBHOOK_MAX_ATTEMPTS", "3")
    monkeypatch.setenv("WEBHOOK_BACKOFF_BASE_SECONDS", "0.01")
    monkeypatch.setenv("WEBHOOK_BACKOFF_CAP_SECONDS", "0.01")


class LogRecorder:
    """Structlog-shaped logger that keeps every event and its fields."""

    def __init__(self) -> None:
        """Start with no recorded events."""
        self.events: list[tuple[str, dict[str, Any]]] = []

    def _log(self, event: str, **fields: Any) -> None:
        """Store one log call."""
        self.events.append((event, fields))

    debug = info = warning = error = _log

    def named(self, event: str) -> list[dict[str, Any]]:
        """Return the fields of every call logged under ``event``."""
        return [fields for name, fields in self.events if name == event]


def _make_tenant(db: Any, name: str) -> int:
    """Create a dedicated tenant row with a UUID reference."""
    tid = db.tenants.insert(
        name=name,
        slug=f"hard-{uuid4().hex[:10]}",
        is_active=True,
        village_id=uuid4().hex,
    )
    db.commit()
    return tid


def _make_webhook(db: Any, tenant_id: int, **overrides: Any) -> Any:
    """Insert an active webhook row and return it."""
    now = datetime.now(UTC)
    fields: dict[str, Any] = {
        "tenant_id": tenant_id,
        "village_id": f"00000001-{uuid4().hex[:16]}",
        "name": "hardening hook",
        "url": "https://hooks.example.com/hardening",
        "events": ["entity.created"],
        "is_active": True,
        "created_at": now,
        "updated_at": now,
    }
    fields.update(overrides)
    webhook_id = db.webhooks.insert(**fields)
    db.commit()
    return db.webhooks[webhook_id]


def _insert_outbox_row(
    db: Any,
    webhook_id: int,
    *,
    age_seconds: float,
    status: str = "pending",
    attempts: int = 0,
) -> int:
    """Insert an unfinished delivery row created ``age_seconds`` ago."""
    delivery_id = db.webhook_deliveries.insert(
        webhook_id=webhook_id,
        event_type="entity.created",
        request_payload={"event": "entity.created", "data": {"n": 1}},
        status=status,
        attempt_count=attempts,
        created_at=datetime.now(UTC) - timedelta(seconds=age_seconds),
    )
    db.commit()
    return int(delivery_id)


@pytest.fixture
def _instant_recovery(monkeypatch: pytest.MonkeyPatch) -> None:
    """Recover everything unfinished at once (no age floor, no observation)."""
    monkeypatch.setenv("WEBHOOK_RECOVERY_MIN_AGE_SECONDS", "60")
    monkeypatch.setenv("WEBHOOK_RECOVERY_OBSERVE_SECONDS", "0")
    monkeypatch.setenv("WEBHOOK_RECOVERY_BATCH", "5000")


# --- L1: allowlist prefix floor + embedded IPv4 -----------------------------


@pytest.mark.parametrize(
    "allowlist",
    ["0.0.0.0/0", "10.0.0.0/8,0.0.0.0/0", "::/0,10.0.0.0/8", "10.0.0.0/7", "fd00::/8"],
)
def test_overbroad_allowlist_is_ignored_entirely(monkeypatch, allowlist):
    """A prefix shorter than /8 (v4) or /32 (v6) voids the whole allowlist."""
    log = LogRecorder()
    monkeypatch.setattr(delivery, "logger", log)
    monkeypatch.setenv(delivery.ALLOWED_PRIVATE_CIDRS_ENV, allowlist)

    assert delivery.is_blocked_address("10.1.2.3") is True
    assert delivery.is_blocked_address("192.168.1.1") is True
    assert delivery.is_blocked_address("127.0.0.1") is True
    assert delivery.is_blocked_address("169.254.169.254") is True
    assert delivery.is_blocked_address("fd12:3456::1") is True
    assert delivery.is_blocked_address("93.184.216.34") is False
    # Loud, once per configuration value, and never echoing the value.
    rejected = log.named("webhook_private_allowlist_rejected")
    assert len(rejected) == 1
    assert allowlist not in repr(rejected)


def test_minimum_prefix_allowlist_entries_are_honoured(monkeypatch):
    """Exactly /8 (v4) and /32 (v6) are the broadest accepted entries."""
    monkeypatch.setenv(delivery.ALLOWED_PRIVATE_CIDRS_ENV, "10.0.0.0/8,fd12:3456::/32")

    assert delivery.is_blocked_address("10.1.2.3") is False
    assert delivery.is_blocked_address("fd12:3456::1") is False
    assert delivery.is_blocked_address("192.168.1.1") is True
    assert delivery.is_blocked_address("fd12:3457::1") is True


def _teredo(server: str, client: str) -> str:
    """Build a Teredo address embedding ``server`` and obfuscated ``client``."""
    import ipaddress

    value = (
        (0x20010000 << 96)
        | (int(ipaddress.IPv4Address(server)) << 64)
        | (int(ipaddress.IPv4Address(client)) ^ 0xFFFFFFFF)
    )
    return str(ipaddress.IPv6Address(value))


@pytest.mark.parametrize(
    "address",
    [
        "2002:7f00:1::1",  # 6to4 -> 127.0.0.1
        "2002:a9fe:a9fe::1",  # 6to4 -> 169.254.169.254 (metadata)
        "2002:0a01:0101::1",  # 6to4 -> 10.1.1.1 (private, not allowlisted)
        _teredo("127.0.0.1", "93.184.216.34"),
        _teredo("93.184.216.34", "169.254.169.254"),
        "::ffff:127.0.0.1",
    ],
)
def test_embedded_ipv4_cannot_ride_an_allowlisted_ipv6_range(monkeypatch, address):
    """Allowlisting the 6to4/Teredo ranges never admits loopback or metadata."""
    log = LogRecorder()
    monkeypatch.setattr(delivery, "logger", log)
    monkeypatch.setenv(
        delivery.ALLOWED_PRIVATE_CIDRS_ENV,
        "2002:7f00::/32,2002:a9fe::/32,2002:a01::/32,2001::/32,::ffff:0:0/96",
    )

    assert delivery.is_blocked_address(address) is True
    # Blocked by unwrapping, not because the allowlist itself was thrown out.
    assert log.named("webhook_private_allowlist_rejected") == []
    assert len(delivery._allowed_private_networks()) == 5


def test_embedded_public_ipv4_follows_the_allowlist(monkeypatch):
    """A 6to4 address around a public IPv4 is allowed only if allowlisted."""
    assert delivery.is_blocked_address("2002:5db8:d822::1") is True
    monkeypatch.setenv(delivery.ALLOWED_PRIVATE_CIDRS_ENV, "2002:5db8::/32")
    assert delivery._classify_address("2002:5db8:d822::1") == (False, True)


# --- L2: response excerpt policy -------------------------------------------


@pytest.mark.asyncio
async def test_private_target_response_body_is_never_kept(
    monkeypatch, webhook_transport
):
    """An allowlisted private receiver yields status + reason, no body text."""
    monkeypatch.setenv(delivery.ALLOWED_PRIVATE_CIDRS_ENV, "10.0.0.0/8")
    webhook_transport.handler = lambda request: httpx.Response(
        200, text="INTERNAL-SECRET-BODY", headers={"X-Internal": "INTERNAL-HEADER"}
    )

    result = await deliver(
        DeliveryRequest(url="http://10.9.9.9/hook", body=b"{}"), policy=FAST
    )

    assert result.success is True and result.status_code == 200
    assert result.private_target is True
    assert result.response_excerpt == delivery.PRIVATE_TARGET_EXCERPT
    assert "INTERNAL" not in repr(result)


@pytest.mark.asyncio
async def test_public_target_excerpt_is_short_and_inert(webhook_transport):
    """Public responses keep <=256 chars with control characters removed."""
    noisy = "line1\r\nline2\x1b[31mred\x00\x7f" + "A" * 5000
    webhook_transport.handler = lambda request: httpx.Response(
        200, text=noisy, headers={"X-Leak": "HEADER-VALUE"}
    )

    result = await deliver(
        DeliveryRequest(url="https://hooks.example.com/hook", body=b"{}"), policy=FAST
    )

    assert result.private_target is False
    assert 0 < len(result.response_excerpt) <= delivery.RESPONSE_EXCERPT_MAX_CHARS
    assert all(
        ord(c) >= 32 and not 127 <= ord(c) < 160 for c in result.response_excerpt
    )
    assert result.response_excerpt.startswith("line1  line2 [31mred")
    assert "HEADER-VALUE" not in repr(result)


@pytest.mark.asyncio
@patch("apps.api.auth.decorators.get_current_user")
async def test_delivery_history_never_exposes_private_response(
    mock_get_user, async_client, generate_token, app, webhook_transport, monkeypatch
):
    """Stored row and the deliveries API carry no private response text."""
    mock_get_user.return_value = MagicMock(id=1, is_superuser=True)
    monkeypatch.setenv(delivery.ALLOWED_PRIVATE_CIDRS_ENV, "10.0.0.0/8")
    webhook_transport.handler = lambda request: httpx.Response(
        500, text="INTERNAL-SECRET-BODY " * 40
    )
    token = generate_token(tenant_id=1, scopes=ALL_SCOPES)
    async with app.app_context():
        webhook = _make_webhook(current_app.db, 1, url="http://10.9.9.9/internal")
        webhook_id = webhook.id

    resp = await async_client.post(
        f"/api/v1/webhooks/{webhook_id}/test",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 400
    assert "INTERNAL" not in (await resp.get_data()).decode()

    listing = await async_client.get(
        f"/api/v1/webhooks/{webhook_id}/deliveries",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert listing.status_code == 200
    body = await listing.get_json()
    assert body["count"] == 1
    assert body["deliveries"][0]["http_status"] == 500
    assert body["deliveries"][0]["response_body"] == delivery.PRIVATE_TARGET_EXCERPT
    assert "INTERNAL" not in repr(body)
    async with app.app_context():
        db = current_app.db
        row = db(db.webhook_deliveries.webhook_id == webhook_id).select().first()
        assert row.response_body == delivery.PRIVATE_TARGET_EXCERPT


@pytest.mark.asyncio
async def test_stored_public_excerpt_is_capped(app, webhook_transport):
    """A public receiver's stored excerpt is <=256 chars, legacy rows too."""
    webhook_transport.handler = lambda request: httpx.Response(200, text="B" * 5000)
    async with app.app_context():
        db = current_app.db
        tenant_id = _make_tenant(db, "Excerpt cap")
        webhook = _make_webhook(db, tenant_id)
        service = WebhookService(db)

        outcome = await service.deliver_event(
            webhook, "entity.created", {"n": 1}, kind="native"
        )
        assert outcome["success"] is True
        assert "response" not in outcome and "response_body" not in outcome
        row = db.webhook_deliveries[outcome["delivery_id"]]
        assert len(row.response_body) == delivery.RESPONSE_EXCERPT_MAX_CHARS

        # A row written before the cap existed is trimmed on the way out.
        db(db.webhook_deliveries.id == row.id).update(response_body="C\n" * 800)
        db.commit()
        listed = service.get_webhook_deliveries(webhook.id, tenant_id)
        assert len(listed[0]["response_body"]) <= delivery.RESPONSE_EXCERPT_MAX_CHARS
        assert "\n" not in listed[0]["response_body"]


# --- L3: timestamped v2 signature ------------------------------------------


@pytest.mark.asyncio
async def test_v2_signature_verifies_and_legacy_header_is_unchanged(
    app, webhook_transport, monkeypatch
):
    """Both signatures verify over the delivered bytes; v2 binds a timestamp."""
    monkeypatch.setattr(delivery, "_now_unix", lambda: 1_800_000_000)
    async with app.app_context():
        db = current_app.db
        webhook = _make_webhook(db, _make_tenant(db, "Sig v2"), secret="s3cr3t")
        outcome = await WebhookService(db).deliver_event(
            webhook, "entity.created", {"b": 2, "a": 1}, kind="native"
        )
    assert outcome["success"] is True

    sent = webhook_transport.requests[0]
    legacy = hmac.new(b"s3cr3t", sent.content, hashlib.sha256).hexdigest()
    assert sent.headers["X-Elder-Signature"] == legacy
    assert sent.headers["X-Elder-Timestamp"] == "1800000000"
    header = sent.headers["X-Elder-Signature-V2"]
    expected = hmac.new(
        b"s3cr3t", b"1800000000." + sent.content, hashlib.sha256
    ).hexdigest()
    assert header == f"t=1800000000,v1={expected}"
    assert delivery.verify_signature_v2(
        "s3cr3t", header, sent.content, now=1_800_000_100
    )
    # Replay outside the 5-minute tolerance, tampering and wrong key all fail.
    assert not delivery.verify_signature_v2(
        "s3cr3t", header, sent.content, now=1_800_000_301
    )
    assert not delivery.verify_signature_v2(
        "s3cr3t", header, sent.content + b" ", now=1_800_000_000
    )
    assert not delivery.verify_signature_v2(
        "other", header, sent.content, now=1_800_000_000
    )
    assert not delivery.verify_signature_v2("s3cr3t", "garbage", sent.content)
    assert delivery.SIGNATURE_TOLERANCE_SECONDS == 300


@pytest.mark.asyncio
async def test_each_retry_has_fresh_timestamp_and_same_idempotency_key(
    webhook_transport, monkeypatch
):
    """Retries are re-signed with a new timestamp but keep one key."""
    clock = iter(range(1_800_000_000, 1_800_000_100, 7))
    monkeypatch.setattr(delivery, "_now_unix", lambda: next(clock))
    webhook_transport.respond_with(500, 503, 200)

    result = await deliver(
        DeliveryRequest(
            url="https://hooks.example.com/hook",
            body=b'{"a":1}',
            headers={
                "X-Elder-Signature": "legacy-value",
                "X-Elder-Signature-V2": "spoofed",
                "X-Elder-Timestamp": "1",
            },
            signing_secret="s3cr3t",
        ),
        policy=FAST,
    )

    assert result.success is True and result.attempts == 3
    sent = webhook_transport.requests
    timestamps = [request.headers["X-Elder-Timestamp"] for request in sent]
    assert len(set(timestamps)) == 3
    assert len({request.headers["Idempotency-Key"] for request in sent}) == 1
    assert {request.headers["X-Elder-Signature"] for request in sent} == {
        "legacy-value"
    }
    for request, timestamp in zip(sent, timestamps, strict=True):
        header = request.headers["X-Elder-Signature-V2"]
        assert header.startswith(f"t={timestamp},v1=")
        assert delivery.verify_signature_v2(
            "s3cr3t", header, request.content, now=int(timestamp)
        )


@pytest.mark.asyncio
async def test_unsigned_request_carries_no_signature_headers(webhook_transport):
    """Without a secret no timestamp/v2 header is sent, and repr hides secrets."""
    await deliver(
        DeliveryRequest(url="https://hooks.example.com/h", body=b"{}"), policy=FAST
    )

    sent = webhook_transport.requests[0]
    assert "X-Elder-Timestamp" not in sent.headers
    assert "X-Elder-Signature-V2" not in sent.headers
    assert "s3cr3t" not in repr(
        DeliveryRequest(url="https://x.example.com", body=b"", signing_secret="s3cr3t")
    )


# --- M2: caps, fairness, load shedding -------------------------------------


@pytest.mark.asyncio
async def test_storm_of_enqueues_stays_under_the_global_cap(monkeypatch):
    """5000 enqueues admit at most the cap; the rest are shed, not queued."""
    log = LogRecorder()
    monkeypatch.setattr(delivery, "logger", log)
    shed_metric = MagicMock()
    monkeypatch.setattr(delivery, "_shed_counter", shed_metric)
    release = asyncio.Event()
    started = 0

    async def held() -> None:
        """Stay pending until the test releases the storm."""
        nonlocal started
        started += 1
        await release.wait()

    before = len(delivery._background_tasks)
    admitted = 0
    for index in range(5000):
        tenant = f"tenant-{index % 50:02d}"
        task = delivery.schedule_background(
            held(), tenant=tenant, host="hooks.example.com"
        )
        admitted += task is not None
        assert delivery.pending_count() <= delivery.max_pending()

    assert admitted == delivery.max_pending() == 2000
    assert delivery.pending_count() == 2000
    assert len(delivery._background_tasks) - before == 2000
    shed = log.named("webhook_delivery_shed")
    assert len(shed) == 3000
    assert {entry["reason"] for entry in shed} == {delivery.SHED_GLOBAL_CAP}
    # Logs name tenant reference + bare host only; the metric only a reason.
    assert set(shed[0]) == {"reason", "tenant", "target_host", "shed"}
    assert shed_metric.add.call_count == 3000
    assert all(
        call.args[1] == {"reason": delivery.SHED_GLOBAL_CAP}
        for call in shed_metric.add.call_args_list
    )

    release.set()
    assert await delivery.drain_background(timeout=10) == 0
    assert started == 2000  # shed coroutines were closed, never run
    assert delivery.pending_count() == 0


@pytest.mark.asyncio
async def test_tenant_at_its_cap_does_not_block_another_tenant(monkeypatch):
    """Tenant A saturating its own cap leaves tenant B fully admitted."""
    monkeypatch.setattr(delivery, "logger", LogRecorder())
    release = asyncio.Event()

    def enqueue(tenant: str) -> bool:
        """Schedule one held delivery for ``tenant``; report admission."""
        return delivery.schedule_background(release.wait(), tenant=tenant) is not None

    admitted_a = sum(enqueue("tenant-a") for _ in range(500))
    assert admitted_a == delivery.max_pending_per_tenant() == 200
    assert delivery.pending_count("tenant-a") == 200
    assert delivery.logger.named("webhook_delivery_shed")[0]["reason"] == (
        delivery.SHED_TENANT_CAP
    )

    assert all(enqueue("tenant-b") for _ in range(200))
    assert delivery.pending_count("tenant-b") == 200
    assert enqueue("tenant-a") is False

    release.set()
    await delivery.drain_background(timeout=10)
    assert delivery.pending_count() == 0
    assert enqueue("tenant-a") is True
    await delivery.drain_background(timeout=10)


@pytest.mark.asyncio
async def test_busy_tenant_cannot_starve_another_tenants_sockets(
    monkeypatch, webhook_transport
):
    """With A's deliveries all stuck, B's delivery still completes."""
    monkeypatch.setenv("WEBHOOK_MAX_CONCURRENCY", "3")
    monkeypatch.setenv("WEBHOOK_MAX_CONCURRENCY_PER_TENANT", "2")
    stuck = asyncio.Event()
    in_flight_a = 0
    peak_a = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        """Hold tenant A's requests open; answer everyone else at once."""
        nonlocal in_flight_a, peak_a
        if request.url.path == "/a":
            in_flight_a += 1
            peak_a = max(peak_a, in_flight_a)
            await stuck.wait()
            in_flight_a -= 1
        return httpx.Response(200, text="ok")

    webhook_transport.handler = handler
    slow_policy = DeliveryPolicy(
        max_attempts=1, attempt_timeout=30.0, read_timeout=30.0
    )
    a_tasks = [
        asyncio.create_task(
            deliver(
                DeliveryRequest(
                    url="https://hooks.example.com/a", body=b"{}", tenant="tenant-a"
                ),
                policy=slow_policy,
            )
        )
        for _ in range(20)
    ]
    await asyncio.sleep(0.05)

    result_b = await asyncio.wait_for(
        deliver(
            DeliveryRequest(
                url="https://hooks.example.com/b", body=b"{}", tenant="tenant-b"
            ),
            policy=slow_policy,
        ),
        timeout=5,
    )

    assert result_b.success is True
    assert peak_a == 2  # never more than A's share of the 3 global sockets
    stuck.set()
    results_a = await asyncio.gather(*a_tasks)
    assert all(result.success for result in results_a)
    assert peak_a == 2
    # Idle tenant gates are dropped, so the map cannot grow without bound.
    assert delivery._tenant_gates[asyncio.get_running_loop()] == {}


@pytest.mark.asyncio
@patch("apps.api.auth.decorators.get_current_user")
async def test_explicit_sends_get_429_with_retry_after_when_shed(
    mock_get_user, async_client, generate_token, app, webhook_transport, monkeypatch
):
    """Broadcast and test routes answer 429 + Retry-After at the cap."""
    mock_get_user.return_value = MagicMock(id=1, is_superuser=True)
    monkeypatch.setenv("WEBHOOK_MAX_PENDING", "1")
    monkeypatch.setenv("WEBHOOK_SHED_RETRY_AFTER_SECONDS", "17")
    token = generate_token(tenant_id=1, scopes=ALL_SCOPES)
    headers = {"Authorization": f"Bearer {token}"}
    async with app.app_context():
        db = current_app.db
        now = datetime.now(UTC)
        org_id = db.organizations.insert(
            name=f"Shed Org {uuid4().hex[:6]}",
            tenant_id=1,
            created_at=now,
            updated_at=now,
        )
        db.commit()
        webhook_id = _make_webhook(db, 1, organization_id=org_id).id
    occupied = delivery.reserve_slot(None)
    assert occupied is not None

    broadcast = await async_client.post(
        "/api/v1/webhooks/broadcast",
        json={"event_type": "entity.created", "payload": {}, "organization_id": org_id},
        headers=headers,
    )
    tested = await async_client.post(
        f"/api/v1/webhooks/{webhook_id}/test", headers=headers
    )

    for resp in (broadcast, tested):
        assert resp.status_code == 429, (await resp.get_data()).decode()[:300]
        assert resp.headers["Retry-After"] == "17"
        body = await resp.get_json()
        assert body["reason"] == delivery.SHED_GLOBAL_CAP
        assert body["retry_after"] == 17
    assert webhook_transport.requests == []
    async with app.app_context():
        db = current_app.db
        # Refused before the outbox: nothing is left behind to be resumed.
        assert db(db.webhook_deliveries.webhook_id == webhook_id).count() == 0

    delivery.release_slot(occupied)
    retried = await async_client.post(
        f"/api/v1/webhooks/{webhook_id}/test", headers=headers
    )
    assert retried.status_code == 200
    assert delivery.pending_count() == 0


@pytest.mark.asyncio
@patch("apps.api.auth.decorators.get_current_user")
async def test_originating_request_succeeds_when_event_delivery_is_shed(
    mock_get_user, async_client, generate_token, app, webhook_transport, monkeypatch
):
    """Issue creation still returns 201 when its webhooks are shed."""
    mock_get_user.return_value = MagicMock(id=1, is_superuser=True)
    log = LogRecorder()
    monkeypatch.setattr(delivery, "logger", log)
    monkeypatch.setenv("WEBHOOK_MAX_PENDING", "1")
    token = generate_token(tenant_id=1, scopes=["issues:write"])
    async with app.app_context():
        db = current_app.db
        now = datetime.now(UTC)
        org_id = db.organizations.insert(
            name=f"Shed Issue Org {uuid4().hex[:6]}",
            tenant_id=1,
            created_at=now,
            updated_at=now,
        )
        db.commit()
        webhook_id = _make_webhook(db, 1, events=["issue.assigned"]).id
    occupied = delivery.reserve_slot(None)

    resp = await async_client.post(
        "/api/v1/issues",
        json={
            "title": "Created during a webhook storm",
            "organization_id": org_id,
            "assignee_id": 1,
            "assignee_type": "identity",
        },
        headers={"Authorization": f"Bearer {token}"},
    )

    assert resp.status_code == 201, (await resp.get_data()).decode()[:300]
    await asyncio.sleep(0.2)
    shed = log.named("webhook_delivery_shed")
    assert shed and all(entry["reason"] == delivery.SHED_GLOBAL_CAP for entry in shed)
    assert webhook_transport.requests == []
    async with app.app_context():
        db = current_app.db
        assert db(db.webhook_deliveries.webhook_id == webhook_id).count() == 0
    delivery.release_slot(occupied)


@pytest.mark.asyncio
async def test_user_created_scheduling_never_raises_when_shed(app, monkeypatch):
    """A shed user.created delivery is dropped silently for the caller."""
    log = LogRecorder()
    monkeypatch.setattr(delivery, "logger", log)
    monkeypatch.setenv("WEBHOOK_MAX_PENDING", "1")
    occupied = delivery.reserve_slot(None)
    async with app.app_context():
        event = user_events.UserCreatedEvent(
            user_id=1,
            tenant_id=1,
            user_source="identities",
            identity_type="human",
            auth_provider="local",
        )
        before = len(delivery._background_tasks)

        assert user_events.schedule_user_created_webhooks(current_app.db, event) is None

    assert len(delivery._background_tasks) == before
    assert len(log.named("webhook_delivery_shed")) == 1
    delivery.release_slot(occupied)


# --- M3: outbox + restart recovery -----------------------------------------


@pytest.mark.asyncio
async def test_outbox_row_is_pending_before_send_and_retrying_between_attempts(
    app, webhook_transport
):
    """The row exists as pending before I/O and tracks every attempt."""
    async with app.app_context():
        db = current_app.db
        webhook = _make_webhook(db, _make_tenant(db, "Outbox states"))
        seen: list[tuple[str, int]] = []

        def handler(request: httpx.Request) -> httpx.Response:
            """Record the row's state as each attempt reaches the wire."""
            row = db(db.webhook_deliveries.webhook_id == webhook.id).select().first()
            db.commit()
            seen.append((row.status, row.attempt_count))
            return httpx.Response(500 if len(seen) < 3 else 200, text="x")

        webhook_transport.handler = handler
        outcome = await WebhookService(db).deliver_event(
            webhook, "entity.created", {"n": 1}, kind="native"
        )

        assert outcome["success"] is True and outcome["attempts"] == 3
        assert seen == [("pending", 0), ("retrying", 1), ("retrying", 2)]
        row = db.webhook_deliveries[outcome["delivery_id"]]
        assert (row.status, row.attempt_count) == ("success", 3)
        keys = {r.headers["Idempotency-Key"] for r in webhook_transport.requests}
        assert keys == {delivery_idempotency_key(webhook.id, row.id)}


@pytest.mark.asyncio
async def test_recovery_resumes_orphaned_rows_with_the_same_idempotency_key(
    app, webhook_transport, _instant_recovery
):
    """Rows left pending/retrying by a dead process are delivered on startup."""
    async with app.app_context():
        db = current_app.db
        webhook = _make_webhook(db, _make_tenant(db, "Recovery"), secret="s3cr3t")
        orphan = _insert_outbox_row(db, webhook.id, age_seconds=600)
        retrying = _insert_outbox_row(
            db, webhook.id, age_seconds=900, status="retrying", attempts=2
        )
        fresh = _insert_outbox_row(db, webhook.id, age_seconds=5)
        done = _insert_outbox_row(db, webhook.id, age_seconds=900, status="failed")

        counts = await WebhookService(db).recover_pending_deliveries()

        ours = [r for r in webhook_transport.requests if r.url.path == "/hardening"]
        assert sorted(r.headers["Idempotency-Key"] for r in ours) == sorted(
            delivery_idempotency_key(webhook.id, i) for i in (orphan, retrying)
        )
        assert counts["resumed"] >= 2
        assert db.webhook_deliveries[orphan].status == "success"
        assert db.webhook_deliveries[orphan].attempt_count == 1
        assert db.webhook_deliveries[retrying].status == "success"
        assert db.webhook_deliveries[retrying].attempt_count == 3
        # Too young to be an orphan, and terminal rows, are left alone.
        assert db.webhook_deliveries[fresh].status == "pending"
        assert db.webhook_deliveries[done].status == "failed"
        # The resumed body is the stored payload, signed like the original.
        body = ours[0].content
        assert (
            ours[0].headers["X-Elder-Signature"]
            == hmac.new(b"s3cr3t", body, hashlib.sha256).hexdigest()
        )


@pytest.mark.asyncio
async def test_two_replicas_recovering_at_once_send_each_row_once(
    app, webhook_transport, _instant_recovery
):
    """The atomic claim lets exactly one of several racing passes send."""
    async with app.app_context():
        db = current_app.db
        webhook = _make_webhook(
            db, _make_tenant(db, "Replica race"), url="https://hooks.example.com/race"
        )
        rows = [_insert_outbox_row(db, webhook.id, age_seconds=600) for _ in range(6)]

        results = await asyncio.gather(
            *(WebhookService(db).recover_pending_deliveries() for _ in range(3))
        )

        sent = [r for r in webhook_transport.requests if r.url.path == "/race"]
        assert len(sent) == len(rows)
        assert len({r.headers["Idempotency-Key"] for r in sent}) == len(rows)
        assert all(db.webhook_deliveries[i].status == "success" for i in rows)
        assert all(db.webhook_deliveries[i].attempt_count == 1 for i in rows)
        assert sum(r["resumed"] for r in results) >= len(rows)


@pytest.mark.asyncio
async def test_recovery_leaves_rows_another_pass_just_claimed(
    app, webhook_transport, _instant_recovery
):
    """A freshly claimed row is in flight elsewhere, not an orphan to resume."""
    async with app.app_context():
        db = current_app.db
        webhook = _make_webhook(
            db, _make_tenant(db, "Claimed"), url="https://hooks.example.com/claimed"
        )
        row_id = _insert_outbox_row(
            db, webhook.id, age_seconds=600, status="retrying", attempts=1
        )
        db(db.webhook_deliveries.id == row_id).update(claimed_at=datetime.now(UTC))
        db.commit()

        counts = await WebhookService(db).recover_pending_deliveries()

        assert [r for r in webhook_transport.requests if r.url.path == "/claimed"] == []
        assert counts["candidates"] == 0
        row = db.webhook_deliveries[row_id]
        assert (row.status, row.attempt_count) == ("retrying", 1)


@pytest.mark.asyncio
async def test_recovery_skips_rows_a_live_owner_is_still_advancing(
    app, webhook_transport, _instant_recovery
):
    """A row whose attempt count moved during observation is not taken over."""
    async with app.app_context():
        db = current_app.db
        webhook = _make_webhook(
            db, _make_tenant(db, "Live owner"), url="https://hooks.example.com/live"
        )
        row_id = _insert_outbox_row(
            db, webhook.id, age_seconds=600, status="retrying", attempts=1
        )

        async def owner_makes_progress(_delay: float) -> None:
            """Stand in for the observation window: the owner retries meanwhile."""
            db(db.webhook_deliveries.id == row_id).update(attempt_count=2)
            db.commit()

        counts = await WebhookService(db).recover_pending_deliveries(
            sleep=owner_makes_progress
        )

        assert [r for r in webhook_transport.requests if r.url.path == "/live"] == []
        assert counts["skipped"] >= 1
        row = db.webhook_deliveries[row_id]
        assert (row.status, row.attempt_count) == ("retrying", 2)


@pytest.mark.asyncio
async def test_recovery_expires_rows_past_the_max_age(
    app, webhook_transport, _instant_recovery, monkeypatch
):
    """Deliveries older than the cut-off are marked expired, never sent."""
    monkeypatch.setenv("WEBHOOK_RECOVERY_MAX_AGE_SECONDS", "3600")
    async with app.app_context():
        db = current_app.db
        webhook = _make_webhook(
            db, _make_tenant(db, "Expiry"), url="https://hooks.example.com/expired"
        )
        stale = _insert_outbox_row(db, webhook.id, age_seconds=7200)
        disabled_hook = _make_webhook(
            db, webhook.tenant_id, url="https://hooks.example.com/off", is_active=False
        )
        orphan_of_disabled = _insert_outbox_row(db, disabled_hook.id, age_seconds=600)

        counts = await WebhookService(db).recover_pending_deliveries()

        paths = {r.url.path for r in webhook_transport.requests}
        assert "/expired" not in paths and "/off" not in paths
        assert counts["expired"] >= 1
        assert db.webhook_deliveries[stale].status == "expired"
        assert db.webhook_deliveries[stale].error_message == "expired before delivery"
        assert db.webhook_deliveries[orphan_of_disabled].status == "failed"


@pytest.mark.asyncio
async def test_recovery_is_bounded_and_can_be_disabled(
    app, webhook_transport, _instant_recovery, monkeypatch
):
    """The pass handles at most the batch size; a batch of 0 turns it off."""
    async with app.app_context():
        db = current_app.db
        webhook = _make_webhook(
            db, _make_tenant(db, "Bounded"), url="https://hooks.example.com/bounded"
        )
        for _ in range(3):
            _insert_outbox_row(db, webhook.id, age_seconds=600)

        monkeypatch.setenv("WEBHOOK_RECOVERY_BATCH", "0")
        assert (await WebhookService(db).recover_pending_deliveries())[
            "candidates"
        ] == 0
        assert webhook_transport.requests == []

        monkeypatch.setenv("WEBHOOK_RECOVERY_BATCH", "2")
        assert (await WebhookService(db).recover_pending_deliveries())[
            "candidates"
        ] == 2


@pytest.mark.asyncio
async def test_drain_reports_each_delivery_still_pending_at_shutdown(monkeypatch):
    """Unfinished deliveries are named in a WARN, not dropped silently."""
    log = LogRecorder()
    monkeypatch.setattr(delivery, "logger", log)
    metric = MagicMock()
    monkeypatch.setattr(delivery, "_shutdown_pending_counter", metric)
    release = asyncio.Event()
    task = delivery.schedule_background(
        release.wait(),
        tenant="tenant-uuid",
        host="hooks.example.com",
        delivery_id=4242,
    )

    unfinished = await delivery.drain_background(timeout=0.05)

    assert unfinished >= 1
    warned = [
        entry
        for entry in log.named("webhook_delivery_pending_at_shutdown")
        if entry["delivery_id"] == 4242
    ]
    assert warned == [
        {
            "task": "webhook-delivery",
            "delivery_id": 4242,
            "tenant": "tenant-uuid",
            "target_host": "hooks.example.com",
            "recoverable": True,
        }
    ]
    metric.add.assert_called_once_with(unfinished)
    release.set()
    await task


# --- M1 bridge + thread pool isolation -------------------------------------


@pytest.mark.asyncio
async def test_run_blocking_refuses_to_run_inside_a_running_loop():
    """The sync bridge fails loudly instead of blocking or nesting a loop."""

    async def never_awaited() -> int:
        """Stand-in coroutine that must not run."""
        return 1

    with pytest.raises(RuntimeError, match="running event loop"):
        delivery.run_blocking(never_awaited())


def test_sync_dispatch_uses_the_pinned_engine(app, webhook_transport):
    """With no running loop the dispatcher still sends via the engine."""

    async def scenario() -> tuple[Any, int]:
        """Create a tenant + user.created webhook inside an app context."""
        async with app.app_context():
            db = current_app.db
            tenant_id = _make_tenant(db, "Sync bridge")
            _make_webhook(
                db,
                tenant_id,
                url="https://hooks.example.com/sync",
                events=["user.created"],
            )
            return db, tenant_id

    db, tenant_id = asyncio.run(scenario())
    event = user_events.UserCreatedEvent(
        user_id=5,
        tenant_id=tenant_id,
        user_source="identities",
        identity_type="human",
        auth_provider="local",
    )

    results = user_events.dispatch_user_created_webhooks_sync(db, event)

    assert [r["success"] for r in results] == [True]
    sent = [r for r in webhook_transport.requests if r.url.path == "/sync"]
    # Dialled at the vetted address with the hostname preserved for routing.
    assert [(r.url.host, r.headers["Host"]) for r in sent] == [
        ("93.184.216.34", "hooks.example.com")
    ]
    assert not hasattr(user_events, "requests")


@pytest.mark.asyncio
async def test_delivery_blocking_work_runs_on_the_dedicated_pool():
    """DNS/DB work never lands on the event loop's shared default executor."""
    name = await delivery.run_in_io_pool(lambda: threading.current_thread().name)

    assert name.startswith("webhook-io")
    assert delivery._executor()._max_workers <= 64
