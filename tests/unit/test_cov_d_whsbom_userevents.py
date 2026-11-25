"""Coverage tests for ``apps/api/services/webhooks/user_events.py``.

Matching and persistence run on the real schema; the outbound HTTP transport
is the shared ``webhook_transport`` stub.
"""

# flake8: noqa: E501

import asyncio
import uuid
from types import SimpleNamespace

import pytest

from apps.api.services.webhooks import user_events as ue
from apps.api.services.webhooks.service import WebhookService
from shared.webhooks import delivery as _delivery
from tests.unit import _cov_d_helpers as _cov_helpers
from tests.unit._cov_d_helpers import in_db

#: Re-export the shared two-tenant fixture for pytest discovery in this module.
world = _cov_helpers.world


def _event(tenant: int, **kw) -> ue.UserCreatedEvent:
    """Build a user-created event for ``tenant``."""
    base = {
        "user_id": 5,
        "tenant_id": tenant,
        "user_source": "identities",
        "identity_type": "human",
        "auth_provider": "local",
    }
    base.update(kw)
    return ue.UserCreatedEvent(**base)


def _hook(
    db,
    tenant: int,
    events: list[str],
    active: bool = True,
    url: str = "https://hooks.example.com/u",
) -> int:
    """Insert a webhook subscribed to ``events``; return its id."""
    wid = WebhookService(db).create_webhook(
        tenant_id=tenant,
        name="u-" + uuid.uuid4().hex[:6],
        url=url,
        events=events,
        is_active=active,
    )["id"]
    return wid


def test_admin_classification() -> None:
    """Admin detection spans every role attribute and normalises case."""
    assert ue.is_admin_user() is False
    assert ue.is_admin_user(is_superuser=True)
    assert ue.is_admin_user(portal_role=" Admin ")
    assert ue.is_admin_user(portal_role="superadmin")
    assert ue.is_admin_user(global_role="ADMIN")
    assert ue.is_admin_user(tenant_role="admin")
    assert not ue.is_admin_user(tenant_role="superadmin", portal_role="viewer")
    enum_like = SimpleNamespace(value="human")
    ev = _event(1, identity_type=enum_like, auth_provider=SimpleNamespace(value="oidc"))
    assert (ev.identity_type, ev.auth_provider) == ("human", "oidc")
    assert ue.event_types_for_user(ev) == ["user.created"]
    admin = _event(1, is_superuser=True)
    assert ue.event_types_for_user(admin) == ["user.created", "admin_user.created"]
    payload = ue.build_user_created_payload(admin, "user.created")
    assert payload["user"]["is_admin"] is True and payload["tenant_id"] == 1


def test_extract_actor_id() -> None:
    """Only genuine integer ids are accepted as the actor."""
    assert ue.extract_actor_id(SimpleNamespace(id=4)) == 4
    assert ue.extract_actor_id(SimpleNamespace(id=True)) is None
    assert ue.extract_actor_id(SimpleNamespace(id="4")) is None
    assert ue.extract_actor_id(object()) is None


def test_payload_safe_and_failure_result(monkeypatch: pytest.MonkeyPatch) -> None:
    """Payload build failures are contained; dispatch failures get a uniform result."""
    ev = _event(1)
    assert ue._build_payload_safe(ev, "user.created")["event"] == "user.created"
    monkeypatch.setattr(ue, "build_user_created_payload", lambda *a: 1 / 0)
    assert ue._build_payload_safe(ev, "user.created") is None
    res = ue._dispatch_failure_result(
        SimpleNamespace(id=3), "user.created", ValueError("x")
    )
    assert res == {
        "webhook_id": 3,
        "event_type": "user.created",
        "success": False,
        "error": "x",
    }
    assert (
        ue._dispatch_failure_result(object(), "e", ValueError("y"))["webhook_id"]
        is None
    )


