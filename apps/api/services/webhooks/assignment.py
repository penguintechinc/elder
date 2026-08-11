"""Issue-assignment webhook dispatch: matches a per-issue assignment event
against every active, filter-matching webhook in its tenant and delivers
`issue.assigned`, HMAC-signed and non-blocking.

Three call sites can change an issue's assignee and each builds an
AssignmentEvent and fires send_issue_assigned_webhooks (Task 5) after its DB
write commits: apps/api/modules/issues/routes/issues.py::create_issue,
apps/api/modules/issues/routes/issues.py::update_issue (only when the
assignee actually changes), and
apps/api/modules/helpdesk/routes/intake_forms.py::submit_public_intake_form
(when the form has a default assignee configured).
"""

import asyncio
import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timezone
from typing import Any, Dict, List, Optional

import requests

from apps.api.services.webhooks.service import _parse_json_column, generate_signature

logger = logging.getLogger(__name__)

ASSIGNMENT_EVENT_TYPE = "issue.assigned"


@dataclass(slots=True)
class AssignmentEvent:
    """A single issue-assignment occurrence to match against tenant webhooks.

    Built by each of the three assignment call sites and passed to
    send_issue_assigned_webhooks for matching + delivery.
    """

    issue_id: int
    village_id: str | None
    issue_type: str
    status: str
    assignee_type: str | None
    assignee_id: int | None
    tenant_id: int
    actor_id: int | None


def webhook_matches_assignment(webhook: Any, event: AssignmentEvent) -> bool:
    """Return True if `webhook`'s filters (if any) match `event`.

    Filters are additive (AND, not OR): an unset filter (None) matches
    anything; a set filter must match exactly. A webhook with no filters set
    at all matches every assignment in its tenant. Pure function — no DB or
    network access, so it's fully unit-testable against a plain stand-in
    object exposing the three filter_* attributes.
    """
    if (
        webhook.filter_issue_type is not None
        and webhook.filter_issue_type != event.issue_type
    ):
        return False
    if (
        webhook.filter_assignee_type is not None
        and webhook.filter_assignee_type != event.assignee_type
    ):
        return False
    if (
        webhook.filter_assignee_id is not None
        and webhook.filter_assignee_id != event.assignee_id
    ):
        return False
    return True


def find_matching_webhooks(db: Any, event: AssignmentEvent) -> list[Any]:
    """Return every active, tenant-scoped webhook subscribed to issue.assigned
    whose filters match `event`. Synchronous/blocking — callers offload this
    via asyncio.to_thread (see send_issue_assigned_webhooks).
    """
    candidates = db(
        (db.webhooks.tenant_id == event.tenant_id) & (db.webhooks.is_active == True)  # noqa: E712
    ).select()

    matched = []
    for webhook in candidates:
        # JSON columns may come back from a pydal SELECT already parsed or
        # as a raw string — see WebhookService._parse_json_column, which
        # this reuses so the two callers never disagree on decoding.
        events = _parse_json_column(webhook.events) or []
        if ASSIGNMENT_EVENT_TYPE not in events:
            continue
        if webhook_matches_assignment(webhook, event):
            matched.append(webhook)
    return matched


def build_assignment_payload(event: AssignmentEvent) -> dict[str, Any]:
    """Build the issue.assigned webhook payload body (spec §7)."""
    return {
        "event": ASSIGNMENT_EVENT_TYPE,
        "issue": {
            "id": event.issue_id,
            "village_id": event.village_id,
            "issue_type": event.issue_type,
            "status": event.status,
            "assignee": {"type": event.assignee_type, "id": event.assignee_id},
        },
        "actor": event.actor_id,
        "tenant_id": event.tenant_id,
        "ts": datetime.now(UTC).isoformat(),
    }


