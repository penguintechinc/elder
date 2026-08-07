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

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from apps.api.services.webhooks.service import _parse_json_column

ASSIGNMENT_EVENT_TYPE = "issue.assigned"


@dataclass(slots=True)
class AssignmentEvent:
    """A single issue-assignment occurrence to match against tenant webhooks.

    Built by each of the three assignment call sites and passed to
    send_issue_assigned_webhooks for matching + delivery.
    """

    issue_id: int
    village_id: Optional[str]
    issue_type: str
    status: str
    assignee_type: Optional[str]
    assignee_id: Optional[int]
    tenant_id: int
    actor_id: Optional[int]


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


def find_matching_webhooks(db: Any, event: AssignmentEvent) -> List[Any]:
    """Return every active, tenant-scoped webhook subscribed to issue.assigned
    whose filters match `event`. Synchronous/blocking — callers offload this
    via asyncio.to_thread (see send_issue_assigned_webhooks).
    """
    candidates = db(
        (db.webhooks.tenant_id == event.tenant_id)
        & (db.webhooks.is_active == True)  # noqa: E712
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


def build_assignment_payload(event: AssignmentEvent) -> Dict[str, Any]:
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
        "ts": datetime.now(timezone.utc).isoformat(),
    }
