"""User-creation webhook dispatch: matches user-creation events against
active tenant-scoped webhooks and delivers ``user.created`` and
``admin_user.created``, HMAC-signed and non-blocking.

Seven call sites can create users across the two auth tables (``identities``
and ``portal_users``) and each builds a :class:`UserCreatedEvent` and calls
:func:`schedule_user_created_webhooks` after its DB write commits.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import structlog

from apps.api.services.webhooks.service import (
    WebhookService,
    _parse_json_column,
    tenant_ref_nowait,
)
from shared.webhooks import delivery as _delivery
from shared.webhooks.delivery import scrub_error

logger = structlog.get_logger(__name__)

USER_CREATED_EVENT_TYPE = "user.created"
ADMIN_USER_CREATED_EVENT_TYPE = "admin_user.created"
USER_CREATED_EVENT_TYPES = (
    USER_CREATED_EVENT_TYPE,
    ADMIN_USER_CREATED_EVENT_TYPE,
)

# The delivery engine owns the strong references to fire-and-forget tasks
# (and their admission accounting); this alias keeps the historical name.
_background_tasks = _delivery._background_tasks


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


async def _dispatch_user_webhook(
    db: Any, webhook: Any, event_type: str, payload: dict[str, Any]
) -> dict[str, Any]:
    """Deliver ``payload`` to one webhook through the shared delivery engine.

    Delegates to :meth:`WebhookService.deliver_event`: the outbox row is
    written first, the target is resolved once and the connection is pinned
    to the vetted address (so a changed DNS answer cannot steer it to an
    internal host), and retries, timeouts, signing and logs/metrics/traces
    are the engine's. Never raises - failures are captured and returned.
    """
    outcome = await WebhookService(db).deliver_event(
        webhook, event_type, payload, kind="user_created"
    )
    result: dict[str, Any] = {
        "webhook_id": webhook.id,
        "event_type": event_type,
        "success": bool(outcome.get("success")),
    }
    if "delivery_id" in outcome:
        result["delivery_id"] = outcome["delivery_id"]
    if "status_code" in outcome:
        result["http_status"] = outcome["status_code"]
    if result["success"]:
        logger.info(
            "user_created_webhook_delivered",
            webhook_id=webhook.id,
            delivery_id=result.get("delivery_id"),
            event_type=event_type,
            http_status=result.get("http_status"),
        )
    else:
        result["error"] = outcome.get("error")
        logger.warning(
            "user_created_webhook_delivery_failed",
            webhook_id=webhook.id,
            delivery_id=result.get("delivery_id"),
            event_type=event_type,
            error=str(result["error"])[:200],
        )
    return result


def dispatch_user_created_webhooks_sync(
    db: Any, event: UserCreatedEvent
) -> list[dict[str, Any]]:
    """Match and deliver user-created webhooks from synchronous code.

    Sync bridge for call sites with no running event loop: drives the same
    async engine on a private loop (see ``run_blocking``) instead of
    keeping a second, unpinned HTTP path. Never raises for delivery
    failures; calling it from a running loop is a programming error and
    fails loudly there.
    """
    if not event.tenant_id:
        return []
    return _delivery.run_blocking(send_user_created_webhooks(db, event))


async def send_user_created_webhooks(
    db: Any, event: UserCreatedEvent
) -> list[dict[str, Any]]:
    """Match ``event`` against tenant webhooks and deliver asynchronously."""
    if not event.tenant_id:
        return []

    results: list[dict[str, Any]] = []
    for event_type in event_types_for_user(event):
        try:
            matched = await _delivery.run_in_io_pool(
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
                result = await _dispatch_user_webhook(db, webhook, event_type, payload)
            except (
                Exception
            ) as exc:  # pragma: no cover - _dispatch_user_webhook catches
                result = _dispatch_failure_result(webhook, event_type, exc)
            results.append(result)
    return results


def schedule_user_created_webhooks(db: Any, event: UserCreatedEvent) -> None:
    """Schedule non-blocking user-created webhook delivery when possible.

    Inside a running event loop (async Quart routes/services) delivery is
    queued through the engine's admission control: if the global or the
    tenant's pending cap is reached the event is shed (WARN log + metric)
    and user creation carries on. With no running loop (sync call sites,
    synchronous unit tests) it is delivered inline via
    :func:`dispatch_user_created_webhooks_sync`. Never raises.
    """
    if not event.tenant_id:
        return

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        try:
            dispatch_user_created_webhooks_sync(db, event)
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning(
                "user_created_webhook_sync_dispatch_failed",
                tenant_id=event.tenant_id,
                error=scrub_error(exc)[:200],
            )
        return

    try:
        _delivery.schedule_background(
            send_user_created_webhooks(db, event),
            name="webhook-user-created",
            tenant=tenant_ref_nowait(event.tenant_id),
        )
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning(
            "user_created_webhook_schedule_failed",
            tenant_id=event.tenant_id,
            error=scrub_error(exc)[:200],
        )
