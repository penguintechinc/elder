"""Pure unit tests for issue.assigned webhook matching (Plan 05). No DB,
no network — webhook_matches_assignment takes a plain object with the four
filter attributes, so a lightweight stand-in is enough.

A second group below (test_find_matching_webhooks_*) covers
find_matching_webhooks's DB-touching half — tenant scoping, is_active, and
event-subscription — which webhook_matches_assignment itself never sees.
Those use real WebhookService-created rows against a freshly minted tenant
per test (see _make_tenant) — webhooks.tenant_id is a real foreign key into
tenants, and a fresh tenant is also guaranteed unused by any other test,
since the test DB isn't truncated between test functions within a session.
"""

from dataclasses import dataclass
from typing import Optional
from uuid import uuid4

import pytest
from quart import current_app


@dataclass
class _FakeWebhook:
    filter_issue_type: str | None = None
    filter_assignee_type: str | None = None
    filter_assignee_id: int | None = None


def _event(**overrides):
    from apps.api.services.webhooks.assignment import AssignmentEvent

    defaults = dict(
        issue_id=1,
        village_id="00000001-0000000000000001",
        issue_type="SUPPORT",
        status="OPEN",
        assignee_type="identity",
        assignee_id=42,
        tenant_id=1,
        actor_id=7,
    )
    defaults.update(overrides)
    return AssignmentEvent(**defaults)


def test_empty_filter_matches_any_assignment():
    from apps.api.services.webhooks.assignment import webhook_matches_assignment

    webhook = _FakeWebhook()
    assert webhook_matches_assignment(webhook, _event()) is True
    assert (
        webhook_matches_assignment(
            webhook, _event(issue_type="BUG", assignee_type="org_unit", assignee_id=99)
        )
        is True
    )


def test_issue_type_filter_only():
    from apps.api.services.webhooks.assignment import webhook_matches_assignment

    webhook = _FakeWebhook(filter_issue_type="SUPPORT")
    assert webhook_matches_assignment(webhook, _event(issue_type="SUPPORT")) is True
    assert webhook_matches_assignment(webhook, _event(issue_type="BUG")) is False


def test_assignee_filter_only():
    from apps.api.services.webhooks.assignment import webhook_matches_assignment

    webhook = _FakeWebhook(filter_assignee_type="org_unit", filter_assignee_id=5)
    assert (
        webhook_matches_assignment(
            webhook, _event(assignee_type="org_unit", assignee_id=5)
        )
        is True
    )
    assert (
        webhook_matches_assignment(
            webhook, _event(assignee_type="org_unit", assignee_id=6)
        )
        is False
    )
    assert (
        webhook_matches_assignment(
            webhook, _event(assignee_type="identity", assignee_id=5)
        )
        is False
    )


def test_both_filters_are_additive_and():
    from apps.api.services.webhooks.assignment import webhook_matches_assignment

    webhook = _FakeWebhook(
        filter_issue_type="SUPPORT",
        filter_assignee_type="identity",
        filter_assignee_id=42,
    )
    # Both match -> True
    assert (
        webhook_matches_assignment(
            webhook,
            _event(issue_type="SUPPORT", assignee_type="identity", assignee_id=42),
        )
        is True
    )
    # issue_type matches but assignee doesn't -> False
    assert (
        webhook_matches_assignment(
            webhook,
            _event(issue_type="SUPPORT", assignee_type="identity", assignee_id=43),
        )
        is False
    )
    # assignee matches but issue_type doesn't -> False
    assert (
        webhook_matches_assignment(
            webhook, _event(issue_type="BUG", assignee_type="identity", assignee_id=42)
        )
        is False
    )


def test_build_assignment_payload_shape():
    from apps.api.services.webhooks.assignment import build_assignment_payload

    payload = build_assignment_payload(_event())
    assert payload["event"] == "issue.assigned"
    assert payload["issue"]["id"] == 1
    assert payload["issue"]["village_id"] == "00000001-0000000000000001"
    assert payload["issue"]["issue_type"] == "SUPPORT"
    assert payload["issue"]["status"] == "OPEN"
    assert payload["issue"]["assignee"] == {"type": "identity", "id": 42}
    assert payload["actor"] == 7
    assert payload["tenant_id"] == 1
    assert "ts" in payload


# ---------------------------------------------------------------------------
# find_matching_webhooks — DB-integration tests. webhooks.tenant_id is a real
# foreign key into tenants, so each test mints its own fresh tenant row(s)
# via _make_tenant rather than a hardcoded literal — that's both FK-valid
# and guaranteed unused by any other test, since the test DB isn't truncated
# between test functions within a session. Webhooks are created via
# WebhookService, mirroring tests/unit/test_webhook_service.py's fixture
# pattern exactly.
# ---------------------------------------------------------------------------


