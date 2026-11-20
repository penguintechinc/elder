"""User-creation webhook dispatch: matches user-creation events against
active tenant-scoped webhooks and delivers ``user.created`` and
``admin_user.created``, HMAC-signed and non-blocking.

Seven call sites can create users across the two auth tables (``identities``
and ``portal_users``) and each builds a :class:`UserCreatedEvent` and calls
:func:`schedule_user_created_webhooks` after its DB write commits.
"""

from __future__ import annotations

import asyncio
import json
import time
from contextlib import nullcontext
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import requests
import structlog

from apps.api.services.webhooks.service import _parse_json_column, generate_signature
from shared.observability import get_meter, get_tracer

logger = structlog.get_logger(__name__)

USER_CREATED_EVENT_TYPE = "user.created"
ADMIN_USER_CREATED_EVENT_TYPE = "admin_user.created"
USER_CREATED_EVENT_TYPES = (
    USER_CREATED_EVENT_TYPE,
    ADMIN_USER_CREATED_EVENT_TYPE,
)

# Keep strong references to fire-and-forget tasks until they finish so the
# event loop's weak-ref task table cannot garbage-collect them mid-flight.
_background_tasks: set[asyncio.Task[Any]] = set()

_tracer = get_tracer("elder")
_meter = get_meter("elder")
_delivery_counter = _meter.create_counter(
    name="elder.webhook.deliveries",
    description="Total outbound webhook delivery attempts",
    unit="1",
)
_delivery_duration_ms = _meter.create_histogram(
    name="elder.webhook.delivery.duration_ms",
    description="Duration of outbound webhook HTTP delivery attempts in ms",
    unit="ms",
)


def is_admin_user(
    *,
    is_superuser: bool = False,
    portal_role: str | None = None,
    global_role: str | None = None,
    tenant_role: str | None = None,
) -> bool:
    """Return True when the user attributes classify as an admin user.

    Mirrors the admin classification heuristics in
    ``apps/api/common/licensing/counters.py`` across both ``identities``
    (``is_superuser``, ``portal_role``) and ``portal_users``
    (``global_role``, ``tenant_role``).
    """
    return (
        bool(is_superuser)
        or (portal_role or "").strip().lower() in {"admin", "superadmin"}
        or (global_role or "").strip().lower() in {"admin", "superadmin"}
        or (tenant_role or "").strip().lower() == "admin"
    )


@dataclass(slots=True)
class UserCreatedEvent:
    """A single user-creation occurrence to match against tenant webhooks.

    Built by each user-creation call site after ``db.commit()`` and passed to
    :func:`schedule_user_created_webhooks` or :func:`send_user_created_webhooks`.
    """

    user_id: int
    tenant_id: int
    user_source: str
    identity_type: str
    auth_provider: str
    is_superuser: bool = False
    portal_role: str | None = None
    global_role: str | None = None
    tenant_role: str | None = None
    organization_id: int | None = None
    is_active: bool = True
    actor_id: int | None = None

    def __post_init__(self) -> None:
        if hasattr(self.identity_type, "value"):
            self.identity_type = str(self.identity_type.value)
        if hasattr(self.auth_provider, "value"):
            self.auth_provider = str(self.auth_provider.value)

    @property
    def is_admin(self) -> bool:
        """Return True when this created user is classified as an admin."""
        return is_admin_user(
            is_superuser=self.is_superuser,
            portal_role=self.portal_role,
            global_role=self.global_role,
            tenant_role=self.tenant_role,
        )


def event_types_for_user(event: UserCreatedEvent) -> list[str]:
    """Return the outbound event types to emit for ``event``."""
    if event.is_admin:
        return [USER_CREATED_EVENT_TYPE, ADMIN_USER_CREATED_EVENT_TYPE]
    return [USER_CREATED_EVENT_TYPE]


def find_matching_user_webhooks(db: Any, tenant_id: int, event_type: str) -> list[Any]:
    """Return active, tenant-scoped webhooks subscribed to ``event_type``."""
    if not tenant_id:
        return []

    candidates = db(
        (db.webhooks.tenant_id == tenant_id) & (db.webhooks.is_active == True)  # noqa: E712
    ).select()

    matched = []
    for webhook in candidates:
        events = _parse_json_column(webhook.events) or []
        if event_type in events:
            matched.append(webhook)
    return matched


def build_user_created_payload(
    event: UserCreatedEvent, event_type: str
) -> dict[str, Any]:
    """Build a strictly PII-free webhook payload for user-creation events."""
    return {
        "event": event_type,
        "user": {
            "id": event.user_id,
            "user_source": event.user_source,
            "identity_type": event.identity_type,
            "auth_provider": event.auth_provider,
            "is_admin": event.is_admin,
            "is_superuser": event.is_superuser,
            "portal_role": event.portal_role,
            "global_role": event.global_role,
            "tenant_role": event.tenant_role,
            "organization_id": event.organization_id,
            "is_active": event.is_active,
        },
        "actor": event.actor_id,
        "tenant_id": event.tenant_id,
        "ts": datetime.now(UTC).isoformat(),
    }


