"""Issue-assignment webhook dispatch: matches a per-issue assignment event
against every active, filter-matching webhook in its tenant and delivers
`issue.assigned`, HMAC-signed and non-blocking.

Two call sites can change an issue's assignee and each builds an
AssignmentEvent and fires send_issue_assigned_webhooks (Task 5) after its DB
write commits: apps/api/modules/issues/routes/issues.py::create_issue and
apps/api/modules/issues/routes/issues.py::update_issue (only when the
assignee actually changes).
"""

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timezone
from typing import Any, Dict, List, Optional

from apps.api.services.webhooks.service import (
    WebhookService,
    _parse_json_column,
    tenant_ref_nowait,
)
from shared.webhooks.delivery import run_in_io_pool, schedule_background, scrub_error

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
    to a worker thread (see send_issue_assigned_webhooks).
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


async def _dispatch_one(
    db: Any, webhook: Any, payload: dict[str, Any]
) -> dict[str, Any]:
    """Deliver `payload` to a single webhook and record the attempt.

    Delegates to WebhookService.deliver_event, i.e. the shared delivery
    engine: the target is resolved once, vetted, and the connection is made
    to that vetted address (a second DNS answer cannot redirect it to an
    internal host), with the engine's retry/backoff, timeouts, signing and
    telemetry. The outbox row is written first so an interrupted delivery
    is resumable. Never raises: a failure is recorded on the
    webhook_deliveries row and returned, so one webhook's failure can never
    break another's delivery or the caller's fire-and-forget task.
    """
    outcome = await WebhookService(db).deliver_event(
        webhook, ASSIGNMENT_EVENT_TYPE, payload, kind="issue_assigned"
    )
    result: dict[str, Any] = {
        "webhook_id": webhook.id,
        "success": bool(outcome.get("success")),
    }
    if "delivery_id" in outcome:
        result["delivery_id"] = outcome["delivery_id"]
    if "status_code" in outcome:
        result["http_status"] = outcome["status_code"]
    if not result["success"]:
        result["error"] = outcome.get("error")
        logger.warning(
            "issue_assigned_webhook_delivery_failed",
            extra={"webhook_id": webhook.id, "error": str(result["error"])[:200]},
        )
    return result


async def send_issue_assigned_webhooks(
    db: Any, event: AssignmentEvent
) -> list[dict[str, Any]]:
    """Match `event` against every active webhook in its tenant and deliver
    issue.assigned to each match, HMAC-signed, non-blocking.

    Fire-and-forget: callers hand this to schedule_issue_assigned_webhooks
    right after the assignment-changing DB write commits. Database work
    runs on the delivery engine's bounded thread pool and HTTP on the event
    loop, so nothing here blocks the loop; a failure in matching or in any
    single delivery is caught and logged rather than raised, so it can
    never fail the issue create/update/intake-submit it was triggered by.
    """
    try:
        matched = await run_in_io_pool(find_matching_webhooks, db, event)
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
            result = await _dispatch_one(db, webhook, payload)
        except Exception as exc:  # pragma: no cover - _dispatch_one already catches
            logger.warning(
                "issue_assigned_webhook_dispatch_failed",
                extra={"webhook_id": webhook.id, "error": scrub_error(exc)[:200]},
            )
            result = {
                "webhook_id": webhook.id,
                "success": False,
                "error": scrub_error(exc),
            }
        results.append(result)
    return results


def schedule_issue_assigned_webhooks(db: Any, event: AssignmentEvent) -> bool:
    """Queue issue.assigned delivery for `event` off the request path.

    Admission-controlled through the delivery engine: when the global or
    the tenant's pending cap is reached the event is dropped (WARN log +
    shed metric) and False is returned - never an exception, so an
    overloaded webhook queue cannot fail the issue write that triggered it.
    Must be called from the running event loop.
    """
    task = schedule_background(
        send_issue_assigned_webhooks(db, event),
        name="webhook-issue-assigned",
        tenant=tenant_ref_nowait(event.tenant_id),
    )
    return task is not None