def _make_tenant(db, name: str) -> int:
    """Create a dedicated tenant row and return its id.

    Mirrors tests/unit/test_helpdesk_tenant_isolation.py's _foreign_tenant
    helper. A fresh tenant per test (or per side of a cross-tenant test)
    guarantees the assignment-matching tests below never collide with
    tenant_ids used elsewhere in the suite.
    """
    tid = db.tenants.insert(
        name=name,
        slug=f"{name.lower().replace(' ', '-')}-{uuid4().hex[:8]}",
        is_active=True,
    )
    db.commit()
    return tid


@pytest.mark.asyncio
async def test_find_matching_webhooks_excludes_other_tenant(app):
    async with app.app_context():
        from apps.api.services.webhooks.assignment import find_matching_webhooks
        from apps.api.services.webhooks.service import WebhookService

        db = current_app.db
        service = WebhookService(db)

        own_tenant_id = _make_tenant(db, "Assignment Match Own")
        other_tenant_id = _make_tenant(db, "Assignment Match Other")

        own = service.create_webhook(
            tenant_id=own_tenant_id,
            name="Own tenant webhook",
            url="https://hooks.example.com/own",
            events=["issue.assigned"],
        )
        other = service.create_webhook(
            tenant_id=other_tenant_id,
            name="Other tenant webhook",
            url="https://hooks.example.com/other",
            events=["issue.assigned"],
        )

        event = _event(tenant_id=own_tenant_id)
        matched_ids = {w.id for w in find_matching_webhooks(db, event)}

        assert own["id"] in matched_ids
        assert other["id"] not in matched_ids


@pytest.mark.asyncio
async def test_find_matching_webhooks_excludes_inactive(app):
    async with app.app_context():
        from apps.api.services.webhooks.assignment import find_matching_webhooks
        from apps.api.services.webhooks.service import WebhookService

        db = current_app.db
        service = WebhookService(db)

        tenant_id = _make_tenant(db, "Assignment Match Inactive")

        webhook = service.create_webhook(
            tenant_id=tenant_id,
            name="Disabled webhook",
            url="https://hooks.example.com/disabled",
            events=["issue.assigned"],
        )
        service.update_webhook(webhook["id"], tenant_id=tenant_id, is_active=False)

        event = _event(tenant_id=tenant_id)
        matched_ids = {w.id for w in find_matching_webhooks(db, event)}

        assert webhook["id"] not in matched_ids


@pytest.mark.asyncio
async def test_find_matching_webhooks_excludes_unsubscribed_event(app):
    async with app.app_context():
        from apps.api.services.webhooks.assignment import find_matching_webhooks
        from apps.api.services.webhooks.service import WebhookService

        db = current_app.db
        service = WebhookService(db)

        tenant_id = _make_tenant(db, "Assignment Match Unsubscribed")

        webhook = service.create_webhook(
            tenant_id=tenant_id,
            name="Not subscribed to assignment",
            url="https://hooks.example.com/unsubscribed",
            events=["issue.created"],
        )

        event = _event(tenant_id=tenant_id)
        matched_ids = {w.id for w in find_matching_webhooks(db, event)}

        assert webhook["id"] not in matched_ids


@pytest.mark.asyncio
async def test_find_matching_webhooks_includes_matching_active_webhook(app):
    async with app.app_context():
        from apps.api.services.webhooks.assignment import find_matching_webhooks
        from apps.api.services.webhooks.service import WebhookService

        db = current_app.db
        service = WebhookService(db)

        tenant_id = _make_tenant(db, "Assignment Match Filter Match")

        webhook = service.create_webhook(
            tenant_id=tenant_id,
            name="Matching webhook",
            url="https://hooks.example.com/matching",
            events=["issue.assigned"],
            filter_issue_type="support",
        )

        event = _event(tenant_id=tenant_id, issue_type="SUPPORT")
        matched_ids = {w.id for w in find_matching_webhooks(db, event)}

        assert webhook["id"] in matched_ids


@pytest.mark.asyncio
async def test_find_matching_webhooks_excludes_filter_mismatch(app):
    async with app.app_context():
        from apps.api.services.webhooks.assignment import find_matching_webhooks
        from apps.api.services.webhooks.service import WebhookService

        db = current_app.db
        service = WebhookService(db)

        tenant_id = _make_tenant(db, "Assignment Match Filter Mismatch")

        webhook = service.create_webhook(
            tenant_id=tenant_id,
            name="Filter mismatch webhook",
            url="https://hooks.example.com/mismatch",
            events=["issue.assigned"],
            filter_issue_type="bug",
        )

        event = _event(tenant_id=tenant_id, issue_type="SUPPORT")
        matched_ids = {w.id for w in find_matching_webhooks(db, event)}

        assert webhook["id"] not in matched_ids