def extract_actor_id(user: Any) -> int | None:
    """Return an integer user ID from ``user``, ignoring unset or mock attrs."""
    raw = getattr(user, "id", None)
    if isinstance(raw, int) and not isinstance(raw, bool):
        return raw
    return None


def _record_delivery_telemetry(
    event_type: str, success: bool, duration_ms: float
) -> None:
    """Emit OTel metrics for a delivery attempt without ever raising."""
    try:
        attrs = {"event_type": event_type, "success": str(success).lower()}
        _delivery_counter.add(1, attributes=attrs)
        _delivery_duration_ms.record(duration_ms, attributes=attrs)
    except Exception:
        pass


def _set_span_attributes(span: Any, attrs: dict[str, Any]) -> None:
    """Safely attach span attributes without raising if telemetry is inactive."""
    if span is None:
        return
    try:
        for key, value in attrs.items():
            span.set_attribute(key, value)
    except Exception:
        pass


def _update_delivery_record(
    db: Any, webhook_id: Any, delivery_id: Any, **fields: Any
) -> None:
    """Persist a webhook_deliveries status update without raising."""
    try:
        db(db.webhook_deliveries.id == delivery_id).update(**fields)
        db.commit()
    except Exception as exc:
        logger.warning(
            "user_created_webhook_delivery_update_failed",
            webhook_id=webhook_id,
            delivery_id=delivery_id,
            error=str(exc)[:200],
        )


def _build_payload_safe(
    event: UserCreatedEvent, event_type: str
) -> dict[str, Any] | None:
    """Build a user-created payload, logging and returning None on error."""
    try:
        return build_user_created_payload(event, event_type)
    except Exception as exc:
        logger.warning(
            "user_created_webhook_payload_build_failed",
            event_type=event_type,
            tenant_id=event.tenant_id,
            error=str(exc)[:200],
        )
        return None


def _dispatch_failure_result(
    webhook: Any, event_type: str, exc: Exception
) -> dict[str, Any]:
    """Log a dispatch exception and return a uniform failure result dict."""
    webhook_id = getattr(webhook, "id", None)
    logger.warning(
        "user_created_webhook_dispatch_failed",
        webhook_id=webhook_id,
        event_type=event_type,
        error=str(exc)[:200],
    )
    return {
        "webhook_id": webhook_id,
        "event_type": event_type,
        "success": False,
        "error": str(exc),
    }


def _dispatch_user_webhook(
    db: Any, webhook: Any, event_type: str, payload: dict[str, Any]
) -> dict[str, Any]:
    """Deliver ``payload`` to one webhook synchronously and record the attempt.

    Serializes ``payload`` once with ``sort_keys=True`` and sends those exact
    bytes as ``data=`` so ``X-Elder-Signature`` verifies against the raw body.
    Never raises — delivery or persistence failures are captured and returned.
    """
    payload_str = json.dumps(payload, sort_keys=True)
    headers = {
        "Content-Type": "application/json",
        "User-Agent": "Elder-Webhook/1.2.0",
    }
    if webhook.headers:
        parsed_headers = _parse_json_column(webhook.headers)
        if parsed_headers:
            headers.update(parsed_headers)
    if webhook.secret:
        headers["X-Elder-Signature"] = generate_signature(webhook.secret, payload_str)

    now = datetime.now(UTC)
    try:
        delivery_id = db.webhook_deliveries.insert(
            webhook_id=webhook.id,
            event_type=event_type,
            request_payload=payload,
            attempt_count=1,
            created_at=now,
        )
        db.commit()
    except Exception as exc:
        logger.warning(
            "user_created_webhook_delivery_record_failed",
            webhook_id=getattr(webhook, "id", None),
            event_type=event_type,
            error=str(exc)[:200],
        )
        return {
            "webhook_id": getattr(webhook, "id", None),
            "event_type": event_type,
            "success": False,
            "error": str(exc),
        }

    try:
        span_ctx = _tracer.start_as_current_span("webhook.user_created.dispatch")
    except Exception:
        span_ctx = nullcontext()

    start = time.monotonic()
    with span_ctx as span:
        _set_span_attributes(
            span,
            {
                "webhook.id": int(webhook.id),
                "webhook.event_type": event_type,
                "webhook.tenant_id": int(payload["tenant_id"]),
            },
        )

        try:
            response = requests.post(
                webhook.url,
                data=payload_str.encode("utf-8"),
                headers=headers,
                timeout=30,
            )
            duration_ms = (time.monotonic() - start) * 1000.0
            success = 200 <= response.status_code < 300
            _set_span_attributes(
                span,
                {
                    "http.status_code": int(response.status_code),
                    "webhook.success": success,
                },
            )
            _record_delivery_telemetry(event_type, success, duration_ms)

            _update_delivery_record(
                db,
                webhook.id,
                delivery_id,
                status="success" if success else "failed",
                http_status=response.status_code,
                response_body=response.text[:1000],
                delivered_at=datetime.now(UTC) if success else None,
            )

            logger.info(
                "user_created_webhook_delivered",
                webhook_id=webhook.id,
                delivery_id=delivery_id,
                event_type=event_type,
                http_status=response.status_code,
                success=success,
            )
            return {
                "webhook_id": webhook.id,
                "delivery_id": delivery_id,
                "event_type": event_type,
                "success": success,
                "http_status": response.status_code,
            }
        except Exception as exc:
            duration_ms = (time.monotonic() - start) * 1000.0
            _set_span_attributes(span, {"webhook.success": False})
            _record_delivery_telemetry(event_type, False, duration_ms)
            _update_delivery_record(
                db,
                webhook.id,
                delivery_id,
                status="failed",
                error_message=str(exc)[:500],
            )
            logger.warning(
                "user_created_webhook_delivery_failed",
                webhook_id=webhook.id,
                delivery_id=delivery_id,
                event_type=event_type,
                error=str(exc)[:200],
            )
            return {
                "webhook_id": webhook.id,
                "delivery_id": delivery_id,
                "event_type": event_type,
                "success": False,
                "error": str(exc),
            }