def _dispatch_one(db: Any, webhook: Any, payload: dict[str, Any]) -> dict[str, Any]:
    """Deliver `payload` to a single webhook synchronously and record the attempt.

    Runs entirely inside asyncio.to_thread (see send_issue_assigned_webhooks)
    — blocking I/O here never touches the event loop. Never raises: any
    failure (network error, non-2xx response) is captured on the
    webhook_deliveries row and returned in the result dict instead of
    propagating, so one webhook's failure can never break another's
    delivery or the caller's fire-and-forget task.

    The payload is serialized to a string exactly once and that same string
    is both signed and sent as the raw request body (`data=`, not `json=`).
    `requests`' own `json=` re-serialization doesn't preserve key order/
    formatting, so signing `payload_str` while posting `json=payload` would
    sign different bytes than a receiver actually gets — the HMAC would
    never verify.
    """
    payload_str = json.dumps(payload, sort_keys=True)
    headers = {
        "Content-Type": "application/json",
        "User-Agent": "Elder-Webhook/1.2.0",
    }
    if webhook.headers:
        # JSON columns may come back from a pydal SELECT already parsed or
        # as a raw string — see _parse_json_column; mirrors
        # WebhookService._attempt_delivery's identical header-merge.
        headers.update(_parse_json_column(webhook.headers))
    if webhook.secret:
        headers["X-Elder-Signature"] = generate_signature(webhook.secret, payload_str)

    now = datetime.now(UTC)
    delivery_id = db.webhook_deliveries.insert(
        webhook_id=webhook.id,
        event_type=ASSIGNMENT_EVENT_TYPE,
        request_payload=payload,
        attempt_count=1,
        created_at=now,
    )
    db.commit()

    try:
        response = requests.post(
            webhook.url, data=payload_str.encode("utf-8"), headers=headers, timeout=30
        )
        success = 200 <= response.status_code < 300
        db(db.webhook_deliveries.id == delivery_id).update(
            status="success" if success else "failed",
            http_status=response.status_code,
            response_body=response.text[:1000],
            delivered_at=datetime.now(UTC) if success else None,
        )
        db.commit()
        return {
            "webhook_id": webhook.id,
            "delivery_id": delivery_id,
            "success": success,
            "http_status": response.status_code,
        }
    except Exception as exc:
        db(db.webhook_deliveries.id == delivery_id).update(
            status="failed",
            error_message=str(exc)[:500],
        )
        db.commit()
        logger.warning(
            "issue_assigned_webhook_delivery_failed",
            extra={"webhook_id": webhook.id, "error": str(exc)[:200]},
        )
        return {
            "webhook_id": webhook.id,
            "delivery_id": delivery_id,
            "success": False,
            "error": str(exc),
        }


async def send_issue_assigned_webhooks(
    db: Any, event: AssignmentEvent
) -> list[dict[str, Any]]:
    """Match `event` against every active webhook in its tenant and deliver
    issue.assigned to each match, HMAC-signed, non-blocking.

    Fire-and-forget: callers wrap this in asyncio.create_task from an async
    route handler right after the assignment-changing DB write commits.
    Every blocking call (DB queries + requests.post) runs inside
    asyncio.to_thread so this never blocks the event loop; a failure in
    matching or in any single delivery is caught and logged rather than
    raised, so it can never fail the issue create/update/intake-submit it
    was triggered by.
    """
    try:
        matched = await asyncio.to_thread(find_matching_webhooks, db, event)
    except Exception as exc:  # pragma: no cover - defensive; matching is pure/cheap
        logger.warning(
            "issue_assigned_webhook_match_failed", extra={"error": str(exc)[:200]}
        )
        return []

    if not matched:
        return []

    try:
        payload = build_assignment_payload(event)
    except (
        Exception
    ) as exc:  # pragma: no cover - defensive; payload build is pure/cheap
        logger.warning(
            "issue_assigned_webhook_payload_build_failed",
            extra={"error": str(exc)[:200]},
        )
        return []

    results = []
    for webhook in matched:
        try:
            result = await asyncio.to_thread(_dispatch_one, db, webhook, payload)
        except Exception as exc:  # pragma: no cover - _dispatch_one already catches
            logger.warning(
                "issue_assigned_webhook_dispatch_failed",
                extra={"webhook_id": webhook.id, "error": str(exc)[:200]},
            )
            result = {"webhook_id": webhook.id, "success": False, "error": str(exc)}
        results.append(result)
    return results
