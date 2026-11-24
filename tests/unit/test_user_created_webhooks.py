"""Unit and integration tests for outbound ``user.created`` and
``admin_user.created`` webhooks across all 7 user-creation paths.
"""

from __future__ import annotations

import asyncio
import json
import time
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from quart import current_app
from webhook_http import ENGINE_HTTP, WebhookPostRecorder

from apps.api.services.portal_auth.service import PortalAuthService
from apps.api.services.sso.oidc_service import OIDCService
from apps.api.services.sso.saml_service import SAMLService
from apps.api.services.sso.scim_service import SCIMService
from apps.api.services.webhooks import (
    ADMIN_USER_CREATED_EVENT_TYPE,
    USER_CREATED_EVENT_TYPE,
    USER_CREATED_EVENT_TYPES,
    UserCreatedEvent,
    build_user_created_payload,
    dispatch_user_created_webhooks_sync,
    event_types_for_user,
    find_matching_user_webhooks,
    is_admin_user,
    send_user_created_webhooks,
)
from apps.api.services.webhooks.service import generate_signature
from apps.api.services.webhooks.user_events import _background_tasks

_FORBIDDEN_PII_KEYS = frozenset(
    {"email", "username", "full_name", "password", "password_hash", "mfa_secret"}
)


def _collect_keys(obj: Any) -> set[str]:
    """Recursively collect every dict key present anywhere inside ``obj``."""
    keys: set[str] = set()
    if isinstance(obj, dict):
        for k, v in obj.items():
            keys.add(str(k))
            keys.update(_collect_keys(v))
    elif isinstance(obj, list):
        for item in obj:
            keys.update(_collect_keys(item))
    return keys


def _make_tenant(db: Any, name: str) -> int:
    """Create a dedicated tenant row and return its ID."""
    tid = db.tenants.insert(
        name=name,
        slug=f"{name.lower().replace(' ', '-')}-{uuid4().hex[:8]}",
        is_active=True,
    )
    db.commit()
    return tid


def _insert_webhook(
    db: Any, tenant_id: int, events: list[str], **overrides: Any
) -> int:
    """Insert a tenant-scoped webhook row with a unique village_id."""
    now = datetime.now(UTC)
    defaults: dict[str, Any] = {
        "tenant_id": tenant_id,
        "village_id": f"{tenant_id:08x}-{uuid4().hex[:16]}",
        "name": "User Events Webhook",
        "url": "https://hooks.example.com/users",
        "events": events,
        "is_active": True,
        "secret": "user-webhook-secret",
        "created_at": now,
        "updated_at": now,
    }
    defaults.update(overrides)
    webhook_id = db.webhooks.insert(**defaults)
    db.commit()
    return webhook_id


def _event(tenant_id: int = 1, **overrides: Any) -> UserCreatedEvent:
    """Build a UserCreatedEvent with sensible regular-user defaults."""
    defaults: dict[str, Any] = {
        "user_id": 101,
        "tenant_id": tenant_id,
        "user_source": "identity",
        "identity_type": "human",
        "auth_provider": "local",
        "is_superuser": False,
        "portal_role": "observer",
        "global_role": None,
        "tenant_role": None,
        "organization_id": 5,
        "is_active": True,
        "actor_id": 7,
    }
    defaults.update(overrides)
    return UserCreatedEvent(**defaults)


async def _drain_background_tasks() -> None:
    """Wait until all scheduled user-created webhook tasks complete."""
    while _background_tasks:
        pending = list(_background_tasks)
        await asyncio.gather(*pending, return_exceptions=True)