def dispatch_user_created_webhooks_sync(
    db: Any, event: UserCreatedEvent
) -> list[dict[str, Any]]:
    """Synchronously match and deliver user-created webhooks without raising."""
    if not event.tenant_id:
        return []

    results: list[dict[str, Any]] = []
    for event_type in event_types_for_user(event):
        try:
            matched = find_matching_user_webhooks(db, event.tenant_id, event_type)
        except Exception as exc:
            logger.warning(
                "user_created_webhook_match_failed",
                event_type=event_type,
                tenant_id=event.tenant_id,
                error=str(exc)[:200],
            )
            continue

        if not matched:
            continue

        payload = _build_payload_safe(event, event_type)
        if payload is None:
            continue

        for webhook in matched:
            try:
                result = _dispatch_user_webhook(db, webhook, event_type, payload)
            except (
                Exception
            ) as exc:  # pragma: no cover - _dispatch_user_webhook catches
                result = _dispatch_failure_result(webhook, event_type, exc)
            results.append(result)
    return results


async def send_user_created_webhooks(
    db: Any, event: UserCreatedEvent
) -> list[dict[str, Any]]:
    """Match ``event`` against tenant webhooks and deliver asynchronously."""
    if not event.tenant_id:
        return []

    results: list[dict[str, Any]] = []
    for event_type in event_types_for_user(event):
        try:
            matched = await asyncio.to_thread(
                find_matching_user_webhooks, db, event.tenant_id, event_type
            )
        except Exception as exc:
            logger.warning(
                "user_created_webhook_match_failed",
                event_type=event_type,
                tenant_id=event.tenant_id,
                error=str(exc)[:200],
            )
            continue

        if not matched:
            continue

        payload = _build_payload_safe(event, event_type)
        if payload is None:
            continue

        for webhook in matched:
            try:
                result = await asyncio.to_thread(
                    _dispatch_user_webhook, db, webhook, event_type, payload
                )
            except (
                Exception
            ) as exc:  # pragma: no cover - _dispatch_user_webhook catches
                result = _dispatch_failure_result(webhook, event_type, exc)
            results.append(result)
    return results


def schedule_user_created_webhooks(db: Any, event: UserCreatedEvent) -> None:
    """Schedule non-blocking user-created webhook delivery when possible.

    When called inside an active asyncio event loop (async Quart routes/services),
    schedules :func:`send_user_created_webhooks` via ``loop.create_task``. When
    called from a synchronous context without a running loop (sync Quart routes
    or synchronous unit tests), dispatches directly via
    :func:`dispatch_user_created_webhooks_sync`. Never raises.
    """
    if not event.tenant_id:
        return

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        try:
            dispatch_user_created_webhooks_sync(db, event)
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning(
                "user_created_webhook_sync_dispatch_failed",
                tenant_id=event.tenant_id,
                error=str(exc)[:200],
            )
        return

    try:
        task = loop.create_task(send_user_created_webhooks(db, event))
        _background_tasks.add(task)
        task.add_done_callback(_background_tasks.discard)
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning(
            "user_created_webhook_schedule_failed",
            tenant_id=event.tenant_id,
            error=str(exc)[:200],
        )