@pytest.mark.asyncio
async def test_find_matching_and_send(app, world, webhook_transport) -> None:
    """Matching honours tenant, activity and subscription; delivery results are reported."""
    a, b = world["a"], world["b"]
    db = world["db"]
    ids = await in_db(
        lambda: (
            _hook(db, a["tenant"], ["user.created"]),
            _hook(db, a["tenant"], ["admin_user.created"]),
            _hook(db, a["tenant"], ["user.created"], active=False),
            _hook(db, b["tenant"], ["user.created"]),
        )
    )
    assert ue.find_matching_user_webhooks(db, 0, "user.created") == []
    matched = ue.find_matching_user_webhooks(db, a["tenant"], "user.created")
    assert [m.id for m in matched] == [ids[0]]
    assert ue.find_matching_user_webhooks(db, a["tenant"], "nothing") == []

    assert await ue.send_user_created_webhooks(db, _event(0)) == []
    results = await ue.send_user_created_webhooks(
        db, _event(a["tenant"], is_superuser=True)
    )
    assert sorted(r["event_type"] for r in results) == [
        "admin_user.created",
        "user.created",
    ]
    assert all(
        r["success"] and r["delivery_id"] and r["http_status"] == 200 for r in results
    )
    assert await ue.send_user_created_webhooks(db, _event(a["tenant"] + 10**6)) == []

    webhook_transport.respond_with(400)
    failed = await ue.send_user_created_webhooks(db, _event(a["tenant"]))
    assert failed[0]["success"] is False and failed[0]["http_status"] == 400


@pytest.mark.asyncio
async def test_send_contains_internal_failures(
    app, world, webhook_transport, monkeypatch
) -> None:
    """Match errors and payload errors skip an event type without raising."""
    a = world["a"]
    db = world["db"]
    await in_db(lambda: _hook(db, a["tenant"], ["user.created"]))

    real = _delivery.run_in_io_pool

    async def failing_pool(fn, *args, **kw):
        """Fail only the matching step."""
        if fn is ue.find_matching_user_webhooks:
            raise RuntimeError("db gone")
        return await real(fn, *args, **kw)

    monkeypatch.setattr(ue._delivery, "run_in_io_pool", failing_pool)
    assert await ue.send_user_created_webhooks(db, _event(a["tenant"])) == []
    monkeypatch.setattr(ue._delivery, "run_in_io_pool", real)
    monkeypatch.setattr(ue, "build_user_created_payload", lambda *a: 1 / 0)
    assert await ue.send_user_created_webhooks(db, _event(a["tenant"])) == []


@pytest.mark.asyncio
async def test_schedule_inside_running_loop(
    app, world, webhook_transport, monkeypatch
) -> None:
    """Inside a running loop delivery is scheduled in the background and never raises."""
    a = world["a"]
    db = world["db"]
    await in_db(lambda: _hook(db, a["tenant"], ["user.created"]))
    ue.schedule_user_created_webhooks(db, _event(0))
    ue.schedule_user_created_webhooks(db, _event(a["tenant"]))
    for _ in range(300):
        if webhook_transport.requests and not ue._background_tasks:
            break
        await asyncio.sleep(0.02)
    assert len(webhook_transport.requests) >= 1

    def boom(coro, **kw):
        """Close the coroutine and fail scheduling."""
        coro.close()
        raise RuntimeError("shed")

    monkeypatch.setattr(ue._delivery, "schedule_background", boom)
    ue.schedule_user_created_webhooks(db, _event(a["tenant"]))


def test_sync_dispatch_without_loop(app, world, webhook_transport, monkeypatch) -> None:
    """With no running loop, delivery runs inline on a private loop."""
    a = world["a"]
    db = world["db"]
    _hook(db, a["tenant"], ["user.created"])
    assert ue.dispatch_user_created_webhooks_sync(db, _event(0)) == []
    out = ue.dispatch_user_created_webhooks_sync(db, _event(a["tenant"]))
    assert out and out[0]["success"] is True
    ue.schedule_user_created_webhooks(db, _event(0))
    before = len(webhook_transport.requests)
    ue.schedule_user_created_webhooks(db, _event(a["tenant"]))
    assert len(webhook_transport.requests) == before + 1

    monkeypatch.setattr(ue, "dispatch_user_created_webhooks_sync", lambda *a: 1 / 0)
    ue.schedule_user_created_webhooks(db, _event(a["tenant"]))