async def _wait_for_deliveries(
    db: Any,
    webhook_id: int,
    event_type: str,
    expected_count: int = 1,
    timeout: float = 5.0,
) -> list[Any]:
    """Poll webhook_deliveries for ``webhook_id`` and ``event_type``."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        rows = db(
            (db.webhook_deliveries.webhook_id == webhook_id)
            & (db.webhook_deliveries.event_type == event_type)
        ).select(orderby=db.webhook_deliveries.id)
        if len(rows) >= expected_count:
            return list(rows)
        await asyncio.sleep(0.05)
    return []


# ---------------------------------------------------------------------------
# Pure unit tests: is_admin_user, event_types_for_user, payload shape & PII
# ---------------------------------------------------------------------------


class TestIsAdminUser:
    """Pure classification tests for ``is_admin_user`` across all role inputs."""

    def test_constants(self):
        assert USER_CREATED_EVENT_TYPE == "user.created"
        assert ADMIN_USER_CREATED_EVENT_TYPE == "admin_user.created"
        assert USER_CREATED_EVENT_TYPES == ("user.created", "admin_user.created")

    @pytest.mark.parametrize(
        ("kwargs", "expected"),
        [
            ({}, False),
            ({"is_superuser": False, "portal_role": "observer"}, False),
            ({"portal_role": "editor"}, False),
            ({"portal_role": "viewer"}, False),
            ({"global_role": "support"}, False),
            ({"tenant_role": "reader"}, False),
            ({"tenant_role": "maintainer"}, False),
            ({"tenant_role": "superadmin"}, False),
            ({"is_superuser": True}, True),
            ({"is_superuser": True, "portal_role": "observer"}, True),
            ({"portal_role": "admin"}, True),
            ({"portal_role": " ADMIN "}, True),
            ({"portal_role": "superadmin"}, True),
            ({"global_role": "admin"}, True),
            ({"global_role": "superadmin"}, True),
            ({"global_role": " SUPERADMIN "}, True),
            ({"tenant_role": "admin"}, True),
            ({"tenant_role": " Admin "}, True),
            (
                {
                    "is_superuser": True,
                    "portal_role": "admin",
                    "global_role": "admin",
                    "tenant_role": "admin",
                },
                True,
            ),
        ],
    )
    def test_combinations(self, kwargs: dict[str, Any], expected: bool):
        assert is_admin_user(**kwargs) is expected

    def test_event_types_for_user(self):
        regular = _event(is_superuser=False, portal_role="observer")
        assert regular.is_admin is False
        assert event_types_for_user(regular) == ["user.created"]

        admin = _event(portal_role="admin")
        assert admin.is_admin is True
        assert event_types_for_user(admin) == ["user.created", "admin_user.created"]


class TestPayloadShapeAndPiiFreedom:
    """Verify exact payload structure and zero PII leakage."""

    @pytest.mark.parametrize(
        ("event_overrides", "event_type", "expected_is_admin"),
        [
            ({}, "user.created", False),
            (
                {
                    "user_source": "portal_user",
                    "identity_type": "portal_user",
                    "auth_provider": "oidc",
                    "tenant_role": "admin",
                    "global_role": "superadmin",
                    "actor_id": None,
                },
                "admin_user.created",
                True,
            ),
        ],
    )
    def test_payload_has_exact_shape_and_zero_pii(
        self,
        event_overrides: dict[str, Any],
        event_type: str,
        expected_is_admin: bool,
    ):
        event = _event(tenant_id=12, **event_overrides)
        payload = build_user_created_payload(event, event_type)

        assert set(payload.keys()) == {"event", "user", "actor", "tenant_id", "ts"}
        assert payload["event"] == event_type
        assert payload["tenant_id"] == 12
        assert payload["actor"] == event.actor_id
        assert isinstance(payload["ts"], str) and len(payload["ts"]) > 0

        user_obj = payload["user"]
        assert set(user_obj.keys()) == {
            "id",
            "user_source",
            "identity_type",
            "auth_provider",
            "is_admin",
            "is_superuser",
            "portal_role",
            "global_role",
            "tenant_role",
            "organization_id",
            "is_active",
        }
        assert user_obj["is_admin"] is expected_is_admin
        assert not (_collect_keys(payload) & _FORBIDDEN_PII_KEYS)
        # Must serialize cleanly without TypeError (e.g. from Enums)
        json.dumps(payload, sort_keys=True)


# ---------------------------------------------------------------------------
# Matching, tenant isolation, active filtering, signing & failure resilience
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_matching_enforces_tenant_isolation_and_active_flag(app):
    async with app.app_context():
        db = current_app.db
        tenant_a = _make_tenant(db, "User Webhook Match A")
        tenant_b = _make_tenant(db, "User Webhook Match B")

        own_active = _insert_webhook(db, tenant_a, ["user.created"])
        own_inactive = _insert_webhook(db, tenant_a, ["user.created"], is_active=False)
        own_other_event = _insert_webhook(db, tenant_a, ["issue.assigned"])
        other_tenant = _insert_webhook(db, tenant_b, ["user.created"])

        matched_ids = {
            w.id
            for w in find_matching_user_webhooks(db, tenant_a, USER_CREATED_EVENT_TYPE)
        }
        assert own_active in matched_ids
        assert own_inactive not in matched_ids
        assert own_other_event not in matched_ids
        assert other_tenant not in matched_ids
        assert find_matching_user_webhooks(db, 0, USER_CREATED_EVENT_TYPE) == []


@pytest.mark.asyncio
@patch(ENGINE_HTTP, new_callable=WebhookPostRecorder)
async def test_delivery_signs_hmac_merges_headers_and_records_row(mock_post, app):
    mock_post.return_value = MagicMock(status_code=201, text="created")

    async with app.app_context():
        db = current_app.db
        tenant_id = _make_tenant(db, "User Webhook Delivery Signed")
        webhook_id = _insert_webhook(
            db,
            tenant_id,
            ["user.created"],
            secret="top-secret-key",
            headers={"X-Custom-Header": "elder-test"},
        )

        results = await send_user_created_webhooks(db, _event(tenant_id=tenant_id))

        assert len(results) == 1
        assert results[0]["success"] is True
        assert results[0]["http_status"] == 201
        mock_post.assert_called_once()

        sent_headers = mock_post.call_args.kwargs["headers"]
        sent_body = mock_post.call_args.kwargs["data"]
        assert isinstance(sent_body, bytes)
        assert sent_headers["X-Custom-Header"] == "elder-test"
        assert sent_headers["Content-Type"] == "application/json"
        assert sent_headers["X-Elder-Signature"] == generate_signature(
            "top-secret-key", sent_body.decode("utf-8")
        )

        parsed_body = json.loads(sent_body.decode("utf-8"))
        assert not (_collect_keys(parsed_body) & _FORBIDDEN_PII_KEYS)

        delivery = (
            db(
                (db.webhook_deliveries.webhook_id == webhook_id)
                & (db.webhook_deliveries.event_type == "user.created")
            )
            .select()
            .first()
        )
        assert delivery is not None
        assert delivery.status == "success"
        assert delivery.http_status == 201
        assert delivery.attempt_count == 1
        assert delivery.delivered_at is not None


@pytest.mark.asyncio
@patch(ENGINE_HTTP, new_callable=WebhookPostRecorder)
async def test_delivery_failure_does_not_raise_and_records_error(mock_post, app):
    mock_post.side_effect = ConnectionError("connection refused")

    async with app.app_context():
        db = current_app.db
        tenant_id = _make_tenant(db, "User Webhook Failure")
        webhook_id = _insert_webhook(db, tenant_id, ["user.created"])

        results = await send_user_created_webhooks(db, _event(tenant_id=tenant_id))

        assert len(results) == 1
        assert results[0]["success"] is False
        assert "connection refused" in results[0]["error"]

        delivery = (
            db(
                (db.webhook_deliveries.webhook_id == webhook_id)
                & (db.webhook_deliveries.event_type == "user.created")
            )
            .select()
            .first()
        )
        assert delivery is not None
        assert delivery.status == "failed"
        assert "connection refused" in (delivery.error_message or "")


@pytest.mark.asyncio
@patch(ENGINE_HTTP, new_callable=WebhookPostRecorder)
async def test_regular_vs_admin_event_routing(mock_post, app):
    mock_post.return_value = MagicMock(status_code=200, text="ok")

    async with app.app_context():
        db = current_app.db
        tenant_id = _make_tenant(db, "User Webhook Routing")
        w_user = _insert_webhook(db, tenant_id, ["user.created"])
        w_admin = _insert_webhook(db, tenant_id, ["admin_user.created"])

        # Regular user -> fires ONLY user.created; 0 deliveries to admin_user.created
        reg_results = await send_user_created_webhooks(
            db, _event(tenant_id=tenant_id, is_superuser=False, portal_role="observer")
        )
        assert len(reg_results) == 1
        assert reg_results[0]["event_type"] == "user.created"
        assert reg_results[0]["webhook_id"] == w_user

        admin_rows_after_regular = db(
            db.webhook_deliveries.webhook_id == w_admin
        ).select()
        assert len(admin_rows_after_regular) == 0

        # Admin user -> fires BOTH user.created and admin_user.created
        mock_post.reset_mock()
        admin_results = await send_user_created_webhooks(
            db, _event(tenant_id=tenant_id, portal_role="admin")
        )
        assert len(admin_results) == 2
        assert {r["event_type"] for r in admin_results} == {
            "user.created",
            "admin_user.created",
        }
        assert {r["webhook_id"] for r in admin_results} == {w_user, w_admin}
        assert mock_post.call_count == 2


def test_sync_dispatch_delivers_outside_event_loop(app):
    """Verify dispatch_user_created_webhooks_sync works in synchronous code."""
    db = app.db
    tenant_id = _make_tenant(db, "User Webhook Sync")
    webhook_id = _insert_webhook(db, tenant_id, ["user.created", "admin_user.created"])

    with patch(ENGINE_HTTP, new_callable=WebhookPostRecorder) as mock_post:
        mock_post.return_value = MagicMock(status_code=200, text="ok")
        results = dispatch_user_created_webhooks_sync(
            db, _event(tenant_id=tenant_id, tenant_role="admin")
        )

    assert len(results) == 2
    assert {r["event_type"] for r in results} == {
        "user.created",
        "admin_user.created",
    }
    deliveries = db(db.webhook_deliveries.webhook_id == webhook_id).select()
    assert len(deliveries) == 2


# ---------------------------------------------------------------------------
# Integration tests across all 7 user-creation sites
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@patch(ENGINE_HTTP, new_callable=WebhookPostRecorder)
@patch("apps.api.auth.decorators.get_current_user")
async def test_users_create_route_regular_and_admin(
    mock_get_user, mock_post, async_client, app
):
    mock_post.return_value = MagicMock(status_code=200, text="ok")

    async with app.app_context():
        db = current_app.db
        tenant_id = _make_tenant(db, "Route Users Create")
        w_user = _insert_webhook(db, tenant_id, ["user.created"])
        w_admin = _insert_webhook(db, tenant_id, ["admin_user.created"])

    mock_get_user.return_value = MagicMock(
        id=901, is_superuser=True, tenant_id=tenant_id
    )

    # Regular user
    reg_username = f"reg-user-{uuid4().hex[:8]}"
    resp = await async_client.post(
        "/api/v1/users",
        json={
            "username": reg_username,
            "password": "Password123!",
            "email": f"{reg_username}@example.com",
            "portal_role": "observer",
            "is_superuser": False,
            "tenant_id": tenant_id,
        },
        headers={"Authorization": "Bearer test-token"},
    )
    assert resp.status_code == 201, (await resp.get_data()).decode()[:300]
    reg_body = json.loads(await resp.get_data())

    await _drain_background_tasks()
    async with app.app_context():
        db = current_app.db
        user_deliveries = await _wait_for_deliveries(db, w_user, "user.created", 1)
        assert len(user_deliveries) == 1
        assert user_deliveries[0].request_payload["user"]["id"] == reg_body["id"]
        assert user_deliveries[0].request_payload["user"]["is_admin"] is False
        assert user_deliveries[0].request_payload["actor"] == 901
        assert not (
            _collect_keys(user_deliveries[0].request_payload) & _FORBIDDEN_PII_KEYS
        )
        assert len(db(db.webhook_deliveries.webhook_id == w_admin).select()) == 0

    # Admin user (superuser + admin role)
    admin_username = f"adm-user-{uuid4().hex[:8]}"
    resp2 = await async_client.post(
        "/api/v1/users",
        json={
            "username": admin_username,
            "password": "Password123!",
            "email": f"{admin_username}@example.com",
            "portal_role": "admin",
            "is_superuser": True,
            "tenant_id": tenant_id,
        },
        headers={"Authorization": "Bearer test-token"},
    )
    assert resp2.status_code == 201, (await resp2.get_data()).decode()[:300]
    admin_body = json.loads(await resp2.get_data())

    await _drain_background_tasks()
    async with app.app_context():
        db = current_app.db
        user_deliveries = await _wait_for_deliveries(db, w_user, "user.created", 2)
        admin_deliveries = await _wait_for_deliveries(
            db, w_admin, "admin_user.created", 1
        )
        assert len(user_deliveries) == 2
        assert len(admin_deliveries) == 1
        assert admin_deliveries[0].request_payload["user"]["id"] == admin_body["id"]
        assert admin_deliveries[0].request_payload["user"]["is_admin"] is True


@pytest.mark.asyncio
@patch(ENGINE_HTTP, new_callable=WebhookPostRecorder)
@patch("apps.api.auth.decorators.get_current_user")
async def test_users_create_succeeds_even_when_webhook_fails(
    mock_get_user, mock_post, async_client, app
):
    mock_post.side_effect = RuntimeError("webhook endpoint unreachable")

    async with app.app_context():
        db = current_app.db
        tenant_id = _make_tenant(db, "Route Users Webhook Failure")
        w_user = _insert_webhook(db, tenant_id, ["user.created"])

    mock_get_user.return_value = MagicMock(
        id=903, is_superuser=True, tenant_id=tenant_id
    )

    username = f"resilient-{uuid4().hex[:8]}"
    resp = await async_client.post(
        "/api/v1/users",
        json={
            "username": username,
            "password": "Password123!",
            "email": f"{username}@example.com",
            "tenant_id": tenant_id,
        },
        headers={"Authorization": "Bearer test-token"},
    )
    assert resp.status_code == 201, (await resp.get_data()).decode()[:300]

    await _drain_background_tasks()
    async with app.app_context():
        db = current_app.db
        deliveries = await _wait_for_deliveries(db, w_user, "user.created", 1)
        assert len(deliveries) == 1
        assert deliveries[0].status == "failed"
        assert "webhook endpoint unreachable" in (deliveries[0].error_message or "")


@pytest.mark.asyncio
@patch(ENGINE_HTTP, new_callable=WebhookPostRecorder)
async def test_auth_register_fires_only_user_created(mock_post, async_client, app):
    mock_post.return_value = MagicMock(status_code=200, text="ok")

    async with app.app_context():
        db = current_app.db
        default_tenant = db(db.tenants.id > 0).select(limitby=(0, 1)).first()
        if not default_tenant:
            default_tid = _make_tenant(db, "Default Register Tenant")
        else:
            default_tid = default_tenant.id

        w_user = _insert_webhook(db, default_tid, ["user.created"])
        w_admin = _insert_webhook(db, default_tid, ["admin_user.created"])

    try:
        email = f"register-{uuid4().hex[:8]}@example.com"
        resp = await async_client.post(
            "/api/v1/auth/register",
            json={
                "username": email,
                "email": email,
                "password": "Password123!",
                "full_name": "Registered User",
            },
        )
        assert resp.status_code == 201, (await resp.get_data()).decode()[:300]
        body = json.loads(await resp.get_data())

        await _drain_background_tasks()
        async with app.app_context():
            db = current_app.db
            deliveries = await _wait_for_deliveries(db, w_user, "user.created", 1)
            assert len(deliveries) == 1
            payload = deliveries[0].request_payload
            assert payload["user"]["id"] == body["user"]["id"]
            assert payload["user"]["user_source"] == "identity"
            assert payload["user"]["auth_provider"] == "local"
            assert payload["user"]["portal_role"] == "observer"
            assert payload["user"]["is_admin"] is False
            assert not (_collect_keys(payload) & _FORBIDDEN_PII_KEYS)
            assert len(db(db.webhook_deliveries.webhook_id == w_admin).select()) == 0
    finally:
        async with app.app_context():
            db = current_app.db
            db(db.webhooks.id.belongs([w_user, w_admin])).update(is_active=False)
            db.commit()


@pytest.mark.asyncio
@patch(ENGINE_HTTP, new_callable=WebhookPostRecorder)
@patch("apps.api.auth.decorators.get_current_user")
async def test_identities_create_route_fires_user_created(
    mock_get_user, mock_post, async_client, app
):
    mock_post.return_value = MagicMock(status_code=200, text="ok")

    async with app.app_context():
        db = current_app.db
        tenant_id = _make_tenant(db, "Route Identities Create")
        w_user = _insert_webhook(db, tenant_id, ["user.created"])
        w_admin = _insert_webhook(db, tenant_id, ["admin_user.created"])

    mock_get_user.return_value = MagicMock(
        id=902, is_superuser=True, tenant_id=tenant_id
    )

    username = f"svc-acct-{uuid4().hex[:8]}"
    resp = await async_client.post(
        "/api/v1/identities",
        json={
            "username": username,
            "identity_type": "service_account",
            "auth_provider": "local",
            "password": "Password123!",
            "email": f"{username}@example.com",
            "tenant_id": tenant_id,
        },
        headers={"Authorization": "Bearer test-token"},
    )
    assert resp.status_code == 201, (await resp.get_data()).decode()[:300]
    body = json.loads(await resp.get_data())

    await _drain_background_tasks()
    async with app.app_context():
        db = current_app.db
        deliveries = await _wait_for_deliveries(db, w_user, "user.created", 1)
        assert len(deliveries) == 1
        payload = deliveries[0].request_payload
        assert payload["user"]["id"] == body["id"]
        assert payload["user"]["user_source"] == "identity"
        assert payload["user"]["identity_type"] == "service_account"
        assert payload["user"]["auth_provider"] == "local"
        assert payload["user"]["is_admin"] is False
        assert payload["actor"] == 902
        assert not (_collect_keys(payload) & _FORBIDDEN_PII_KEYS)
        assert len(db(db.webhook_deliveries.webhook_id == w_admin).select()) == 0


@pytest.mark.asyncio
@patch(
    "apps.api.services.portal_auth.service.check_limit",
    new_callable=AsyncMock,
    return_value=None,
)
@patch(ENGINE_HTTP, new_callable=WebhookPostRecorder)
async def test_portal_auth_create_portal_user_regular_tenant_and_global_admin(
    mock_post, _mock_check_limit, app
):
    mock_post.return_value = MagicMock(status_code=200, text="ok")

    async with app.app_context():
        db = current_app.db
        tenant_id = _make_tenant(db, "Portal Auth Create")
        w_user = _insert_webhook(db, tenant_id, ["user.created"])
        w_admin = _insert_webhook(db, tenant_id, ["admin_user.created"])

        # 1. Regular reader -> user.created only
        reg = await PortalAuthService.create_portal_user(
            tenant_id=tenant_id,
            email=f"reader-{uuid4().hex[:8]}@example.com",
            password="Password123!",
            tenant_role="reader",
        )
        assert "id" in reg
        await _drain_background_tasks()

        assert len(await _wait_for_deliveries(db, w_user, "user.created", 1)) == 1
        assert len(db(db.webhook_deliveries.webhook_id == w_admin).select()) == 0

        # 2. Tenant admin -> user.created + admin_user.created
        t_admin = await PortalAuthService.create_portal_user(
            tenant_id=tenant_id,
            email=f"tadmin-{uuid4().hex[:8]}@example.com",
            password="Password123!",
            tenant_role="admin",
        )
        assert "id" in t_admin
        await _drain_background_tasks()

        assert len(await _wait_for_deliveries(db, w_user, "user.created", 2)) == 2
        admin_deliveries = await _wait_for_deliveries(
            db, w_admin, "admin_user.created", 1
        )
        assert len(admin_deliveries) == 1
        assert (
            admin_deliveries[0].request_payload["user"]["user_source"] == "portal_user"
        )
        assert admin_deliveries[0].request_payload["user"]["tenant_role"] == "admin"

        # 3. Global admin -> user.created + admin_user.created
        g_admin = await PortalAuthService.create_portal_user(
            tenant_id=tenant_id,
            email=f"gadmin-{uuid4().hex[:8]}@example.com",
            password="Password123!",
            tenant_role="reader",
            global_role="admin",
        )
        assert "id" in g_admin
        await _drain_background_tasks()

        assert len(await _wait_for_deliveries(db, w_user, "user.created", 3)) == 3
        assert (
            len(await _wait_for_deliveries(db, w_admin, "admin_user.created", 2)) == 2
        )


@pytest.mark.asyncio
@patch(ENGINE_HTTP, new_callable=WebhookPostRecorder)
async def test_scim_create_user_fires_only_on_creation(mock_post, app):
    mock_post.return_value = MagicMock(status_code=200, text="ok")

    async with app.app_context():
        db = current_app.db
        tenant_id = _make_tenant(db, "SCIM Create User")
        w_user = _insert_webhook(db, tenant_id, ["user.created"])
        w_admin = _insert_webhook(db, tenant_id, ["admin_user.created"])

        email = f"scim-{uuid4().hex[:8]}@example.com"
        res = SCIMService.create_user(
            tenant_id,
            {
                "emails": [{"value": email, "primary": True}],
                "displayName": "SCIM User",
                "active": True,
            },
        )
        assert "error" not in res
        await _drain_background_tasks()

        deliveries = await _wait_for_deliveries(db, w_user, "user.created", 1)
        assert len(deliveries) == 1
        payload = deliveries[0].request_payload
        assert payload["user"]["user_source"] == "portal_user"
        assert payload["user"]["auth_provider"] == "scim"
        assert payload["user"]["tenant_role"] == "reader"
        assert payload["user"]["is_admin"] is False
        assert not (_collect_keys(payload) & _FORBIDDEN_PII_KEYS)
        assert len(db(db.webhook_deliveries.webhook_id == w_admin).select()) == 0

        # Duplicate SCIM create returns 409 and does NOT fire webhook again
        dup = SCIMService.create_user(
            tenant_id,
            {"emails": [{"value": email, "primary": True}]},
        )
        assert dup.get("status") == 409
        await _drain_background_tasks()
        assert len(db(db.webhook_deliveries.webhook_id == w_user).select()) == 1


@pytest.mark.asyncio
@patch(ENGINE_HTTP, new_callable=WebhookPostRecorder)
async def test_oidc_jit_fires_only_for_new_users(mock_post, app):
    mock_post.return_value = MagicMock(status_code=200, text="ok")

    async with app.app_context():
        db = current_app.db
        tenant_id = _make_tenant(db, "OIDC JIT Webhook")
        w_user = _insert_webhook(db, tenant_id, ["user.created"])
        w_admin = _insert_webhook(db, tenant_id, ["admin_user.created"])

        email = f"oidc-{uuid4().hex[:8]}@example.com"
        idp_cfg = {"default_role": "reader", "attribute_mappings": {}}

        created = OIDCService.jit_provision_user(
            tenant_id=tenant_id,
            idp_config=idp_cfg,
            id_token_claims={"email": email, "name": "OIDC User", "sub": "sub-1"},
        )
        assert created.get("jit_provisioned") is True
        await _drain_background_tasks()

        deliveries = await _wait_for_deliveries(db, w_user, "user.created", 1)
        assert len(deliveries) == 1
        assert deliveries[0].request_payload["user"]["auth_provider"] == "oidc"
        assert deliveries[0].request_payload["user"]["is_admin"] is False
        assert not (_collect_keys(deliveries[0].request_payload) & _FORBIDDEN_PII_KEYS)
        assert len(db(db.webhook_deliveries.webhook_id == w_admin).select()) == 0

        # Returning user login -> MUST NOT fire webhook
        mock_post.reset_mock()
        returning = OIDCService.jit_provision_user(
            tenant_id=tenant_id,
            idp_config=idp_cfg,
            id_token_claims={"email": email, "name": "OIDC User", "sub": "sub-1"},
        )
        assert "jit_provisioned" not in returning
        await _drain_background_tasks()
        mock_post.assert_not_called()
        assert len(db(db.webhook_deliveries.webhook_id == w_user).select()) == 1

        # New admin via OIDC default_role="admin" -> fires both
        admin_email = f"oidc-adm-{uuid4().hex[:8]}@example.com"
        OIDCService.jit_provision_user(
            tenant_id=tenant_id,
            idp_config={"default_role": "admin", "attribute_mappings": {}},
            id_token_claims={
                "email": admin_email,
                "name": "OIDC Admin",
                "sub": "sub-2",
            },
        )
        await _drain_background_tasks()
        assert len(await _wait_for_deliveries(db, w_user, "user.created", 2)) == 2
        assert (
            len(await _wait_for_deliveries(db, w_admin, "admin_user.created", 1)) == 1
        )


@pytest.mark.asyncio
@patch(ENGINE_HTTP, new_callable=WebhookPostRecorder)
async def test_saml_jit_fires_only_for_new_users(mock_post, app):
    mock_post.return_value = MagicMock(status_code=200, text="ok")

    async with app.app_context():
        db = current_app.db
        tenant_id = _make_tenant(db, "SAML JIT Webhook")
        w_user = _insert_webhook(db, tenant_id, ["user.created"])
        w_admin = _insert_webhook(db, tenant_id, ["admin_user.created"])

        email = f"saml-{uuid4().hex[:8]}@example.com"
        idp_cfg = {"default_role": "reader", "attribute_mappings": {}}

        created = SAMLService.jit_provision_user(
            tenant_id=tenant_id,
            email=email,
            idp_config=idp_cfg,
            attributes={"name": "SAML User"},
        )
        assert created.get("jit_provisioned") is True
        await _drain_background_tasks()

        deliveries = await _wait_for_deliveries(db, w_user, "user.created", 1)
        assert len(deliveries) == 1
        assert deliveries[0].request_payload["user"]["auth_provider"] == "saml"
        assert deliveries[0].request_payload["user"]["is_admin"] is False
        assert not (_collect_keys(deliveries[0].request_payload) & _FORBIDDEN_PII_KEYS)
        assert len(db(db.webhook_deliveries.webhook_id == w_admin).select()) == 0

        # Returning user login -> MUST NOT fire webhook
        mock_post.reset_mock()
        returning = SAMLService.jit_provision_user(
            tenant_id=tenant_id,
            email=email,
            idp_config=idp_cfg,
            attributes={"name": "SAML User"},
        )
        assert "jit_provisioned" not in returning
        await _drain_background_tasks()
        mock_post.assert_not_called()
        assert len(db(db.webhook_deliveries.webhook_id == w_user).select()) == 1

        # New admin via SAML default_role="admin" -> fires both
        admin_email = f"saml-adm-{uuid4().hex[:8]}@example.com"
        SAMLService.jit_provision_user(
            tenant_id=tenant_id,
            email=admin_email,
            idp_config={"default_role": "admin", "attribute_mappings": {}},
            attributes={"name": "SAML Admin"},
        )
        await _drain_background_tasks()
        assert len(await _wait_for_deliveries(db, w_user, "user.created", 2)) == 2
        assert (
            len(await _wait_for_deliveries(db, w_admin, "admin_user.created", 1)) == 1
        )
